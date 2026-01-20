"""
Producer Service - Demonstrates different message delivery semantics.

Supports:
- at-most-once: Fire and forget, no retries
- at-least-once: Retries until acknowledged
- exactly-once: Transactional writes with idempotence
"""
import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from enum import Enum

from confluent_kafka import Producer, KafkaError, KafkaException
from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "producer")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "orders")
DELIVERY_MODE = os.getenv("DELIVERY_MODE", "exactly-once")
ENABLE_TRANSACTIONS = os.getenv("ENABLE_TRANSACTIONS", "true").lower() == "true"
TRANSACTIONAL_ID = os.getenv("TRANSACTIONAL_ID", "producer-1")

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
    "messages_produced_total",
    "Total messages produced",
    ["service", "topic", "delivery_mode", "status"]
)
PRODUCE_LATENCY = Histogram(
    "produce_latency_seconds",
    "Message produce latency",
    ["service", "topic", "delivery_mode"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
TRANSACTION_COUNT = Counter(
    "transactions_total",
    "Total transactions",
    ["service", "status"]
)
CURRENT_DELIVERY_MODE = Gauge(
    "current_delivery_mode",
    "Current delivery mode (0=at-most-once, 1=at-least-once, 2=exactly-once)",
    ["service"]
)
MESSAGES_IN_TRANSACTION = Counter(
    "messages_in_transaction_total",
    "Messages sent within transactions",
    ["service", "topic"]
)


class DeliveryMode(str, Enum):
    AT_MOST_ONCE = "at-most-once"
    AT_LEAST_ONCE = "at-least-once"
    EXACTLY_ONCE = "exactly-once"


class OrderRequest(BaseModel):
    order_id: str = None
    customer_id: str
    items: list[dict]
    total_amount: float


class BatchOrderRequest(BaseModel):
    orders: list[OrderRequest]


class ConfigUpdate(BaseModel):
    delivery_mode: DeliveryMode = None
    enable_transactions: bool = None


# Global state
current_config = {
    "delivery_mode": DeliveryMode(DELIVERY_MODE),
    "enable_transactions": ENABLE_TRANSACTIONS
}
producer = None


def get_producer_config(delivery_mode: DeliveryMode, transactional: bool = False):
    """Get Kafka producer configuration based on delivery mode."""
    base_config = {
        "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
        "client.id": SERVICE_NAME,
    }

    if delivery_mode == DeliveryMode.AT_MOST_ONCE:
        # Fire and forget - no retries, no acks
        base_config.update({
            "acks": 0,
            "retries": 0,
            "linger.ms": 0,
        })
    elif delivery_mode == DeliveryMode.AT_LEAST_ONCE:
        # Retry until acknowledged
        base_config.update({
            "acks": "all",
            "retries": 3,
            "retry.backoff.ms": 100,
            "enable.idempotence": False,
        })
    elif delivery_mode == DeliveryMode.EXACTLY_ONCE:
        # Idempotent producer with optional transactions
        base_config.update({
            "acks": "all",
            "retries": 2147483647,  # Infinite retries for exactly-once
            "max.in.flight.requests.per.connection": 5,
            "enable.idempotence": True,
        })
        if transactional:
            base_config["transactional.id"] = TRANSACTIONAL_ID

    return base_config


def create_producer():
    """Create Kafka producer with current configuration."""
    global producer
    config = get_producer_config(
        current_config["delivery_mode"],
        current_config["enable_transactions"]
    )
    producer = Producer(config)

    # Initialize transactions if enabled
    if current_config["delivery_mode"] == DeliveryMode.EXACTLY_ONCE and current_config["enable_transactions"]:
        try:
            producer.init_transactions()
            logger.info("Transactions initialized successfully")
        except KafkaException as e:
            logger.error(f"Failed to initialize transactions: {e}")
            raise

    # Update metric
    mode_value = {"at-most-once": 0, "at-least-once": 1, "exactly-once": 2}
    CURRENT_DELIVERY_MODE.labels(service=SERVICE_NAME).set(
        mode_value[current_config["delivery_mode"].value]
    )

    logger.info(f"Producer created with mode: {current_config['delivery_mode']}, transactions: {current_config['enable_transactions']}")


def delivery_callback(err, msg):
    """Callback for message delivery reports."""
    if err:
        logger.error(f"Message delivery failed: {err}")
        MESSAGES_PRODUCED.labels(
            service=SERVICE_NAME,
            topic=KAFKA_TOPIC,
            delivery_mode=current_config["delivery_mode"].value,
            status="failed"
        ).inc()
    else:
        logger.debug(f"Message delivered to {msg.topic()} [{msg.partition()}] @ {msg.offset()}")
        MESSAGES_PRODUCED.labels(
            service=SERVICE_NAME,
            topic=KAFKA_TOPIC,
            delivery_mode=current_config["delivery_mode"].value,
            status="success"
        ).inc()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Delivery mode: {current_config['delivery_mode']}")
    logger.info(f"Transactions enabled: {current_config['enable_transactions']}")

    # Wait for Kafka to be ready
    await asyncio.sleep(5)
    create_producer()

    yield

    if producer:
        producer.flush(10)
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/config")
async def get_config():
    """Get current producer configuration."""
    return {
        "delivery_mode": current_config["delivery_mode"].value,
        "enable_transactions": current_config["enable_transactions"],
        "kafka_topic": KAFKA_TOPIC
    }


@app.post("/config")
async def update_config(update: ConfigUpdate):
    """Update producer configuration (requires restart of producer)."""
    global current_config

    if update.delivery_mode:
        current_config["delivery_mode"] = update.delivery_mode
    if update.enable_transactions is not None:
        current_config["enable_transactions"] = update.enable_transactions

    # Recreate producer with new config
    create_producer()

    return {
        "status": "updated",
        "delivery_mode": current_config["delivery_mode"].value,
        "enable_transactions": current_config["enable_transactions"]
    }


@app.post("/orders")
async def create_order(order: OrderRequest):
    """Create a single order - sends to Kafka."""
    with tracer.start_as_current_span("produce_order") as span:
        start_time = time.time()

        # Generate order ID if not provided
        if not order.order_id:
            order.order_id = str(uuid.uuid4())

        span.set_attribute("order_id", order.order_id)
        span.set_attribute("delivery_mode", current_config["delivery_mode"].value)

        # Get trace context for propagation
        current_span = trace.get_current_span()
        trace_id = format(current_span.get_span_context().trace_id, "032x")

        message = {
            "order_id": order.order_id,
            "customer_id": order.customer_id,
            "items": order.items,
            "total_amount": order.total_amount,
            "timestamp": datetime.utcnow().isoformat(),
            "trace_id": trace_id,
            "producer": SERVICE_NAME,
            "delivery_mode": current_config["delivery_mode"].value
        }

        try:
            if current_config["delivery_mode"] == DeliveryMode.AT_MOST_ONCE:
                # Fire and forget
                producer.produce(
                    KAFKA_TOPIC,
                    key=order.order_id.encode('utf-8'),
                    value=json.dumps(message).encode('utf-8'),
                )
                # Don't wait for delivery
                producer.poll(0)

            elif current_config["delivery_mode"] == DeliveryMode.AT_LEAST_ONCE:
                # Wait for acknowledgment
                producer.produce(
                    KAFKA_TOPIC,
                    key=order.order_id.encode('utf-8'),
                    value=json.dumps(message).encode('utf-8'),
                    callback=delivery_callback
                )
                producer.flush(10)

            elif current_config["delivery_mode"] == DeliveryMode.EXACTLY_ONCE:
                if current_config["enable_transactions"]:
                    # Transactional send
                    producer.begin_transaction()
                    try:
                        producer.produce(
                            KAFKA_TOPIC,
                            key=order.order_id.encode('utf-8'),
                            value=json.dumps(message).encode('utf-8'),
                            callback=delivery_callback
                        )
                        producer.commit_transaction()
                        TRANSACTION_COUNT.labels(service=SERVICE_NAME, status="committed").inc()
                        MESSAGES_IN_TRANSACTION.labels(service=SERVICE_NAME, topic=KAFKA_TOPIC).inc()
                    except Exception as e:
                        producer.abort_transaction()
                        TRANSACTION_COUNT.labels(service=SERVICE_NAME, status="aborted").inc()
                        raise e
                else:
                    # Idempotent but non-transactional
                    producer.produce(
                        KAFKA_TOPIC,
                        key=order.order_id.encode('utf-8'),
                        value=json.dumps(message).encode('utf-8'),
                        callback=delivery_callback
                    )
                    producer.flush(10)

            duration = time.time() - start_time
            PRODUCE_LATENCY.labels(
                service=SERVICE_NAME,
                topic=KAFKA_TOPIC,
                delivery_mode=current_config["delivery_mode"].value
            ).observe(duration)

            logger.info(f"Order {order.order_id} sent with {current_config['delivery_mode'].value} semantics")

            return {
                "status": "accepted",
                "order_id": order.order_id,
                "delivery_mode": current_config["delivery_mode"].value,
                "trace_id": trace_id,
                "latency_ms": round(duration * 1000, 2)
            }

        except KafkaException as e:
            logger.error(f"Failed to produce message: {e}")
            MESSAGES_PRODUCED.labels(
                service=SERVICE_NAME,
                topic=KAFKA_TOPIC,
                delivery_mode=current_config["delivery_mode"].value,
                status="error"
            ).inc()
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/orders/batch")
async def create_orders_batch(batch: BatchOrderRequest):
    """Create multiple orders in a single transaction (exactly-once only)."""
    if current_config["delivery_mode"] != DeliveryMode.EXACTLY_ONCE:
        raise HTTPException(
            status_code=400,
            detail="Batch orders require exactly-once delivery mode"
        )

    if not current_config["enable_transactions"]:
        raise HTTPException(
            status_code=400,
            detail="Batch orders require transactions to be enabled"
        )

    with tracer.start_as_current_span("produce_order_batch") as span:
        start_time = time.time()
        order_ids = []

        current_span = trace.get_current_span()
        trace_id = format(current_span.get_span_context().trace_id, "032x")

        span.set_attribute("batch_size", len(batch.orders))
        span.set_attribute("trace_id", trace_id)

        try:
            producer.begin_transaction()

            for order in batch.orders:
                if not order.order_id:
                    order.order_id = str(uuid.uuid4())

                order_ids.append(order.order_id)

                message = {
                    "order_id": order.order_id,
                    "customer_id": order.customer_id,
                    "items": order.items,
                    "total_amount": order.total_amount,
                    "timestamp": datetime.utcnow().isoformat(),
                    "trace_id": trace_id,
                    "producer": SERVICE_NAME,
                    "delivery_mode": current_config["delivery_mode"].value,
                    "batch": True
                }

                producer.produce(
                    KAFKA_TOPIC,
                    key=order.order_id.encode('utf-8'),
                    value=json.dumps(message).encode('utf-8'),
                    callback=delivery_callback
                )
                MESSAGES_IN_TRANSACTION.labels(service=SERVICE_NAME, topic=KAFKA_TOPIC).inc()

            producer.commit_transaction()
            TRANSACTION_COUNT.labels(service=SERVICE_NAME, status="committed").inc()

            duration = time.time() - start_time
            PRODUCE_LATENCY.labels(
                service=SERVICE_NAME,
                topic=KAFKA_TOPIC,
                delivery_mode=current_config["delivery_mode"].value
            ).observe(duration)

            logger.info(f"Batch of {len(order_ids)} orders committed in transaction")

            return {
                "status": "committed",
                "order_ids": order_ids,
                "count": len(order_ids),
                "delivery_mode": current_config["delivery_mode"].value,
                "trace_id": trace_id,
                "latency_ms": round(duration * 1000, 2)
            }

        except Exception as e:
            producer.abort_transaction()
            TRANSACTION_COUNT.labels(service=SERVICE_NAME, status="aborted").inc()
            logger.error(f"Transaction aborted: {e}")
            raise HTTPException(status_code=500, detail=f"Transaction aborted: {str(e)}")


@app.post("/simulate/duplicate")
async def simulate_duplicate(order: OrderRequest):
    """Simulate sending the same message multiple times (for testing idempotency)."""
    with tracer.start_as_current_span("simulate_duplicate") as span:
        if not order.order_id:
            order.order_id = str(uuid.uuid4())

        span.set_attribute("order_id", order.order_id)

        results = []

        # Send the same message 3 times
        for i in range(3):
            current_span = trace.get_current_span()
            trace_id = format(current_span.get_span_context().trace_id, "032x")

            message = {
                "order_id": order.order_id,  # Same order ID
                "customer_id": order.customer_id,
                "items": order.items,
                "total_amount": order.total_amount,
                "timestamp": datetime.utcnow().isoformat(),
                "trace_id": trace_id,
                "producer": SERVICE_NAME,
                "delivery_mode": current_config["delivery_mode"].value,
                "duplicate_attempt": i + 1
            }

            producer.produce(
                KAFKA_TOPIC,
                key=order.order_id.encode('utf-8'),
                value=json.dumps(message).encode('utf-8'),
                callback=delivery_callback
            )
            producer.flush(5)
            results.append({"attempt": i + 1, "sent": True})

            await asyncio.sleep(0.1)  # Small delay between duplicates

        logger.info(f"Simulated 3 duplicate messages for order {order.order_id}")

        return {
            "status": "duplicates_sent",
            "order_id": order.order_id,
            "attempts": results,
            "message": "Check consumer logs to see idempotent processing"
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
