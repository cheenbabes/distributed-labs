"""
Producer Service - Sends messages to the main task queue.
Messages are processed by workers and failed messages go to DLQ.
"""
import asyncio
import json
import logging
import os
import time
import uuid
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "producer")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab17-otel-collector:4317")
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "lab17-rabbitmq")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "guest")

# Queue names
MAIN_QUEUE = "tasks"
DLQ_QUEUE = "tasks.dlq"
DLQ_EXCHANGE = "tasks.dlx"

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
MESSAGES_PUBLISHED = Counter(
    "messages_published_total",
    "Total messages published to the queue",
    ["service", "queue"]
)
MESSAGES_PUBLISH_LATENCY = Histogram(
    "message_publish_duration_seconds",
    "Message publish latency",
    ["service"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
QUEUE_DEPTH = Gauge(
    "queue_depth",
    "Current queue depth",
    ["queue"]
)

# RabbitMQ connection
connection: Optional[pika.BlockingConnection] = None
channel: Optional[pika.channel.Channel] = None


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


def update_queue_metrics():
    """Update queue depth metrics."""
    try:
        if channel and channel.is_open:
            # Get main queue depth
            main_queue = channel.queue_declare(queue=MAIN_QUEUE, passive=True)
            QUEUE_DEPTH.labels(queue=MAIN_QUEUE).set(main_queue.method.message_count)

            # Get DLQ depth
            dlq = channel.queue_declare(queue=DLQ_QUEUE, passive=True)
            QUEUE_DEPTH.labels(queue=DLQ_QUEUE).set(dlq.method.message_count)
    except Exception as e:
        logger.warning(f"Failed to update queue metrics: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info(f"{SERVICE_NAME} starting up")
    get_rabbitmq_connection()
    yield
    logger.info(f"{SERVICE_NAME} shutting down")
    if connection and connection.is_open:
        connection.close()


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


class TaskMessage(BaseModel):
    """Task message payload."""
    task_type: str = "default"
    payload: dict = {}
    priority: int = 0


class BulkTaskRequest(BaseModel):
    """Bulk task creation request."""
    count: int = 10
    task_type: str = "default"
    delay_ms: int = 0


@app.get("/health")
async def health():
    """Health check endpoint."""
    rabbitmq_ok = connection is not None and connection.is_open
    return {
        "status": "ok" if rabbitmq_ok else "degraded",
        "service": SERVICE_NAME,
        "rabbitmq": "connected" if rabbitmq_ok else "disconnected"
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    update_queue_metrics()
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/tasks")
async def create_task(task: TaskMessage = TaskMessage()):
    """Create a single task and publish to the queue."""
    start_time = time.time()

    with tracer.start_as_current_span("publish_task") as span:
        task_id = str(uuid.uuid4())
        trace_id = format(span.get_span_context().trace_id, "032x")

        # Inject trace context into message headers
        carrier = {}
        propagator.inject(carrier)

        message = {
            "task_id": task_id,
            "task_type": task.task_type,
            "payload": task.payload,
            "priority": task.priority,
            "created_at": datetime.utcnow().isoformat(),
            "trace_context": carrier,
            "attempt": 0
        }

        span.set_attribute("task.id", task_id)
        span.set_attribute("task.type", task.task_type)

        try:
            channel.basic_publish(
                exchange='',
                routing_key=MAIN_QUEUE,
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # Persistent
                    message_id=task_id,
                    headers=carrier
                )
            )

            duration = time.time() - start_time
            MESSAGES_PUBLISHED.labels(service=SERVICE_NAME, queue=MAIN_QUEUE).inc()
            MESSAGES_PUBLISH_LATENCY.labels(service=SERVICE_NAME).observe(duration)

            logger.info(f"Published task task_id={task_id} trace_id={trace_id}")

            return {
                "status": "published",
                "task_id": task_id,
                "trace_id": trace_id,
                "queue": MAIN_QUEUE
            }
        except Exception as e:
            span.record_exception(e)
            logger.error(f"Failed to publish task: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/tasks/bulk")
async def create_bulk_tasks(request: BulkTaskRequest):
    """Create multiple tasks at once."""
    with tracer.start_as_current_span("publish_bulk_tasks") as span:
        span.set_attribute("task.count", request.count)

        task_ids = []
        for i in range(request.count):
            task = TaskMessage(
                task_type=request.task_type,
                payload={"index": i, "batch_size": request.count}
            )

            task_id = str(uuid.uuid4())
            carrier = {}
            propagator.inject(carrier)

            message = {
                "task_id": task_id,
                "task_type": task.task_type,
                "payload": task.payload,
                "priority": task.priority,
                "created_at": datetime.utcnow().isoformat(),
                "trace_context": carrier,
                "attempt": 0
            }

            channel.basic_publish(
                exchange='',
                routing_key=MAIN_QUEUE,
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    message_id=task_id,
                    headers=carrier
                )
            )

            task_ids.append(task_id)
            MESSAGES_PUBLISHED.labels(service=SERVICE_NAME, queue=MAIN_QUEUE).inc()

            if request.delay_ms > 0:
                await asyncio.sleep(request.delay_ms / 1000.0)

        logger.info(f"Published {request.count} tasks")

        return {
            "status": "published",
            "count": request.count,
            "task_ids": task_ids[:5],  # Return first 5 IDs only
            "queue": MAIN_QUEUE
        }


@app.get("/queues/status")
async def queue_status():
    """Get current queue status."""
    try:
        main_queue = channel.queue_declare(queue=MAIN_QUEUE, passive=True)
        dlq = channel.queue_declare(queue=DLQ_QUEUE, passive=True)

        return {
            "main_queue": {
                "name": MAIN_QUEUE,
                "messages": main_queue.method.message_count,
                "consumers": main_queue.method.consumer_count
            },
            "dlq": {
                "name": DLQ_QUEUE,
                "messages": dlq.method.message_count,
                "consumers": dlq.method.consumer_count
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
