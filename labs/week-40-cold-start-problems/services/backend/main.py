"""
Backend Service - Simulates a database-heavy service with connection pool management.
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
from fastapi import FastAPI, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "backend")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.getenv("POSTGRES_USER", "labuser")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "labpass")
POSTGRES_DB = os.getenv("POSTGRES_DB", "labdb")
CONNECTION_POOL_SIZE = int(os.getenv("CONNECTION_POOL_SIZE", "5"))
PREWARM_CONNECTIONS = os.getenv("PREWARM_CONNECTIONS", "false").lower() == "true"

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
    "backend_requests_total",
    "Total backend requests",
    ["method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "backend_request_duration_seconds",
    "Backend request latency",
    ["method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
CONNECTION_POOL_GAUGE = Gauge(
    "backend_connection_pool_size",
    "Current backend connection pool size"
)
CONNECTION_ACQUIRE_TIME = Histogram(
    "backend_connection_acquire_seconds",
    "Time to acquire a database connection",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5]
)

# Global state
postgres_pool: Optional[asyncpg.Pool] = None
startup_timestamp = time.time()
connections_warmed = False


async def warm_connections():
    """Pre-establish connections in the pool."""
    global connections_warmed

    with tracer.start_as_current_span("warm_backend_connections") as span:
        start = time.time()

        # Execute queries to establish connections
        tasks = []
        for _ in range(CONNECTION_POOL_SIZE):
            tasks.append(postgres_pool.execute("SELECT 1"))
        await asyncio.gather(*tasks)

        duration = time.time() - start
        span.set_attribute("connections_warmed", CONNECTION_POOL_SIZE)
        span.set_attribute("duration_ms", duration * 1000)

        connections_warmed = True
        logger.info(f"Backend connections warmed in {duration*1000:.2f}ms")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global postgres_pool

    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Pre-warm connections: {PREWARM_CONNECTIONS}")

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

    # Initialize database schema
    async with postgres_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                price DECIMAL(10, 2) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Seed with sample data if empty
        count = await conn.fetchval("SELECT COUNT(*) FROM products")
        if count == 0:
            for i in range(1, 101):
                await conn.execute(
                    "INSERT INTO products (name, price) VALUES ($1, $2)",
                    f"Product {i}",
                    round(random.uniform(10, 500), 2)
                )
            logger.info("Seeded 100 products")

    if PREWARM_CONNECTIONS:
        await warm_connections()

    CONNECTION_POOL_GAUGE.set(postgres_pool.get_size())
    logger.info(f"Backend startup complete, pool size: {postgres_pool.get_size()}")

    yield

    await postgres_pool.close()
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "connections_warmed": connections_warmed,
        "pool_size": postgres_pool.get_size() if postgres_pool else 0
    }


@app.get("/metrics")
async def metrics():
    if postgres_pool:
        CONNECTION_POOL_GAUGE.set(postgres_pool.get_size())
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/product/{product_id}")
async def get_product(product_id: int, request: Request):
    """Get product from database."""
    start_time = time.time()
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Track connection acquisition time
    with tracer.start_as_current_span("acquire_connection") as span:
        acquire_start = time.time()
        async with postgres_pool.acquire() as conn:
            acquire_time = time.time() - acquire_start
            span.set_attribute("acquire_time_ms", acquire_time * 1000)
            CONNECTION_ACQUIRE_TIME.observe(acquire_time)

            # Execute query
            with tracer.start_as_current_span("query_product") as query_span:
                query_start = time.time()
                row = await conn.fetchrow(
                    "SELECT id, name, price FROM products WHERE id = $1",
                    product_id % 100 + 1  # Wrap to valid range
                )
                query_time = time.time() - query_start
                query_span.set_attribute("query_time_ms", query_time * 1000)

    duration = time.time() - start_time

    REQUEST_COUNT.labels(method="GET", endpoint="/product", status="200").inc()
    REQUEST_LATENCY.labels(method="GET", endpoint="/product").observe(duration)

    if row:
        result = {
            "id": row["id"],
            "name": row["name"],
            "price": float(row["price"])
        }
    else:
        result = {"id": product_id, "name": f"Product {product_id}", "price": product_id * 10.99}

    return {
        "service": SERVICE_NAME,
        "acquire_time_ms": round(acquire_time * 1000, 2),
        "query_time_ms": round(query_time * 1000, 2),
        "duration_ms": round(duration * 1000, 2),
        "trace_id": trace_id,
        "data": result
    }


@app.get("/products")
async def list_products(request: Request, limit: int = 10):
    """List products from database."""
    start_time = time.time()
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    with tracer.start_as_current_span("acquire_connection") as span:
        acquire_start = time.time()
        async with postgres_pool.acquire() as conn:
            acquire_time = time.time() - acquire_start
            span.set_attribute("acquire_time_ms", acquire_time * 1000)
            CONNECTION_ACQUIRE_TIME.observe(acquire_time)

            with tracer.start_as_current_span("query_products") as query_span:
                query_start = time.time()
                rows = await conn.fetch(
                    "SELECT id, name, price FROM products LIMIT $1",
                    min(limit, 100)
                )
                query_time = time.time() - query_start
                query_span.set_attribute("query_time_ms", query_time * 1000)
                query_span.set_attribute("row_count", len(rows))

    duration = time.time() - start_time

    REQUEST_COUNT.labels(method="GET", endpoint="/products", status="200").inc()
    REQUEST_LATENCY.labels(method="GET", endpoint="/products").observe(duration)

    products = [{"id": r["id"], "name": r["name"], "price": float(r["price"])} for r in rows]

    return {
        "service": SERVICE_NAME,
        "acquire_time_ms": round(acquire_time * 1000, 2),
        "query_time_ms": round(query_time * 1000, 2),
        "duration_ms": round(duration * 1000, 2),
        "count": len(products),
        "trace_id": trace_id,
        "data": products
    }


@app.get("/heavy-query")
async def heavy_query(request: Request):
    """Simulate a heavy database query."""
    start_time = time.time()
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    with tracer.start_as_current_span("acquire_connection") as span:
        acquire_start = time.time()
        async with postgres_pool.acquire() as conn:
            acquire_time = time.time() - acquire_start
            span.set_attribute("acquire_time_ms", acquire_time * 1000)

            with tracer.start_as_current_span("heavy_query") as query_span:
                query_start = time.time()

                # Simulate heavy computation
                result = await conn.fetchval("""
                    SELECT COUNT(*), AVG(price), MAX(price), MIN(price)
                    FROM products
                    CROSS JOIN generate_series(1, 100) AS s
                """)

                # Add artificial delay to simulate complex query
                await asyncio.sleep(0.02 + random.uniform(0.01, 0.03))

                query_time = time.time() - query_start
                query_span.set_attribute("query_time_ms", query_time * 1000)

    duration = time.time() - start_time

    REQUEST_COUNT.labels(method="GET", endpoint="/heavy-query", status="200").inc()
    REQUEST_LATENCY.labels(method="GET", endpoint="/heavy-query").observe(duration)

    return {
        "service": SERVICE_NAME,
        "acquire_time_ms": round(acquire_time * 1000, 2),
        "query_time_ms": round(query_time * 1000, 2),
        "duration_ms": round(duration * 1000, 2),
        "trace_id": trace_id
    }


@app.get("/pool-status")
async def pool_status():
    """Get connection pool status."""
    return {
        "service": SERVICE_NAME,
        "pool_size": postgres_pool.get_size(),
        "pool_idle": postgres_pool.get_idle_size(),
        "pool_max": CONNECTION_POOL_SIZE,
        "connections_warmed": connections_warmed,
        "uptime_seconds": time.time() - startup_timestamp
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
