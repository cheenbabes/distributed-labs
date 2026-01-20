"""
Backend - Service with configurable latency distribution.

Simulates a backend with tail latency:
- p50: ~30ms (normal case)
- p95: ~100ms (slow case)
- p99: ~300ms (very slow case)

This distribution is typical of services with occasional slow requests
due to GC pauses, cache misses, or resource contention.
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Query
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "backend")
INSTANCE_ID = os.getenv("INSTANCE_ID", "backend-1")
PORT = int(os.getenv("PORT", "8001"))
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

# Latency distribution configuration
LATENCY_P50_MS = int(os.getenv("LATENCY_P50_MS", "30"))
LATENCY_P95_MS = int(os.getenv("LATENCY_P95_MS", "100"))
LATENCY_P99_MS = int(os.getenv("LATENCY_P99_MS", "300"))

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(f"{SERVICE_NAME}-{INSTANCE_ID}")

# OpenTelemetry setup
resource = Resource.create({
    "service.name": SERVICE_NAME,
    "service.instance.id": INSTANCE_ID
})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# Prometheus metrics
REQUEST_COUNT = Counter(
    "backend_requests_total",
    "Total HTTP requests",
    ["instance", "status"]
)
REQUEST_LATENCY = Histogram(
    "backend_request_duration_seconds",
    "HTTP request latency",
    ["instance"],
    buckets=[0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0]
)
SIMULATED_LATENCY = Histogram(
    "backend_simulated_latency_seconds",
    "Simulated processing latency",
    ["instance", "latency_bucket"],
    buckets=[0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0]
)


class LatencyConfig:
    """Configurable latency distribution."""
    def __init__(self):
        self.p50_ms = LATENCY_P50_MS
        self.p95_ms = LATENCY_P95_MS
        self.p99_ms = LATENCY_P99_MS
        self.force_slow = False
        self.force_slow_ms = 500

config = LatencyConfig()


def get_latency_ms() -> tuple[int, str]:
    """
    Generate a latency value based on configured distribution.

    Returns:
        tuple of (latency_ms, bucket_name)
    """
    if config.force_slow:
        return config.force_slow_ms, "forced"

    # Generate latency using exponential distribution to simulate tail latency
    # This creates a realistic distribution with occasional slow requests

    roll = random.random()

    if roll < 0.50:
        # 50% of requests: p50 latency with some variance
        base = config.p50_ms * 0.7
        variance = config.p50_ms * 0.6
        latency = int(base + random.random() * variance)
        return latency, "p50"
    elif roll < 0.95:
        # 45% of requests: between p50 and p95
        base = config.p50_ms
        range_size = config.p95_ms - config.p50_ms
        latency = int(base + random.random() * range_size)
        return latency, "p50-p95"
    elif roll < 0.99:
        # 4% of requests: between p95 and p99
        base = config.p95_ms
        range_size = config.p99_ms - config.p95_ms
        latency = int(base + random.random() * range_size)
        return latency, "p95-p99"
    else:
        # 1% of requests: above p99 (tail latency)
        base = config.p99_ms
        extra = random.expovariate(1.0 / config.p99_ms)  # Exponential tail
        latency = int(base + extra * 0.5)
        return min(latency, config.p99_ms * 3), "p99+"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{INSTANCE_ID} starting up on port {PORT}")
    logger.info(f"Latency distribution: p50={config.p50_ms}ms, p95={config.p95_ms}ms, p99={config.p99_ms}ms")
    yield
    logger.info(f"{INSTANCE_ID} shutting down")


app = FastAPI(title=f"{SERVICE_NAME}-{INSTANCE_ID}", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "instance": INSTANCE_ID
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/process")
async def process(request: Request, request_id: Optional[str] = None):
    """
    Process a request with simulated latency.

    The latency distribution simulates real-world tail latency.
    """
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Get latency from distribution
    latency_ms, bucket = get_latency_ms()

    current_span.set_attribute("instance_id", INSTANCE_ID)
    current_span.set_attribute("simulated_latency_ms", latency_ms)
    current_span.set_attribute("latency_bucket", bucket)
    if request_id:
        current_span.set_attribute("request_id", request_id)

    # Simulate processing time
    with tracer.start_as_current_span("simulate_work") as work_span:
        work_span.set_attribute("latency_ms", latency_ms)
        await asyncio.sleep(latency_ms / 1000.0)

    duration = time.time() - start_time

    REQUEST_COUNT.labels(instance=INSTANCE_ID, status="200").inc()
    REQUEST_LATENCY.labels(instance=INSTANCE_ID).observe(duration)
    SIMULATED_LATENCY.labels(instance=INSTANCE_ID, latency_bucket=bucket).observe(latency_ms / 1000.0)

    logger.info(f"Request completed instance={INSTANCE_ID} trace_id={trace_id} "
                f"latency={latency_ms}ms bucket={bucket}")

    return {
        "service": SERVICE_NAME,
        "instance": INSTANCE_ID,
        "request_id": request_id,
        "trace_id": trace_id,
        "simulated_latency_ms": latency_ms,
        "latency_bucket": bucket,
        "total_duration_ms": round(duration * 1000, 2)
    }


@app.get("/admin/config")
async def get_config():
    """Get current latency configuration."""
    return {
        "instance": INSTANCE_ID,
        "latency_distribution": {
            "p50_ms": config.p50_ms,
            "p95_ms": config.p95_ms,
            "p99_ms": config.p99_ms
        },
        "force_slow": {
            "enabled": config.force_slow,
            "latency_ms": config.force_slow_ms
        }
    }


@app.post("/admin/config")
async def update_config(request: Request):
    """Update latency configuration."""
    data = await request.json()

    if "p50_ms" in data:
        config.p50_ms = int(data["p50_ms"])
    if "p95_ms" in data:
        config.p95_ms = int(data["p95_ms"])
    if "p99_ms" in data:
        config.p99_ms = int(data["p99_ms"])
    if "force_slow" in data:
        config.force_slow = data["force_slow"]
    if "force_slow_ms" in data:
        config.force_slow_ms = int(data["force_slow_ms"])

    logger.info(f"Config updated: p50={config.p50_ms}ms, p95={config.p95_ms}ms, "
                f"p99={config.p99_ms}ms, force_slow={config.force_slow}")

    return await get_config()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
