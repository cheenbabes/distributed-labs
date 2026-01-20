"""
Slow Backend Service - Simulates a slow dependency that can consume resources.

This service can be configured to introduce latency dynamically, simulating:
- A slow database query
- A slow external API call
- Resource exhaustion scenarios
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager

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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "backend-slow")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

# Default latency (can be changed at runtime)
LATENCY_MIN_MS = int(os.getenv("LATENCY_MIN_MS", "500"))
LATENCY_MAX_MS = int(os.getenv("LATENCY_MAX_MS", "2000"))

# Slow mode configuration
SLOW_MODE_ENABLED = os.getenv("SLOW_MODE_ENABLED", "true").lower() == "true"
SLOW_MODE_LATENCY_MS = int(os.getenv("SLOW_MODE_LATENCY_MS", "3000"))

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
    "Total requests",
    ["service", "status"]
)

REQUEST_LATENCY = Histogram(
    "backend_request_duration_seconds",
    "Request latency",
    ["service"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0]
)

SLOW_MODE_GAUGE = Gauge(
    "backend_slow_mode_enabled",
    "Whether slow mode is enabled",
    ["service"]
)

CONFIGURED_LATENCY = Gauge(
    "backend_configured_latency_ms",
    "Configured latency in milliseconds",
    ["service"]
)

# Runtime configuration
runtime_config = {
    "slow_mode_enabled": SLOW_MODE_ENABLED,
    "slow_mode_latency_ms": SLOW_MODE_LATENCY_MS,
    "latency_min_ms": LATENCY_MIN_MS,
    "latency_max_ms": LATENCY_MAX_MS,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Slow mode: {runtime_config['slow_mode_enabled']}")
    logger.info(f"Slow mode latency: {runtime_config['slow_mode_latency_ms']}ms")
    SLOW_MODE_GAUGE.labels(service=SERVICE_NAME).set(1 if runtime_config['slow_mode_enabled'] else 0)
    CONFIGURED_LATENCY.labels(service=SERVICE_NAME).set(runtime_config['slow_mode_latency_ms'])
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
async def status():
    """Return current configuration."""
    return {
        "service": SERVICE_NAME,
        "slow_mode_enabled": runtime_config["slow_mode_enabled"],
        "slow_mode_latency_ms": runtime_config["slow_mode_latency_ms"],
        "latency_range": f"{runtime_config['latency_min_ms']}-{runtime_config['latency_max_ms']}ms"
    }


@app.post("/admin/slow-mode")
async def set_slow_mode(request: Request):
    """Enable or disable slow mode."""
    data = await request.json()

    if "enabled" in data:
        runtime_config["slow_mode_enabled"] = data["enabled"]
        SLOW_MODE_GAUGE.labels(service=SERVICE_NAME).set(1 if data["enabled"] else 0)
        logger.info(f"Slow mode {'enabled' if data['enabled'] else 'disabled'}")

    if "latency_ms" in data:
        runtime_config["slow_mode_latency_ms"] = data["latency_ms"]
        CONFIGURED_LATENCY.labels(service=SERVICE_NAME).set(data["latency_ms"])
        logger.info(f"Slow mode latency set to {data['latency_ms']}ms")

    return runtime_config


@app.get("/process")
async def process(request: Request):
    """Process request with configurable latency."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Determine latency based on slow mode
    if runtime_config["slow_mode_enabled"]:
        # Add some variance to slow mode
        base_latency = runtime_config["slow_mode_latency_ms"]
        variance = int(base_latency * 0.2)  # 20% variance
        latency_ms = base_latency + random.randint(-variance, variance)
    else:
        latency_ms = random.randint(
            runtime_config["latency_min_ms"],
            runtime_config["latency_max_ms"]
        )

    # Simulate slow processing
    await asyncio.sleep(latency_ms / 1000.0)

    duration = time.time() - start_time

    current_span.set_attribute("backend.latency_ms", latency_ms)
    current_span.set_attribute("backend.type", "slow")
    current_span.set_attribute("backend.slow_mode", runtime_config["slow_mode_enabled"])

    REQUEST_COUNT.labels(service=SERVICE_NAME, status="success").inc()
    REQUEST_LATENCY.labels(service=SERVICE_NAME).observe(duration)

    logger.info(f"Request processed trace_id={trace_id} latency={latency_ms}ms slow_mode={runtime_config['slow_mode_enabled']}")

    return {
        "service": SERVICE_NAME,
        "type": "slow",
        "latency_ms": latency_ms,
        "slow_mode_enabled": runtime_config["slow_mode_enabled"],
        "trace_id": trace_id
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
