"""
CDC Consumer - Consumes Change Data Capture events from Kafka.
Processes Debezium change events and forwards to downstream services.
"""
import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, List, Optional

import httpx
from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "cdc-consumer")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
SEARCH_SERVICE_URL = os.getenv("SEARCH_SERVICE_URL", "http://search-service:8002")

# Topics to consume (Debezium creates topics in format: <connector>.<schema>.<table>)
CDC_TOPICS = [
    "cdc.public.products",
    "cdc.public.orders"
]

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
EVENTS_RECEIVED = Counter(
    "cdc_events_received_total",
    "Total CDC events received from Kafka",
    ["service", "table", "operation"]
)
EVENTS_PROCESSED = Counter(
    "cdc_events_processed_total",
    "Total CDC events successfully processed",
    ["service", "table", "operation"]
)
EVENTS_FAILED = Counter(
    "cdc_events_failed_total",
    "Total CDC events that failed processing",
    ["service", "table", "reason"]
)
PROCESSING_LATENCY = Histogram(
    "cdc_event_processing_seconds",
    "CDC event processing latency",
    ["service", "table", "operation"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
CDC_LAG = Gauge(
    "cdc_consumer_lag_ms",
    "Lag between event timestamp and processing time",
    ["service", "table"]
)
CONSUMER_STATUS = Gauge(
    "cdc_consumer_status",
    "Consumer status (1=running, 0=stopped)",
    ["service"]
)

# Global state
consumer: Optional[AIOKafkaConsumer] = None
consumer_task: Optional[asyncio.Task] = None
recent_events: List[Dict] = []
MAX_RECENT_EVENTS = 100
http_client: Optional[httpx.AsyncClient] = None


def parse_debezium_event(msg_value: bytes) -> Optional[Dict]:
    """Parse a Debezium CDC event from JSON."""
    try:
        event = json.loads(msg_value.decode('utf-8'))
        if event is None:
            # Tombstone event (delete marker)
            return {"operation": "tombstone", "data": None}

        payload = event.get("payload", event)

        # Determine operation type
        op = payload.get("op", "unknown")
        operation_map = {
            "c": "create",
            "u": "update",
            "d": "delete",
            "r": "read"  # snapshot
        }
        operation = operation_map.get(op, op)

        # Extract data
        before = payload.get("before")
        after = payload.get("after")
        source = payload.get("source", {})

        return {
            "operation": operation,
            "before": before,
            "after": after,
            "table": source.get("table", "unknown"),
            "schema": source.get("schema", "public"),
            "ts_ms": source.get("ts_ms", 0),
            "lsn": source.get("lsn"),
            "txId": source.get("txId")
        }
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse CDC event: {e}")
        return None


async def forward_to_search_service(event: Dict):
    """Forward processed event to the search service to update the read model."""
    global http_client

    if not http_client:
        return

    table = event.get("table", "unknown")
    operation = event.get("operation", "unknown")

    # Only forward product events to search service
    if table != "products":
        return

    try:
        with tracer.start_as_current_span("forward_to_search") as span:
            span.set_attribute("table", table)
            span.set_attribute("operation", operation)

            if operation == "delete":
                # For deletes, we need the ID from 'before'
                data = event.get("before", {})
                if data and data.get("id"):
                    await http_client.delete(
                        f"{SEARCH_SERVICE_URL}/index/{data['id']}"
                    )
            elif operation in ["create", "update"]:
                data = event.get("after", {})
                if data:
                    await http_client.post(
                        f"{SEARCH_SERVICE_URL}/index",
                        json=data
                    )

            logger.debug(f"Forwarded {operation} event for {table} to search service")
    except Exception as e:
        logger.error(f"Failed to forward to search service: {e}")
        EVENTS_FAILED.labels(service=SERVICE_NAME, table=table, reason="forward_failed").inc()


async def consume_cdc_events():
    """Main consumer loop for CDC events."""
    global consumer, recent_events

    logger.info(f"Starting CDC consumer for topics: {CDC_TOPICS}")
    CONSUMER_STATUS.labels(service=SERVICE_NAME).set(1)

    retry_count = 0
    max_retries = 30

    while retry_count < max_retries:
        try:
            consumer = AIOKafkaConsumer(
                *CDC_TOPICS,
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                group_id="cdc-consumer-group",
                auto_offset_reset="earliest",
                enable_auto_commit=True,
                value_deserializer=lambda x: x  # Keep as bytes for parsing
            )

            await consumer.start()
            logger.info("CDC consumer connected to Kafka")
            retry_count = 0  # Reset on successful connection

            try:
                async for msg in consumer:
                    start_time = time.time()

                    with tracer.start_as_current_span("process_cdc_event") as span:
                        span.set_attribute("kafka.topic", msg.topic)
                        span.set_attribute("kafka.partition", msg.partition)
                        span.set_attribute("kafka.offset", msg.offset)

                        # Parse the event
                        event = parse_debezium_event(msg.value)

                        if event is None:
                            EVENTS_FAILED.labels(
                                service=SERVICE_NAME,
                                table="unknown",
                                reason="parse_error"
                            ).inc()
                            continue

                        table = event.get("table", "unknown")
                        operation = event.get("operation", "unknown")

                        span.set_attribute("cdc.table", table)
                        span.set_attribute("cdc.operation", operation)

                        # Record received metric
                        EVENTS_RECEIVED.labels(
                            service=SERVICE_NAME,
                            table=table,
                            operation=operation
                        ).inc()

                        # Calculate and record lag
                        event_ts = event.get("ts_ms", 0)
                        if event_ts > 0:
                            lag_ms = (time.time() * 1000) - event_ts
                            CDC_LAG.labels(service=SERVICE_NAME, table=table).set(lag_ms)
                            span.set_attribute("cdc.lag_ms", lag_ms)

                        # Store in recent events
                        recent_event = {
                            "topic": msg.topic,
                            "partition": msg.partition,
                            "offset": msg.offset,
                            "timestamp": datetime.now().isoformat(),
                            "table": table,
                            "operation": operation,
                            "data": event.get("after") or event.get("before"),
                            "lag_ms": lag_ms if event_ts > 0 else None
                        }
                        recent_events.append(recent_event)
                        if len(recent_events) > MAX_RECENT_EVENTS:
                            recent_events = recent_events[-MAX_RECENT_EVENTS:]

                        # Forward to search service
                        await forward_to_search_service(event)

                        # Record processing metrics
                        duration = time.time() - start_time
                        PROCESSING_LATENCY.labels(
                            service=SERVICE_NAME,
                            table=table,
                            operation=operation
                        ).observe(duration)
                        EVENTS_PROCESSED.labels(
                            service=SERVICE_NAME,
                            table=table,
                            operation=operation
                        ).inc()

                        logger.info(
                            f"Processed CDC event: {operation} on {table} "
                            f"(offset={msg.offset}, lag={lag_ms:.0f}ms)"
                        )

            except asyncio.CancelledError:
                logger.info("Consumer task cancelled")
                break

        except KafkaError as e:
            retry_count += 1
            logger.warning(f"Kafka error (attempt {retry_count}/{max_retries}): {e}")
            CONSUMER_STATUS.labels(service=SERVICE_NAME).set(0)
            await asyncio.sleep(2)
        except Exception as e:
            retry_count += 1
            logger.error(f"Consumer error (attempt {retry_count}/{max_retries}): {e}")
            CONSUMER_STATUS.labels(service=SERVICE_NAME).set(0)
            await asyncio.sleep(2)
        finally:
            if consumer:
                try:
                    await consumer.stop()
                except Exception:
                    pass

    CONSUMER_STATUS.labels(service=SERVICE_NAME).set(0)
    logger.error("Consumer stopped after maximum retries")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global consumer_task, http_client

    logger.info(f"{SERVICE_NAME} starting up")

    # Create HTTP client for forwarding
    http_client = httpx.AsyncClient(timeout=10.0)

    # Start consumer in background
    consumer_task = asyncio.create_task(consume_cdc_events())

    yield

    # Cleanup
    if consumer_task:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass

    if http_client:
        await http_client.aclose()

    if consumer:
        await consumer.stop()

    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "consumer_running": consumer_task is not None and not consumer_task.done()
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/events")
async def get_recent_events(limit: int = 20):
    """Get recent CDC events for debugging."""
    return {
        "events": recent_events[-limit:],
        "total_stored": len(recent_events),
        "topics": CDC_TOPICS
    }


@app.get("/events/stats")
async def get_event_stats():
    """Get statistics about processed events."""
    # Count by operation
    operation_counts = {}
    table_counts = {}

    for event in recent_events:
        op = event.get("operation", "unknown")
        table = event.get("table", "unknown")

        operation_counts[op] = operation_counts.get(op, 0) + 1
        table_counts[table] = table_counts.get(table, 0) + 1

    # Calculate average lag
    lags = [e.get("lag_ms") for e in recent_events if e.get("lag_ms") is not None]
    avg_lag = sum(lags) / len(lags) if lags else 0

    return {
        "total_events": len(recent_events),
        "by_operation": operation_counts,
        "by_table": table_counts,
        "avg_lag_ms": round(avg_lag, 2),
        "min_lag_ms": min(lags) if lags else 0,
        "max_lag_ms": max(lags) if lags else 0
    }


@app.post("/consumer/restart")
async def restart_consumer():
    """Restart the Kafka consumer."""
    global consumer_task

    if consumer_task:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass

    consumer_task = asyncio.create_task(consume_cdc_events())
    return {"status": "restarted"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
