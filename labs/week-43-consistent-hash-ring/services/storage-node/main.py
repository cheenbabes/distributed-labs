"""
Storage Node - Simple in-memory key-value store with metrics.

This service provides:
- In-memory key-value storage
- Metrics for storage operations
- Key enumeration for migration support
"""
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "storage-node")
NODE_ID = os.getenv("NODE_ID", "unknown")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(f"{SERVICE_NAME}-{NODE_ID}")

# OpenTelemetry setup
resource = Resource.create({
    "service.name": SERVICE_NAME,
    "service.instance.id": NODE_ID
})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# Prometheus metrics
STORE_OPS = Counter(
    "storage_node_store_operations_total",
    "Total store operations",
    ["node_id", "operation"]
)
GET_OPS = Counter(
    "storage_node_get_operations_total",
    "Total get operations",
    ["node_id", "result"]
)
KEY_COUNT = Gauge(
    "storage_node_key_count",
    "Current number of keys stored",
    ["node_id"]
)
STORAGE_SIZE_BYTES = Gauge(
    "storage_node_size_bytes",
    "Approximate storage size in bytes",
    ["node_id"]
)
OP_LATENCY = Histogram(
    "storage_node_operation_duration_seconds",
    "Operation latency",
    ["node_id", "operation"],
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1]
)

# In-memory storage
storage: dict[str, str] = {}


def update_metrics():
    """Update storage metrics."""
    KEY_COUNT.labels(node_id=NODE_ID).set(len(storage))
    # Approximate size calculation
    size = sum(len(k) + len(v) for k, v in storage.items())
    STORAGE_SIZE_BYTES.labels(node_id=NODE_ID).set(size)


class StoreRequest(BaseModel):
    key: str
    value: str


class MigrateRequest(BaseModel):
    keys: list[str]
    target_address: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Storage node {NODE_ID} starting up")
    update_metrics()
    yield
    logger.info(f"Storage node {NODE_ID} shutting down")


app = FastAPI(title=f"{SERVICE_NAME}-{NODE_ID}", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "node_id": NODE_ID,
        "key_count": len(storage)
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/store")
async def store(request: StoreRequest):
    """Store a key-value pair."""
    with tracer.start_as_current_span("store") as span:
        start_time = time.time()
        span.set_attribute("key", request.key)
        span.set_attribute("node_id", NODE_ID)

        is_update = request.key in storage
        storage[request.key] = request.value

        duration = time.time() - start_time

        STORE_OPS.labels(
            node_id=NODE_ID,
            operation="update" if is_update else "insert"
        ).inc()
        OP_LATENCY.labels(node_id=NODE_ID, operation="store").observe(duration)

        update_metrics()

        logger.info(f"Stored key '{request.key}' (update={is_update})")

        return {
            "stored": True,
            "key": request.key,
            "node_id": NODE_ID,
            "is_update": is_update
        }


@app.get("/get/{key}")
async def get(key: str):
    """Get a value by key."""
    with tracer.start_as_current_span("get") as span:
        start_time = time.time()
        span.set_attribute("key", key)
        span.set_attribute("node_id", NODE_ID)

        if key not in storage:
            GET_OPS.labels(node_id=NODE_ID, result="miss").inc()
            OP_LATENCY.labels(node_id=NODE_ID, operation="get").observe(time.time() - start_time)
            raise HTTPException(status_code=404, detail=f"Key '{key}' not found")

        value = storage[key]
        duration = time.time() - start_time

        GET_OPS.labels(node_id=NODE_ID, result="hit").inc()
        OP_LATENCY.labels(node_id=NODE_ID, operation="get").observe(duration)

        return {
            "key": key,
            "value": value,
            "node_id": NODE_ID
        }


@app.delete("/delete/{key}")
async def delete(key: str):
    """Delete a key."""
    with tracer.start_as_current_span("delete") as span:
        start_time = time.time()
        span.set_attribute("key", key)
        span.set_attribute("node_id", NODE_ID)

        if key not in storage:
            raise HTTPException(status_code=404, detail=f"Key '{key}' not found")

        del storage[key]
        duration = time.time() - start_time

        STORE_OPS.labels(node_id=NODE_ID, operation="delete").inc()
        OP_LATENCY.labels(node_id=NODE_ID, operation="delete").observe(duration)

        update_metrics()

        return {
            "deleted": True,
            "key": key,
            "node_id": NODE_ID
        }


@app.get("/keys")
async def list_keys():
    """List all keys (for migration support)."""
    with tracer.start_as_current_span("list_keys") as span:
        span.set_attribute("node_id", NODE_ID)
        span.set_attribute("key_count", len(storage))

        return {
            "node_id": NODE_ID,
            "keys": list(storage.keys()),
            "count": len(storage)
        }


@app.get("/dump")
async def dump():
    """Dump all key-value pairs (for migration/debugging)."""
    with tracer.start_as_current_span("dump") as span:
        span.set_attribute("node_id", NODE_ID)
        span.set_attribute("key_count", len(storage))

        return {
            "node_id": NODE_ID,
            "data": storage.copy(),
            "count": len(storage)
        }


@app.post("/bulk-store")
async def bulk_store(data: dict[str, str]):
    """Bulk store multiple key-value pairs (for migration)."""
    with tracer.start_as_current_span("bulk_store") as span:
        start_time = time.time()
        span.set_attribute("node_id", NODE_ID)
        span.set_attribute("key_count", len(data))

        for key, value in data.items():
            storage[key] = value

        duration = time.time() - start_time

        STORE_OPS.labels(node_id=NODE_ID, operation="bulk_insert").inc()
        OP_LATENCY.labels(node_id=NODE_ID, operation="bulk_store").observe(duration)

        update_metrics()

        logger.info(f"Bulk stored {len(data)} keys")

        return {
            "stored": True,
            "count": len(data),
            "node_id": NODE_ID
        }


@app.post("/bulk-delete")
async def bulk_delete(keys: list[str]):
    """Bulk delete multiple keys (for migration)."""
    with tracer.start_as_current_span("bulk_delete") as span:
        start_time = time.time()
        span.set_attribute("node_id", NODE_ID)
        span.set_attribute("key_count", len(keys))

        deleted = 0
        for key in keys:
            if key in storage:
                del storage[key]
                deleted += 1

        duration = time.time() - start_time

        STORE_OPS.labels(node_id=NODE_ID, operation="bulk_delete").inc()
        OP_LATENCY.labels(node_id=NODE_ID, operation="bulk_delete").observe(duration)

        update_metrics()

        logger.info(f"Bulk deleted {deleted} keys")

        return {
            "deleted": deleted,
            "requested": len(keys),
            "node_id": NODE_ID
        }


@app.get("/stats")
async def stats():
    """Get storage statistics."""
    size_bytes = sum(len(k) + len(v) for k, v in storage.items())
    avg_key_size = sum(len(k) for k in storage.keys()) / len(storage) if storage else 0
    avg_value_size = sum(len(v) for v in storage.values()) / len(storage) if storage else 0

    return {
        "node_id": NODE_ID,
        "key_count": len(storage),
        "size_bytes": size_bytes,
        "avg_key_size": avg_key_size,
        "avg_value_size": avg_value_size
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
