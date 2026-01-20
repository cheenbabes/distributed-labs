"""
Primary Database Service - Handles writes and replicates to replicas.
Supports leader election via Redis and exposes failover metrics.
"""
import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Any

import httpx
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from pydantic import BaseModel

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "primary-db")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
NODE_ID = os.getenv("NODE_ID", "primary-1")
IS_PRIMARY = os.getenv("IS_PRIMARY", "true").lower() == "true"
REPLICATION_LAG_MS = int(os.getenv("REPLICATION_LAG_MS", "0"))

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

# Instrument httpx for trace propagation
HTTPXClientInstrumentor().instrument()

# Prometheus metrics
REQUEST_COUNT = Counter(
    "db_requests_total",
    "Total database requests",
    ["service", "operation", "status"]
)
WRITE_LATENCY = Histogram(
    "db_write_duration_seconds",
    "Database write latency",
    ["service"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
READ_LATENCY = Histogram(
    "db_read_duration_seconds",
    "Database read latency",
    ["service"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
PRIMARY_HEALTH = Gauge(
    "primary_health",
    "Primary node health status (1=healthy, 0=unhealthy)",
    ["node_id"]
)
REPLICATION_LAG = Gauge(
    "replication_lag_ms",
    "Current replication lag in milliseconds",
    ["node_id"]
)
WRITE_SEQUENCE = Gauge(
    "write_sequence_number",
    "Current write sequence number",
    ["node_id"]
)
DATA_VERSION = Gauge(
    "data_version",
    "Current data version on this node",
    ["node_id"]
)

# Global state
redis_client: redis.Redis = None
current_sequence = 0
is_healthy = True
simulated_failure = False


class WriteRequest(BaseModel):
    key: str
    value: Any


class FailureConfig(BaseModel):
    enabled: bool
    duration_seconds: int = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, current_sequence
    logger.info(f"{SERVICE_NAME} ({NODE_ID}) starting up as {'PRIMARY' if IS_PRIMARY else 'REPLICA'}")

    # Connect to Redis
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

    # Initialize sequence from Redis
    seq = await redis_client.get(f"sequence:{NODE_ID}")
    current_sequence = int(seq) if seq else 0

    # Register node
    await redis_client.hset("cluster:nodes", NODE_ID, f"primary={IS_PRIMARY},healthy=true,port=8001")

    # Set initial metrics
    PRIMARY_HEALTH.labels(node_id=NODE_ID).set(1)
    REPLICATION_LAG.labels(node_id=NODE_ID).set(REPLICATION_LAG_MS)
    WRITE_SEQUENCE.labels(node_id=NODE_ID).set(current_sequence)
    DATA_VERSION.labels(node_id=NODE_ID).set(0)

    yield

    # Cleanup
    await redis_client.hdel("cluster:nodes", NODE_ID)
    await redis_client.close()
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    """Health check endpoint."""
    global is_healthy, simulated_failure

    if simulated_failure:
        PRIMARY_HEALTH.labels(node_id=NODE_ID).set(0)
        raise HTTPException(status_code=503, detail="Simulated failure")

    PRIMARY_HEALTH.labels(node_id=NODE_ID).set(1)
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "node_id": NODE_ID,
        "is_primary": IS_PRIMARY,
        "sequence": current_sequence,
        "healthy": is_healthy
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
async def status():
    """Detailed status including cluster state."""
    global current_sequence, is_healthy, simulated_failure

    # Get cluster info from Redis
    nodes = await redis_client.hgetall("cluster:nodes")
    current_primary = await redis_client.get("cluster:current_primary")

    return {
        "node_id": NODE_ID,
        "is_primary": IS_PRIMARY,
        "is_healthy": is_healthy and not simulated_failure,
        "sequence": current_sequence,
        "replication_lag_ms": REPLICATION_LAG_MS,
        "current_cluster_primary": current_primary,
        "cluster_nodes": nodes
    }


@app.post("/write")
async def write(request: WriteRequest):
    """
    Write data to the primary.
    Only the primary should accept writes.
    """
    global current_sequence

    if simulated_failure:
        REQUEST_COUNT.labels(service=SERVICE_NAME, operation="write", status="error").inc()
        raise HTTPException(status_code=503, detail="Primary is unavailable")

    if not IS_PRIMARY:
        REQUEST_COUNT.labels(service=SERVICE_NAME, operation="write", status="rejected").inc()
        raise HTTPException(status_code=400, detail="Cannot write to replica - redirect to primary")

    start_time = time.time()

    with tracer.start_as_current_span("db_write") as span:
        span.set_attribute("key", request.key)
        span.set_attribute("node_id", NODE_ID)

        # Increment sequence number
        current_sequence += 1

        # Store in Redis with sequence metadata
        data = {
            "value": request.value,
            "sequence": current_sequence,
            "written_at": datetime.utcnow().isoformat(),
            "written_by": NODE_ID
        }

        await redis_client.hset("data:store", request.key, str(data))
        await redis_client.set(f"sequence:{NODE_ID}", current_sequence)

        # Publish to replication channel
        await redis_client.publish("replication:updates", f"{request.key}:{current_sequence}")

        # Update metrics
        duration = time.time() - start_time
        WRITE_LATENCY.labels(service=SERVICE_NAME).observe(duration)
        WRITE_SEQUENCE.labels(node_id=NODE_ID).set(current_sequence)
        DATA_VERSION.labels(node_id=NODE_ID).set(current_sequence)
        REQUEST_COUNT.labels(service=SERVICE_NAME, operation="write", status="success").inc()

        span.set_attribute("sequence", current_sequence)
        span.set_attribute("duration_ms", duration * 1000)

        logger.info(f"Write completed: key={request.key} sequence={current_sequence}")

        return {
            "status": "ok",
            "key": request.key,
            "sequence": current_sequence,
            "node_id": NODE_ID,
            "duration_ms": round(duration * 1000, 2)
        }


@app.get("/read/{key}")
async def read(key: str):
    """Read data from the database."""
    if simulated_failure:
        REQUEST_COUNT.labels(service=SERVICE_NAME, operation="read", status="error").inc()
        raise HTTPException(status_code=503, detail="Node is unavailable")

    start_time = time.time()

    with tracer.start_as_current_span("db_read") as span:
        span.set_attribute("key", key)
        span.set_attribute("node_id", NODE_ID)

        # Simulate replication lag for reads
        if REPLICATION_LAG_MS > 0:
            await asyncio.sleep(REPLICATION_LAG_MS / 1000.0)

        data = await redis_client.hget("data:store", key)

        duration = time.time() - start_time
        READ_LATENCY.labels(service=SERVICE_NAME).observe(duration)

        if data is None:
            REQUEST_COUNT.labels(service=SERVICE_NAME, operation="read", status="not_found").inc()
            raise HTTPException(status_code=404, detail=f"Key not found: {key}")

        REQUEST_COUNT.labels(service=SERVICE_NAME, operation="read", status="success").inc()

        return {
            "key": key,
            "data": eval(data),  # Parse stored dict string
            "served_by": NODE_ID,
            "is_primary": IS_PRIMARY,
            "duration_ms": round(duration * 1000, 2)
        }


@app.get("/sequence")
async def get_sequence():
    """Get current sequence number for replication tracking."""
    return {
        "node_id": NODE_ID,
        "sequence": current_sequence,
        "is_primary": IS_PRIMARY
    }


@app.post("/admin/simulate-failure")
async def simulate_failure_endpoint(config: FailureConfig):
    """Admin endpoint to simulate primary failure."""
    global simulated_failure

    simulated_failure = config.enabled

    if config.enabled:
        PRIMARY_HEALTH.labels(node_id=NODE_ID).set(0)
        logger.warning(f"SIMULATED FAILURE ENABLED for {NODE_ID}")

        if config.duration_seconds > 0:
            # Auto-recover after duration
            asyncio.create_task(auto_recover(config.duration_seconds))
    else:
        PRIMARY_HEALTH.labels(node_id=NODE_ID).set(1)
        logger.info(f"SIMULATED FAILURE DISABLED for {NODE_ID}")

    return {
        "status": "ok",
        "failure_enabled": simulated_failure,
        "node_id": NODE_ID,
        "duration_seconds": config.duration_seconds if config.enabled else 0
    }


async def auto_recover(duration_seconds: int):
    """Automatically recover from simulated failure after duration."""
    global simulated_failure
    await asyncio.sleep(duration_seconds)
    simulated_failure = False
    PRIMARY_HEALTH.labels(node_id=NODE_ID).set(1)
    logger.info(f"AUTO-RECOVERY: {NODE_ID} is now healthy again")


@app.get("/admin/failure-status")
async def get_failure_status():
    """Get current failure simulation status."""
    return {
        "failure_enabled": simulated_failure,
        "node_id": NODE_ID,
        "is_primary": IS_PRIMARY
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
