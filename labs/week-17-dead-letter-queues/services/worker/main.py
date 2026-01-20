"""
Worker Service - Consumes messages from the main queue and processes them.
Messages that fail after max retries are sent to the DLQ.
"""
import asyncio
import json
import logging
import os
import random
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import pika
from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "worker")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab17-otel-collector:4317")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "lab17-rabbitmq")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest")

# Worker configuration
FAILURE_RATE = float(os.getenv("FAILURE_RATE", "0.0"))  # 0.0 to 1.0
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_DELAY_MS = int(os.getenv("RETRY_DELAY_MS", "1000"))
PROCESSING_TIME_MS = int(os.getenv("PROCESSING_TIME_MS", "100"))

# Queue names
MAIN_QUEUE = "tasks"
DLQ_QUEUE = "tasks.dlq"
DLQ_EXCHANGE = "tasks.dlx"
RETRY_QUEUE = "tasks.retry"

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
propagator = TraceContextTextMapPropagator()

# Prometheus metrics
MESSAGES_PROCESSED = Counter(
    "messages_processed_total",
    "Total messages processed",
    ["service", "status"]
)
MESSAGES_FAILED = Counter(
    "messages_failed_total",
    "Total messages that failed processing",
    ["service", "reason"]
)
MESSAGES_RETRIED = Counter(
    "messages_retried_total",
    "Total message retry attempts",
    ["service"]
)
DLQ_MESSAGES = Counter(
    "dlq_messages_total",
    "Total messages sent to DLQ",
    ["service"]
)
PROCESSING_TIME = Histogram(
    "message_processing_duration_seconds",
    "Message processing time",
    ["service"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
CURRENT_FAILURE_RATE = Gauge(
    "worker_failure_rate",
    "Current configured failure rate",
    ["service"]
)
RETRY_COUNT = Histogram(
    "message_retry_count",
    "Number of retries before success or DLQ",
    ["service"],
    buckets=[0, 1, 2, 3, 4, 5]
)

# Runtime configuration (mutable)
runtime_config = {
    "failure_rate": FAILURE_RATE,
    "max_retries": MAX_RETRIES,
    "retry_delay_ms": RETRY_DELAY_MS,
    "processing_time_ms": PROCESSING_TIME_MS,
    "paused": False
}

# RabbitMQ connection
connection: Optional[pika.BlockingConnection] = None
channel: Optional[pika.channel.Channel] = None
consumer_thread: Optional[threading.Thread] = None
stop_consuming = threading.Event()


def get_rabbitmq_connection():
    """Establish connection to RabbitMQ with retry logic."""
    global connection, channel

    credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
    parameters = pika.ConnectionParameters(
        host=RABBITMQ_HOST,
        port=RABBITMQ_PORT,
        credentials=credentials,
        heartbeat=600,
        blocked_connection_timeout=300
    )

    for attempt in range(10):
        try:
            connection = pika.BlockingConnection(parameters)
            channel = connection.channel()

            # Set prefetch count for fair dispatch
            channel.basic_qos(prefetch_count=1)

            # Declare dead letter exchange
            channel.exchange_declare(
                exchange=DLQ_EXCHANGE,
                exchange_type='direct',
                durable=True
            )

            # Declare DLQ
            channel.queue_declare(
                queue=DLQ_QUEUE,
                durable=True
            )
            channel.queue_bind(
                queue=DLQ_QUEUE,
                exchange=DLQ_EXCHANGE,
                routing_key=MAIN_QUEUE
            )

            # Declare main queue with DLQ settings
            channel.queue_declare(
                queue=MAIN_QUEUE,
                durable=True,
                arguments={
                    'x-dead-letter-exchange': DLQ_EXCHANGE,
                    'x-dead-letter-routing-key': MAIN_QUEUE
                }
            )

            logger.info(f"Connected to RabbitMQ at {RABBITMQ_HOST}:{RABBITMQ_PORT}")
            return True
        except Exception as e:
            logger.warning(f"RabbitMQ connection attempt {attempt + 1} failed: {e}")
            time.sleep(2)

    raise Exception("Failed to connect to RabbitMQ after 10 attempts")


def process_message(ch, method, properties, body):
    """Process a single message from the queue."""
    if runtime_config["paused"]:
        # Requeue the message if paused
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
        return

    start_time = time.time()
    message = json.loads(body)
    task_id = message.get("task_id", "unknown")
    attempt = message.get("attempt", 0)

    # Extract trace context from message
    trace_context = message.get("trace_context", {})
    ctx = propagator.extract(trace_context) if trace_context else None

    with tracer.start_as_current_span("process_task", context=ctx) as span:
        span.set_attribute("task.id", task_id)
        span.set_attribute("task.type", message.get("task_type", "unknown"))
        span.set_attribute("task.attempt", attempt)

        trace_id = format(span.get_span_context().trace_id, "032x")

        try:
            # Simulate processing time
            processing_time = runtime_config["processing_time_ms"] / 1000.0
            time.sleep(processing_time)

            # Check if this message should fail (configurable failure rate)
            if random.random() < runtime_config["failure_rate"]:
                raise Exception("Simulated processing failure")

            # Success!
            duration = time.time() - start_time
            MESSAGES_PROCESSED.labels(service=SERVICE_NAME, status="success").inc()
            PROCESSING_TIME.labels(service=SERVICE_NAME).observe(duration)
            RETRY_COUNT.labels(service=SERVICE_NAME).observe(attempt)

            ch.basic_ack(delivery_tag=method.delivery_tag)
            logger.info(f"Processed task task_id={task_id} attempt={attempt} duration={duration*1000:.0f}ms trace_id={trace_id}")

        except Exception as e:
            span.record_exception(e)
            span.set_attribute("error", True)

            MESSAGES_FAILED.labels(service=SERVICE_NAME, reason="processing_error").inc()

            if attempt < runtime_config["max_retries"]:
                # Retry the message
                MESSAGES_RETRIED.labels(service=SERVICE_NAME).inc()

                message["attempt"] = attempt + 1
                message["last_error"] = str(e)
                message["last_retry_at"] = datetime.utcnow().isoformat()

                # Republish with incremented attempt count
                ch.basic_publish(
                    exchange='',
                    routing_key=MAIN_QUEUE,
                    body=json.dumps(message),
                    properties=pika.BasicProperties(
                        delivery_mode=2,
                        message_id=properties.message_id
                    )
                )
                ch.basic_ack(delivery_tag=method.delivery_tag)

                logger.warning(f"Retrying task task_id={task_id} attempt={attempt + 1}/{runtime_config['max_retries']} trace_id={trace_id}")

                # Add delay before next retry
                time.sleep(runtime_config["retry_delay_ms"] / 1000.0)
            else:
                # Max retries exceeded, send to DLQ
                DLQ_MESSAGES.labels(service=SERVICE_NAME).inc()
                RETRY_COUNT.labels(service=SERVICE_NAME).observe(attempt)

                message["dlq_reason"] = "max_retries_exceeded"
                message["dlq_at"] = datetime.utcnow().isoformat()
                message["final_error"] = str(e)

                ch.basic_publish(
                    exchange=DLQ_EXCHANGE,
                    routing_key=MAIN_QUEUE,
                    body=json.dumps(message),
                    properties=pika.BasicProperties(
                        delivery_mode=2,
                        message_id=properties.message_id
                    )
                )
                ch.basic_ack(delivery_tag=method.delivery_tag)

                logger.error(f"Task sent to DLQ task_id={task_id} after {attempt + 1} attempts trace_id={trace_id}")


def consume_messages():
    """Consumer thread that processes messages from the queue."""
    logger.info("Starting message consumer")

    while not stop_consuming.is_set():
        try:
            if channel and channel.is_open:
                channel.basic_consume(
                    queue=MAIN_QUEUE,
                    on_message_callback=process_message,
                    auto_ack=False
                )
                channel.start_consuming()
        except Exception as e:
            if not stop_consuming.is_set():
                logger.error(f"Consumer error: {e}")
                time.sleep(5)
                get_rabbitmq_connection()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    global consumer_thread

    logger.info(f"{SERVICE_NAME} starting up")
    get_rabbitmq_connection()

    # Update initial metrics
    CURRENT_FAILURE_RATE.labels(service=SERVICE_NAME).set(runtime_config["failure_rate"])

    # Start consumer thread
    consumer_thread = threading.Thread(target=consume_messages, daemon=True)
    consumer_thread.start()

    yield

    logger.info(f"{SERVICE_NAME} shutting down")
    stop_consuming.set()
    if channel and channel.is_open:
        channel.stop_consuming()
    if connection and connection.is_open:
        connection.close()


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


class WorkerConfig(BaseModel):
    """Worker configuration update request."""
    failure_rate: Optional[float] = None
    max_retries: Optional[int] = None
    retry_delay_ms: Optional[int] = None
    processing_time_ms: Optional[int] = None
    paused: Optional[bool] = None


@app.get("/health")
async def health():
    """Health check endpoint."""
    rabbitmq_ok = connection is not None and connection.is_open
    return {
        "status": "ok" if rabbitmq_ok else "degraded",
        "service": SERVICE_NAME,
        "rabbitmq": "connected" if rabbitmq_ok else "disconnected",
        "paused": runtime_config["paused"]
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/admin/config")
async def get_config():
    """Get current worker configuration."""
    return {
        "failure_rate": runtime_config["failure_rate"],
        "max_retries": runtime_config["max_retries"],
        "retry_delay_ms": runtime_config["retry_delay_ms"],
        "processing_time_ms": runtime_config["processing_time_ms"],
        "paused": runtime_config["paused"]
    }


@app.post("/admin/config")
async def update_config(config: WorkerConfig):
    """Update worker configuration at runtime."""
    if config.failure_rate is not None:
        if not 0 <= config.failure_rate <= 1:
            raise HTTPException(status_code=400, detail="failure_rate must be between 0 and 1")
        runtime_config["failure_rate"] = config.failure_rate
        CURRENT_FAILURE_RATE.labels(service=SERVICE_NAME).set(config.failure_rate)

    if config.max_retries is not None:
        if config.max_retries < 0:
            raise HTTPException(status_code=400, detail="max_retries must be non-negative")
        runtime_config["max_retries"] = config.max_retries

    if config.retry_delay_ms is not None:
        if config.retry_delay_ms < 0:
            raise HTTPException(status_code=400, detail="retry_delay_ms must be non-negative")
        runtime_config["retry_delay_ms"] = config.retry_delay_ms

    if config.processing_time_ms is not None:
        if config.processing_time_ms < 0:
            raise HTTPException(status_code=400, detail="processing_time_ms must be non-negative")
        runtime_config["processing_time_ms"] = config.processing_time_ms

    if config.paused is not None:
        runtime_config["paused"] = config.paused

    logger.info(f"Worker config updated: {runtime_config}")

    return {
        "status": "updated",
        "config": runtime_config
    }


@app.post("/admin/failure-rate")
async def set_failure_rate(rate: float):
    """Convenience endpoint to set failure rate."""
    if not 0 <= rate <= 1:
        raise HTTPException(status_code=400, detail="rate must be between 0 and 1")

    runtime_config["failure_rate"] = rate
    CURRENT_FAILURE_RATE.labels(service=SERVICE_NAME).set(rate)

    logger.info(f"Failure rate set to {rate}")

    return {
        "status": "updated",
        "failure_rate": rate
    }


@app.get("/stats")
async def get_stats():
    """Get worker processing statistics."""
    return {
        "config": runtime_config,
        "service": SERVICE_NAME
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
