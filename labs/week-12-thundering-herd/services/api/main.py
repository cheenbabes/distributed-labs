"""
Thundering Herd / Cache Stampede Demo API

This service demonstrates the thundering herd problem (aka cache stampede):
When a cached value expires, all concurrent requests try to regenerate it
simultaneously, overwhelming the database.

Protection mechanisms implemented:
1. Lock-based refresh: Only one request regenerates the cache
2. Probabilistic early refresh: Randomly refresh before expiry
"""
import asyncio
import logging
import math
import os
import random
import time
from contextlib import asynccontextmanager
from typing import Optional

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from pydantic import BaseModel

# =============================================================================
# Configuration
# =============================================================================

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "lab12-api")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab12-otel-collector:4317")
REDIS_URL = os.getenv("REDIS_URL", "redis://lab12-redis:6379")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://lab:lab@lab12-postgres:5432/lab")

# Cache configuration
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "5"))

# Database simulation
DB_QUERY_MIN_MS = int(os.getenv("DB_QUERY_MIN_MS", "500"))
DB_QUERY_MAX_MS = int(os.getenv("DB_QUERY_MAX_MS", "1000"))

# Protection mechanisms
ENABLE_LOCK_PROTECTION = os.getenv("ENABLE_LOCK_PROTECTION", "false").lower() == "true"
ENABLE_PROBABILISTIC_REFRESH = os.getenv("ENABLE_PROBABILISTIC_REFRESH", "false").lower() == "true"
# Factor for probabilistic refresh (higher = more likely to refresh early)
PROBABILISTIC_REFRESH_FACTOR = float(os.getenv("PROBABILISTIC_REFRESH_FACTOR", "0.1"))

# =============================================================================
# Logging
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(SERVICE_NAME)

# =============================================================================
# OpenTelemetry Setup
# =============================================================================

resource = Resource.create({"service.name": SERVICE_NAME})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# =============================================================================
# Prometheus Metrics
# =============================================================================

# Request metrics
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["service", "method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

# Cache metrics
CACHE_HITS = Counter(
    "cache_hits_total",
    "Total cache hits",
    ["service", "key_pattern"]
)
CACHE_MISSES = Counter(
    "cache_misses_total",
    "Total cache misses",
    ["service", "key_pattern"]
)

# Database metrics
DB_QUERIES = Counter(
    "db_queries_total",
    "Total database queries executed",
    ["service", "query_type"]
)
DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds",
    "Database query duration",
    ["service", "query_type"],
    buckets=[0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0]
)

# Thundering herd specific metrics
CONCURRENT_DB_QUERIES = Gauge(
    "concurrent_db_queries",
    "Number of concurrent database queries (shows stampede)",
    ["service"]
)
STAMPEDE_EVENTS = Counter(
    "stampede_events_total",
    "Number of cache stampede events detected",
    ["service"]
)
LOCK_ACQUISITIONS = Counter(
    "lock_acquisitions_total",
    "Number of lock acquisitions for cache refresh",
    ["service", "result"]  # result: acquired, waited, timeout
)
EARLY_REFRESH_TRIGGERS = Counter(
    "early_refresh_triggers_total",
    "Number of probabilistic early refresh triggers",
    ["service"]
)

# =============================================================================
# Global State
# =============================================================================

redis_client: Optional[redis.Redis] = None
concurrent_queries = 0
concurrent_queries_lock = asyncio.Lock()

# Runtime configuration (can be modified via admin API)
runtime_config = {
    "enable_lock_protection": ENABLE_LOCK_PROTECTION,
    "enable_probabilistic_refresh": ENABLE_PROBABILISTIC_REFRESH,
    "probabilistic_refresh_factor": PROBABILISTIC_REFRESH_FACTOR,
    "cache_ttl_seconds": CACHE_TTL_SECONDS,
    "db_query_min_ms": DB_QUERY_MIN_MS,
    "db_query_max_ms": DB_QUERY_MAX_MS,
}

# =============================================================================
# Application Lifecycle
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client

    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Cache TTL: {CACHE_TTL_SECONDS}s")
    logger.info(f"DB Query Time: {DB_QUERY_MIN_MS}-{DB_QUERY_MAX_MS}ms")
    logger.info(f"Lock Protection: {ENABLE_LOCK_PROTECTION}")
    logger.info(f"Probabilistic Refresh: {ENABLE_PROBABILISTIC_REFRESH}")

    # Initialize Redis connection
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

    try:
        await redis_client.ping()
        logger.info("Redis connection established")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        raise

    yield

    # Cleanup
    if redis_client:
        await redis_client.close()
    logger.info(f"{SERVICE_NAME} shutting down")

app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)

# =============================================================================
# Pydantic Models
# =============================================================================

class ProtectionConfig(BaseModel):
    enable_lock_protection: Optional[bool] = None
    enable_probabilistic_refresh: Optional[bool] = None
    probabilistic_refresh_factor: Optional[float] = None

class CacheConfig(BaseModel):
    cache_ttl_seconds: Optional[int] = None
    db_query_min_ms: Optional[int] = None
    db_query_max_ms: Optional[int] = None

# =============================================================================
# Core Functions
# =============================================================================

async def simulate_slow_database_query(product_id: str) -> dict:
    """
    Simulates a slow database query.
    This represents the expensive operation that we want to cache.
    """
    global concurrent_queries

    query_time_ms = random.randint(
        runtime_config["db_query_min_ms"],
        runtime_config["db_query_max_ms"]
    )

    # Track concurrent queries (this shows the stampede!)
    async with concurrent_queries_lock:
        concurrent_queries += 1
        current_concurrent = concurrent_queries
        CONCURRENT_DB_QUERIES.labels(service=SERVICE_NAME).set(concurrent_queries)

        # Detect stampede: more than 3 concurrent queries is suspicious
        if concurrent_queries > 3:
            STAMPEDE_EVENTS.labels(service=SERVICE_NAME).inc()
            logger.warning(f"STAMPEDE DETECTED: {concurrent_queries} concurrent DB queries!")

    with tracer.start_as_current_span("database_query") as span:
        span.set_attribute("product_id", product_id)
        span.set_attribute("query_time_ms", query_time_ms)
        span.set_attribute("concurrent_queries", current_concurrent)

        start_time = time.time()

        # Simulate the slow query
        await asyncio.sleep(query_time_ms / 1000.0)

        duration = time.time() - start_time

        # Record metrics
        DB_QUERIES.labels(service=SERVICE_NAME, query_type="product_lookup").inc()
        DB_QUERY_DURATION.labels(service=SERVICE_NAME, query_type="product_lookup").observe(duration)

        logger.info(f"DB query for {product_id} took {duration*1000:.0f}ms (concurrent: {current_concurrent})")

    # Decrement concurrent counter
    async with concurrent_queries_lock:
        concurrent_queries -= 1
        CONCURRENT_DB_QUERIES.labels(service=SERVICE_NAME).set(concurrent_queries)

    # Return simulated product data
    return {
        "product_id": product_id,
        "name": f"Product {product_id}",
        "price": round(random.uniform(10, 1000), 2),
        "stock": random.randint(0, 100),
        "generated_at": time.time(),
        "query_time_ms": query_time_ms
    }


async def get_cached_value(key: str) -> Optional[tuple[dict, float]]:
    """
    Get value from cache along with its remaining TTL.
    Returns (value, ttl_remaining) or None if not cached.
    """
    import json

    pipeline = redis_client.pipeline()
    pipeline.get(key)
    pipeline.ttl(key)

    results = await pipeline.execute()
    value_str, ttl = results

    if value_str is None:
        return None

    return json.loads(value_str), ttl


async def set_cached_value(key: str, value: dict, ttl_seconds: int):
    """Set value in cache with TTL."""
    import json
    await redis_client.setex(key, ttl_seconds, json.dumps(value))


async def acquire_refresh_lock(key: str, lock_timeout: int = 5) -> bool:
    """
    Try to acquire a lock for refreshing a cache key.
    Returns True if lock acquired, False otherwise.
    """
    lock_key = f"lock:{key}"
    # SET NX with expiration
    acquired = await redis_client.set(lock_key, "1", nx=True, ex=lock_timeout)
    return acquired is not None


async def release_refresh_lock(key: str):
    """Release the refresh lock."""
    lock_key = f"lock:{key}"
    await redis_client.delete(lock_key)


async def wait_for_cache(key: str, max_wait: float = 5.0) -> Optional[dict]:
    """
    Wait for another process to populate the cache.
    Used when we didn't acquire the lock.
    """
    start = time.time()
    while time.time() - start < max_wait:
        result = await get_cached_value(key)
        if result is not None:
            return result[0]
        await asyncio.sleep(0.05)  # Check every 50ms
    return None


def should_refresh_early(ttl_remaining: float, ttl_total: float) -> bool:
    """
    Probabilistic early refresh (XFetch algorithm variant).

    The probability of refresh increases as we get closer to expiry.
    This spreads out the refresh load over time instead of everyone
    hitting the database at exactly the expiry time.

    Formula: probability = factor * log(random()) / remaining_fraction

    Higher factor = more likely to refresh early
    As remaining_fraction approaches 0, probability approaches 1
    """
    if ttl_remaining <= 0:
        return True

    factor = runtime_config["probabilistic_refresh_factor"]
    remaining_fraction = ttl_remaining / ttl_total

    # Avoid division by zero and log of zero
    if remaining_fraction <= 0.01:
        return True

    # XFetch-style probability
    # Using -log(random) which is exponentially distributed
    random_value = random.random()
    if random_value <= 0:
        return True

    threshold = factor * (-math.log(random_value))
    probability = threshold / remaining_fraction

    should_refresh = random.random() < probability

    if should_refresh:
        logger.debug(f"Probabilistic early refresh triggered (ttl_remaining={ttl_remaining:.1f}s)")

    return should_refresh


async def get_product_with_protection(product_id: str) -> dict:
    """
    Get product with thundering herd protection mechanisms.
    """
    cache_key = f"product:{product_id}"
    ttl_total = runtime_config["cache_ttl_seconds"]

    # Try to get from cache
    cached = await get_cached_value(cache_key)

    if cached is not None:
        value, ttl_remaining = cached

        # Check if we should do probabilistic early refresh
        if (runtime_config["enable_probabilistic_refresh"] and
            should_refresh_early(ttl_remaining, ttl_total)):

            EARLY_REFRESH_TRIGGERS.labels(service=SERVICE_NAME).inc()
            logger.info(f"Probabilistic early refresh for {cache_key} (ttl={ttl_remaining:.1f}s)")

            # Try to refresh in background (non-blocking)
            if runtime_config["enable_lock_protection"]:
                if await acquire_refresh_lock(cache_key):
                    LOCK_ACQUISITIONS.labels(service=SERVICE_NAME, result="acquired").inc()
                    try:
                        new_value = await simulate_slow_database_query(product_id)
                        await set_cached_value(cache_key, new_value, ttl_total)
                        return new_value
                    finally:
                        await release_refresh_lock(cache_key)

            # Return cached value while we tried to refresh
            value["cache_status"] = "early_refresh_attempted"
            CACHE_HITS.labels(service=SERVICE_NAME, key_pattern="product").inc()
            return value

        # Cache hit - return cached value
        value["cache_status"] = "hit"
        CACHE_HITS.labels(service=SERVICE_NAME, key_pattern="product").inc()
        return value

    # Cache miss
    CACHE_MISSES.labels(service=SERVICE_NAME, key_pattern="product").inc()

    # Lock-based protection
    if runtime_config["enable_lock_protection"]:
        if await acquire_refresh_lock(cache_key):
            LOCK_ACQUISITIONS.labels(service=SERVICE_NAME, result="acquired").inc()
            try:
                # We got the lock - do the expensive query
                logger.info(f"Lock acquired, refreshing cache for {cache_key}")
                value = await simulate_slow_database_query(product_id)
                await set_cached_value(cache_key, value, ttl_total)
                value["cache_status"] = "miss_with_lock"
                return value
            finally:
                await release_refresh_lock(cache_key)
        else:
            # Someone else has the lock - wait for them to populate cache
            LOCK_ACQUISITIONS.labels(service=SERVICE_NAME, result="waited").inc()
            logger.info(f"Lock not acquired, waiting for cache to be populated for {cache_key}")

            value = await wait_for_cache(cache_key)
            if value:
                value["cache_status"] = "waited_for_refresh"
                return value
            else:
                # Timeout waiting - do the query ourselves
                LOCK_ACQUISITIONS.labels(service=SERVICE_NAME, result="timeout").inc()
                logger.warning(f"Timeout waiting for cache, querying directly for {cache_key}")
                value = await simulate_slow_database_query(product_id)
                value["cache_status"] = "waited_timeout"
                return value

    # No protection - just do the query (THUNDERING HERD!)
    value = await simulate_slow_database_query(product_id)
    await set_cached_value(cache_key, value, ttl_total)
    value["cache_status"] = "miss_no_protection"
    return value


# =============================================================================
# API Endpoints
# =============================================================================

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "config": {
            "cache_ttl": runtime_config["cache_ttl_seconds"],
            "lock_protection": runtime_config["enable_lock_protection"],
            "probabilistic_refresh": runtime_config["enable_probabilistic_refresh"],
        }
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/product/{product_id}")
async def get_product(product_id: str):
    """
    Get product by ID.

    This is the main endpoint that demonstrates the thundering herd problem.
    When the cache expires, all concurrent requests will try to regenerate it.
    """
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")
    current_span.set_attribute("product_id", product_id)

    try:
        product = await get_product_with_protection(product_id)

        duration = time.time() - start_time

        # Record metrics
        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/product/{id}",
            status="200"
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/product/{id}"
        ).observe(duration)

        return {
            "data": product,
            "trace_id": trace_id,
            "duration_ms": round(duration * 1000, 2),
            "protection": {
                "lock_enabled": runtime_config["enable_lock_protection"],
                "probabilistic_enabled": runtime_config["enable_probabilistic_refresh"],
            }
        }

    except Exception as e:
        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/product/{id}",
            status="500"
        ).inc()
        logger.error(f"Error getting product {product_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/cache/{product_id}")
async def invalidate_cache(product_id: str):
    """Manually invalidate cache for a product."""
    cache_key = f"product:{product_id}"
    await redis_client.delete(cache_key)
    return {"status": "invalidated", "key": cache_key}


@app.delete("/cache")
async def clear_all_cache():
    """Clear all cached products."""
    keys = await redis_client.keys("product:*")
    if keys:
        await redis_client.delete(*keys)
    return {"status": "cleared", "keys_deleted": len(keys) if keys else 0}


# =============================================================================
# Admin Endpoints
# =============================================================================

@app.get("/admin/config")
async def get_config():
    """Get current runtime configuration."""
    return {
        "config": runtime_config,
        "concurrent_queries": concurrent_queries
    }


@app.post("/admin/protection")
async def configure_protection(config: ProtectionConfig):
    """
    Enable or disable protection mechanisms.

    This allows toggling protection on/off during the demo without restarting.
    """
    if config.enable_lock_protection is not None:
        runtime_config["enable_lock_protection"] = config.enable_lock_protection
        logger.info(f"Lock protection: {config.enable_lock_protection}")

    if config.enable_probabilistic_refresh is not None:
        runtime_config["enable_probabilistic_refresh"] = config.enable_probabilistic_refresh
        logger.info(f"Probabilistic refresh: {config.enable_probabilistic_refresh}")

    if config.probabilistic_refresh_factor is not None:
        runtime_config["probabilistic_refresh_factor"] = config.probabilistic_refresh_factor
        logger.info(f"Probabilistic refresh factor: {config.probabilistic_refresh_factor}")

    return {"status": "updated", "config": runtime_config}


@app.post("/admin/cache")
async def configure_cache(config: CacheConfig):
    """
    Configure cache and database simulation parameters.
    """
    if config.cache_ttl_seconds is not None:
        runtime_config["cache_ttl_seconds"] = config.cache_ttl_seconds
        logger.info(f"Cache TTL: {config.cache_ttl_seconds}s")

    if config.db_query_min_ms is not None:
        runtime_config["db_query_min_ms"] = config.db_query_min_ms
        logger.info(f"DB query min: {config.db_query_min_ms}ms")

    if config.db_query_max_ms is not None:
        runtime_config["db_query_max_ms"] = config.db_query_max_ms
        logger.info(f"DB query max: {config.db_query_max_ms}ms")

    return {"status": "updated", "config": runtime_config}


@app.get("/admin/stats")
async def get_stats():
    """Get current system stats."""
    # Get Redis stats
    info = await redis_client.info("stats")
    keys = await redis_client.keys("product:*")

    return {
        "concurrent_db_queries": concurrent_queries,
        "cached_products": len(keys) if keys else 0,
        "redis_hits": info.get("keyspace_hits", 0),
        "redis_misses": info.get("keyspace_misses", 0),
        "protection": {
            "lock_enabled": runtime_config["enable_lock_protection"],
            "probabilistic_enabled": runtime_config["enable_probabilistic_refresh"],
        }
    }


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
