"""
Consumer Service - Demonstrates idempotent message processing.

Supports different processing modes and failure injection for testing.
Uses database for deduplication and exactly-once processing guarantees.
"""
import asyncio
import json
import logging
import os
import signal
import sys
import threading
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime
from enum import Enum

import psycopg2
import psycopg2.extras
from confluent_kafka import Consumer, KafkaError, KafkaException, TopicPartition, OFFSET_STORED
from fastapi import FastAPI
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "consumer")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "orders")
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "order-processors")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://lab:lab123@postgres:5432/exactly_once")
PROCESSING_MODE = os.getenv("PROCESSING_MODE", "exactly-once")
ENABLE_IDEMPOTENCY = os.getenv("ENABLE_IDEMPOTENCY", "true").lower() == "true"
FAILURE_RATE = float(os.getenv("FAILURE_RATE", "0.0"))
CRASH_AFTER_MESSAGES = int(os.getenv("CRASH_AFTER_MESSAGES", "0"))

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
MESSAGES_CONSUMED = Counter(
    "messages_consumed_total",
    "Total messages consumed",
    ["service", "topic", "status"]
)
MESSAGES_PROCESSED = Counter(
    "messages_processed_total",
    "Total messages successfully processed",
    ["service", "topic", "processing_mode"]
)
DUPLICATES_DETECTED = Counter(
    "duplicates_detected_total",
    "Total duplicate messages detected and skipped",
    ["service", "topic"]
)
PROCESSING_LATENCY = Histogram(
    "processing_latency_seconds",
    "Message processing latency",
    ["service", "topic"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5]
)
CONSUMER_LAG = Gauge(
    "consumer_lag",
    "Consumer lag (messages behind)",
    ["service", "topic", "partition"]
)
PROCESSING_ERRORS = Counter(
    "processing_errors_total",
    "Total processing errors",
    ["service", "topic", "error_type"]
)
CURRENT_PROCESSING_MODE = Gauge(
    "current_processing_mode",
    "Current processing mode (0=at-most-once, 1=at-least-once, 2=exactly-once)",
    ["service"]
)
IDEMPOTENCY_ENABLED = Gauge(
    "idempotency_enabled",
    "Whether idempotency checking is enabled",
    ["service"]
)
MESSAGES_IN_DB = Gauge(
    "messages_in_database_total",
    "Total messages stored in database",
    ["service"]
)
CRASH_SIMULATED = Counter(
    "crash_simulated_total",
    "Total simulated crashes",
    ["service"]
)


class ProcessingMode(str, Enum):
    AT_MOST_ONCE = "at-most-once"
    AT_LEAST_ONCE = "at-least-once"
    EXACTLY_ONCE = "exactly-once"


class ConfigUpdate(BaseModel):
    processing_mode: ProcessingMode = None
    enable_idempotency: bool = None
    failure_rate: float = None
    crash_after_messages: int = None


# Global state
current_config = {
    "processing_mode": ProcessingMode(PROCESSING_MODE),
    "enable_idempotency": ENABLE_IDEMPOTENCY,
    "failure_rate": FAILURE_RATE,
    "crash_after_messages": CRASH_AFTER_MESSAGES
}
consumer = None
db_conn = None
consumer_thread = None
running = True
message_count = 0


def get_db_connection():
    """Create database connection."""
    return psycopg2.connect(DATABASE_URL)


def get_consumer_config(processing_mode: ProcessingMode):
    """Get Kafka consumer configuration based on processing mode."""
    base_config = {
        "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
        "group.id": KAFKA_GROUP_ID,
        "client.id": SERVICE_NAME,
        "auto.offset.reset": "earliest",
    }

    if processing_mode == ProcessingMode.AT_MOST_ONCE:
        # Commit before processing - may lose messages on crash
        base_config.update({
            "enable.auto.commit": True,
            "auto.commit.interval.ms": 100,  # Commit frequently
        })
    elif processing_mode == ProcessingMode.AT_LEAST_ONCE:
        # Manual commit after processing - may reprocess on crash
        base_config.update({
            "enable.auto.commit": False,
        })
    elif processing_mode == ProcessingMode.EXACTLY_ONCE:
        # Manual commit with idempotent processing
        base_config.update({
            "enable.auto.commit": False,
            "isolation.level": "read_committed",  # Only read committed transactional messages
        })

    return base_config


def is_duplicate(order_id: str, conn) -> bool:
    """Check if message has already been processed."""
    if not current_config["enable_idempotency"]:
        return False

    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM processed_messages WHERE message_id = %s",
            (order_id,)
        )
        return cur.fetchone() is not None


def mark_processed(order_id: str, message_data: dict, conn) -> bool:
    """Mark message as processed in database with idempotency check."""
    try:
        with conn.cursor() as cur:
            # Use INSERT ... ON CONFLICT for atomic upsert
            cur.execute("""
                INSERT INTO processed_messages (message_id, payload, processed_at, consumer_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (message_id) DO NOTHING
                RETURNING message_id
            """, (
                order_id,
                json.dumps(message_data),
                datetime.utcnow(),
                SERVICE_NAME
            ))

            result = cur.fetchone()
            conn.commit()

            if result is None:
                # Row already existed - duplicate
                return False
            return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error: {e}")
        raise


def process_order(order_data: dict, conn) -> dict:
    """Process an order - the actual business logic."""
    import random

    # Simulate occasional failures
    if current_config["failure_rate"] > 0 and random.random() < current_config["failure_rate"]:
        PROCESSING_ERRORS.labels(service=SERVICE_NAME, topic=KAFKA_TOPIC, error_type="simulated").inc()
        raise Exception("Simulated processing failure")

    # Store order in orders table
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO orders (order_id, customer_id, items, total_amount, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (order_id) DO UPDATE SET
                status = EXCLUDED.status,
                updated_at = NOW()
            RETURNING order_id
        """, (
            order_data["order_id"],
            order_data["customer_id"],
            json.dumps(order_data["items"]),
            order_data["total_amount"],
            "processed",
            datetime.utcnow()
        ))
        conn.commit()

    # Simulate processing time
    time.sleep(random.uniform(0.01, 0.05))

    return {
        "order_id": order_data["order_id"],
        "status": "processed",
        "processed_at": datetime.utcnow().isoformat()
    }


def consume_messages():
    """Main consumer loop running in background thread."""
    global message_count, running, consumer, db_conn

    logger.info(f"Consumer thread started with mode: {current_config['processing_mode']}")

    config = get_consumer_config(current_config["processing_mode"])
    consumer = Consumer(config)
    consumer.subscribe([KAFKA_TOPIC])

    db_conn = get_db_connection()

    try:
        while running:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    logger.debug(f"End of partition {msg.partition()}")
                else:
                    logger.error(f"Consumer error: {msg.error()}")
                    PROCESSING_ERRORS.labels(
                        service=SERVICE_NAME,
                        topic=KAFKA_TOPIC,
                        error_type="kafka"
                    ).inc()
                continue

            start_time = time.time()
            message_count += 1

            try:
                # Parse message
                value = json.loads(msg.value().decode('utf-8'))
                order_id = value.get("order_id")

                with tracer.start_as_current_span("process_message") as span:
                    span.set_attribute("order_id", order_id)
                    span.set_attribute("kafka.topic", msg.topic())
                    span.set_attribute("kafka.partition", msg.partition())
                    span.set_attribute("kafka.offset", msg.offset())
                    span.set_attribute("processing_mode", current_config["processing_mode"].value)

                    MESSAGES_CONSUMED.labels(
                        service=SERVICE_NAME,
                        topic=KAFKA_TOPIC,
                        status="received"
                    ).inc()

                    if current_config["processing_mode"] == ProcessingMode.AT_MOST_ONCE:
                        # Message already committed by auto-commit
                        # Just process - may lose on crash
                        process_order(value, db_conn)
                        MESSAGES_PROCESSED.labels(
                            service=SERVICE_NAME,
                            topic=KAFKA_TOPIC,
                            processing_mode="at-most-once"
                        ).inc()

                    elif current_config["processing_mode"] == ProcessingMode.AT_LEAST_ONCE:
                        # Process first, then commit
                        process_order(value, db_conn)
                        consumer.commit(message=msg, asynchronous=False)
                        MESSAGES_PROCESSED.labels(
                            service=SERVICE_NAME,
                            topic=KAFKA_TOPIC,
                            processing_mode="at-least-once"
                        ).inc()

                    elif current_config["processing_mode"] == ProcessingMode.EXACTLY_ONCE:
                        # Check for duplicate first
                        if current_config["enable_idempotency"]:
                            # Atomic: check duplicate + mark processed + process order
                            if is_duplicate(order_id, db_conn):
                                logger.info(f"Duplicate detected, skipping: {order_id}")
                                DUPLICATES_DETECTED.labels(
                                    service=SERVICE_NAME,
                                    topic=KAFKA_TOPIC
                                ).inc()
                                span.set_attribute("duplicate", True)
                                consumer.commit(message=msg, asynchronous=False)
                                continue

                            # Process and mark as processed atomically
                            if mark_processed(order_id, value, db_conn):
                                process_order(value, db_conn)
                                consumer.commit(message=msg, asynchronous=False)
                                MESSAGES_PROCESSED.labels(
                                    service=SERVICE_NAME,
                                    topic=KAFKA_TOPIC,
                                    processing_mode="exactly-once"
                                ).inc()
                            else:
                                # Race condition - another consumer processed it
                                logger.info(f"Race condition duplicate: {order_id}")
                                DUPLICATES_DETECTED.labels(
                                    service=SERVICE_NAME,
                                    topic=KAFKA_TOPIC
                                ).inc()
                                consumer.commit(message=msg, asynchronous=False)
                        else:
                            # No idempotency - just process and commit
                            process_order(value, db_conn)
                            consumer.commit(message=msg, asynchronous=False)
                            MESSAGES_PROCESSED.labels(
                                service=SERVICE_NAME,
                                topic=KAFKA_TOPIC,
                                processing_mode="exactly-once"
                            ).inc()

                    duration = time.time() - start_time
                    PROCESSING_LATENCY.labels(
                        service=SERVICE_NAME,
                        topic=KAFKA_TOPIC
                    ).observe(duration)

                    logger.info(
                        f"Processed message: order_id={order_id}, "
                        f"partition={msg.partition()}, offset={msg.offset()}, "
                        f"mode={current_config['processing_mode'].value}, "
                        f"duration={duration*1000:.2f}ms"
                    )

                    # Check for crash simulation
                    if current_config["crash_after_messages"] > 0:
                        if message_count >= current_config["crash_after_messages"]:
                            logger.warning(f"Simulating crash after {message_count} messages!")
                            CRASH_SIMULATED.labels(service=SERVICE_NAME).inc()
                            # Don't commit the last message in at-least-once mode
                            if current_config["processing_mode"] != ProcessingMode.AT_MOST_ONCE:
                                # This simulates a crash before commit
                                pass
                            os._exit(1)  # Hard exit to simulate crash

            except Exception as e:
                logger.error(f"Error processing message: {e}")
                logger.error(traceback.format_exc())
                PROCESSING_ERRORS.labels(
                    service=SERVICE_NAME,
                    topic=KAFKA_TOPIC,
                    error_type="processing"
                ).inc()

                # In exactly-once mode, don't commit failed messages
                if current_config["processing_mode"] == ProcessingMode.AT_LEAST_ONCE:
                    # Don't commit - will be reprocessed
                    pass

    except Exception as e:
        logger.error(f"Consumer thread error: {e}")
        logger.error(traceback.format_exc())
    finally:
        if consumer:
            consumer.close()
        if db_conn:
            db_conn.close()
        logger.info("Consumer thread stopped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global consumer_thread, running

    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Processing mode: {current_config['processing_mode']}")
    logger.info(f"Idempotency enabled: {current_config['enable_idempotency']}")
    logger.info(f"Failure rate: {current_config['failure_rate']}")
    logger.info(f"Crash after messages: {current_config['crash_after_messages']}")

    # Update metrics
    mode_value = {"at-most-once": 0, "at-least-once": 1, "exactly-once": 2}
    CURRENT_PROCESSING_MODE.labels(service=SERVICE_NAME).set(
        mode_value[current_config["processing_mode"].value]
    )
    IDEMPOTENCY_ENABLED.labels(service=SERVICE_NAME).set(
        1 if current_config["enable_idempotency"] else 0
    )

    # Wait for Kafka to be ready
    await asyncio.sleep(5)

    # Start consumer thread
    running = True
    consumer_thread = threading.Thread(target=consume_messages, daemon=True)
    consumer_thread.start()

    yield

    running = False
    if consumer_thread:
        consumer_thread.join(timeout=5)
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "messages_processed": message_count
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/config")
async def get_config():
    """Get current consumer configuration."""
    return {
        "processing_mode": current_config["processing_mode"].value,
        "enable_idempotency": current_config["enable_idempotency"],
        "failure_rate": current_config["failure_rate"],
        "crash_after_messages": current_config["crash_after_messages"],
        "kafka_topic": KAFKA_TOPIC,
        "kafka_group_id": KAFKA_GROUP_ID,
        "messages_processed": message_count
    }


@app.post("/config")
async def update_config(update: ConfigUpdate):
    """Update consumer configuration (some changes require restart)."""
    global current_config

    restart_required = False

    if update.processing_mode and update.processing_mode != current_config["processing_mode"]:
        current_config["processing_mode"] = update.processing_mode
        restart_required = True

    if update.enable_idempotency is not None:
        current_config["enable_idempotency"] = update.enable_idempotency
        IDEMPOTENCY_ENABLED.labels(service=SERVICE_NAME).set(
            1 if update.enable_idempotency else 0
        )

    if update.failure_rate is not None:
        current_config["failure_rate"] = update.failure_rate

    if update.crash_after_messages is not None:
        current_config["crash_after_messages"] = update.crash_after_messages

    # Update processing mode metric
    mode_value = {"at-most-once": 0, "at-least-once": 1, "exactly-once": 2}
    CURRENT_PROCESSING_MODE.labels(service=SERVICE_NAME).set(
        mode_value[current_config["processing_mode"].value]
    )

    return {
        "status": "updated",
        "restart_required": restart_required,
        **current_config,
        "processing_mode": current_config["processing_mode"].value
    }


@app.get("/stats")
async def get_stats():
    """Get processing statistics from database."""
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Total processed messages
            cur.execute("SELECT COUNT(*) as total FROM processed_messages")
            total_processed = cur.fetchone()["total"]

            # Total orders
            cur.execute("SELECT COUNT(*) as total FROM orders")
            total_orders = cur.fetchone()["total"]

            # Recent messages
            cur.execute("""
                SELECT message_id, processed_at, consumer_id
                FROM processed_messages
                ORDER BY processed_at DESC
                LIMIT 10
            """)
            recent = cur.fetchall()

            # Messages by consumer
            cur.execute("""
                SELECT consumer_id, COUNT(*) as count
                FROM processed_messages
                GROUP BY consumer_id
            """)
            by_consumer = cur.fetchall()

        conn.close()

        MESSAGES_IN_DB.labels(service=SERVICE_NAME).set(total_processed)

        return {
            "total_processed_messages": total_processed,
            "total_orders": total_orders,
            "messages_by_consumer": by_consumer,
            "recent_messages": recent
        }
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        return {"error": str(e)}


@app.post("/reset-stats")
async def reset_stats():
    """Reset all statistics (clear database tables)."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE processed_messages CASCADE")
            cur.execute("TRUNCATE TABLE orders CASCADE")
        conn.commit()
        conn.close()

        global message_count
        message_count = 0

        MESSAGES_IN_DB.labels(service=SERVICE_NAME).set(0)

        return {"status": "reset", "message": "All statistics cleared"}
    except Exception as e:
        logger.error(f"Error resetting stats: {e}")
        return {"error": str(e)}


@app.post("/simulate/crash")
async def simulate_crash():
    """Simulate a consumer crash (for testing recovery)."""
    logger.warning("Simulating crash!")
    CRASH_SIMULATED.labels(service=SERVICE_NAME).inc()

    # Hard exit to simulate unexpected crash
    def crash():
        time.sleep(0.1)
        os._exit(1)

    threading.Thread(target=crash).start()

    return {"status": "crashing", "message": "Consumer will crash in 100ms"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
