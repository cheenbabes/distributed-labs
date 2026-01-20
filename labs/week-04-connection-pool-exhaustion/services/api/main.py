"""
API Service with configurable connection pool to PostgreSQL.

This service demonstrates connection pool exhaustion when:
1. Pool size is small (default: 5)
2. Queries hold connections for extended time
3. Concurrent requests exceed pool capacity
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from pydantic import BaseModel

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "api")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

# Database configuration
DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "labdb")
DB_USER = os.getenv("DB_USER", "labuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "labpassword")

# Pool configuration - the key settings for this lab
POOL_MIN_SIZE = int(os.getenv("POOL_MIN_SIZE", "2"))
POOL_MAX_SIZE = int(os.getenv("POOL_MAX_SIZE", "5"))
POOL_ACQUIRE_TIMEOUT = float(os.getenv("POOL_ACQUIRE_TIMEOUT", "10.0"))

# Simulated work time (how long to hold a connection)
WORK_TIME_MS = int(os.getenv("WORK_TIME_MS", "1000"))

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
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0]
)

# Connection pool specific metrics
POOL_SIZE = Gauge(
    "db_pool_size",
    "Current connection pool size",
    ["service"]
)
POOL_FREE_SIZE = Gauge(
    "db_pool_free_size",
    "Number of free connections in pool",
    ["service"]
)
POOL_USED_SIZE = Gauge(
    "db_pool_used_size",
    "Number of used connections in pool",
    ["service"]
)
POOL_WAITING = Gauge(
    "db_pool_waiting_requests",
    "Number of requests waiting for a connection",
    ["service"]
)
POOL_ACQUIRE_TIME = Histogram(
    "db_pool_acquire_seconds",
    "Time spent waiting to acquire a connection",
    ["service"],
    buckets=[0.001, 0.01, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
)
POOL_EXHAUSTION_TOTAL = Counter(
    "db_pool_exhaustion_total",
    "Total number of pool exhaustion events (timeouts)",
    ["service"]
)
QUERY_DURATION = Histogram(
    "db_query_duration_seconds",
    "Database query duration",
    ["service", "query_type"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

# Global connection pool
pool: Optional[asyncpg.Pool] = None

# Runtime configuration (can be changed via API)
runtime_config = {
    "work_time_ms": WORK_TIME_MS,
    "pool_acquire_timeout": POOL_ACQUIRE_TIMEOUT,
}

# Track waiting requests
waiting_count = 0


class ConfigUpdate(BaseModel):
    work_time_ms: Optional[int] = None
    pool_acquire_timeout: Optional[float] = None


class PoolStats(BaseModel):
    pool_min_size: int
    pool_max_size: int
    pool_size: int
    pool_free_size: int
    pool_used_size: int
    waiting_requests: int
    work_time_ms: int
    acquire_timeout: float


async def create_pool() -> asyncpg.Pool:
    """Create the database connection pool with configured limits."""
    logger.info(f"Creating connection pool: min={POOL_MIN_SIZE}, max={POOL_MAX_SIZE}")
    return await asyncpg.create_pool(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
        command_timeout=60,
    )


async def init_db(p: asyncpg.Pool):
    """Initialize database schema."""
    async with p.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Seed some data if empty
        count = await conn.fetchval("SELECT COUNT(*) FROM items")
        if count == 0:
            logger.info("Seeding database with sample data...")
            for i in range(100):
                await conn.execute(
                    "INSERT INTO items (name, data) VALUES ($1, $2)",
                    f"Item {i}",
                    f"Sample data for item {i}" * 10
                )


def update_pool_metrics():
    """Update Prometheus metrics with current pool state."""
    if pool:
        POOL_SIZE.labels(service=SERVICE_NAME).set(pool.get_size())
        POOL_FREE_SIZE.labels(service=SERVICE_NAME).set(pool.get_idle_size())
        POOL_USED_SIZE.labels(service=SERVICE_NAME).set(pool.get_size() - pool.get_idle_size())
        POOL_WAITING.labels(service=SERVICE_NAME).set(waiting_count)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Pool config: min={POOL_MIN_SIZE}, max={POOL_MAX_SIZE}, timeout={POOL_ACQUIRE_TIMEOUT}s")
    logger.info(f"Work time: {WORK_TIME_MS}ms")

    # Wait for Postgres to be ready
    for attempt in range(30):
        try:
            pool = await create_pool()
            await init_db(pool)
            logger.info("Database connection pool ready")
            break
        except Exception as e:
            logger.warning(f"Waiting for database... attempt {attempt + 1}: {e}")
            await asyncio.sleep(1)
    else:
        raise RuntimeError("Could not connect to database")

    yield

    if pool:
        await pool.close()
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
    update_pool_metrics()
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/pool/stats")
async def pool_stats() -> PoolStats:
    """Get current connection pool statistics."""
    update_pool_metrics()
    return PoolStats(
        pool_min_size=POOL_MIN_SIZE,
        pool_max_size=POOL_MAX_SIZE,
        pool_size=pool.get_size() if pool else 0,
        pool_free_size=pool.get_idle_size() if pool else 0,
        pool_used_size=(pool.get_size() - pool.get_idle_size()) if pool else 0,
        waiting_requests=waiting_count,
        work_time_ms=runtime_config["work_time_ms"],
        acquire_timeout=runtime_config["pool_acquire_timeout"],
    )


@app.post("/admin/config")
async def update_config(config: ConfigUpdate):
    """Update runtime configuration."""
    if config.work_time_ms is not None:
        runtime_config["work_time_ms"] = config.work_time_ms
        logger.info(f"Updated work_time_ms to {config.work_time_ms}")
    if config.pool_acquire_timeout is not None:
        runtime_config["pool_acquire_timeout"] = config.pool_acquire_timeout
        logger.info(f"Updated pool_acquire_timeout to {config.pool_acquire_timeout}")
    return runtime_config


@app.get("/admin/config")
async def get_config():
    """Get current runtime configuration."""
    return runtime_config


@app.get("/api/query")
async def api_query(request: Request):
    """
    Main endpoint that acquires a connection and performs simulated work.

    This demonstrates connection pool exhaustion when:
    - Pool has 5 connections
    - Each request holds connection for 1000ms
    - More than 5 concurrent requests arrive
    """
    global waiting_count
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    work_time = runtime_config["work_time_ms"]
    acquire_timeout = runtime_config["pool_acquire_timeout"]

    # Track that we're waiting for a connection
    waiting_count += 1
    update_pool_metrics()

    acquire_start = time.time()

    try:
        with tracer.start_as_current_span("acquire_connection") as acquire_span:
            acquire_span.set_attribute("pool.max_size", POOL_MAX_SIZE)
            acquire_span.set_attribute("pool.timeout", acquire_timeout)

            try:
                # This is where we wait for a connection from the pool
                async with asyncio.timeout(acquire_timeout):
                    conn = await pool.acquire()
            except asyncio.TimeoutError:
                # Pool exhaustion! No connection available within timeout
                acquire_time = time.time() - acquire_start
                POOL_EXHAUSTION_TOTAL.labels(service=SERVICE_NAME).inc()
                POOL_ACQUIRE_TIME.labels(service=SERVICE_NAME).observe(acquire_time)
                acquire_span.set_attribute("pool.exhausted", True)
                acquire_span.set_attribute("pool.acquire_time_s", acquire_time)

                logger.error(f"Connection pool exhausted! Waited {acquire_time:.2f}s trace_id={trace_id}")

                REQUEST_COUNT.labels(
                    service=SERVICE_NAME,
                    method="GET",
                    endpoint="/api/query",
                    status="503"
                ).inc()

                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": "Connection pool exhausted",
                        "waited_seconds": round(acquire_time, 2),
                        "pool_max_size": POOL_MAX_SIZE,
                        "trace_id": trace_id,
                    }
                )

        acquire_time = time.time() - acquire_start
        POOL_ACQUIRE_TIME.labels(service=SERVICE_NAME).observe(acquire_time)

        waiting_count -= 1
        update_pool_metrics()

        try:
            with tracer.start_as_current_span("database_work") as work_span:
                work_span.set_attribute("work.time_ms", work_time)
                work_span.set_attribute("pool.acquire_time_s", acquire_time)

                # Simulate database work that holds the connection
                query_start = time.time()

                # Do a real query
                rows = await conn.fetch("SELECT * FROM items ORDER BY RANDOM() LIMIT 10")

                # Simulate additional processing time (holding the connection)
                await asyncio.sleep(work_time / 1000.0)

                query_time = time.time() - query_start
                QUERY_DURATION.labels(
                    service=SERVICE_NAME,
                    query_type="select_with_work"
                ).observe(query_time)

                work_span.set_attribute("rows.returned", len(rows))
                work_span.set_attribute("query.time_s", query_time)
        finally:
            # CRITICAL: Always release the connection back to the pool
            await pool.release(conn)

        total_time = time.time() - start_time

        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/api/query",
            status="200"
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/api/query"
        ).observe(total_time)

        logger.info(
            f"Query completed trace_id={trace_id} "
            f"acquire_time={acquire_time*1000:.0f}ms "
            f"work_time={work_time}ms "
            f"total={total_time*1000:.0f}ms"
        )

        return {
            "status": "success",
            "service": SERVICE_NAME,
            "trace_id": trace_id,
            "timing": {
                "acquire_time_ms": round(acquire_time * 1000, 2),
                "work_time_ms": work_time,
                "total_time_ms": round(total_time * 1000, 2),
            },
            "pool": {
                "max_size": POOL_MAX_SIZE,
                "current_size": pool.get_size(),
                "free": pool.get_idle_size(),
            },
            "rows_returned": len(rows),
        }

    finally:
        if waiting_count > 0:
            waiting_count -= 1
        update_pool_metrics()


@app.get("/api/query-proper")
async def api_query_proper(request: Request):
    """
    Demonstrates proper connection handling with context manager.

    This is the correct pattern that ensures connections are always released.
    """
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    work_time = runtime_config["work_time_ms"]

    async with pool.acquire() as conn:
        # Connection is automatically released when we exit this block
        rows = await conn.fetch("SELECT * FROM items ORDER BY RANDOM() LIMIT 10")
        await asyncio.sleep(work_time / 1000.0)

    total_time = time.time() - start_time

    return {
        "status": "success",
        "service": SERVICE_NAME,
        "trace_id": trace_id,
        "timing": {
            "total_time_ms": round(total_time * 1000, 2),
        },
        "rows_returned": len(rows),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
