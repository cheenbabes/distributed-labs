"""
Application Service - Handles data requests with Redis caching and Postgres backend.
Demonstrates the dramatic impact of cache failure on database load.
"""
import asyncio
import json
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "app")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://app:app@postgres:5432/app")
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
CACHE_HITS = Counter("app_cache_hits_total", "Cache hits")
CACHE_MISSES = Counter("app_cache_misses_total", "Cache misses")
CACHE_ERRORS = Counter("app_cache_errors_total", "Cache errors (connection failures)")
DB_QUERIES = Counter("app_db_queries_total", "Database queries executed")
DB_QUERY_LATENCY = Histogram(
    "app_db_query_duration_seconds",
    "Database query latency",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
CACHE_LATENCY = Histogram(
    "app_cache_duration_seconds",
    "Cache operation latency",
    buckets=[0.001, 0.002, 0.005, 0.01, 0.025, 0.05, 0.1]
)
REQUEST_LATENCY = Histogram(
    "app_request_duration_seconds",
    "Total request latency",
    ["cache_status"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
CACHE_HIT_RATE = Gauge("app_cache_hit_rate", "Current cache hit rate")

# Global connections
redis_client: Optional[redis.Redis] = None
db_pool: Optional[asyncpg.Pool] = None

# Statistics tracking
stats = {
    "total_requests": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "cache_errors": 0,
    "db_queries": 0,
}


async def get_redis() -> Optional[redis.Redis]:
    """Get Redis connection with error handling."""
    global redis_client
    try:
        if redis_client is None:
            redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        # Test connection
        await redis_client.ping()
        return redis_client
    except Exception as e:
        logger.warning(f"Redis unavailable: {e}")
        redis_client = None
        return None


async def get_db_pool() -> asyncpg.Pool:
    """Get database connection pool."""
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=5, max_size=20)
    return db_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Redis URL: {REDIS_URL}")
    logger.info(f"Database URL: {DATABASE_URL}")

    # Initialize connections
    global redis_client, db_pool
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        await redis_client.ping()
        logger.info("Redis connected")
    except Exception as e:
        logger.warning(f"Redis connection failed on startup: {e}")
        redis_client = None

    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=5, max_size=20)
    logger.info("Database pool created")

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
    redis_status = "unknown"
    db_status = "unknown"

    try:
        r = await get_redis()
        redis_status = "ok" if r else "unavailable"
    except:
        redis_status = "error"

    try:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_status = "ok"
    except:
        db_status = "error"

    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "service": SERVICE_NAME,
        "redis": redis_status,
        "database": db_status
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/data")
async def get_data():
    """
    Main data endpoint with caching.
    - First checks Redis cache
    - On miss, queries Postgres and caches result
    """
    start_time = time.time()
    current_span = trace.get_current_span()

    stats["total_requests"] += 1
    cache_hit = False
    data = None

    # Generate a cache key - in real app this would be based on request params
    # We use a small set of keys to demonstrate high cache hit rate
    item_id = random.randint(1, 10)
    cache_key = f"data:{item_id}"

    # Try to get from cache
    with tracer.start_as_current_span("cache_lookup") as cache_span:
        cache_span.set_attribute("cache.key", cache_key)
        cache_start = time.time()

        try:
            r = await get_redis()
            if r:
                cached = await r.get(cache_key)
                cache_latency = time.time() - cache_start
                CACHE_LATENCY.observe(cache_latency)

                if cached:
                    data = json.loads(cached)
                    cache_hit = True
                    stats["cache_hits"] += 1
                    CACHE_HITS.inc()
                    cache_span.set_attribute("cache.hit", True)
                    logger.debug(f"Cache HIT for {cache_key}")
                else:
                    stats["cache_misses"] += 1
                    CACHE_MISSES.inc()
                    cache_span.set_attribute("cache.hit", False)
                    logger.debug(f"Cache MISS for {cache_key}")
            else:
                # Redis unavailable
                stats["cache_errors"] += 1
                CACHE_ERRORS.inc()
                cache_span.set_attribute("cache.error", True)
                logger.warning("Redis unavailable, falling back to database")
        except Exception as e:
            stats["cache_errors"] += 1
            CACHE_ERRORS.inc()
            cache_span.set_attribute("cache.error", True)
            cache_span.record_exception(e)
            logger.warning(f"Cache error: {e}")

    # If not in cache, query database
    if not data:
        with tracer.start_as_current_span("database_query") as db_span:
            db_span.set_attribute("db.item_id", item_id)
            db_start = time.time()

            try:
                pool = await get_db_pool()
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT * FROM items WHERE id = $1",
                        item_id
                    )

                    if row:
                        data = {
                            "id": row["id"],
                            "name": row["name"],
                            "description": row["description"],
                            "value": float(row["value"]),
                            "created_at": row["created_at"].isoformat()
                        }
                    else:
                        # Generate fallback data if not found
                        data = {
                            "id": item_id,
                            "name": f"Item {item_id}",
                            "description": f"Description for item {item_id}",
                            "value": random.uniform(10, 1000),
                            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")
                        }

                db_latency = time.time() - db_start
                DB_QUERY_LATENCY.observe(db_latency)
                stats["db_queries"] += 1
                DB_QUERIES.inc()
                db_span.set_attribute("db.latency_ms", db_latency * 1000)
                logger.debug(f"Database query took {db_latency*1000:.2f}ms")

            except Exception as e:
                db_span.record_exception(e)
                logger.error(f"Database error: {e}")
                raise HTTPException(status_code=500, detail="Database error")

        # Store in cache for next time
        with tracer.start_as_current_span("cache_store") as store_span:
            try:
                r = await get_redis()
                if r:
                    await r.setex(cache_key, CACHE_TTL_SECONDS, json.dumps(data))
                    store_span.set_attribute("cache.stored", True)
                    logger.debug(f"Stored {cache_key} in cache")
            except Exception as e:
                store_span.set_attribute("cache.store_error", True)
                logger.warning(f"Failed to store in cache: {e}")

    duration = time.time() - start_time
    cache_status = "hit" if cache_hit else "miss"
    REQUEST_LATENCY.labels(cache_status=cache_status).observe(duration)

    # Update hit rate gauge
    if stats["total_requests"] > 0:
        hit_rate = stats["cache_hits"] / stats["total_requests"]
        CACHE_HIT_RATE.set(hit_rate)

    current_span.set_attribute("cache.hit", cache_hit)
    current_span.set_attribute("latency_ms", duration * 1000)

    return {
        "cache_hit": cache_hit,
        "latency_ms": round(duration * 1000, 2),
        "data": data
    }


@app.get("/stats")
async def get_stats():
    """Return cache and database statistics."""
    hit_rate = 0
    if stats["total_requests"] > 0:
        hit_rate = round(stats["cache_hits"] / stats["total_requests"] * 100, 2)

    # Check Redis status
    redis_status = "unknown"
    try:
        r = await get_redis()
        if r:
            info = await r.info("stats")
            redis_status = "connected"
        else:
            redis_status = "disconnected"
    except:
        redis_status = "error"

    return {
        "service": SERVICE_NAME,
        "total_requests": stats["total_requests"],
        "cache_hits": stats["cache_hits"],
        "cache_misses": stats["cache_misses"],
        "cache_errors": stats["cache_errors"],
        "db_queries": stats["db_queries"],
        "cache_hit_rate_percent": hit_rate,
        "redis_status": redis_status
    }


@app.post("/admin/clear-cache")
async def clear_cache():
    """Clear all cached data."""
    try:
        r = await get_redis()
        if r:
            await r.flushdb()
            # Reset stats
            stats["cache_hits"] = 0
            stats["cache_misses"] = 0
            stats["cache_errors"] = 0
            stats["total_requests"] = 0
            stats["db_queries"] = 0
            return {"status": "ok", "message": "Cache cleared"}
        else:
            return {"status": "error", "message": "Redis unavailable"}
    except Exception as e:
        logger.error(f"Error clearing cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/fill-cache")
async def fill_cache(count: int = 1000):
    """Fill cache with junk data to simulate memory pressure."""
    try:
        r = await get_redis()
        if r:
            for i in range(count):
                key = f"junk:{i}"
                value = "x" * 1000  # 1KB of junk
                await r.setex(key, 300, value)
            return {"status": "ok", "message": f"Added {count} junk entries"}
        else:
            return {"status": "error", "message": "Redis unavailable"}
    except Exception as e:
        logger.error(f"Error filling cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
