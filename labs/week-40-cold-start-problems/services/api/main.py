"""
API Service - Demonstrates cold start problems with caches and connection pools.
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
import httpx
import redis.asyncio as redis
from fastapi import FastAPI, Request, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "api")
INSTANCE_ID = os.getenv("INSTANCE_ID", "unknown")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.getenv("POSTGRES_USER", "labuser")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "labpass")
POSTGRES_DB = os.getenv("POSTGRES_DB", "labdb")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8001")
PREWARM_CACHE = os.getenv("PREWARM_CACHE", "false").lower() == "true"
PREWARM_CONNECTIONS = os.getenv("PREWARM_CONNECTIONS", "false").lower() == "true"
CONNECTION_POOL_SIZE = int(os.getenv("CONNECTION_POOL_SIZE", "10"))

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(SERVICE_NAME)

# OpenTelemetry setup
resource = Resource.create({"service.name": SERVICE_NAME, "instance.id": INSTANCE_ID})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# Instrument httpx for trace propagation
HTTPXClientInstrumentor().instrument()

# Prometheus metrics
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "instance", "method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["service", "instance", "method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
CACHE_HITS = Counter(
    "cache_hits_total",
    "Cache hit count",
    ["service", "instance"]
)
CACHE_MISSES = Counter(
    "cache_misses_total",
    "Cache miss count",
    ["service", "instance"]
)
CONNECTION_POOL_SIZE_GAUGE = Gauge(
    "connection_pool_size",
    "Current connection pool size",
    ["service", "instance", "pool_type"]
)
COLD_START_LATENCY = Histogram(
    "cold_start_latency_seconds",
    "Latency of cold start operations",
    ["service", "instance", "operation"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5]
)
STARTUP_TIME = Gauge(
    "startup_time_seconds",
    "Time taken to start the service",
    ["service", "instance", "warm_type"]
)

# Global state
redis_client: Optional[redis.Redis] = None
postgres_pool: Optional[asyncpg.Pool] = None
http_client: Optional[httpx.AsyncClient] = None
cache_warmed = False
connections_warmed = False
startup_timestamp = time.time()


async def warm_cache():
    """Pre-populate cache with common data patterns."""
    global cache_warmed

    with tracer.start_as_current_span("warm_cache") as span:
        start = time.time()

        # Simulate cache warming with product catalog, user preferences, etc.
        common_keys = [
            ("product:popular:1", '{"id": 1, "name": "Popular Product 1", "price": 99.99}'),
            ("product:popular:2", '{"id": 2, "name": "Popular Product 2", "price": 149.99}'),
            ("product:popular:3", '{"id": 3, "name": "Popular Product 3", "price": 199.99}'),
            ("config:feature_flags", '{"dark_mode": true, "new_checkout": false}'),
            ("config:rate_limits", '{"api": 1000, "search": 100}'),
            ("session:template", '{"user_id": null, "cart": [], "preferences": {}}'),
        ]

        for key, value in common_keys:
            await redis_client.set(key, value, ex=3600)  # 1 hour TTL

        # Also warm with computed data that takes time to generate
        for i in range(1, 21):
            key = f"computed:user_recommendations:{i}"
            value = f'{{"user_id": {i}, "recommendations": [1, 2, 3, 4, 5]}}'
            await redis_client.set(key, value, ex=1800)  # 30 min TTL

        duration = time.time() - start
        span.set_attribute("keys_warmed", len(common_keys) + 20)
        span.set_attribute("duration_ms", duration * 1000)

        COLD_START_LATENCY.labels(
            service=SERVICE_NAME,
            instance=INSTANCE_ID,
            operation="cache_warm"
        ).observe(duration)

        cache_warmed = True
        logger.info(f"Cache warmed in {duration*1000:.2f}ms with {len(common_keys) + 20} keys")


async def warm_connections():
    """Pre-establish connections in the pool."""
    global connections_warmed

    with tracer.start_as_current_span("warm_connections") as span:
        start = time.time()

        # Warm PostgreSQL connection pool by executing simple queries
        async with postgres_pool.acquire() as conn:
            await conn.execute("SELECT 1")

        # Verify multiple connections are ready
        warmup_tasks = []
        for _ in range(min(3, CONNECTION_POOL_SIZE)):
            warmup_tasks.append(postgres_pool.execute("SELECT 1"))
        await asyncio.gather(*warmup_tasks)

        # Warm HTTP client connection pool
        try:
            async with http_client.stream("GET", f"{BACKEND_URL}/health") as response:
                await response.aread()
        except Exception as e:
            logger.warning(f"HTTP warmup failed: {e}")

        duration = time.time() - start
        span.set_attribute("duration_ms", duration * 1000)

        COLD_START_LATENCY.labels(
            service=SERVICE_NAME,
            instance=INSTANCE_ID,
            operation="connection_warm"
        ).observe(duration)

        connections_warmed = True
        logger.info(f"Connections warmed in {duration*1000:.2f}ms")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, postgres_pool, http_client

    startup_start = time.time()
    logger.info(f"{SERVICE_NAME} ({INSTANCE_ID}) starting up")
    logger.info(f"Pre-warm cache: {PREWARM_CACHE}, Pre-warm connections: {PREWARM_CONNECTIONS}")

    # Initialize Redis client
    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        decode_responses=True
    )

    # Initialize PostgreSQL connection pool
    postgres_pool = await asyncpg.create_pool(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        database=POSTGRES_DB,
        min_size=1 if not PREWARM_CONNECTIONS else CONNECTION_POOL_SIZE,
        max_size=CONNECTION_POOL_SIZE
    )

    # Initialize HTTP client with connection pooling
    http_client = httpx.AsyncClient(
        timeout=30.0,
        limits=httpx.Limits(
            max_keepalive_connections=CONNECTION_POOL_SIZE,
            max_connections=CONNECTION_POOL_SIZE * 2
        )
    )

    # Pre-warm if configured
    if PREWARM_CACHE:
        await warm_cache()

    if PREWARM_CONNECTIONS:
        await warm_connections()

    startup_duration = time.time() - startup_start
    warm_type = "warm" if (PREWARM_CACHE or PREWARM_CONNECTIONS) else "cold"

    STARTUP_TIME.labels(
        service=SERVICE_NAME,
        instance=INSTANCE_ID,
        warm_type=warm_type
    ).set(startup_duration)

    CONNECTION_POOL_SIZE_GAUGE.labels(
        service=SERVICE_NAME,
        instance=INSTANCE_ID,
        pool_type="postgres"
    ).set(postgres_pool.get_size())

    logger.info(f"Startup completed in {startup_duration*1000:.2f}ms (type: {warm_type})")

    yield

    # Cleanup
    await redis_client.close()
    await postgres_pool.close()
    await http_client.aclose()
    logger.info(f"{SERVICE_NAME} ({INSTANCE_ID}) shutting down")


app = FastAPI(title=f"{SERVICE_NAME}-{INSTANCE_ID}", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "instance": INSTANCE_ID,
        "cache_warmed": cache_warmed,
        "connections_warmed": connections_warmed,
        "startup_time_ms": (time.time() - startup_timestamp) * 1000
    }


@app.get("/metrics")
async def metrics():
    # Update connection pool gauge
    if postgres_pool:
        CONNECTION_POOL_SIZE_GAUGE.labels(
            service=SERVICE_NAME,
            instance=INSTANCE_ID,
            pool_type="postgres"
        ).set(postgres_pool.get_size())

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/product/{product_id}")
async def get_product(product_id: int, request: Request):
    """
    Get product with caching. Demonstrates cold cache latency.
    """
    start_time = time.time()
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    cache_key = f"product:{product_id}"
    cache_hit = False

    # Try cache first
    with tracer.start_as_current_span("redis_get") as span:
        cache_start = time.time()
        cached_value = await redis_client.get(cache_key)
        cache_duration = time.time() - cache_start
        span.set_attribute("cache_key", cache_key)
        span.set_attribute("duration_ms", cache_duration * 1000)

    if cached_value:
        cache_hit = True
        result = cached_value
        CACHE_HITS.labels(service=SERVICE_NAME, instance=INSTANCE_ID).inc()
        current_span.set_attribute("cache_hit", True)
    else:
        CACHE_MISSES.labels(service=SERVICE_NAME, instance=INSTANCE_ID).inc()
        current_span.set_attribute("cache_hit", False)

        # Cache miss - need to fetch from backend (simulates DB/slow operation)
        with tracer.start_as_current_span("backend_call") as span:
            backend_start = time.time()

            try:
                response = await http_client.get(f"{BACKEND_URL}/product/{product_id}")
                response.raise_for_status()
                result = response.text
            except httpx.HTTPError as e:
                # Simulate slow database query on cache miss
                await asyncio.sleep(0.05 + random.uniform(0.01, 0.05))
                result = f'{{"id": {product_id}, "name": "Product {product_id}", "price": {product_id * 10.99}}}'

            backend_duration = time.time() - backend_start
            span.set_attribute("duration_ms", backend_duration * 1000)

            COLD_START_LATENCY.labels(
                service=SERVICE_NAME,
                instance=INSTANCE_ID,
                operation="cache_miss_fetch"
            ).observe(backend_duration)

        # Store in cache
        with tracer.start_as_current_span("redis_set") as span:
            await redis_client.set(cache_key, result, ex=300)  # 5 min TTL

    duration = time.time() - start_time

    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        instance=INSTANCE_ID,
        method="GET",
        endpoint="/api/product",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        instance=INSTANCE_ID,
        method="GET",
        endpoint="/api/product"
    ).observe(duration)

    return {
        "service": SERVICE_NAME,
        "instance": INSTANCE_ID,
        "product_id": product_id,
        "cache_hit": cache_hit,
        "duration_ms": round(duration * 1000, 2),
        "trace_id": trace_id,
        "data": result
    }


@app.get("/api/user/{user_id}/recommendations")
async def get_recommendations(user_id: int, request: Request):
    """
    Get user recommendations. Demonstrates cold cache latency for computed data.
    """
    start_time = time.time()
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    cache_key = f"computed:user_recommendations:{user_id}"
    cache_hit = False
    compute_time = 0

    # Try cache first
    with tracer.start_as_current_span("redis_get") as span:
        cached_value = await redis_client.get(cache_key)
        span.set_attribute("cache_key", cache_key)

    if cached_value:
        cache_hit = True
        result = cached_value
        CACHE_HITS.labels(service=SERVICE_NAME, instance=INSTANCE_ID).inc()
    else:
        CACHE_MISSES.labels(service=SERVICE_NAME, instance=INSTANCE_ID).inc()

        # Cache miss - expensive computation required
        with tracer.start_as_current_span("compute_recommendations") as span:
            compute_start = time.time()

            # Simulate expensive ML inference / recommendation computation
            # This would normally hit a model server or run complex queries
            await asyncio.sleep(0.1 + random.uniform(0.05, 0.15))

            recommendations = [random.randint(1, 100) for _ in range(10)]
            result = f'{{"user_id": {user_id}, "recommendations": {recommendations}}}'

            compute_time = time.time() - compute_start
            span.set_attribute("compute_time_ms", compute_time * 1000)

            COLD_START_LATENCY.labels(
                service=SERVICE_NAME,
                instance=INSTANCE_ID,
                operation="compute_recommendations"
            ).observe(compute_time)

        # Store in cache
        await redis_client.set(cache_key, result, ex=1800)  # 30 min TTL

    duration = time.time() - start_time

    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        instance=INSTANCE_ID,
        method="GET",
        endpoint="/api/recommendations",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        instance=INSTANCE_ID,
        method="GET",
        endpoint="/api/recommendations"
    ).observe(duration)

    return {
        "service": SERVICE_NAME,
        "instance": INSTANCE_ID,
        "user_id": user_id,
        "cache_hit": cache_hit,
        "compute_time_ms": round(compute_time * 1000, 2) if not cache_hit else 0,
        "duration_ms": round(duration * 1000, 2),
        "trace_id": trace_id,
        "data": result
    }


@app.get("/api/db-query")
async def db_query(request: Request):
    """
    Execute database query. Demonstrates connection pool cold start.
    """
    start_time = time.time()
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Time to acquire connection from pool
    with tracer.start_as_current_span("acquire_connection") as span:
        acquire_start = time.time()
        async with postgres_pool.acquire() as conn:
            acquire_time = time.time() - acquire_start
            span.set_attribute("acquire_time_ms", acquire_time * 1000)

            if acquire_time > 0.01:  # > 10ms indicates cold connection
                COLD_START_LATENCY.labels(
                    service=SERVICE_NAME,
                    instance=INSTANCE_ID,
                    operation="connection_acquire"
                ).observe(acquire_time)

            # Execute query
            with tracer.start_as_current_span("execute_query") as query_span:
                query_start = time.time()
                result = await conn.fetchval("SELECT 1 + 1")
                query_time = time.time() - query_start
                query_span.set_attribute("query_time_ms", query_time * 1000)

    duration = time.time() - start_time
    pool_size = postgres_pool.get_size()

    CONNECTION_POOL_SIZE_GAUGE.labels(
        service=SERVICE_NAME,
        instance=INSTANCE_ID,
        pool_type="postgres"
    ).set(pool_size)

    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        instance=INSTANCE_ID,
        method="GET",
        endpoint="/api/db-query",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        instance=INSTANCE_ID,
        method="GET",
        endpoint="/api/db-query"
    ).observe(duration)

    return {
        "service": SERVICE_NAME,
        "instance": INSTANCE_ID,
        "result": result,
        "acquire_time_ms": round(acquire_time * 1000, 2),
        "query_time_ms": round(query_time * 1000, 2),
        "total_duration_ms": round(duration * 1000, 2),
        "pool_size": pool_size,
        "trace_id": trace_id
    }


@app.post("/admin/clear-cache")
async def clear_cache():
    """Clear all cache entries to simulate cold start."""
    global cache_warmed
    await redis_client.flushdb()
    cache_warmed = False
    logger.info("Cache cleared")
    return {"status": "ok", "message": "Cache cleared"}


@app.post("/admin/warm-cache")
async def trigger_warm_cache():
    """Trigger cache warming."""
    await warm_cache()
    return {"status": "ok", "message": "Cache warmed", "cache_warmed": cache_warmed}


@app.get("/admin/status")
async def admin_status():
    """Get detailed status including cold start metrics."""
    pool_size = postgres_pool.get_size() if postgres_pool else 0
    pool_free = postgres_pool.get_idle_size() if postgres_pool else 0

    return {
        "service": SERVICE_NAME,
        "instance": INSTANCE_ID,
        "cache_warmed": cache_warmed,
        "connections_warmed": connections_warmed,
        "prewarm_cache_enabled": PREWARM_CACHE,
        "prewarm_connections_enabled": PREWARM_CONNECTIONS,
        "postgres_pool": {
            "size": pool_size,
            "free": pool_free,
            "max_size": CONNECTION_POOL_SIZE
        },
        "uptime_seconds": time.time() - startup_timestamp
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
