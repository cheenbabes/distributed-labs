"""
Shard - Simulates a shard in a distributed data store.
Tracks per-key access counts and simulates load-dependent latency.
"""
import asyncio
import logging
import os
import random
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from threading import Lock

from fastapi import FastAPI, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from pydantic import BaseModel

# Configuration
SHARD_ID = int(os.getenv("SHARD_ID", "0"))
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", f"shard-{SHARD_ID}")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab39-otel-collector:4317")
PORT = int(os.getenv("PORT", "9000"))
BASE_LATENCY_MS = int(os.getenv("BASE_LATENCY_MS", "5"))
LOAD_LATENCY_FACTOR_MS = float(os.getenv("LOAD_LATENCY_FACTOR_MS", "0.5"))

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
    "shard_requests_total",
    "Total requests to this shard",
    ["shard_id", "operation", "key"]
)
REQUEST_LATENCY = Histogram(
    "shard_request_duration_seconds",
    "Shard request latency",
    ["shard_id", "operation"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
KEY_ACCESS_COUNT = Counter(
    "shard_key_access_total",
    "Total accesses per key on this shard",
    ["shard_id", "key"]
)
ACTIVE_REQUESTS = Gauge(
    "shard_active_requests",
    "Number of currently active requests on this shard",
    ["shard_id"]
)
HOT_KEY_DETECTED = Gauge(
    "shard_hot_key_detected",
    "Indicates if a hot key is detected (1) or not (0)",
    ["shard_id", "key"]
)

# In-memory key-value store and access tracking
kv_store: dict[str, dict] = {}
access_counts: dict[str, int] = defaultdict(int)
recent_accesses: list[tuple[float, str]] = []
access_lock = Lock()
active_request_count = 0
request_count_lock = Lock()

# Hot key detection thresholds
HOT_KEY_WINDOW_SECONDS = 10
HOT_KEY_THRESHOLD_RATIO = 0.3  # Key is hot if it gets >30% of traffic in window


class KeyValue(BaseModel):
    key: str
    value: dict


def detect_hot_keys():
    """Detect keys that are receiving disproportionate traffic."""
    now = time.time()
    window_start = now - HOT_KEY_WINDOW_SECONDS

    with access_lock:
        # Filter to recent window
        recent = [(t, k) for t, k in recent_accesses if t > window_start]
        recent_accesses.clear()
        recent_accesses.extend(recent)

        if len(recent) < 10:
            return []

        # Count accesses per key in window
        key_counts = defaultdict(int)
        for _, key in recent:
            key_counts[key] += 1

        total = len(recent)
        hot_keys = []
        for key, count in key_counts.items():
            ratio = count / total
            if ratio > HOT_KEY_THRESHOLD_RATIO:
                hot_keys.append((key, ratio, count))
                HOT_KEY_DETECTED.labels(shard_id=str(SHARD_ID), key=key).set(1)
            else:
                HOT_KEY_DETECTED.labels(shard_id=str(SHARD_ID), key=key).set(0)

        return hot_keys


def calculate_latency():
    """Calculate latency based on current load."""
    global active_request_count
    with request_count_lock:
        load = active_request_count

    # Base latency + load-dependent component
    # More concurrent requests = higher latency (simulating contention)
    load_latency = load * LOAD_LATENCY_FACTOR_MS
    jitter = random.uniform(-2, 2)
    return max(1, BASE_LATENCY_MS + load_latency + jitter)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Shard {SHARD_ID} starting on port {PORT}")
    yield
    logger.info(f"Shard {SHARD_ID} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "shard_id": SHARD_ID}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/stats")
async def stats():
    """Return shard statistics including hot key detection."""
    hot_keys = detect_hot_keys()

    with access_lock:
        total_accesses = sum(access_counts.values())
        top_keys = sorted(access_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "shard_id": SHARD_ID,
        "total_keys": len(kv_store),
        "total_accesses": total_accesses,
        "active_requests": active_request_count,
        "top_keys": [{"key": k, "count": c} for k, c in top_keys],
        "hot_keys": [{"key": k, "ratio": r, "count": c} for k, r, c in hot_keys]
    }


@app.get("/data/{key}")
async def get_key(key: str):
    """Get a value by key."""
    global active_request_count
    start_time = time.time()

    current_span = trace.get_current_span()
    current_span.set_attribute("shard.id", SHARD_ID)
    current_span.set_attribute("key", key)

    # Track active requests
    with request_count_lock:
        active_request_count += 1
        ACTIVE_REQUESTS.labels(shard_id=str(SHARD_ID)).set(active_request_count)

    try:
        # Track access
        with access_lock:
            access_counts[key] += 1
            recent_accesses.append((time.time(), key))

        KEY_ACCESS_COUNT.labels(shard_id=str(SHARD_ID), key=key).inc()
        REQUEST_COUNT.labels(shard_id=str(SHARD_ID), operation="get", key=key).inc()

        # Simulate processing with load-dependent latency
        latency_ms = calculate_latency()
        await asyncio.sleep(latency_ms / 1000.0)
        current_span.set_attribute("simulated_latency_ms", latency_ms)

        # Get value
        if key in kv_store:
            value = kv_store[key]
            current_span.set_attribute("cache_hit", True)
            result = {"key": key, "value": value, "shard_id": SHARD_ID, "found": True}
        else:
            # Auto-create value for demo purposes
            value = {"data": f"auto-generated-{key}", "created_at": time.time()}
            kv_store[key] = value
            current_span.set_attribute("cache_hit", False)
            result = {"key": key, "value": value, "shard_id": SHARD_ID, "found": False, "auto_created": True}

        return result

    finally:
        with request_count_lock:
            active_request_count -= 1
            ACTIVE_REQUESTS.labels(shard_id=str(SHARD_ID)).set(active_request_count)

        duration = time.time() - start_time
        REQUEST_LATENCY.labels(shard_id=str(SHARD_ID), operation="get").observe(duration)


@app.post("/data")
async def set_key(kv: KeyValue):
    """Set a key-value pair."""
    global active_request_count
    start_time = time.time()

    current_span = trace.get_current_span()
    current_span.set_attribute("shard.id", SHARD_ID)
    current_span.set_attribute("key", kv.key)

    with request_count_lock:
        active_request_count += 1
        ACTIVE_REQUESTS.labels(shard_id=str(SHARD_ID)).set(active_request_count)

    try:
        with access_lock:
            access_counts[kv.key] += 1
            recent_accesses.append((time.time(), kv.key))

        KEY_ACCESS_COUNT.labels(shard_id=str(SHARD_ID), key=kv.key).inc()
        REQUEST_COUNT.labels(shard_id=str(SHARD_ID), operation="set", key=kv.key).inc()

        # Simulate processing
        latency_ms = calculate_latency()
        await asyncio.sleep(latency_ms / 1000.0)
        current_span.set_attribute("simulated_latency_ms", latency_ms)

        kv_store[kv.key] = kv.value

        return {"key": kv.key, "shard_id": SHARD_ID, "status": "stored"}

    finally:
        with request_count_lock:
            active_request_count -= 1
            ACTIVE_REQUESTS.labels(shard_id=str(SHARD_ID)).set(active_request_count)

        duration = time.time() - start_time
        REQUEST_LATENCY.labels(shard_id=str(SHARD_ID), operation="set").observe(duration)


@app.post("/admin/reset")
async def reset_stats():
    """Reset shard statistics."""
    global access_counts, recent_accesses, kv_store

    with access_lock:
        access_counts.clear()
        recent_accesses.clear()

    kv_store.clear()

    return {"status": "reset", "shard_id": SHARD_ID}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
