"""
Replica - Simple in-memory key-value store node.

Each replica stores data independently and supports:
- Versioned writes (for conflict resolution)
- Read-your-writes within the same replica
- Configurable artificial latency
- Failure injection for testing quorum behavior
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from enum import Enum
from typing import Optional, Dict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
REPLICA_ID = int(os.getenv("REPLICA_ID", "1"))
REPLICA_PORT = int(os.getenv("REPLICA_PORT", "9001"))
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", f"replica-{REPLICA_ID}")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
LATENCY_MIN_MS = int(os.getenv("LATENCY_MIN_MS", "5"))
LATENCY_MAX_MS = int(os.getenv("LATENCY_MAX_MS", "20"))
FAILURE_MODE = os.getenv("FAILURE_MODE", "none")  # none, timeout, error, partial

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
WRITE_COUNT = Counter(
    "replica_writes_total",
    "Total write operations",
    ["replica_id", "status"]
)
READ_COUNT = Counter(
    "replica_reads_total",
    "Total read operations",
    ["replica_id", "status"]
)
OPERATION_LATENCY = Histogram(
    "replica_operation_latency_seconds",
    "Operation latency",
    ["replica_id", "operation"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
STORED_KEYS = Gauge(
    "replica_stored_keys",
    "Number of stored keys",
    ["replica_id"]
)
FAILURE_MODE_GAUGE = Gauge(
    "replica_failure_mode",
    "Current failure mode (0=none, 1=timeout, 2=error, 3=partial)",
    ["replica_id"]
)


class FailureMode(str, Enum):
    NONE = "none"
    TIMEOUT = "timeout"  # Simulate slow/hanging replica
    ERROR = "error"  # Return errors for all operations
    PARTIAL = "partial"  # Randomly fail some operations


# In-memory storage: key -> {value, version}
storage: Dict[str, dict] = {}

# Current failure mode (can be changed at runtime)
current_failure_mode = FailureMode(FAILURE_MODE)


class WriteRequest(BaseModel):
    key: str
    value: str
    version: int


class WriteResponse(BaseModel):
    key: str
    version: int
    replica_id: int
    success: bool


class ReadResponse(BaseModel):
    key: str
    value: Optional[str]
    version: Optional[int]
    replica_id: int


class FailureModeRequest(BaseModel):
    mode: str  # none, timeout, error, partial
    timeout_ms: Optional[int] = None  # For timeout mode


failure_timeout_ms = 10000  # Default timeout duration


def get_failure_mode_value(mode: FailureMode) -> int:
    mapping = {
        FailureMode.NONE: 0,
        FailureMode.TIMEOUT: 1,
        FailureMode.ERROR: 2,
        FailureMode.PARTIAL: 3
    }
    return mapping.get(mode, 0)


async def apply_latency():
    """Apply configured artificial latency."""
    latency_ms = random.randint(LATENCY_MIN_MS, LATENCY_MAX_MS)
    await asyncio.sleep(latency_ms / 1000.0)
    return latency_ms


async def check_failure_mode(operation: str) -> bool:
    """
    Check and apply failure mode.
    Returns True if operation should proceed, False if it should fail.
    """
    global current_failure_mode, failure_timeout_ms

    if current_failure_mode == FailureMode.NONE:
        return True

    if current_failure_mode == FailureMode.TIMEOUT:
        # Simulate hanging - sleep for a long time
        logger.warning(f"Simulating timeout for {operation}")
        await asyncio.sleep(failure_timeout_ms / 1000.0)
        return True  # Eventually respond

    if current_failure_mode == FailureMode.ERROR:
        logger.warning(f"Simulating error for {operation}")
        return False

    if current_failure_mode == FailureMode.PARTIAL:
        # 50% chance of failure
        if random.random() < 0.5:
            logger.warning(f"Simulating partial failure for {operation}")
            return False
        return True

    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up on port {REPLICA_PORT}")
    logger.info(f"Latency config: {LATENCY_MIN_MS}-{LATENCY_MAX_MS}ms")
    logger.info(f"Failure mode: {current_failure_mode}")

    FAILURE_MODE_GAUGE.labels(replica_id=str(REPLICA_ID)).set(
        get_failure_mode_value(current_failure_mode)
    )
    STORED_KEYS.labels(replica_id=str(REPLICA_ID)).set(0)

    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "replica_id": REPLICA_ID,
        "stored_keys": len(storage),
        "failure_mode": current_failure_mode.value
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/write")
async def write(request: WriteRequest) -> WriteResponse:
    """
    Write a key-value pair with version.
    Only updates if the incoming version is newer than existing.
    """
    start_time = time.time()
    current_span = trace.get_current_span()
    current_span.set_attribute("replica_id", REPLICA_ID)
    current_span.set_attribute("key", request.key)
    current_span.set_attribute("version", request.version)

    # Apply artificial latency
    latency_ms = await apply_latency()
    current_span.set_attribute("latency_ms", latency_ms)

    # Check failure mode
    should_proceed = await check_failure_mode("write")
    if not should_proceed:
        WRITE_COUNT.labels(replica_id=str(REPLICA_ID), status="failed").inc()
        raise HTTPException(status_code=503, detail="Replica in failure mode")

    # Check version - only accept newer versions
    existing = storage.get(request.key)
    if existing and existing["version"] >= request.version:
        logger.info(f"Rejected write key={request.key} version={request.version} (existing={existing['version']})")
        WRITE_COUNT.labels(replica_id=str(REPLICA_ID), status="rejected").inc()
        return WriteResponse(
            key=request.key,
            version=existing["version"],
            replica_id=REPLICA_ID,
            success=False
        )

    # Store the value
    storage[request.key] = {
        "value": request.value,
        "version": request.version,
        "timestamp": time.time()
    }

    duration = time.time() - start_time
    OPERATION_LATENCY.labels(replica_id=str(REPLICA_ID), operation="write").observe(duration)
    WRITE_COUNT.labels(replica_id=str(REPLICA_ID), status="success").inc()
    STORED_KEYS.labels(replica_id=str(REPLICA_ID)).set(len(storage))

    logger.info(f"Write key={request.key} version={request.version} duration={duration*1000:.0f}ms")

    return WriteResponse(
        key=request.key,
        version=request.version,
        replica_id=REPLICA_ID,
        success=True
    )


@app.get("/read/{key}")
async def read(key: str) -> ReadResponse:
    """Read a value by key."""
    start_time = time.time()
    current_span = trace.get_current_span()
    current_span.set_attribute("replica_id", REPLICA_ID)
    current_span.set_attribute("key", key)

    # Apply artificial latency
    latency_ms = await apply_latency()
    current_span.set_attribute("latency_ms", latency_ms)

    # Check failure mode
    should_proceed = await check_failure_mode("read")
    if not should_proceed:
        READ_COUNT.labels(replica_id=str(REPLICA_ID), status="failed").inc()
        raise HTTPException(status_code=503, detail="Replica in failure mode")

    # Get the value
    data = storage.get(key)

    duration = time.time() - start_time
    OPERATION_LATENCY.labels(replica_id=str(REPLICA_ID), operation="read").observe(duration)

    if data:
        READ_COUNT.labels(replica_id=str(REPLICA_ID), status="hit").inc()
        current_span.set_attribute("version", data["version"])
        logger.info(f"Read key={key} version={data['version']} duration={duration*1000:.0f}ms")
        return ReadResponse(
            key=key,
            value=data["value"],
            version=data["version"],
            replica_id=REPLICA_ID
        )
    else:
        READ_COUNT.labels(replica_id=str(REPLICA_ID), status="miss").inc()
        logger.info(f"Read key={key} not_found duration={duration*1000:.0f}ms")
        raise HTTPException(status_code=404, detail=f"Key '{key}' not found")


@app.get("/data")
async def get_all_data():
    """Debug endpoint to see all stored data."""
    return {
        "replica_id": REPLICA_ID,
        "key_count": len(storage),
        "data": {k: {"value": v["value"], "version": v["version"]} for k, v in storage.items()}
    }


@app.delete("/data")
async def clear_data():
    """Clear all stored data."""
    storage.clear()
    STORED_KEYS.labels(replica_id=str(REPLICA_ID)).set(0)
    return {"status": "cleared", "replica_id": REPLICA_ID}


@app.get("/admin/failure")
async def get_failure_mode():
    """Get current failure mode."""
    return {
        "replica_id": REPLICA_ID,
        "mode": current_failure_mode.value,
        "timeout_ms": failure_timeout_ms
    }


@app.post("/admin/failure")
async def set_failure_mode(request: FailureModeRequest):
    """Set failure mode for this replica."""
    global current_failure_mode, failure_timeout_ms

    try:
        current_failure_mode = FailureMode(request.mode)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {request.mode}. Valid: none, timeout, error, partial")

    if request.timeout_ms:
        failure_timeout_ms = request.timeout_ms

    FAILURE_MODE_GAUGE.labels(replica_id=str(REPLICA_ID)).set(
        get_failure_mode_value(current_failure_mode)
    )

    logger.info(f"Failure mode set to: {current_failure_mode.value}")

    return {
        "replica_id": REPLICA_ID,
        "mode": current_failure_mode.value,
        "timeout_ms": failure_timeout_ms
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=REPLICA_PORT)
