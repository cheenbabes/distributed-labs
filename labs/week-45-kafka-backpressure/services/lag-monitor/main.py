"""
Lag Monitor Service - Monitors Kafka consumer group lag and exposes metrics.
Provides alerts and actionable insights for backpressure management.
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, List, Optional

from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.admin import AdminClient
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "lag-monitor")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "orders")
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "order-processors")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "5"))
LAG_THRESHOLD_WARNING = int(os.getenv("LAG_THRESHOLD_WARNING", "100"))
LAG_THRESHOLD_CRITICAL = int(os.getenv("LAG_THRESHOLD_CRITICAL", "500"))

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(SERVICE_NAME)

# OpenTelemetry setup
resource = Resource.create({"service.name": SERVICE_NAME})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# Prometheus metrics
CONSUMER_LAG = Gauge(
    "kafka_consumer_group_lag",
    "Consumer group lag per partition",
    ["topic", "group_id", "partition"]
)
CONSUMER_LAG_TOTAL = Gauge(
    "kafka_consumer_group_lag_total",
    "Total consumer group lag across all partitions",
    ["topic", "group_id"]
)
PARTITION_END_OFFSET = Gauge(
    "kafka_partition_end_offset",
    "End offset for partition (latest message)",
    ["topic", "partition"]
)
PARTITION_COMMITTED_OFFSET = Gauge(
    "kafka_partition_committed_offset",
    "Committed offset for partition",
    ["topic", "group_id", "partition"]
)
LAG_STATUS = Gauge(
    "kafka_lag_status",
    "Lag status (0=ok, 1=warning, 2=critical)",
    ["topic", "group_id"]
)
LAG_RATE_OF_CHANGE = Gauge(
    "kafka_lag_rate_of_change",
    "Rate of lag change per second (positive=growing, negative=shrinking)",
    ["topic", "group_id"]
)
MONITOR_POLLS = Counter(
    "lag_monitor_polls_total",
    "Total lag monitor poll cycles"
)
MONITOR_ERRORS = Counter(
    "lag_monitor_errors_total",
    "Total lag monitor errors",
    ["error_type"]
)

# Global state
monitor_state = {
    "running": True,
    "last_poll_time": None,
    "last_lag": {},
    "lag_history": [],
    "alerts": []
}


def create_admin_client() -> AdminClient:
    """Create Kafka admin client."""
    conf = {
        'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
    }
    return AdminClient(conf)


def create_consumer_for_offsets() -> Consumer:
    """Create a consumer just to query offsets."""
    conf = {
        'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
        'group.id': f"{KAFKA_GROUP_ID}-monitor",
        'auto.offset.reset': 'earliest',
        'enable.auto.commit': False,
    }
    return Consumer(conf)


def get_lag_info() -> Dict:
    """Get current lag information for the consumer group."""
    consumer = None
    try:
        consumer = create_consumer_for_offsets()

        # Get partition metadata
        metadata = consumer.list_topics(topic=KAFKA_TOPIC, timeout=10)
        topic_metadata = metadata.topics.get(KAFKA_TOPIC)

        if not topic_metadata:
            return {"error": f"Topic {KAFKA_TOPIC} not found"}

        partitions = list(topic_metadata.partitions.keys())
        topic_partitions = [TopicPartition(KAFKA_TOPIC, p) for p in partitions]

        # Get end offsets (watermarks)
        end_offsets = {}
        for tp in topic_partitions:
            low, high = consumer.get_watermark_offsets(tp, timeout=10)
            end_offsets[tp.partition] = high

        # Get committed offsets for the consumer group
        committed = consumer.committed(topic_partitions, timeout=10)
        committed_offsets = {}
        for tp in committed:
            if tp is not None:
                committed_offsets[tp.partition] = tp.offset if tp.offset >= 0 else 0

        # Calculate lag per partition
        lag_per_partition = {}
        total_lag = 0

        for partition in partitions:
            end = end_offsets.get(partition, 0)
            committed = committed_offsets.get(partition, 0)
            lag = max(0, end - committed)
            lag_per_partition[partition] = {
                "lag": lag,
                "end_offset": end,
                "committed_offset": committed
            }
            total_lag += lag

            # Update Prometheus metrics
            CONSUMER_LAG.labels(
                topic=KAFKA_TOPIC,
                group_id=KAFKA_GROUP_ID,
                partition=str(partition)
            ).set(lag)
            PARTITION_END_OFFSET.labels(
                topic=KAFKA_TOPIC,
                partition=str(partition)
            ).set(end)
            PARTITION_COMMITTED_OFFSET.labels(
                topic=KAFKA_TOPIC,
                group_id=KAFKA_GROUP_ID,
                partition=str(partition)
            ).set(committed)

        CONSUMER_LAG_TOTAL.labels(
            topic=KAFKA_TOPIC,
            group_id=KAFKA_GROUP_ID
        ).set(total_lag)

        # Determine status
        if total_lag >= LAG_THRESHOLD_CRITICAL:
            status = "critical"
            status_code = 2
        elif total_lag >= LAG_THRESHOLD_WARNING:
            status = "warning"
            status_code = 1
        else:
            status = "ok"
            status_code = 0

        LAG_STATUS.labels(
            topic=KAFKA_TOPIC,
            group_id=KAFKA_GROUP_ID
        ).set(status_code)

        return {
            "topic": KAFKA_TOPIC,
            "group_id": KAFKA_GROUP_ID,
            "total_lag": total_lag,
            "status": status,
            "partitions": lag_per_partition,
            "thresholds": {
                "warning": LAG_THRESHOLD_WARNING,
                "critical": LAG_THRESHOLD_CRITICAL
            },
            "timestamp": datetime.utcnow().isoformat()
        }

    except Exception as e:
        logger.error(f"Error getting lag info: {e}")
        MONITOR_ERRORS.labels(error_type="get_lag_info").inc()
        return {"error": str(e)}
    finally:
        if consumer:
            consumer.close()


async def monitor_loop():
    """Background loop that monitors lag."""
    logger.info("Lag monitor loop started")

    previous_total_lag = 0
    previous_time = time.time()

    while monitor_state["running"]:
        try:
            MONITOR_POLLS.inc()

            lag_info = get_lag_info()

            if "error" not in lag_info:
                total_lag = lag_info["total_lag"]
                current_time = time.time()

                # Calculate rate of change
                time_delta = current_time - previous_time
                if time_delta > 0:
                    lag_change = total_lag - previous_total_lag
                    rate_of_change = lag_change / time_delta
                    LAG_RATE_OF_CHANGE.labels(
                        topic=KAFKA_TOPIC,
                        group_id=KAFKA_GROUP_ID
                    ).set(rate_of_change)

                    lag_info["rate_of_change"] = round(rate_of_change, 2)

                previous_total_lag = total_lag
                previous_time = current_time

                # Store in history (keep last 100 readings)
                monitor_state["lag_history"].append({
                    "timestamp": lag_info["timestamp"],
                    "total_lag": total_lag,
                    "status": lag_info["status"]
                })
                if len(monitor_state["lag_history"]) > 100:
                    monitor_state["lag_history"] = monitor_state["lag_history"][-100:]

                # Check for alerts
                if lag_info["status"] == "critical":
                    alert = {
                        "level": "critical",
                        "message": f"Consumer lag is critical: {total_lag} messages behind",
                        "timestamp": lag_info["timestamp"],
                        "total_lag": total_lag
                    }
                    monitor_state["alerts"].append(alert)
                    logger.warning(alert["message"])
                elif lag_info["status"] == "warning":
                    alert = {
                        "level": "warning",
                        "message": f"Consumer lag is elevated: {total_lag} messages behind",
                        "timestamp": lag_info["timestamp"],
                        "total_lag": total_lag
                    }
                    monitor_state["alerts"].append(alert)
                    logger.warning(alert["message"])

                # Keep only last 50 alerts
                if len(monitor_state["alerts"]) > 50:
                    monitor_state["alerts"] = monitor_state["alerts"][-50:]

                monitor_state["last_poll_time"] = datetime.utcnow().isoformat()
                monitor_state["last_lag"] = lag_info

            await asyncio.sleep(POLL_INTERVAL_SECONDS)

        except Exception as e:
            logger.error(f"Error in monitor loop: {e}")
            MONITOR_ERRORS.labels(error_type="monitor_loop").inc()
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    logger.info("Lag monitor loop stopped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Monitoring topic: {KAFKA_TOPIC}")
    logger.info(f"Monitoring group: {KAFKA_GROUP_ID}")
    logger.info(f"Poll interval: {POLL_INTERVAL_SECONDS}s")
    logger.info(f"Warning threshold: {LAG_THRESHOLD_WARNING}")
    logger.info(f"Critical threshold: {LAG_THRESHOLD_CRITICAL}")

    # Start monitor loop
    asyncio.create_task(monitor_loop())

    yield

    # Cleanup
    monitor_state["running"] = False
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "monitoring": {
            "topic": KAFKA_TOPIC,
            "group_id": KAFKA_GROUP_ID
        }
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/lag")
async def get_lag():
    """Get current lag information."""
    return get_lag_info()


@app.get("/lag/history")
async def get_lag_history(limit: int = 20):
    """Get lag history."""
    history = monitor_state["lag_history"][-limit:]
    return {
        "topic": KAFKA_TOPIC,
        "group_id": KAFKA_GROUP_ID,
        "history": history,
        "count": len(history)
    }


@app.get("/alerts")
async def get_alerts(limit: int = 10):
    """Get recent alerts."""
    alerts = monitor_state["alerts"][-limit:]
    return {
        "alerts": alerts,
        "count": len(alerts)
    }


@app.delete("/alerts")
async def clear_alerts():
    """Clear all alerts."""
    count = len(monitor_state["alerts"])
    monitor_state["alerts"] = []
    return {"cleared": count}


@app.get("/status")
async def get_status():
    """Get overall monitoring status."""
    last_lag = monitor_state.get("last_lag", {})
    return {
        "topic": KAFKA_TOPIC,
        "group_id": KAFKA_GROUP_ID,
        "total_lag": last_lag.get("total_lag", "unknown"),
        "status": last_lag.get("status", "unknown"),
        "last_poll": monitor_state.get("last_poll_time"),
        "thresholds": {
            "warning": LAG_THRESHOLD_WARNING,
            "critical": LAG_THRESHOLD_CRITICAL
        },
        "recent_alerts": len([a for a in monitor_state["alerts"] if a["level"] == "critical"])
    }


@app.get("/recommendations")
async def get_recommendations():
    """Get recommendations based on current lag state."""
    last_lag = monitor_state.get("last_lag", {})
    total_lag = last_lag.get("total_lag", 0)
    rate = last_lag.get("rate_of_change", 0)

    recommendations = []

    if total_lag >= LAG_THRESHOLD_CRITICAL:
        recommendations.append({
            "priority": "high",
            "action": "Scale consumers immediately",
            "reason": f"Lag is critical ({total_lag} messages)",
            "command": "docker compose --profile scaled up -d consumer-2 consumer-3"
        })
        recommendations.append({
            "priority": "high",
            "action": "Reduce processing time",
            "reason": "Speed up message processing",
            "command": 'curl -X POST http://localhost:8001/fast -d "time_ms=50"'
        })
    elif total_lag >= LAG_THRESHOLD_WARNING:
        recommendations.append({
            "priority": "medium",
            "action": "Consider scaling consumers",
            "reason": f"Lag is elevated ({total_lag} messages)",
            "command": "docker compose --profile scaled up -d consumer-2"
        })
    elif rate > 10:
        recommendations.append({
            "priority": "medium",
            "action": "Monitor closely",
            "reason": f"Lag is growing at {rate:.1f} msg/s",
            "command": "watch -n 5 'curl -s http://localhost:8010/lag | jq .total_lag'"
        })

    if rate < -10 and total_lag > 0:
        recommendations.append({
            "priority": "low",
            "action": "Lag is recovering",
            "reason": f"Lag shrinking at {abs(rate):.1f} msg/s, will clear in ~{total_lag/abs(rate):.0f}s",
            "command": None
        })

    if not recommendations:
        recommendations.append({
            "priority": "low",
            "action": "No action needed",
            "reason": "System is healthy",
            "command": None
        })

    return {
        "total_lag": total_lag,
        "rate_of_change": rate,
        "recommendations": recommendations
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8010)
