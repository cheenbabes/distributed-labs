"""
Consumer Service - Slow Kafka consumer with configurable processing time.
Demonstrates how slow consumers lead to lag buildup.
"""
import asyncio
import json
import logging
import os
import signal
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from confluent_kafka import Consumer, KafkaError, KafkaException
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from pydantic import BaseModel

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "consumer")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "orders")
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "order-processors")
PROCESSING_TIME_MS = int(os.getenv("PROCESSING_TIME_MS", "500"))
CONSUMER_INSTANCE_ID = os.getenv("CONSUMER_INSTANCE_ID", "consumer-1")

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(f"{SERVICE_NAME}-{CONSUMER_INSTANCE_ID}")

# OpenTelemetry setup
resource = Resource.create({
    "service.name": SERVICE_NAME,
    "service.instance.id": CONSUMER_INSTANCE_ID
})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# Prometheus metrics
MESSAGES_CONSUMED = Counter(
    "kafka_messages_consumed_total",
    "Total Kafka messages consumed",
    ["topic", "consumer_id", "partition"]
)
CONSUMPTION_ERRORS = Counter(
    "kafka_consumption_errors_total",
    "Total Kafka consumption errors",
    ["topic", "consumer_id"]
)
PROCESSING_TIME = Histogram(
    "kafka_message_processing_seconds",
    "Message processing time",
    ["topic", "consumer_id"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)
CONSUMER_LAG = Gauge(
    "kafka_consumer_lag",
    "Consumer lag for this instance",
    ["topic", "consumer_id", "partition"]
)
CONSUMER_RUNNING = Gauge(
    "kafka_consumer_running",
    "Whether consumer is running (1=yes, 0=no)",
    ["consumer_id"]
)
MESSAGES_PER_SECOND = Gauge(
    "kafka_consumer_messages_per_second",
    "Current consumption rate",
    ["consumer_id"]
)
CONSUMER_PAUSED = Gauge(
    "kafka_consumer_paused",
    "Whether consumer is paused (1=yes, 0=no)",
    ["consumer_id"]
)

# Global state
consumer_config = {
    "processing_time_ms": PROCESSING_TIME_MS,
    "running": True,
    "paused": False,
    "messages_processed": 0,
    "last_message_time": None,
    "assigned_partitions": []
}
kafka_consumer: Optional[Consumer] = None
consumer_thread: Optional[threading.Thread] = None


class ConsumerConfig(BaseModel):
    processing_time_ms: Optional[int] = None
    paused: Optional[bool] = None


def create_kafka_consumer() -> Consumer:
    """Create Kafka consumer."""
    conf = {
        'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
        'group.id': KAFKA_GROUP_ID,
        'client.id': CONSUMER_INSTANCE_ID,
        'auto.offset.reset': 'earliest',
        'enable.auto.commit': True,
        'auto.commit.interval.ms': 1000,
        'session.timeout.ms': 30000,
        'max.poll.interval.ms': 300000,
    }
    return Consumer(conf)


def process_message(msg) -> bool:
    """Process a single message with simulated work."""
    with tracer.start_as_current_span("process_message") as span:
        start_time = time.time()

        try:
            # Parse message
            key = msg.key().decode('utf-8') if msg.key() else None
            value = json.loads(msg.value().decode('utf-8'))
            partition = msg.partition()

            span.set_attribute("kafka.topic", msg.topic())
            span.set_attribute("kafka.partition", partition)
            span.set_attribute("kafka.offset", msg.offset())
            span.set_attribute("order.id", value.get("order_id", "unknown"))

            # Extract trace context from headers if present
            headers = msg.headers() or []
            for header_key, header_value in headers:
                if header_key == "trace_id":
                    span.set_attribute("producer.trace_id", header_value.decode('utf-8'))

            # Simulate processing work
            processing_time_ms = consumer_config["processing_time_ms"]
            time.sleep(processing_time_ms / 1000.0)

            # Record success
            duration = time.time() - start_time
            MESSAGES_CONSUMED.labels(
                topic=KAFKA_TOPIC,
                consumer_id=CONSUMER_INSTANCE_ID,
                partition=str(partition)
            ).inc()
            PROCESSING_TIME.labels(
                topic=KAFKA_TOPIC,
                consumer_id=CONSUMER_INSTANCE_ID
            ).observe(duration)

            consumer_config["messages_processed"] += 1
            consumer_config["last_message_time"] = datetime.utcnow().isoformat()

            logger.debug(f"Processed order {value.get('order_id')} in {duration*1000:.0f}ms")

            return True

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            CONSUMPTION_ERRORS.labels(
                topic=KAFKA_TOPIC,
                consumer_id=CONSUMER_INSTANCE_ID
            ).inc()
            span.record_exception(e)
            return False


def consumer_loop():
    """Background thread that consumes messages."""
    global kafka_consumer

    logger.info(f"Consumer {CONSUMER_INSTANCE_ID} starting")
    CONSUMER_RUNNING.labels(consumer_id=CONSUMER_INSTANCE_ID).set(1)

    kafka_consumer.subscribe([KAFKA_TOPIC])

    messages_in_window = 0
    window_start = time.time()

    while consumer_config["running"]:
        # Handle pause state
        if consumer_config["paused"]:
            CONSUMER_PAUSED.labels(consumer_id=CONSUMER_INSTANCE_ID).set(1)
            time.sleep(0.5)
            continue
        else:
            CONSUMER_PAUSED.labels(consumer_id=CONSUMER_INSTANCE_ID).set(0)

        try:
            msg = kafka_consumer.poll(timeout=1.0)

            if msg is None:
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    logger.debug(f"End of partition {msg.partition()}")
                else:
                    logger.error(f"Consumer error: {msg.error()}")
                    CONSUMPTION_ERRORS.labels(
                        topic=KAFKA_TOPIC,
                        consumer_id=CONSUMER_INSTANCE_ID
                    ).inc()
                continue

            # Process the message
            process_message(msg)
            messages_in_window += 1

            # Update throughput metric every 5 seconds
            elapsed = time.time() - window_start
            if elapsed >= 5.0:
                rate = messages_in_window / elapsed
                MESSAGES_PER_SECOND.labels(consumer_id=CONSUMER_INSTANCE_ID).set(rate)
                messages_in_window = 0
                window_start = time.time()

        except KafkaException as e:
            logger.error(f"Kafka exception: {e}")
            CONSUMPTION_ERRORS.labels(
                topic=KAFKA_TOPIC,
                consumer_id=CONSUMER_INSTANCE_ID
            ).inc()

    CONSUMER_RUNNING.labels(consumer_id=CONSUMER_INSTANCE_ID).set(0)
    logger.info(f"Consumer {CONSUMER_INSTANCE_ID} stopped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global kafka_consumer, consumer_thread

    logger.info(f"{SERVICE_NAME} ({CONSUMER_INSTANCE_ID}) starting up")
    logger.info(f"Kafka servers: {KAFKA_BOOTSTRAP_SERVERS}")
    logger.info(f"Topic: {KAFKA_TOPIC}")
    logger.info(f"Group ID: {KAFKA_GROUP_ID}")
    logger.info(f"Processing time: {PROCESSING_TIME_MS}ms")

    # Create and start consumer
    kafka_consumer = create_kafka_consumer()
    consumer_thread = threading.Thread(target=consumer_loop, daemon=True)
    consumer_thread.start()

    yield

    # Cleanup
    consumer_config["running"] = False
    if consumer_thread:
        consumer_thread.join(timeout=5.0)
    if kafka_consumer:
        kafka_consumer.close()
    logger.info(f"{SERVICE_NAME} ({CONSUMER_INSTANCE_ID}) shutting down")


app = FastAPI(title=f"{SERVICE_NAME}-{CONSUMER_INSTANCE_ID}", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "instance_id": CONSUMER_INSTANCE_ID,
        "consuming": consumer_config["running"] and not consumer_config["paused"],
        "paused": consumer_config["paused"]
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/stats")
async def get_stats():
    """Get consumer statistics."""
    return {
        "instance_id": CONSUMER_INSTANCE_ID,
        "group_id": KAFKA_GROUP_ID,
        "topic": KAFKA_TOPIC,
        "processing_time_ms": consumer_config["processing_time_ms"],
        "running": consumer_config["running"],
        "paused": consumer_config["paused"],
        "messages_processed": consumer_config["messages_processed"],
        "last_message_time": consumer_config["last_message_time"],
        "assigned_partitions": consumer_config["assigned_partitions"]
    }


@app.get("/config")
async def get_config():
    """Get current consumer configuration."""
    return {
        "processing_time_ms": consumer_config["processing_time_ms"],
        "paused": consumer_config["paused"]
    }


@app.post("/config")
async def set_config(config: ConsumerConfig):
    """Update consumer configuration."""
    if config.processing_time_ms is not None:
        consumer_config["processing_time_ms"] = max(1, config.processing_time_ms)
        logger.info(f"Processing time updated to {consumer_config['processing_time_ms']}ms")

    if config.paused is not None:
        consumer_config["paused"] = config.paused
        logger.info(f"Consumer {'paused' if config.paused else 'resumed'}")

    return {
        "processing_time_ms": consumer_config["processing_time_ms"],
        "paused": consumer_config["paused"]
    }


@app.post("/pause")
async def pause_consumer():
    """Pause the consumer (backpressure simulation)."""
    consumer_config["paused"] = True
    logger.info(f"Consumer {CONSUMER_INSTANCE_ID} paused")
    return {"status": "paused", "instance_id": CONSUMER_INSTANCE_ID}


@app.post("/resume")
async def resume_consumer():
    """Resume the consumer."""
    consumer_config["paused"] = False
    logger.info(f"Consumer {CONSUMER_INSTANCE_ID} resumed")
    return {"status": "resumed", "instance_id": CONSUMER_INSTANCE_ID}


@app.post("/slow")
async def set_slow_mode(time_ms: int = 2000):
    """Set consumer to slow mode (high processing time)."""
    consumer_config["processing_time_ms"] = time_ms
    logger.info(f"Consumer set to slow mode: {time_ms}ms")
    return {"processing_time_ms": time_ms}


@app.post("/fast")
async def set_fast_mode(time_ms: int = 50):
    """Set consumer to fast mode (low processing time)."""
    consumer_config["processing_time_ms"] = time_ms
    logger.info(f"Consumer set to fast mode: {time_ms}ms")
    return {"processing_time_ms": time_ms}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
