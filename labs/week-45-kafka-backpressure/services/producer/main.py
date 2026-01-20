"""
Producer Service - Fast message producer for Kafka with configurable rate.
Demonstrates how to overwhelm consumers and build up lag.
"""
import asyncio
import json
import logging
import os
import random
import string
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from confluent_kafka import Producer
from fastapi import FastAPI, BackgroundTasks
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "producer")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "orders")
PRODUCTION_RATE_MS = int(os.getenv("PRODUCTION_RATE_MS", "100"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "1"))
AUTO_PRODUCE = os.getenv("AUTO_PRODUCE", "false").lower() == "true"

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
MESSAGES_PRODUCED = Counter(
    "kafka_messages_produced_total",
    "Total Kafka messages produced",
    ["topic"]
)
PRODUCTION_ERRORS = Counter(
    "kafka_production_errors_total",
    "Total Kafka production errors",
    ["topic"]
)
PRODUCTION_LATENCY = Histogram(
    "kafka_production_latency_seconds",
    "Kafka production latency",
    ["topic"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
PRODUCTION_RATE = Gauge(
    "kafka_production_rate_per_second",
    "Current production rate (messages/second)",
    ["topic"]
)
AUTO_PRODUCER_RUNNING = Gauge(
    "kafka_auto_producer_running",
    "Whether auto-producer is running (1=yes, 0=no)"
)

# Global state
producer_config = {
    "rate_ms": PRODUCTION_RATE_MS,
    "batch_size": BATCH_SIZE,
    "running": AUTO_PRODUCE
}
kafka_producer: Optional[Producer] = None


class ProducerConfig(BaseModel):
    rate_ms: Optional[int] = None
    batch_size: Optional[int] = None
    running: Optional[bool] = None


class BurstConfig(BaseModel):
    count: int = 1000
    delay_ms: int = 10


def create_kafka_producer() -> Producer:
    """Create Kafka producer with delivery reports."""
    conf = {
        'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
        'client.id': SERVICE_NAME,
        'acks': 'all',
        'retries': 3,
        'retry.backoff.ms': 100,
        'linger.ms': 5,
        'batch.size': 16384,
    }
    return Producer(conf)


def delivery_callback(err, msg):
    """Called once for each message produced to indicate delivery result."""
    if err is not None:
        logger.error(f"Message delivery failed: {err}")
        PRODUCTION_ERRORS.labels(topic=KAFKA_TOPIC).inc()
    else:
        MESSAGES_PRODUCED.labels(topic=KAFKA_TOPIC).inc()


def generate_order():
    """Generate a fake order message."""
    order_id = str(uuid.uuid4())
    return {
        "order_id": order_id,
        "customer_id": f"cust-{random.randint(1000, 9999)}",
        "product": random.choice(["Widget A", "Widget B", "Gadget X", "Gadget Y", "Device Z"]),
        "quantity": random.randint(1, 10),
        "price": round(random.uniform(10.0, 500.0), 2),
        "timestamp": datetime.utcnow().isoformat(),
        "correlation_id": ''.join(random.choices(string.ascii_lowercase, k=8))
    }


def produce_message(order: dict) -> bool:
    """Produce a single message to Kafka."""
    global kafka_producer

    with tracer.start_as_current_span("produce_message") as span:
        start_time = time.time()

        try:
            # Add trace context to message headers
            current_span = trace.get_current_span()
            trace_id = format(current_span.get_span_context().trace_id, "032x")

            headers = [
                ("trace_id", trace_id.encode('utf-8')),
                ("producer", SERVICE_NAME.encode('utf-8'))
            ]

            span.set_attribute("order.id", order["order_id"])
            span.set_attribute("kafka.topic", KAFKA_TOPIC)

            kafka_producer.produce(
                topic=KAFKA_TOPIC,
                key=order["order_id"].encode('utf-8'),
                value=json.dumps(order).encode('utf-8'),
                headers=headers,
                callback=delivery_callback
            )
            kafka_producer.poll(0)  # Trigger delivery callbacks

            duration = time.time() - start_time
            PRODUCTION_LATENCY.labels(topic=KAFKA_TOPIC).observe(duration)

            return True

        except Exception as e:
            logger.error(f"Failed to produce message: {e}")
            PRODUCTION_ERRORS.labels(topic=KAFKA_TOPIC).inc()
            span.record_exception(e)
            return False


async def auto_producer_loop():
    """Background loop that produces messages at configured rate."""
    global producer_config

    logger.info("Auto-producer loop started")
    AUTO_PRODUCER_RUNNING.set(1)

    while producer_config["running"]:
        rate_ms = producer_config["rate_ms"]
        batch_size = producer_config["batch_size"]

        for _ in range(batch_size):
            if not producer_config["running"]:
                break
            order = generate_order()
            produce_message(order)

        # Flush to ensure messages are sent
        kafka_producer.flush(timeout=0.1)

        # Update rate metric
        messages_per_second = (batch_size / rate_ms) * 1000
        PRODUCTION_RATE.labels(topic=KAFKA_TOPIC).set(messages_per_second)

        await asyncio.sleep(rate_ms / 1000.0)

    AUTO_PRODUCER_RUNNING.set(0)
    logger.info("Auto-producer loop stopped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global kafka_producer

    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Kafka servers: {KAFKA_BOOTSTRAP_SERVERS}")
    logger.info(f"Topic: {KAFKA_TOPIC}")

    # Create Kafka producer
    kafka_producer = create_kafka_producer()

    # Start auto-producer if enabled
    if AUTO_PRODUCE:
        producer_config["running"] = True
        asyncio.create_task(auto_producer_loop())

    yield

    # Cleanup
    producer_config["running"] = False
    if kafka_producer:
        kafka_producer.flush()
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "kafka_connected": kafka_producer is not None
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/config")
async def get_config():
    """Get current producer configuration."""
    return {
        "rate_ms": producer_config["rate_ms"],
        "batch_size": producer_config["batch_size"],
        "running": producer_config["running"],
        "topic": KAFKA_TOPIC
    }


@app.post("/config")
async def set_config(config: ProducerConfig, background_tasks: BackgroundTasks):
    """Update producer configuration."""
    was_running = producer_config["running"]

    if config.rate_ms is not None:
        producer_config["rate_ms"] = max(1, config.rate_ms)
    if config.batch_size is not None:
        producer_config["batch_size"] = max(1, config.batch_size)
    if config.running is not None:
        producer_config["running"] = config.running

    # Start auto-producer if it wasn't running but now should be
    if not was_running and producer_config["running"]:
        background_tasks.add_task(auto_producer_loop)

    return {
        "rate_ms": producer_config["rate_ms"],
        "batch_size": producer_config["batch_size"],
        "running": producer_config["running"]
    }


@app.post("/produce")
async def produce_single():
    """Produce a single message on demand."""
    order = generate_order()
    success = produce_message(order)
    kafka_producer.flush(timeout=1.0)

    return {
        "success": success,
        "order": order
    }


@app.post("/produce/batch")
async def produce_batch(count: int = 10):
    """Produce a batch of messages on demand."""
    results = []
    for _ in range(count):
        order = generate_order()
        success = produce_message(order)
        results.append({"success": success, "order_id": order["order_id"]})

    kafka_producer.flush(timeout=5.0)

    successful = sum(1 for r in results if r["success"])
    return {
        "total": count,
        "successful": successful,
        "failed": count - successful
    }


@app.post("/burst")
async def produce_burst(config: BurstConfig):
    """
    Produce a burst of messages to quickly build up lag.
    This is useful for demonstrating backpressure scenarios.
    """
    logger.info(f"Starting burst: {config.count} messages with {config.delay_ms}ms delay")

    successful = 0
    failed = 0
    start_time = time.time()

    for i in range(config.count):
        order = generate_order()
        if produce_message(order):
            successful += 1
        else:
            failed += 1

        # Periodic flush and small delay
        if i % 100 == 0:
            kafka_producer.flush(timeout=0.5)
            await asyncio.sleep(config.delay_ms / 1000.0)

    kafka_producer.flush(timeout=5.0)
    duration = time.time() - start_time

    return {
        "total": config.count,
        "successful": successful,
        "failed": failed,
        "duration_seconds": round(duration, 2),
        "messages_per_second": round(successful / duration, 2)
    }


@app.post("/start")
async def start_producer(background_tasks: BackgroundTasks):
    """Start the auto-producer."""
    if producer_config["running"]:
        return {"status": "already_running"}

    producer_config["running"] = True
    background_tasks.add_task(auto_producer_loop)

    return {
        "status": "started",
        "rate_ms": producer_config["rate_ms"],
        "batch_size": producer_config["batch_size"]
    }


@app.post("/stop")
async def stop_producer():
    """Stop the auto-producer."""
    producer_config["running"] = False
    return {"status": "stopped"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
