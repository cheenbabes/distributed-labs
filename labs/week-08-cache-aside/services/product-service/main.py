"""
Product Service with Cache-Aside (Lazy Loading) Pattern

This service demonstrates the cache-aside caching pattern:
- READ: Check cache first, on miss read from DB and populate cache
- WRITE: Write to DB only, then invalidate cache (or let it expire via TTL)

Key difference from write-through:
- Write-through: Every write updates both cache AND database
- Cache-aside: Application manages cache separately, writes only go to DB

Trade-offs:
- Cache-aside has lower write latency (only one write to DB)
- Cache-aside may serve stale data until TTL expires or explicit invalidation
- Cache-aside is simpler for complex caching scenarios
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "product-service")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://labuser:labpass@localhost:5432/products_db")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "60"))

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

# Cache-specific metrics
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
CACHE_LATENCY = Histogram(
    "cache_latency_seconds",
    "Cache operation latency",
    ["service", "operation"],
    buckets=[0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1]
)
CACHE_INVALIDATIONS = Counter(
    "cache_invalidations_total",
    "Total cache invalidations",
    ["service", "reason"]
)
CACHE_SIZE = Gauge(
    "cache_size_items",
    "Number of items currently in cache",
    ["service"]
)

DB_OPERATIONS = Counter(
    "db_operations_total",
    "Database operations",
    ["service", "operation", "status"]
)
DB_LATENCY = Histogram(
    "db_latency_seconds",
    "Database operation latency",
    ["service", "operation"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5]
)

# Global connections
redis_client: Optional[redis.Redis] = None
pg_pool: Optional[asyncpg.Pool] = None

# In-memory stats for demonstration
stats = {
    "total_reads": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "writes": 0,
    "invalidations": 0,
    "stale_reads_simulated": 0
}


class Product(BaseModel):
    name: str
    description: Optional[str] = None
    price: float


class ProductResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    price: float
    created_at: str
    updated_at: str
    cache_status: Optional[str] = None  # "hit", "miss", or None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, pg_pool

    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Redis URL: {REDIS_URL}")
    logger.info(f"Postgres URL: {POSTGRES_URL.replace('labpass', '***')}")
    logger.info(f"Cache TTL: {CACHE_TTL_SECONDS} seconds")

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


def get_cache_key(product_id: str) -> str:
    """Generate cache key for a product."""
    return f"product:{product_id}"


def serialize_product(row: asyncpg.Record) -> dict:
    """Convert database row to dictionary."""
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "price": float(row["price"]),
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat()
    }


async def update_cache_size_metric():
    """Update the cache size gauge."""
    try:
        keys = await redis_client.keys("product:*")
        CACHE_SIZE.labels(service=SERVICE_NAME).set(len(keys))
    except Exception:
        pass


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
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "dependencies": {
            "redis": "ok" if redis_ok else "down",
            "postgres": "ok" if postgres_ok else "down"
        }
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    await update_cache_size_metric()
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/products/{product_id}", response_model=ProductResponse)
async def get_product(product_id: str):
    """
    Get a product using CACHE-ASIDE pattern.

    Read pattern:
    1. Check Redis cache
    2. If HIT: return cached data immediately
    3. If MISS: read from Postgres, populate cache, return

    This is "lazy loading" - we only cache on read, not on write.
    """
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")
    start_time = time.time()

    cache_key = get_cache_key(product_id)
    product_data = None
    cache_status = None

    stats["total_reads"] += 1

    # Step 1: Check Redis cache (the "aside" lookup)
    with tracer.start_as_current_span("cache_lookup") as cache_span:
        cache_start = time.time()
        cache_span.set_attribute("db.system", "redis")
        cache_span.set_attribute("db.operation", "GET")
        cache_span.set_attribute("cache.key", cache_key)

        try:
            cached = await redis_client.get(cache_key)
            cache_duration = time.time() - cache_start
            CACHE_LATENCY.labels(service=SERVICE_NAME, operation="get").observe(cache_duration)

            if cached:
                product_data = json.loads(cached)
                cache_status = "hit"
                cache_span.set_attribute("cache.hit", True)
                cache_span.set_attribute("cache.duration_ms", round(cache_duration * 1000, 2))
                CACHE_HITS.labels(service=SERVICE_NAME, operation="get").inc()
                stats["cache_hits"] += 1
                logger.info(f"Cache HIT key={cache_key} duration={cache_duration*1000:.2f}ms")
            else:
                cache_status = "miss"
                cache_span.set_attribute("cache.hit", False)
                CACHE_MISSES.labels(service=SERVICE_NAME, operation="get").inc()
                stats["cache_misses"] += 1
                logger.info(f"Cache MISS key={cache_key}")
        except Exception as e:
            cache_span.set_attribute("error", True)
            cache_span.set_attribute("error.message", str(e))
            CACHE_MISSES.labels(service=SERVICE_NAME, operation="get").inc()
            stats["cache_misses"] += 1
            logger.warning(f"Redis GET failed: {e}")
            cache_status = "error"

    # Step 2: If cache miss, read from Postgres and populate cache
    if not product_data:
        with tracer.start_as_current_span("db_read") as db_span:
            db_start = time.time()
            db_span.set_attribute("db.system", "postgresql")
            db_span.set_attribute("db.operation", "SELECT")
            db_span.set_attribute("product.id", product_id)

            try:
                async with pg_pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT * FROM products WHERE id = $1",
                        product_id
                    )

                db_duration = time.time() - db_start
                DB_LATENCY.labels(service=SERVICE_NAME, operation="select").observe(db_duration)

                if not row:
                    db_span.set_attribute("db.rows_affected", 0)
                    REQUEST_COUNT.labels(service=SERVICE_NAME, method="GET", endpoint="/products/{id}", status="404").inc()
                    raise HTTPException(status_code=404, detail="Product not found")

                product_data = serialize_product(row)
                db_span.set_attribute("db.rows_affected", 1)
                db_span.set_attribute("db.duration_ms", round(db_duration * 1000, 2))
                DB_OPERATIONS.labels(service=SERVICE_NAME, operation="select", status="success").inc()
                logger.info(f"Postgres SELECT completed product_id={product_id} duration={db_duration*1000:.2f}ms")

            except HTTPException:
                raise
            except Exception as e:
                DB_OPERATIONS.labels(service=SERVICE_NAME, operation="select", status="error").inc()
                db_span.set_attribute("error", True)
                logger.error(f"Postgres SELECT failed: {e}")
                raise HTTPException(status_code=500, detail="Database error")

        # Step 3: Populate cache for next time (lazy loading)
        with tracer.start_as_current_span("cache_populate") as populate_span:
            populate_start = time.time()
            populate_span.set_attribute("db.system", "redis")
            populate_span.set_attribute("db.operation", "SETEX")
            populate_span.set_attribute("cache.key", cache_key)
            populate_span.set_attribute("cache.ttl_seconds", CACHE_TTL_SECONDS)

            try:
                await redis_client.setex(
                    cache_key,
                    CACHE_TTL_SECONDS,
                    json.dumps(product_data)
                )
                populate_duration = time.time() - populate_start
                CACHE_LATENCY.labels(service=SERVICE_NAME, operation="set").observe(populate_duration)
                populate_span.set_attribute("cache.duration_ms", round(populate_duration * 1000, 2))
                logger.info(f"Cache populated key={cache_key} ttl={CACHE_TTL_SECONDS}s")
            except Exception as e:
                populate_span.set_attribute("error", True)
                logger.warning(f"Failed to populate cache: {e}")

    total_duration = time.time() - start_time
    REQUEST_LATENCY.labels(service=SERVICE_NAME, method="GET", endpoint="/products/{id}").observe(total_duration)
    REQUEST_COUNT.labels(service=SERVICE_NAME, method="GET", endpoint="/products/{id}", status="200").inc()

    logger.info(f"GET completed product_id={product_id} trace_id={trace_id} cache={cache_status} duration={total_duration*1000:.2f}ms")

    return ProductResponse(**product_data, cache_status=cache_status)


@app.post("/products", response_model=ProductResponse)
async def create_product(product: Product):
    """
    Create a new product using CACHE-ASIDE pattern.

    Write pattern (differs from write-through):
    1. Write ONLY to Postgres (the source of truth)
    2. Do NOT write to cache - let it be populated on first read

    This is the key difference from write-through:
    - Lower write latency (only one write)
    - Data will be cached lazily on first read
    """
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")
    start_time = time.time()

    product_id = str(uuid.uuid4())
    now = datetime.utcnow()

    stats["writes"] += 1

    # Write to Postgres ONLY (not to cache)
    with tracer.start_as_current_span("db_insert") as db_span:
        db_start = time.time()
        db_span.set_attribute("db.system", "postgresql")
        db_span.set_attribute("db.operation", "INSERT")
        db_span.set_attribute("product.id", product_id)
        db_span.set_attribute("cache.write", False)  # Cache-aside: no cache write

        try:
            async with pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO products (id, name, description, price, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    product_id, product.name, product.description, product.price, now, now
                )

            db_duration = time.time() - db_start
            DB_LATENCY.labels(service=SERVICE_NAME, operation="insert").observe(db_duration)
            db_span.set_attribute("db.duration_ms", round(db_duration * 1000, 2))
            DB_OPERATIONS.labels(service=SERVICE_NAME, operation="insert", status="success").inc()
            logger.info(f"Postgres INSERT completed product_id={product_id} duration={db_duration*1000:.2f}ms")

        except Exception as e:
            DB_OPERATIONS.labels(service=SERVICE_NAME, operation="insert", status="error").inc()
            db_span.set_attribute("error", True)
            logger.error(f"Postgres INSERT failed: {e}")
            raise HTTPException(status_code=500, detail="Database error")

    product_data = {
        "id": product_id,
        "name": product.name,
        "description": product.description,
        "price": product.price,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat()
    }

    total_duration = time.time() - start_time
    REQUEST_LATENCY.labels(service=SERVICE_NAME, method="POST", endpoint="/products").observe(total_duration)
    REQUEST_COUNT.labels(service=SERVICE_NAME, method="POST", endpoint="/products", status="201").inc()

    logger.info(f"CREATE completed product_id={product_id} trace_id={trace_id} duration={total_duration*1000:.2f}ms (cache-aside: no cache write)")

    return ProductResponse(**product_data, cache_status=None)


@app.put("/products/{product_id}", response_model=ProductResponse)
async def update_product(product_id: str, product: Product):
    """
    Update a product using CACHE-ASIDE pattern.

    Write pattern:
    1. Update in Postgres (source of truth)
    2. INVALIDATE cache (delete the key) - let it be repopulated on next read

    This allows stale data to be served until the cache entry expires or is invalidated.
    The explicit invalidation here prevents stale reads after update.
    """
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")
    start_time = time.time()

    cache_key = get_cache_key(product_id)
    now = datetime.utcnow()

    stats["writes"] += 1

    # Step 1: Update in Postgres
    with tracer.start_as_current_span("db_update") as db_span:
        db_start = time.time()
        db_span.set_attribute("db.system", "postgresql")
        db_span.set_attribute("db.operation", "UPDATE")
        db_span.set_attribute("product.id", product_id)

        try:
            async with pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE products
                    SET name = $2, description = $3, price = $4, updated_at = $5
                    WHERE id = $1
                    RETURNING *
                    """,
                    product_id, product.name, product.description, product.price, now
                )

            if not row:
                db_span.set_attribute("db.rows_affected", 0)
                REQUEST_COUNT.labels(service=SERVICE_NAME, method="PUT", endpoint="/products/{id}", status="404").inc()
                raise HTTPException(status_code=404, detail="Product not found")

            product_data = serialize_product(row)
            db_duration = time.time() - db_start
            DB_LATENCY.labels(service=SERVICE_NAME, operation="update").observe(db_duration)
            db_span.set_attribute("db.rows_affected", 1)
            db_span.set_attribute("db.duration_ms", round(db_duration * 1000, 2))
            DB_OPERATIONS.labels(service=SERVICE_NAME, operation="update", status="success").inc()
            logger.info(f"Postgres UPDATE completed product_id={product_id} duration={db_duration*1000:.2f}ms")

        except HTTPException:
            raise
        except Exception as e:
            DB_OPERATIONS.labels(service=SERVICE_NAME, operation="update", status="error").inc()
            db_span.set_attribute("error", True)
            logger.error(f"Postgres UPDATE failed: {e}")
            raise HTTPException(status_code=500, detail="Database error")

    # Step 2: Invalidate cache (delete, don't update)
    with tracer.start_as_current_span("cache_invalidate") as cache_span:
        cache_start = time.time()
        cache_span.set_attribute("db.system", "redis")
        cache_span.set_attribute("db.operation", "DEL")
        cache_span.set_attribute("cache.key", cache_key)
        cache_span.set_attribute("invalidation.reason", "update")

        try:
            deleted = await redis_client.delete(cache_key)
            cache_duration = time.time() - cache_start
            CACHE_LATENCY.labels(service=SERVICE_NAME, operation="delete").observe(cache_duration)
            cache_span.set_attribute("cache.key_existed", deleted > 0)
            cache_span.set_attribute("cache.duration_ms", round(cache_duration * 1000, 2))
            CACHE_INVALIDATIONS.labels(service=SERVICE_NAME, reason="update").inc()
            stats["invalidations"] += 1
            logger.info(f"Cache invalidated key={cache_key} existed={deleted > 0}")
        except Exception as e:
            cache_span.set_attribute("error", True)
            logger.warning(f"Cache invalidation failed: {e}")

    total_duration = time.time() - start_time
    REQUEST_LATENCY.labels(service=SERVICE_NAME, method="PUT", endpoint="/products/{id}").observe(total_duration)
    REQUEST_COUNT.labels(service=SERVICE_NAME, method="PUT", endpoint="/products/{id}", status="200").inc()

    logger.info(f"UPDATE completed product_id={product_id} trace_id={trace_id} duration={total_duration*1000:.2f}ms (cache invalidated)")

    return ProductResponse(**product_data, cache_status="invalidated")


@app.delete("/products/{product_id}")
async def delete_product(product_id: str):
    """
    Delete a product - removes from database and invalidates cache.
    """
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")
    start_time = time.time()

    cache_key = get_cache_key(product_id)

    # Step 1: Delete from Postgres
    with tracer.start_as_current_span("db_delete") as db_span:
        db_span.set_attribute("db.system", "postgresql")
        db_span.set_attribute("db.operation", "DELETE")
        db_span.set_attribute("product.id", product_id)

        try:
            async with pg_pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM products WHERE id = $1",
                    product_id
                )

            rows_deleted = int(result.split()[-1])
            if rows_deleted == 0:
                db_span.set_attribute("db.rows_affected", 0)
                REQUEST_COUNT.labels(service=SERVICE_NAME, method="DELETE", endpoint="/products/{id}", status="404").inc()
                raise HTTPException(status_code=404, detail="Product not found")

            db_span.set_attribute("db.rows_affected", rows_deleted)
            DB_OPERATIONS.labels(service=SERVICE_NAME, operation="delete", status="success").inc()
            logger.info(f"Postgres DELETE completed product_id={product_id}")

        except HTTPException:
            raise
        except Exception as e:
            DB_OPERATIONS.labels(service=SERVICE_NAME, operation="delete", status="error").inc()
            db_span.set_attribute("error", True)
            logger.error(f"Postgres DELETE failed: {e}")
            raise HTTPException(status_code=500, detail="Database error")

    # Step 2: Invalidate cache
    with tracer.start_as_current_span("cache_invalidate") as cache_span:
        cache_span.set_attribute("db.system", "redis")
        cache_span.set_attribute("db.operation", "DEL")
        cache_span.set_attribute("cache.key", cache_key)

        try:
            await redis_client.delete(cache_key)
            CACHE_INVALIDATIONS.labels(service=SERVICE_NAME, reason="delete").inc()
            stats["invalidations"] += 1
            logger.info(f"Cache invalidated key={cache_key}")
        except Exception as e:
            cache_span.set_attribute("error", True)
            logger.warning(f"Cache invalidation failed: {e}")

    total_duration = time.time() - start_time
    REQUEST_LATENCY.labels(service=SERVICE_NAME, method="DELETE", endpoint="/products/{id}").observe(total_duration)
    REQUEST_COUNT.labels(service=SERVICE_NAME, method="DELETE", endpoint="/products/{id}", status="200").inc()

    logger.info(f"DELETE completed product_id={product_id} trace_id={trace_id} duration={total_duration*1000:.2f}ms")

    return {"status": "deleted", "id": product_id}


@app.get("/products")
async def list_products(limit: int = 10):
    """
    List all products from the database.
    Note: List operations typically bypass cache or use a separate caching strategy.
    """
    current_span = trace.get_current_span()
    start_time = time.time()

    with tracer.start_as_current_span("db_select_all") as db_span:
        db_span.set_attribute("db.system", "postgresql")
        db_span.set_attribute("db.operation", "SELECT")
        db_span.set_attribute("db.limit", limit)

        try:
            async with pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM products ORDER BY created_at DESC LIMIT $1",
                    limit
                )

            products = [serialize_product(row) for row in rows]
            db_span.set_attribute("db.rows_affected", len(products))
            DB_OPERATIONS.labels(service=SERVICE_NAME, operation="select_all", status="success").inc()

        except Exception as e:
            DB_OPERATIONS.labels(service=SERVICE_NAME, operation="select_all", status="error").inc()
            db_span.set_attribute("error", True)
            logger.error(f"Postgres SELECT ALL failed: {e}")
            raise HTTPException(status_code=500, detail="Database error")

    total_duration = time.time() - start_time
    REQUEST_LATENCY.labels(service=SERVICE_NAME, method="GET", endpoint="/products").observe(total_duration)
    REQUEST_COUNT.labels(service=SERVICE_NAME, method="GET", endpoint="/products", status="200").inc()

    return {"products": products, "count": len(products)}


# ===========================================
# Admin Endpoints for Cache Management
# ===========================================

@app.post("/admin/invalidate/{product_id}")
async def admin_invalidate(product_id: str):
    """
    Manually invalidate a cache entry.
    Useful for demonstrating explicit cache invalidation.
    """
    cache_key = get_cache_key(product_id)

    try:
        deleted = await redis_client.delete(cache_key)
        CACHE_INVALIDATIONS.labels(service=SERVICE_NAME, reason="manual").inc()
        stats["invalidations"] += 1

        return {
            "status": "invalidated",
            "key": cache_key,
            "key_existed": deleted > 0
        }
    except Exception as e:
        logger.error(f"Manual invalidation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/cache-stats")
async def admin_cache_stats():
    """
    Get detailed cache statistics.
    Shows hit rate, miss rate, and invalidation counts.
    """
    try:
        info = await redis_client.info("stats")
        keys = await redis_client.keys("product:*")

        # Get TTL for each key
        key_ttls = {}
        for key in keys[:10]:  # Limit to first 10 for performance
            ttl = await redis_client.ttl(key)
            key_ttls[key] = ttl

        total_ops = stats["cache_hits"] + stats["cache_misses"]
        hit_rate = (stats["cache_hits"] / total_ops * 100) if total_ops > 0 else 0

        return {
            "service_stats": {
                "total_reads": stats["total_reads"],
                "cache_hits": stats["cache_hits"],
                "cache_misses": stats["cache_misses"],
                "hit_rate_percent": round(hit_rate, 2),
                "writes": stats["writes"],
                "invalidations": stats["invalidations"]
            },
            "redis_stats": {
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0),
            },
            "cache_state": {
                "cached_items": len(keys),
                "cache_ttl_seconds": CACHE_TTL_SECONDS,
                "sample_keys_with_ttl": key_ttls
            }
        }
    except Exception as e:
        logger.error(f"Failed to get cache stats: {e}")
        return {"error": str(e)}


@app.post("/admin/cache/clear")
async def admin_clear_cache():
    """Clear all product entries from the cache."""
    try:
        keys = await redis_client.keys("product:*")
        if keys:
            await redis_client.delete(*keys)
        CACHE_INVALIDATIONS.labels(service=SERVICE_NAME, reason="clear_all").inc()
        stats["invalidations"] += len(keys)
        logger.info(f"Cleared {len(keys)} items from cache")
        return {"status": "cleared", "keys_deleted": len(keys)}
    except Exception as e:
        logger.error(f"Failed to clear cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/cache/key/{product_id}")
async def admin_get_cache_key(product_id: str):
    """
    Inspect a specific cache key.
    Useful for debugging cache state.
    """
    cache_key = get_cache_key(product_id)

    try:
        cached = await redis_client.get(cache_key)
        ttl = await redis_client.ttl(cache_key)

        if cached:
            return {
                "key": cache_key,
                "exists": True,
                "ttl_seconds": ttl,
                "value": json.loads(cached)
            }
        else:
            return {
                "key": cache_key,
                "exists": False,
                "ttl_seconds": -2  # Key doesn't exist
            }
    except Exception as e:
        logger.error(f"Failed to inspect cache key: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/simulate-stale-read/{product_id}")
async def admin_simulate_stale_read(product_id: str, new_price: float):
    """
    Simulate a stale read scenario.

    This demonstrates the cache-aside trade-off:
    1. We update the database directly (bypassing the service)
    2. The cache still has the old value
    3. Reads will return stale data until TTL expires or explicit invalidation

    This helps understand why cache-aside can serve stale data.
    """
    cache_key = get_cache_key(product_id)

    # Check if item is cached
    cached = await redis_client.get(cache_key)
    if not cached:
        raise HTTPException(
            status_code=400,
            detail="Product not in cache. Read it first to populate cache."
        )

    old_data = json.loads(cached)
    old_price = old_data["price"]

    # Update database directly (simulating external update)
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "UPDATE products SET price = $2, updated_at = $3 WHERE id = $1",
            product_id, new_price, datetime.utcnow()
        )

    # Get TTL of cached entry
    ttl = await redis_client.ttl(cache_key)

    stats["stale_reads_simulated"] += 1

    return {
        "status": "stale_scenario_created",
        "product_id": product_id,
        "database_price": new_price,
        "cached_price": old_price,
        "cache_ttl_remaining": ttl,
        "message": f"Database updated to {new_price}, but cache still shows {old_price}. "
                   f"Cache will expire in {ttl} seconds, or use /admin/invalidate/{product_id} to force refresh."
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
