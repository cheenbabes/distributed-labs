"""
API Service with Write-Through Cache Pattern

This service demonstrates the write-through caching pattern:
- Every WRITE goes to BOTH Redis (cache) and Postgres (database)
- Every READ checks Redis first, then falls back to Postgres on cache miss
- Guarantees consistency: cache and database are always in sync
- Trade-off: Higher write latency (two operations) for consistency
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
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "api")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://labuser:labpass@localhost:5432/items_db")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "300"))

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
    "Cache hits",
    ["service", "operation"]
)
CACHE_MISSES = Counter(
    "cache_misses_total",
    "Cache misses",
    ["service", "operation"]
)
DB_OPERATIONS = Counter(
    "db_operations_total",
    "Database operations",
    ["service", "operation", "status"]
)
WRITE_LATENCY = Histogram(
    "write_operation_seconds",
    "Write operation latency breakdown",
    ["service", "target"],  # target: redis, postgres, total
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5]
)

# Global connections
redis_client: Optional[redis.Redis] = None
pg_pool: Optional[asyncpg.Pool] = None


class Item(BaseModel):
    name: str
    description: Optional[str] = None
    price: float


class ItemResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    price: float
    created_at: str
    updated_at: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, pg_pool

    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Redis URL: {REDIS_URL}")
    logger.info(f"Postgres URL: {POSTGRES_URL.replace('labpass', '***')}")

    # Connect to Redis
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    await redis_client.ping()
    logger.info("Connected to Redis")

    # Connect to Postgres
    pg_pool = await asyncpg.create_pool(POSTGRES_URL, min_size=2, max_size=10)
    logger.info("Connected to Postgres")

    yield

    # Cleanup
    if redis_client:
        await redis_client.close()
    if pg_pool:
        await pg_pool.close()
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


def get_cache_key(item_id: str) -> str:
    """Generate cache key for an item."""
    return f"item:{item_id}"


def serialize_item(row: asyncpg.Record) -> dict:
    """Convert database row to dictionary."""
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "price": float(row["price"]),
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat()
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    redis_ok = False
    postgres_ok = False

    try:
        await redis_client.ping()
        redis_ok = True
    except Exception as e:
        logger.warning(f"Redis health check failed: {e}")

    try:
        async with pg_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        postgres_ok = True
    except Exception as e:
        logger.warning(f"Postgres health check failed: {e}")

    return {
        "status": "ok" if (redis_ok and postgres_ok) else "degraded",
        "service": SERVICE_NAME,
        "dependencies": {
            "redis": "ok" if redis_ok else "down",
            "postgres": "ok" if postgres_ok else "down"
        }
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/items", response_model=ItemResponse)
async def create_item(item: Item):
    """
    Create a new item using WRITE-THROUGH pattern.

    The write goes to BOTH Redis AND Postgres:
    1. Generate ID and timestamps
    2. Write to Postgres (source of truth)
    3. Write to Redis (cache)

    Both writes happen in sequence - if Postgres fails, we don't cache.
    This ensures consistency.
    """
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")
    start_time = time.time()

    item_id = str(uuid.uuid4())
    now = datetime.utcnow()

    item_data = {
        "id": item_id,
        "name": item.name,
        "description": item.description,
        "price": item.price,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat()
    }

    # Step 1: Write to Postgres (source of truth)
    with tracer.start_as_current_span("postgres_insert") as pg_span:
        pg_start = time.time()
        pg_span.set_attribute("db.system", "postgresql")
        pg_span.set_attribute("db.operation", "INSERT")
        pg_span.set_attribute("item.id", item_id)

        try:
            async with pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO items (id, name, description, price, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    item_id, item.name, item.description, item.price, now, now
                )
            pg_duration = time.time() - pg_start
            pg_span.set_attribute("db.duration_ms", round(pg_duration * 1000, 2))
            WRITE_LATENCY.labels(service=SERVICE_NAME, target="postgres").observe(pg_duration)
            DB_OPERATIONS.labels(service=SERVICE_NAME, operation="insert", status="success").inc()
            logger.info(f"Postgres INSERT completed item_id={item_id} duration={pg_duration*1000:.2f}ms")
        except Exception as e:
            DB_OPERATIONS.labels(service=SERVICE_NAME, operation="insert", status="error").inc()
            pg_span.set_attribute("error", True)
            pg_span.set_attribute("error.message", str(e))
            logger.error(f"Postgres INSERT failed: {e}")
            raise HTTPException(status_code=500, detail="Database error")

    # Step 2: Write to Redis (cache)
    with tracer.start_as_current_span("redis_set") as redis_span:
        redis_start = time.time()
        redis_span.set_attribute("db.system", "redis")
        redis_span.set_attribute("db.operation", "SET")
        redis_span.set_attribute("cache.key", get_cache_key(item_id))
        redis_span.set_attribute("cache.ttl_seconds", CACHE_TTL_SECONDS)

        try:
            cache_key = get_cache_key(item_id)
            await redis_client.setex(
                cache_key,
                CACHE_TTL_SECONDS,
                json.dumps(item_data)
            )
            redis_duration = time.time() - redis_start
            redis_span.set_attribute("cache.duration_ms", round(redis_duration * 1000, 2))
            WRITE_LATENCY.labels(service=SERVICE_NAME, target="redis").observe(redis_duration)
            logger.info(f"Redis SET completed key={cache_key} duration={redis_duration*1000:.2f}ms")
        except Exception as e:
            # Log but don't fail - Postgres write succeeded
            redis_span.set_attribute("error", True)
            redis_span.set_attribute("error.message", str(e))
            logger.warning(f"Redis SET failed (non-fatal): {e}")

    total_duration = time.time() - start_time
    WRITE_LATENCY.labels(service=SERVICE_NAME, target="total").observe(total_duration)
    REQUEST_LATENCY.labels(service=SERVICE_NAME, method="POST", endpoint="/items").observe(total_duration)
    REQUEST_COUNT.labels(service=SERVICE_NAME, method="POST", endpoint="/items", status="201").inc()

    logger.info(f"CREATE completed item_id={item_id} trace_id={trace_id} total_duration={total_duration*1000:.2f}ms")

    return ItemResponse(**item_data)


@app.get("/items/{item_id}", response_model=ItemResponse)
async def get_item(item_id: str):
    """
    Get an item - checks cache first, then database.

    Read pattern:
    1. Check Redis cache
    2. If HIT: return cached data
    3. If MISS: read from Postgres, cache result, return
    """
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")
    start_time = time.time()

    cache_key = get_cache_key(item_id)
    item_data = None
    cache_hit = False

    # Step 1: Check Redis cache
    with tracer.start_as_current_span("redis_get") as redis_span:
        redis_span.set_attribute("db.system", "redis")
        redis_span.set_attribute("db.operation", "GET")
        redis_span.set_attribute("cache.key", cache_key)

        try:
            cached = await redis_client.get(cache_key)
            if cached:
                item_data = json.loads(cached)
                cache_hit = True
                redis_span.set_attribute("cache.hit", True)
                CACHE_HITS.labels(service=SERVICE_NAME, operation="get").inc()
                logger.info(f"Cache HIT key={cache_key}")
            else:
                redis_span.set_attribute("cache.hit", False)
                CACHE_MISSES.labels(service=SERVICE_NAME, operation="get").inc()
                logger.info(f"Cache MISS key={cache_key}")
        except Exception as e:
            # Redis error - fall through to Postgres
            redis_span.set_attribute("error", True)
            redis_span.set_attribute("error.message", str(e))
            CACHE_MISSES.labels(service=SERVICE_NAME, operation="get").inc()
            logger.warning(f"Redis GET failed: {e}")

    # Step 2: If cache miss, read from Postgres
    if not item_data:
        with tracer.start_as_current_span("postgres_select") as pg_span:
            pg_span.set_attribute("db.system", "postgresql")
            pg_span.set_attribute("db.operation", "SELECT")
            pg_span.set_attribute("item.id", item_id)

            try:
                async with pg_pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT * FROM items WHERE id = $1",
                        item_id
                    )

                if not row:
                    pg_span.set_attribute("db.rows_affected", 0)
                    REQUEST_COUNT.labels(service=SERVICE_NAME, method="GET", endpoint="/items/{id}", status="404").inc()
                    raise HTTPException(status_code=404, detail="Item not found")

                item_data = serialize_item(row)
                pg_span.set_attribute("db.rows_affected", 1)
                DB_OPERATIONS.labels(service=SERVICE_NAME, operation="select", status="success").inc()
                logger.info(f"Postgres SELECT completed item_id={item_id}")

                # Populate cache for next time
                try:
                    await redis_client.setex(
                        cache_key,
                        CACHE_TTL_SECONDS,
                        json.dumps(item_data)
                    )
                    logger.info(f"Cache populated key={cache_key}")
                except Exception as e:
                    logger.warning(f"Failed to populate cache: {e}")

            except HTTPException:
                raise
            except Exception as e:
                DB_OPERATIONS.labels(service=SERVICE_NAME, operation="select", status="error").inc()
                pg_span.set_attribute("error", True)
                logger.error(f"Postgres SELECT failed: {e}")
                raise HTTPException(status_code=500, detail="Database error")

    total_duration = time.time() - start_time
    REQUEST_LATENCY.labels(service=SERVICE_NAME, method="GET", endpoint="/items/{id}").observe(total_duration)
    REQUEST_COUNT.labels(service=SERVICE_NAME, method="GET", endpoint="/items/{id}", status="200").inc()

    logger.info(f"GET completed item_id={item_id} trace_id={trace_id} cache_hit={cache_hit} duration={total_duration*1000:.2f}ms")

    return ItemResponse(**item_data)


@app.put("/items/{item_id}", response_model=ItemResponse)
async def update_item(item_id: str, item: Item):
    """
    Update an item using WRITE-THROUGH pattern.

    Like create, the update goes to BOTH Redis AND Postgres:
    1. Update in Postgres (source of truth)
    2. Update in Redis (cache)

    This ensures the cache is always consistent with the database.
    """
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")
    start_time = time.time()

    now = datetime.utcnow()
    cache_key = get_cache_key(item_id)

    # Step 1: Update in Postgres
    with tracer.start_as_current_span("postgres_update") as pg_span:
        pg_start = time.time()
        pg_span.set_attribute("db.system", "postgresql")
        pg_span.set_attribute("db.operation", "UPDATE")
        pg_span.set_attribute("item.id", item_id)

        try:
            async with pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE items
                    SET name = $2, description = $3, price = $4, updated_at = $5
                    WHERE id = $1
                    RETURNING *
                    """,
                    item_id, item.name, item.description, item.price, now
                )

            if not row:
                pg_span.set_attribute("db.rows_affected", 0)
                REQUEST_COUNT.labels(service=SERVICE_NAME, method="PUT", endpoint="/items/{id}", status="404").inc()
                raise HTTPException(status_code=404, detail="Item not found")

            item_data = serialize_item(row)
            pg_duration = time.time() - pg_start
            pg_span.set_attribute("db.rows_affected", 1)
            pg_span.set_attribute("db.duration_ms", round(pg_duration * 1000, 2))
            WRITE_LATENCY.labels(service=SERVICE_NAME, target="postgres").observe(pg_duration)
            DB_OPERATIONS.labels(service=SERVICE_NAME, operation="update", status="success").inc()
            logger.info(f"Postgres UPDATE completed item_id={item_id} duration={pg_duration*1000:.2f}ms")

        except HTTPException:
            raise
        except Exception as e:
            DB_OPERATIONS.labels(service=SERVICE_NAME, operation="update", status="error").inc()
            pg_span.set_attribute("error", True)
            logger.error(f"Postgres UPDATE failed: {e}")
            raise HTTPException(status_code=500, detail="Database error")

    # Step 2: Update in Redis
    with tracer.start_as_current_span("redis_set") as redis_span:
        redis_start = time.time()
        redis_span.set_attribute("db.system", "redis")
        redis_span.set_attribute("db.operation", "SET")
        redis_span.set_attribute("cache.key", cache_key)

        try:
            await redis_client.setex(
                cache_key,
                CACHE_TTL_SECONDS,
                json.dumps(item_data)
            )
            redis_duration = time.time() - redis_start
            redis_span.set_attribute("cache.duration_ms", round(redis_duration * 1000, 2))
            WRITE_LATENCY.labels(service=SERVICE_NAME, target="redis").observe(redis_duration)
            logger.info(f"Redis SET completed key={cache_key} duration={redis_duration*1000:.2f}ms")
        except Exception as e:
            redis_span.set_attribute("error", True)
            logger.warning(f"Redis SET failed (non-fatal): {e}")

    total_duration = time.time() - start_time
    WRITE_LATENCY.labels(service=SERVICE_NAME, target="total").observe(total_duration)
    REQUEST_LATENCY.labels(service=SERVICE_NAME, method="PUT", endpoint="/items/{id}").observe(total_duration)
    REQUEST_COUNT.labels(service=SERVICE_NAME, method="PUT", endpoint="/items/{id}", status="200").inc()

    logger.info(f"UPDATE completed item_id={item_id} trace_id={trace_id} total_duration={total_duration*1000:.2f}ms")

    return ItemResponse(**item_data)


@app.delete("/items/{item_id}")
async def delete_item(item_id: str):
    """
    Delete an item - removes from both cache and database.
    """
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")
    start_time = time.time()

    cache_key = get_cache_key(item_id)

    # Step 1: Delete from Postgres
    with tracer.start_as_current_span("postgres_delete") as pg_span:
        pg_span.set_attribute("db.system", "postgresql")
        pg_span.set_attribute("db.operation", "DELETE")
        pg_span.set_attribute("item.id", item_id)

        try:
            async with pg_pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM items WHERE id = $1",
                    item_id
                )

            # Check if row was deleted
            rows_deleted = int(result.split()[-1])
            if rows_deleted == 0:
                pg_span.set_attribute("db.rows_affected", 0)
                REQUEST_COUNT.labels(service=SERVICE_NAME, method="DELETE", endpoint="/items/{id}", status="404").inc()
                raise HTTPException(status_code=404, detail="Item not found")

            pg_span.set_attribute("db.rows_affected", rows_deleted)
            DB_OPERATIONS.labels(service=SERVICE_NAME, operation="delete", status="success").inc()
            logger.info(f"Postgres DELETE completed item_id={item_id}")

        except HTTPException:
            raise
        except Exception as e:
            DB_OPERATIONS.labels(service=SERVICE_NAME, operation="delete", status="error").inc()
            pg_span.set_attribute("error", True)
            logger.error(f"Postgres DELETE failed: {e}")
            raise HTTPException(status_code=500, detail="Database error")

    # Step 2: Delete from Redis
    with tracer.start_as_current_span("redis_del") as redis_span:
        redis_span.set_attribute("db.system", "redis")
        redis_span.set_attribute("db.operation", "DEL")
        redis_span.set_attribute("cache.key", cache_key)

        try:
            await redis_client.delete(cache_key)
            logger.info(f"Redis DEL completed key={cache_key}")
        except Exception as e:
            redis_span.set_attribute("error", True)
            logger.warning(f"Redis DEL failed (non-fatal): {e}")

    total_duration = time.time() - start_time
    REQUEST_LATENCY.labels(service=SERVICE_NAME, method="DELETE", endpoint="/items/{id}").observe(total_duration)
    REQUEST_COUNT.labels(service=SERVICE_NAME, method="DELETE", endpoint="/items/{id}", status="200").inc()

    logger.info(f"DELETE completed item_id={item_id} trace_id={trace_id} duration={total_duration*1000:.2f}ms")

    return {"status": "deleted", "id": item_id}


@app.get("/items")
async def list_items(limit: int = 10):
    """
    List all items from the database.
    Note: This doesn't use the cache - it always reads from Postgres.
    For a production system, you'd want a more sophisticated caching strategy for lists.
    """
    current_span = trace.get_current_span()
    start_time = time.time()

    with tracer.start_as_current_span("postgres_select_all") as pg_span:
        pg_span.set_attribute("db.system", "postgresql")
        pg_span.set_attribute("db.operation", "SELECT")
        pg_span.set_attribute("db.limit", limit)

        try:
            async with pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM items ORDER BY created_at DESC LIMIT $1",
                    limit
                )

            items = [serialize_item(row) for row in rows]
            pg_span.set_attribute("db.rows_affected", len(items))
            DB_OPERATIONS.labels(service=SERVICE_NAME, operation="select_all", status="success").inc()

        except Exception as e:
            DB_OPERATIONS.labels(service=SERVICE_NAME, operation="select_all", status="error").inc()
            pg_span.set_attribute("error", True)
            logger.error(f"Postgres SELECT ALL failed: {e}")
            raise HTTPException(status_code=500, detail="Database error")

    total_duration = time.time() - start_time
    REQUEST_LATENCY.labels(service=SERVICE_NAME, method="GET", endpoint="/items").observe(total_duration)
    REQUEST_COUNT.labels(service=SERVICE_NAME, method="GET", endpoint="/items", status="200").inc()

    return {"items": items, "count": len(items)}


@app.get("/cache/stats")
async def cache_stats():
    """Get Redis cache statistics."""
    try:
        info = await redis_client.info("stats")
        keyspace = await redis_client.info("keyspace")

        # Count item keys
        keys = await redis_client.keys("item:*")

        return {
            "cached_items": len(keys),
            "total_hits": info.get("keyspace_hits", 0),
            "total_misses": info.get("keyspace_misses", 0),
            "hit_rate": round(
                info.get("keyspace_hits", 0) /
                max(info.get("keyspace_hits", 0) + info.get("keyspace_misses", 0), 1) * 100,
                2
            )
        }
    except Exception as e:
        logger.error(f"Failed to get cache stats: {e}")
        return {"error": str(e)}


@app.post("/cache/clear")
async def clear_cache():
    """Clear all item entries from the cache."""
    try:
        keys = await redis_client.keys("item:*")
        if keys:
            await redis_client.delete(*keys)
        logger.info(f"Cleared {len(keys)} items from cache")
        return {"status": "cleared", "keys_deleted": len(keys)}
    except Exception as e:
        logger.error(f"Failed to clear cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
