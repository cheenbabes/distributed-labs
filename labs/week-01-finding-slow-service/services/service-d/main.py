"""
Service D - Leaf service with configurable latency injection.

This is the last service in the chain. It can have latency injected
via environment variables or runtime API calls.
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
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "service-d")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
LATENCY_MIN_MS = int(os.getenv("LATENCY_MIN_MS", "20"))
LATENCY_MAX_MS = int(os.getenv("LATENCY_MAX_MS", "50"))

# Latency injection state (mutable at runtime)
latency_config = {
    "enabled": os.getenv("INJECT_LATENCY", "false").lower() == "true",
    "ms": int(os.getenv("INJECTED_LATENCY_MS", "500"))
}

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
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Latency range: {LATENCY_MIN_MS}-{LATENCY_MAX_MS}ms")
    logger.info(f"Latency injection: {'enabled' if latency_config['enabled'] else 'disabled'}")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


class LatencyConfig(BaseModel):
    enabled: bool
    ms: int = 500


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/admin/latency")
async def get_latency_config():
    """Get current latency injection configuration."""
    return latency_config


@app.post("/admin/latency")
async def set_latency_config(config: LatencyConfig):
    """Set latency injection configuration at runtime."""
    latency_config["enabled"] = config.enabled
    latency_config["ms"] = config.ms
    logger.info(f"Latency injection updated: enabled={config.enabled}, ms={config.ms}")
    return latency_config


@app.get("/process")
async def process(request: Request):
    """Process request - this is the leaf service."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Add base latency (simulates real work)
    base_latency_ms = random.randint(LATENCY_MIN_MS, LATENCY_MAX_MS)

    # Add injected latency if enabled
    injected_latency_ms = 0
    if latency_config["enabled"]:
        injected_latency_ms = latency_config["ms"]
        current_span.set_attribute("injected_latency_ms", injected_latency_ms)
        current_span.set_attribute("latency_injection_enabled", True)

    total_latency_ms = base_latency_ms + injected_latency_ms

    # Actually sleep
    await asyncio.sleep(total_latency_ms / 1000.0)

    # Record span attributes
    current_span.set_attribute("base_latency_ms", base_latency_ms)
    current_span.set_attribute("total_latency_ms", total_latency_ms)

    duration = time.time() - start_time

    # Record metrics
    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/process",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/process"
    ).observe(duration)

    logger.info(f"Processed request trace_id={trace_id} latency={total_latency_ms}ms")

    return {
        "service": SERVICE_NAME,
        "processed": True,
        "latency_ms": total_latency_ms,
        "base_latency_ms": base_latency_ms,
        "injected_latency_ms": injected_latency_ms,
        "trace_id": trace_id
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
