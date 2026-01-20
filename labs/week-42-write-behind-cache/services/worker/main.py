"""
Background Worker for Write-Behind Cache.

This service:
1. Polls the Redis write queue
2. Batches writes for efficiency
3. Persists data to PostgreSQL
4. Handles retries on failure
5. Demonstrates data loss risk when cache fails
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Optional

import asyncpg
import redis.asyncio as redis
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, start_http_server

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "worker")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://labuser:labpass@localhost:5432/labdb")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))
FLUSH_INTERVAL_MS = int(os.getenv("FLUSH_INTERVAL_MS", "1000"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_DELAY_MS = int(os.getenv("RETRY_DELAY_MS", "500"))
SIMULATE_DB_FAILURE = os.getenv("SIMULATE_DB_FAILURE", "false").lower() == "true"
FAILURE_RATE = float(os.getenv("FAILURE_RATE", "0.3"))

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
WRITES_PROCESSED = Counter(
    "worker_writes_processed_total",
    "Total writes processed by worker",
    ["service", "operation", "status"]
)
BATCH_SIZE_HISTOGRAM = Histogram(
    "worker_batch_size",
    "Size of write batches processed",
    ["service"],
    buckets=[1, 2, 5, 10, 20, 50, 100]
)
WRITE_LATENCY = Histogram(
    "worker_write_duration_seconds",
    "Time to process writes",
    ["service", "operation"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
RETRY_COUNT = Counter(
    "worker_retries_total",
    "Total retry attempts",
    ["service"]
)
QUEUE_SIZE = Gauge(
    "worker_queue_size",
    "Current size of write queue",
    ["service"]
)
FAILED_WRITES = Counter(
    "worker_failed_writes_total",
    "Writes that failed after all retries (DATA LOSS)",
    ["service", "operation"]
)
COALESCED_WRITES_PROCESSED = Counter(
    "worker_coalesced_writes_processed_total",
    "Number of coalesced writes processed",
    ["service"]
)
DB_FAILURE_SIMULATION = Gauge(
    "worker_db_failure_simulation",
    "Whether DB failure simulation is enabled",
    ["service"]
)

# Redis keys
WRITE_QUEUE_KEY = "write_behind:queue"
DEAD_LETTER_QUEUE = "write_behind:dlq"

# Global connections
redis_client: Optional[redis.Redis] = None
db_pool: Optional[asyncpg.Pool] = None


async def init_connections():
    """Initialize Redis and PostgreSQL connections."""
    global redis_client, db_pool

    # Initialize Redis with retry
    for attempt in range(5):
        try:
            redis_client = redis.from_url(REDIS_URL, decode_responses=True)
            await redis_client.ping()
            logger.info("Connected to Redis")
            break
        except Exception as e:
            logger.warning(f"Redis connection attempt {attempt + 1} failed: {e}")
            await asyncio.sleep(2)
    else:
        raise RuntimeError("Failed to connect to Redis")

    # Initialize PostgreSQL with retry
    for attempt in range(5):
        try:
            db_pool = await asyncpg.create_pool(DATABASE_URL)
            logger.info("Connected to PostgreSQL")
            break
        except Exception as e:
            logger.warning(f"PostgreSQL connection attempt {attempt + 1} failed: {e}")
            await asyncio.sleep(2)
    else:
        raise RuntimeError("Failed to connect to PostgreSQL")


async def process_write(item: dict, attempt: int = 1) -> bool:
    """
    Process a single write operation with retry logic.
    Returns True on success, False on failure.
    """
    operation = item["operation"]
    data = item["data"]
    entity_id = item["id"]
    coalesced_count = item.get("coalesced_count", 1)

    with tracer.start_as_current_span(f"process_{operation}") as span:
        span.set_attribute("entity.id", entity_id)
        span.set_attribute("attempt", attempt)
        span.set_attribute("coalesced_count", coalesced_count)

        if coalesced_count > 1:
            COALESCED_WRITES_PROCESSED.labels(service=SERVICE_NAME).inc()
            logger.info(f"Processing coalesced write for {entity_id}, {coalesced_count} updates merged")

        start_time = time.time()

        try:
            # Simulate database failure for testing
            if SIMULATE_DB_FAILURE:
                import random
                if random.random() < FAILURE_RATE:
                    raise Exception("Simulated database failure")

            async with db_pool.acquire() as conn:
                if operation == "insert":
                    await conn.execute(
                        """
                        INSERT INTO products (id, name, price, quantity, created_at, updated_at)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT (id) DO UPDATE SET
                            name = EXCLUDED.name,
                            price = EXCLUDED.price,
                            quantity = EXCLUDED.quantity,
                            updated_at = EXCLUDED.updated_at
                        """,
                        data["id"],
                        data["name"],
                        float(data["price"]),
                        int(data["quantity"]),
                        datetime.fromisoformat(data["created_at"]),
                        datetime.fromisoformat(data["updated_at"])
                    )
                elif operation == "update":
                    await conn.execute(
                        """
                        UPDATE products SET name = $2, price = $3, quantity = $4, updated_at = $5
                        WHERE id = $1
                        """,
                        data["id"],
                        data["name"],
                        float(data["price"]),
                        int(data["quantity"]),
                        datetime.fromisoformat(data["updated_at"])
                    )
                elif operation == "delete":
                    await conn.execute("DELETE FROM products WHERE id = $1", data["id"])

            duration = time.time() - start_time
            WRITE_LATENCY.labels(service=SERVICE_NAME, operation=operation).observe(duration)
            WRITES_PROCESSED.labels(service=SERVICE_NAME, operation=operation, status="success").inc()
            span.set_attribute("success", True)

            logger.debug(f"Write {operation} completed for {entity_id}")
            return True

        except Exception as e:
            span.set_attribute("success", False)
            span.set_attribute("error", str(e))
            logger.warning(f"Write failed for {entity_id}: {e} (attempt {attempt}/{MAX_RETRIES})")

            if attempt < MAX_RETRIES:
                RETRY_COUNT.labels(service=SERVICE_NAME).inc()
                await asyncio.sleep(RETRY_DELAY_MS / 1000.0)
                return await process_write(item, attempt + 1)
            else:
                # All retries exhausted - move to dead letter queue
                FAILED_WRITES.labels(service=SERVICE_NAME, operation=operation).inc()
                WRITES_PROCESSED.labels(service=SERVICE_NAME, operation=operation, status="failed").inc()

                # Store in DLQ for manual intervention
                await redis_client.rpush(DEAD_LETTER_QUEUE, json.dumps({
                    **item,
                    "error": str(e),
                    "failed_at": datetime.utcnow().isoformat()
                }))

                logger.error(f"Write FAILED permanently for {entity_id} - moved to DLQ")
                return False


async def process_batch():
    """Process a batch of writes from the queue."""
    with tracer.start_as_current_span("process_batch") as span:
        # Get batch of items from queue
        items = []
        for _ in range(BATCH_SIZE):
            item = await redis_client.lpop(WRITE_QUEUE_KEY)
            if item:
                items.append(json.loads(item))
            else:
                break

        if not items:
            return 0

        span.set_attribute("batch.size", len(items))
        BATCH_SIZE_HISTOGRAM.labels(service=SERVICE_NAME).observe(len(items))

        logger.info(f"Processing batch of {len(items)} writes")

        # Process each write
        success_count = 0
        for item in items:
            if await process_write(item):
                success_count += 1

        span.set_attribute("batch.success_count", success_count)
        span.set_attribute("batch.failure_count", len(items) - success_count)

        return len(items)


async def worker_loop():
    """Main worker loop."""
    logger.info(f"Worker starting with batch_size={BATCH_SIZE}, flush_interval={FLUSH_INTERVAL_MS}ms")
    logger.info(f"DB failure simulation: {SIMULATE_DB_FAILURE} (rate: {FAILURE_RATE})")

    DB_FAILURE_SIMULATION.labels(service=SERVICE_NAME).set(1 if SIMULATE_DB_FAILURE else 0)

    while True:
        try:
            # Update queue size metric
            queue_size = await redis_client.llen(WRITE_QUEUE_KEY)
            QUEUE_SIZE.labels(service=SERVICE_NAME).set(queue_size)

            if queue_size > 0:
                processed = await process_batch()
                if processed > 0:
                    logger.debug(f"Processed {processed} writes, queue size: {queue_size - processed}")
                    # If we processed a full batch, immediately check for more
                    if processed >= BATCH_SIZE:
                        continue

            # Wait for next flush interval
            await asyncio.sleep(FLUSH_INTERVAL_MS / 1000.0)

        except Exception as e:
            logger.error(f"Worker loop error: {e}")
            await asyncio.sleep(1)


async def main():
    """Main entry point."""
    # Start Prometheus metrics server
    start_http_server(8001)
    logger.info("Prometheus metrics server started on port 8001")

    # Initialize connections
    await init_connections()

    # Start worker loop
    await worker_loop()


if __name__ == "__main__":
    asyncio.run(main())
