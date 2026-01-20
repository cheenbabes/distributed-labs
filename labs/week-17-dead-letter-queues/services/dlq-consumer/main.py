"""
DLQ Consumer Service - Monitors and handles messages in the dead letter queue.
Provides visibility into failed messages and supports replay functionality.
"""
import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, List

import pika
from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "dlq-consumer")
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
DLQ_INSPECTED = Counter(
    "dlq_messages_inspected_total",
    "Total DLQ messages inspected",
    ["service"]
)
DLQ_REPLAYED = Counter(
    "dlq_messages_replayed_total",
    "Total DLQ messages replayed to main queue",
    ["service"]
)
DLQ_DISCARDED = Counter(
    "dlq_messages_discarded_total",
    "Total DLQ messages discarded",
    ["service"]
)
DLQ_DEPTH = Gauge(
    "dlq_current_depth",
    "Current number of messages in DLQ",
    ["service"]
)

# In-memory cache of DLQ messages for inspection
dlq_cache: List[dict] = []
dlq_cache_lock = threading.Lock()
MAX_CACHE_SIZE = 1000

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

            # Declare main queue (for replay)
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


def update_dlq_depth():
    """Update DLQ depth metric."""
    try:
        if channel and channel.is_open:
            dlq = channel.queue_declare(queue=DLQ_QUEUE, passive=True)
            DLQ_DEPTH.labels(service=SERVICE_NAME).set(dlq.method.message_count)
            return dlq.method.message_count
    except Exception as e:
        logger.warning(f"Failed to update DLQ depth: {e}")
    return 0


def fetch_dlq_messages(limit: int = 100) -> List[dict]:
    """Fetch messages from DLQ without consuming them."""
    messages = []

    try:
        if not channel or not channel.is_open:
            get_rabbitmq_connection()

        for _ in range(limit):
            method_frame, properties, body = channel.basic_get(queue=DLQ_QUEUE, auto_ack=False)

            if method_frame is None:
                break

            message = json.loads(body)
            message["_delivery_tag"] = method_frame.delivery_tag
            message["_message_id"] = properties.message_id

            messages.append(message)

            # Nack to return message to queue
            channel.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=True)

            DLQ_INSPECTED.labels(service=SERVICE_NAME).inc()

    except Exception as e:
        logger.error(f"Error fetching DLQ messages: {e}")

    return messages


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


class ReplayRequest(BaseModel):
    """Replay configuration."""
    count: int = 1
    reset_attempts: bool = True


class ReplayByIdRequest(BaseModel):
    """Replay specific message by task_id."""
    task_id: str
    reset_attempts: bool = True


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
    update_dlq_depth()
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/dlq")
async def get_dlq_contents(limit: int = 100):
    """Get current contents of the DLQ."""
    with tracer.start_as_current_span("inspect_dlq") as span:
        span.set_attribute("dlq.limit", limit)

        messages = fetch_dlq_messages(min(limit, MAX_CACHE_SIZE))
        depth = update_dlq_depth()

        span.set_attribute("dlq.message_count", len(messages))
        span.set_attribute("dlq.total_depth", depth)

        return {
            "queue": DLQ_QUEUE,
            "total_messages": depth,
            "fetched": len(messages),
            "messages": messages
        }


@app.get("/dlq/stats")
async def get_dlq_stats():
    """Get DLQ statistics."""
    depth = update_dlq_depth()

    # Fetch a sample to analyze
    messages = fetch_dlq_messages(min(100, depth if depth > 0 else 1))

    # Analyze failure reasons
    reasons = {}
    task_types = {}

    for msg in messages:
        reason = msg.get("dlq_reason", "unknown")
        reasons[reason] = reasons.get(reason, 0) + 1

        task_type = msg.get("task_type", "unknown")
        task_types[task_type] = task_types.get(task_type, 0) + 1

    return {
        "queue": DLQ_QUEUE,
        "total_messages": depth,
        "sample_size": len(messages),
        "failure_reasons": reasons,
        "task_types": task_types
    }


@app.get("/dlq/message/{task_id}")
async def get_dlq_message(task_id: str):
    """Get a specific message from the DLQ by task_id."""
    with tracer.start_as_current_span("get_dlq_message") as span:
        span.set_attribute("task.id", task_id)

        messages = fetch_dlq_messages(MAX_CACHE_SIZE)

        for msg in messages:
            if msg.get("task_id") == task_id:
                return {
                    "found": True,
                    "message": msg
                }

        raise HTTPException(status_code=404, detail=f"Message with task_id {task_id} not found in DLQ")


@app.post("/dlq/replay")
async def replay_messages(request: ReplayRequest):
    """Replay messages from DLQ back to main queue."""
    with tracer.start_as_current_span("replay_dlq_messages") as span:
        span.set_attribute("replay.count", request.count)
        span.set_attribute("replay.reset_attempts", request.reset_attempts)

        replayed = 0
        failed = 0

        try:
            for _ in range(request.count):
                method_frame, properties, body = channel.basic_get(queue=DLQ_QUEUE, auto_ack=False)

                if method_frame is None:
                    break

                message = json.loads(body)

                # Reset attempt counter if requested
                if request.reset_attempts:
                    message["attempt"] = 0
                    message["replayed_at"] = datetime.utcnow().isoformat()
                    message["replayed_from_dlq"] = True

                # Remove DLQ-specific fields
                message.pop("dlq_reason", None)
                message.pop("dlq_at", None)
                message.pop("final_error", None)
                message.pop("_delivery_tag", None)
                message.pop("_message_id", None)

                # Republish to main queue
                channel.basic_publish(
                    exchange='',
                    routing_key=MAIN_QUEUE,
                    body=json.dumps(message),
                    properties=pika.BasicProperties(
                        delivery_mode=2,
                        message_id=message.get("task_id")
                    )
                )

                # Acknowledge removal from DLQ
                channel.basic_ack(delivery_tag=method_frame.delivery_tag)

                replayed += 1
                DLQ_REPLAYED.labels(service=SERVICE_NAME).inc()

                logger.info(f"Replayed message task_id={message.get('task_id')}")

        except Exception as e:
            span.record_exception(e)
            logger.error(f"Error replaying messages: {e}")
            failed += 1

        span.set_attribute("replay.replayed", replayed)
        span.set_attribute("replay.failed", failed)

        return {
            "status": "completed",
            "requested": request.count,
            "replayed": replayed,
            "failed": failed,
            "remaining_in_dlq": update_dlq_depth()
        }


@app.post("/dlq/replay/{task_id}")
async def replay_specific_message(task_id: str, reset_attempts: bool = True):
    """Replay a specific message from DLQ by task_id."""
    with tracer.start_as_current_span("replay_specific_message") as span:
        span.set_attribute("task.id", task_id)

        # Fetch all messages to find the specific one
        found = False

        try:
            while True:
                method_frame, properties, body = channel.basic_get(queue=DLQ_QUEUE, auto_ack=False)

                if method_frame is None:
                    break

                message = json.loads(body)

                if message.get("task_id") == task_id:
                    # Found the message, replay it
                    if reset_attempts:
                        message["attempt"] = 0
                        message["replayed_at"] = datetime.utcnow().isoformat()
                        message["replayed_from_dlq"] = True

                    # Remove DLQ-specific fields
                    message.pop("dlq_reason", None)
                    message.pop("dlq_at", None)
                    message.pop("final_error", None)

                    channel.basic_publish(
                        exchange='',
                        routing_key=MAIN_QUEUE,
                        body=json.dumps(message),
                        properties=pika.BasicProperties(
                            delivery_mode=2,
                            message_id=task_id
                        )
                    )

                    channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                    found = True
                    DLQ_REPLAYED.labels(service=SERVICE_NAME).inc()

                    logger.info(f"Replayed specific message task_id={task_id}")
                    break
                else:
                    # Not the message we're looking for, nack to requeue
                    channel.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=True)

        except Exception as e:
            span.record_exception(e)
            raise HTTPException(status_code=500, detail=str(e))

        if not found:
            raise HTTPException(status_code=404, detail=f"Message with task_id {task_id} not found in DLQ")

        return {
            "status": "replayed",
            "task_id": task_id,
            "remaining_in_dlq": update_dlq_depth()
        }


@app.delete("/dlq/purge")
async def purge_dlq():
    """Purge all messages from the DLQ."""
    with tracer.start_as_current_span("purge_dlq") as span:
        try:
            initial_depth = update_dlq_depth()
            channel.queue_purge(queue=DLQ_QUEUE)

            DLQ_DISCARDED.labels(service=SERVICE_NAME).inc(initial_depth)

            logger.warning(f"Purged {initial_depth} messages from DLQ")

            return {
                "status": "purged",
                "messages_removed": initial_depth
            }
        except Exception as e:
            span.record_exception(e)
            raise HTTPException(status_code=500, detail=str(e))


@app.delete("/dlq/message/{task_id}")
async def discard_message(task_id: str):
    """Discard a specific message from DLQ."""
    with tracer.start_as_current_span("discard_message") as span:
        span.set_attribute("task.id", task_id)

        found = False

        try:
            while True:
                method_frame, properties, body = channel.basic_get(queue=DLQ_QUEUE, auto_ack=False)

                if method_frame is None:
                    break

                message = json.loads(body)

                if message.get("task_id") == task_id:
                    channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                    found = True
                    DLQ_DISCARDED.labels(service=SERVICE_NAME).inc()

                    logger.info(f"Discarded message task_id={task_id}")
                    break
                else:
                    channel.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=True)

        except Exception as e:
            span.record_exception(e)
            raise HTTPException(status_code=500, detail=str(e))

        if not found:
            raise HTTPException(status_code=404, detail=f"Message with task_id {task_id} not found in DLQ")

        return {
            "status": "discarded",
            "task_id": task_id,
            "remaining_in_dlq": update_dlq_depth()
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
