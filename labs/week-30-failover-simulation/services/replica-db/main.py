"""
Replica Database Service - Read-only replica that can be promoted to primary.
Tracks replication lag and supports failover promotion.
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "replica-db")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
NODE_ID = os.getenv("NODE_ID", "replica-1")
IS_PRIMARY = os.getenv("IS_PRIMARY", "false").lower() == "true"
PRIMARY_URL = os.getenv("PRIMARY_URL", "http://primary-db:8001")
REPLICATION_LAG_MS = int(os.getenv("REPLICATION_LAG_MS", "50"))
PORT = int(os.getenv("PORT", "8002"))

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
    "replica_requests_total",
    "Total replica requests",
    ["service", "operation", "status"]
)
READ_LATENCY = Histogram(
    "replica_read_duration_seconds",
    "Replica read latency",
    ["service"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
REPLICA_HEALTH = Gauge(
    "replica_health",
    "Replica node health status (1=healthy, 0=unhealthy)",
    ["node_id"]
)
REPLICATION_LAG = Gauge(
    "replication_lag_ms",
    "Current replication lag in milliseconds",
    ["node_id"]
)
REPLICA_SEQUENCE = Gauge(
    "replica_sequence_number",
    "Current sequence number on replica",
    ["node_id"]
)
PRIMARY_SEQUENCE = Gauge(
    "primary_sequence_observed",
    "Last observed primary sequence number",
    ["node_id"]
)
IS_PROMOTED = Gauge(
    "replica_is_promoted",
    "Whether this replica has been promoted to primary",
    ["node_id"]
)

# Global state
redis_client: redis.Redis = None
current_sequence = 0
observed_primary_sequence = 0
is_healthy = True
simulated_failure = False
promoted_to_primary = False
replication_task = None


class WriteRequest(BaseModel):
    key: str
    value: Any


class FailureConfig(BaseModel):
    enabled: bool
    duration_seconds: int = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, current_sequence, replication_task
    logger.info(f"{SERVICE_NAME} ({NODE_ID}) starting up as REPLICA")
    logger.info(f"Configured replication lag: {REPLICATION_LAG_MS}ms")

    # Connect to Redis
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

    # Initialize sequence from Redis
    seq = await redis_client.get(f"sequence:{NODE_ID}")
    current_sequence = int(seq) if seq else 0

    # Register node
    await redis_client.hset("cluster:nodes", NODE_ID, f"primary=false,healthy=true,port={PORT}")

    # Set initial metrics
    REPLICA_HEALTH.labels(node_id=NODE_ID).set(1)
    REPLICATION_LAG.labels(node_id=NODE_ID).set(REPLICATION_LAG_MS)
    REPLICA_SEQUENCE.labels(node_id=NODE_ID).set(current_sequence)
    IS_PROMOTED.labels(node_id=NODE_ID).set(0)

    # Start replication listener
    replication_task = asyncio.create_task(replication_listener())

    yield

    # Cleanup
    if replication_task:
        replication_task.cancel()
    await redis_client.hdel("cluster:nodes", NODE_ID)
    await redis_client.close()
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


async def replication_listener():
    """Listen for replication updates from primary."""
    global current_sequence, observed_primary_sequence

    try:
        pubsub = redis_client.pubsub()
        await pubsub.subscribe("replication:updates")

        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = message["data"]
                    key, seq = data.rsplit(":", 1)
                    observed_primary_sequence = int(seq)
                    PRIMARY_SEQUENCE.labels(node_id=NODE_ID).set(observed_primary_sequence)

                    # Simulate replication lag
                    await asyncio.sleep(REPLICATION_LAG_MS / 1000.0)

                    # Update our sequence
                    current_sequence = observed_primary_sequence
                    REPLICA_SEQUENCE.labels(node_id=NODE_ID).set(current_sequence)
                    await redis_client.set(f"sequence:{NODE_ID}", current_sequence)

                    # Calculate actual replication lag
                    lag = observed_primary_sequence - current_sequence
                    REPLICATION_LAG.labels(node_id=NODE_ID).set(REPLICATION_LAG_MS + (lag * 10))

                    logger.debug(f"Replicated: key={key} sequence={seq}")
                except Exception as e:
                    logger.error(f"Replication error: {e}")
    except asyncio.CancelledError:
        logger.info("Replication listener cancelled")
    except Exception as e:
        logger.error(f"Replication listener failed: {e}")


@app.get("/health")
async def health():
    """Health check endpoint."""
    global is_healthy, simulated_failure

    if simulated_failure:
        REPLICA_HEALTH.labels(node_id=NODE_ID).set(0)
        raise HTTPException(status_code=503, detail="Simulated failure")

    REPLICA_HEALTH.labels(node_id=NODE_ID).set(1)
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "node_id": NODE_ID,
        "is_primary": promoted_to_primary,
        "sequence": current_sequence,
        "healthy": is_healthy,
        "replication_lag_ms": REPLICATION_LAG_MS
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
async def status():
    """Detailed status including replication state."""
    global current_sequence, observed_primary_sequence

    # Calculate replication lag
    lag = observed_primary_sequence - current_sequence if observed_primary_sequence > 0 else 0

    # Get cluster info from Redis
    nodes = await redis_client.hgetall("cluster:nodes")
    current_primary = await redis_client.get("cluster:current_primary")

    return {
        "node_id": NODE_ID,
        "is_primary": promoted_to_primary,
        "is_healthy": is_healthy and not simulated_failure,
        "local_sequence": current_sequence,
        "observed_primary_sequence": observed_primary_sequence,
        "replication_lag_sequences": lag,
        "replication_lag_ms": REPLICATION_LAG_MS,
        "current_cluster_primary": current_primary,
        "cluster_nodes": nodes
    }


@app.get("/read/{key}")
async def read(key: str):
    """Read data from the replica (with simulated lag)."""
    if simulated_failure:
        REQUEST_COUNT.labels(service=SERVICE_NAME, operation="read", status="error").inc()
        raise HTTPException(status_code=503, detail="Node is unavailable")

    start_time = time.time()

    with tracer.start_as_current_span("replica_read") as span:
        span.set_attribute("key", key)
        span.set_attribute("node_id", NODE_ID)
        span.set_attribute("is_promoted", promoted_to_primary)

        # Simulate replication lag for reads
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
            "is_primary": promoted_to_primary,
            "replication_lag_ms": REPLICATION_LAG_MS,
            "duration_ms": round(duration * 1000, 2)
        }


@app.post("/write")
async def write(request: WriteRequest):
    """
    Write endpoint - only works if promoted to primary.
    """
    global current_sequence

    if simulated_failure:
        REQUEST_COUNT.labels(service=SERVICE_NAME, operation="write", status="error").inc()
        raise HTTPException(status_code=503, detail="Node is unavailable")

    if not promoted_to_primary:
        REQUEST_COUNT.labels(service=SERVICE_NAME, operation="write", status="rejected").inc()
        raise HTTPException(
            status_code=400,
            detail="Cannot write to replica - this node has not been promoted"
        )

    start_time = time.time()

    with tracer.start_as_current_span("promoted_write") as span:
        span.set_attribute("key", request.key)
        span.set_attribute("node_id", NODE_ID)

        # Increment sequence number
        current_sequence += 1

        # Store in Redis with sequence metadata
        data = {
            "value": request.value,
            "sequence": current_sequence,
            "written_at": datetime.utcnow().isoformat(),
            "written_by": NODE_ID,
            "note": "written_after_failover"
        }

        await redis_client.hset("data:store", request.key, str(data))
        await redis_client.set(f"sequence:{NODE_ID}", current_sequence)

        # Publish to replication channel
        await redis_client.publish("replication:updates", f"{request.key}:{current_sequence}")

        duration = time.time() - start_time
        REPLICA_SEQUENCE.labels(node_id=NODE_ID).set(current_sequence)
        REQUEST_COUNT.labels(service=SERVICE_NAME, operation="write", status="success").inc()

        span.set_attribute("sequence", current_sequence)

        logger.info(f"Write completed on promoted replica: key={request.key} sequence={current_sequence}")

        return {
            "status": "ok",
            "key": request.key,
            "sequence": current_sequence,
            "node_id": NODE_ID,
            "duration_ms": round(duration * 1000, 2)
        }


@app.get("/sequence")
async def get_sequence():
    """Get current sequence number for replication tracking."""
    return {
        "node_id": NODE_ID,
        "sequence": current_sequence,
        "is_primary": promoted_to_primary,
        "observed_primary_sequence": observed_primary_sequence,
        "replication_lag_ms": REPLICATION_LAG_MS
    }


@app.post("/admin/promote")
async def promote_to_primary():
    """Promote this replica to become the new primary."""
    global promoted_to_primary

    if promoted_to_primary:
        return {
            "status": "already_promoted",
            "node_id": NODE_ID,
            "sequence": current_sequence
        }

    promoted_to_primary = True
    IS_PROMOTED.labels(node_id=NODE_ID).set(1)

    # Update cluster state in Redis
    await redis_client.set("cluster:current_primary", NODE_ID)
    await redis_client.hset("cluster:nodes", NODE_ID, f"primary=true,healthy=true,port={PORT}")

    logger.warning(f"NODE PROMOTED TO PRIMARY: {NODE_ID} at sequence {current_sequence}")

    return {
        "status": "promoted",
        "node_id": NODE_ID,
        "sequence": current_sequence,
        "message": f"Replica {NODE_ID} is now the primary"
    }


@app.post("/admin/demote")
async def demote_to_replica():
    """Demote this node back to replica status."""
    global promoted_to_primary

    if not promoted_to_primary:
        return {
            "status": "already_replica",
            "node_id": NODE_ID
        }

    promoted_to_primary = False
    IS_PROMOTED.labels(node_id=NODE_ID).set(0)

    # Update cluster state in Redis
    await redis_client.hset("cluster:nodes", NODE_ID, f"primary=false,healthy=true,port={PORT}")

    logger.info(f"NODE DEMOTED TO REPLICA: {NODE_ID}")

    return {
        "status": "demoted",
        "node_id": NODE_ID,
        "message": f"Node {NODE_ID} is now a replica"
    }


@app.post("/admin/simulate-failure")
async def simulate_failure_endpoint(config: FailureConfig):
    """Admin endpoint to simulate replica failure."""
    global simulated_failure

    simulated_failure = config.enabled

    if config.enabled:
        REPLICA_HEALTH.labels(node_id=NODE_ID).set(0)
        logger.warning(f"SIMULATED FAILURE ENABLED for {NODE_ID}")

        if config.duration_seconds > 0:
            asyncio.create_task(auto_recover(config.duration_seconds))
    else:
        REPLICA_HEALTH.labels(node_id=NODE_ID).set(1)
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
    REPLICA_HEALTH.labels(node_id=NODE_ID).set(1)
    logger.info(f"AUTO-RECOVERY: {NODE_ID} is now healthy again")


@app.get("/admin/failure-status")
async def get_failure_status():
    """Get current failure simulation status."""
    return {
        "failure_enabled": simulated_failure,
        "node_id": NODE_ID,
        "is_primary": promoted_to_primary
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
