"""
API Service with Write-Behind (Write-Back) Cache Implementation.

This service demonstrates write-behind caching where:
1. Writes go to Redis first (cache)
2. Redis queues writes for async persistence to PostgreSQL
3. Reads check cache first, fall back to database
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

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "api")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://labuser:labpass@localhost:5432/labdb")
WRITE_BEHIND_ENABLED = os.getenv("WRITE_BEHIND_ENABLED", "true").lower() == "true"
WRITE_COALESCING_ENABLED = os.getenv("WRITE_COALESCING_ENABLED", "true").lower() == "true"
COALESCING_WINDOW_MS = int(os.getenv("COALESCING_WINDOW_MS", "500"))

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
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["service", "method", "endpoint"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
CACHE_HITS = Counter(
    "cache_hits_total",
    "Total cache hits",
    ["service", "operation"]
)
CACHE_MISSES = Counter(
    "cache_misses_total",
    "Total cache misses",
    ["service", "operation"]
)
WRITE_QUEUE_SIZE = Gauge(
    "write_queue_size",
    "Number of items in write-behind queue",
    ["service"]
)
WRITES_COALESCED = Counter(
    "writes_coalesced_total",
    "Number of writes that were coalesced",
    ["service"]
)
WRITE_MODE = Gauge(
    "write_mode_enabled",
    "Write mode: 1=write-behind, 0=write-through",
    ["service"]
)

# Redis keys
WRITE_QUEUE_KEY = "write_behind:queue"
COALESCING_KEY_PREFIX = "write_behind:coalescing:"
CACHE_KEY_PREFIX = "cache:product:"

# Global connections
redis_client: Optional[redis.Redis] = None
db_pool: Optional[asyncpg.Pool] = None


class Product(BaseModel):
    name: str
    price: float
    quantity: int


class ProductResponse(BaseModel):
    id: str
    name: str
    price: float
    quantity: int
    created_at: str
    updated_at: str
    source: str  # "cache" or "database"


class WriteQueueItem(BaseModel):
    id: str
    operation: str  # "insert", "update", "delete"
    data: dict
    timestamp: float
    coalesced_count: int = 1


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, db_pool

    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Write-behind enabled: {WRITE_BEHIND_ENABLED}")
    logger.info(f"Write coalescing enabled: {WRITE_COALESCING_ENABLED}")

    # Initialize Redis
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    await redis_client.ping()
    logger.info("Connected to Redis")

    # Initialize PostgreSQL
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    logger.info("Connected to PostgreSQL")

    # Set initial metrics
    WRITE_MODE.labels(service=SERVICE_NAME).set(1 if WRITE_BEHIND_ENABLED else 0)

    yield

    # Cleanup
    if redis_client:
        await redis_client.close()
    if db_pool:
        await db_pool.close()
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/admin/config")
async def get_config():
    """Get current configuration."""
    queue_size = await redis_client.llen(WRITE_QUEUE_KEY)
    WRITE_QUEUE_SIZE.labels(service=SERVICE_NAME).set(queue_size)

    return {
        "write_behind_enabled": WRITE_BEHIND_ENABLED,
        "write_coalescing_enabled": WRITE_COALESCING_ENABLED,
        "coalescing_window_ms": COALESCING_WINDOW_MS,
        "queue_size": queue_size
    }


@app.get("/admin/queue")
async def get_queue_status():
    """Get write queue status for debugging."""
    queue_items = await redis_client.lrange(WRITE_QUEUE_KEY, 0, -1)
    items = [json.loads(item) for item in queue_items]

    return {
        "queue_size": len(items),
        "items": items[:20]  # Return first 20 items
    }


@app.post("/admin/flush-queue")
async def flush_queue():
    """Manually trigger queue flush (for testing)."""
    queue_size = await redis_client.llen(WRITE_QUEUE_KEY)
    # The worker handles actual flushing - this just returns current state
    return {
        "message": "Queue flush is handled by worker service",
        "current_queue_size": queue_size
    }


@app.post("/products", response_model=ProductResponse)
async def create_product(product: Product):
    """Create a new product using write-behind caching."""
    start_time = time.time()

    with tracer.start_as_current_span("create_product") as span:
        product_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        product_data = {
            "id": product_id,
            "name": product.name,
            "price": product.price,
            "quantity": product.quantity,
            "created_at": now,
            "updated_at": now
        }

        span.set_attribute("product.id", product_id)
        span.set_attribute("write_behind.enabled", WRITE_BEHIND_ENABLED)

        # Write to cache
        with tracer.start_as_current_span("cache_write"):
            cache_key = f"{CACHE_KEY_PREFIX}{product_id}"
            await redis_client.set(cache_key, json.dumps(product_data))
            await redis_client.expire(cache_key, 3600)  # 1 hour TTL

        if WRITE_BEHIND_ENABLED:
            # Queue for async write to database
            with tracer.start_as_current_span("queue_write"):
                await queue_write_operation("insert", product_id, product_data)
                span.set_attribute("write_behind.queued", True)
        else:
            # Write-through: synchronous database write
            with tracer.start_as_current_span("database_write_sync"):
                await write_to_database("insert", product_id, product_data)
                span.set_attribute("write_behind.queued", False)

        duration = time.time() - start_time
        REQUEST_COUNT.labels(
            service=SERVICE_NAME, method="POST", endpoint="/products", status="201"
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME, method="POST", endpoint="/products"
        ).observe(duration)

        logger.info(f"Product created id={product_id} write_behind={WRITE_BEHIND_ENABLED}")

        return ProductResponse(**product_data, source="cache")


@app.get("/products/{product_id}", response_model=ProductResponse)
async def get_product(product_id: str):
    """Get a product, checking cache first."""
    start_time = time.time()

    with tracer.start_as_current_span("get_product") as span:
        span.set_attribute("product.id", product_id)

        # Check cache first
        with tracer.start_as_current_span("cache_read"):
            cache_key = f"{CACHE_KEY_PREFIX}{product_id}"
            cached = await redis_client.get(cache_key)

        if cached:
            CACHE_HITS.labels(service=SERVICE_NAME, operation="read").inc()
            span.set_attribute("cache.hit", True)
            product_data = json.loads(cached)

            duration = time.time() - start_time
            REQUEST_COUNT.labels(
                service=SERVICE_NAME, method="GET", endpoint="/products/{id}", status="200"
            ).inc()
            REQUEST_LATENCY.labels(
                service=SERVICE_NAME, method="GET", endpoint="/products/{id}"
            ).observe(duration)

            return ProductResponse(**product_data, source="cache")

        # Cache miss - read from database
        CACHE_MISSES.labels(service=SERVICE_NAME, operation="read").inc()
        span.set_attribute("cache.hit", False)

        with tracer.start_as_current_span("database_read"):
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM products WHERE id = $1",
                    product_id
                )

        if not row:
            REQUEST_COUNT.labels(
                service=SERVICE_NAME, method="GET", endpoint="/products/{id}", status="404"
            ).inc()
            raise HTTPException(status_code=404, detail="Product not found")

        product_data = {
            "id": str(row["id"]),
            "name": row["name"],
            "price": float(row["price"]),
            "quantity": row["quantity"],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat()
        }

        # Populate cache
        with tracer.start_as_current_span("cache_populate"):
            await redis_client.set(cache_key, json.dumps(product_data))
            await redis_client.expire(cache_key, 3600)

        duration = time.time() - start_time
        REQUEST_COUNT.labels(
            service=SERVICE_NAME, method="GET", endpoint="/products/{id}", status="200"
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME, method="GET", endpoint="/products/{id}"
        ).observe(duration)

        return ProductResponse(**product_data, source="database")


@app.put("/products/{product_id}", response_model=ProductResponse)
async def update_product(product_id: str, product: Product):
    """Update a product using write-behind caching with coalescing."""
    start_time = time.time()

    with tracer.start_as_current_span("update_product") as span:
        span.set_attribute("product.id", product_id)
        span.set_attribute("write_behind.enabled", WRITE_BEHIND_ENABLED)
        span.set_attribute("write_coalescing.enabled", WRITE_COALESCING_ENABLED)

        # Check if product exists in cache or database
        cache_key = f"{CACHE_KEY_PREFIX}{product_id}"
        cached = await redis_client.get(cache_key)

        if cached:
            existing = json.loads(cached)
        else:
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM products WHERE id = $1", product_id)
                if not row:
                    raise HTTPException(status_code=404, detail="Product not found")
                existing = {
                    "id": str(row["id"]),
                    "created_at": row["created_at"].isoformat()
                }

        now = datetime.utcnow().isoformat()
        product_data = {
            "id": product_id,
            "name": product.name,
            "price": product.price,
            "quantity": product.quantity,
            "created_at": existing.get("created_at", now),
            "updated_at": now
        }

        # Update cache
        with tracer.start_as_current_span("cache_write"):
            await redis_client.set(cache_key, json.dumps(product_data))
            await redis_client.expire(cache_key, 3600)

        if WRITE_BEHIND_ENABLED:
            with tracer.start_as_current_span("queue_write"):
                coalesced = await queue_write_operation("update", product_id, product_data)
                span.set_attribute("write_behind.coalesced", coalesced)
        else:
            with tracer.start_as_current_span("database_write_sync"):
                await write_to_database("update", product_id, product_data)

        duration = time.time() - start_time
        REQUEST_COUNT.labels(
            service=SERVICE_NAME, method="PUT", endpoint="/products/{id}", status="200"
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME, method="PUT", endpoint="/products/{id}"
        ).observe(duration)

        logger.info(f"Product updated id={product_id}")

        return ProductResponse(**product_data, source="cache")


@app.delete("/products/{product_id}")
async def delete_product(product_id: str):
    """Delete a product using write-behind caching."""
    start_time = time.time()

    with tracer.start_as_current_span("delete_product") as span:
        span.set_attribute("product.id", product_id)

        # Remove from cache
        cache_key = f"{CACHE_KEY_PREFIX}{product_id}"
        await redis_client.delete(cache_key)

        if WRITE_BEHIND_ENABLED:
            with tracer.start_as_current_span("queue_write"):
                await queue_write_operation("delete", product_id, {"id": product_id})
        else:
            with tracer.start_as_current_span("database_write_sync"):
                await write_to_database("delete", product_id, {"id": product_id})

        duration = time.time() - start_time
        REQUEST_COUNT.labels(
            service=SERVICE_NAME, method="DELETE", endpoint="/products/{id}", status="200"
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME, method="DELETE", endpoint="/products/{id}"
        ).observe(duration)

        return {"message": "Product deleted", "id": product_id}


async def queue_write_operation(operation: str, entity_id: str, data: dict) -> bool:
    """
    Queue a write operation for async processing.
    Returns True if the write was coalesced with an existing pending write.
    """
    coalesced = False

    if WRITE_COALESCING_ENABLED and operation == "update":
        # Check for existing pending write for this entity
        coalescing_key = f"{COALESCING_KEY_PREFIX}{entity_id}"
        existing = await redis_client.get(coalescing_key)

        if existing:
            # Coalesce: update the existing queued item instead of adding new
            existing_item = json.loads(existing)
            existing_item["data"] = data
            existing_item["timestamp"] = time.time()
            existing_item["coalesced_count"] = existing_item.get("coalesced_count", 1) + 1

            # Update the coalescing window
            await redis_client.set(coalescing_key, json.dumps(existing_item))
            await redis_client.pexpire(coalescing_key, COALESCING_WINDOW_MS)

            WRITES_COALESCED.labels(service=SERVICE_NAME).inc()
            coalesced = True
            logger.debug(f"Coalesced write for {entity_id}, count={existing_item['coalesced_count']}")
        else:
            # New write - add to queue and set coalescing marker
            queue_item = WriteQueueItem(
                id=entity_id,
                operation=operation,
                data=data,
                timestamp=time.time()
            )
            await redis_client.rpush(WRITE_QUEUE_KEY, queue_item.model_dump_json())
            await redis_client.set(coalescing_key, queue_item.model_dump_json())
            await redis_client.pexpire(coalescing_key, COALESCING_WINDOW_MS)
    else:
        # No coalescing or not an update
        queue_item = WriteQueueItem(
            id=entity_id,
            operation=operation,
            data=data,
            timestamp=time.time()
        )
        await redis_client.rpush(WRITE_QUEUE_KEY, queue_item.model_dump_json())

    # Update queue size metric
    queue_size = await redis_client.llen(WRITE_QUEUE_KEY)
    WRITE_QUEUE_SIZE.labels(service=SERVICE_NAME).set(queue_size)

    return coalesced


async def write_to_database(operation: str, entity_id: str, data: dict):
    """Synchronous write to database (write-through mode)."""
    async with db_pool.acquire() as conn:
        if operation == "insert":
            await conn.execute(
                """
                INSERT INTO products (id, name, price, quantity, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                data["id"],
                data["name"],
                data["price"],
                data["quantity"],
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
                data["price"],
                data["quantity"],
                datetime.fromisoformat(data["updated_at"])
            )
        elif operation == "delete":
            await conn.execute("DELETE FROM products WHERE id = $1", data["id"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
