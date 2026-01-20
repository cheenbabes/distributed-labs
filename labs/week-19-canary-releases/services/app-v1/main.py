"""
App v1 (Stable) - The stable version of the application.
Always returns successful responses with consistent latency.
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "app-v1")
SERVICE_VERSION = os.getenv("SERVICE_VERSION", "v1")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
LATENCY_MIN_MS = int(os.getenv("LATENCY_MIN_MS", "20"))
LATENCY_MAX_MS = int(os.getenv("LATENCY_MAX_MS", "50"))

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(SERVICE_NAME)

# OpenTelemetry setup
resource = Resource.create({
    "service.name": SERVICE_NAME,
    "service.version": SERVICE_VERSION
})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# Prometheus metrics
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "version", "method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["service", "version", "method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
SERVICE_INFO = Gauge(
    "service_info",
    "Service version information",
    ["service", "version"]
)
SERVICE_INFO.labels(service=SERVICE_NAME, version=SERVICE_VERSION).set(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} (version={SERVICE_VERSION}) starting up")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME, "version": SERVICE_VERSION}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/process")
async def api_process(request: Request):
    """Main API endpoint - simulates processing with consistent latency."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")
    current_span.set_attribute("service.version", SERVICE_VERSION)

    # Simulate processing time (stable, predictable latency)
    latency_ms = random.randint(LATENCY_MIN_MS, LATENCY_MAX_MS)
    await asyncio.sleep(latency_ms / 1000.0)
    current_span.set_attribute("processing_latency_ms", latency_ms)

    duration = time.time() - start_time

    # Record metrics
    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        version=SERVICE_VERSION,
        method="GET",
        endpoint="/api/process",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        version=SERVICE_VERSION,
        method="GET",
        endpoint="/api/process"
    ).observe(duration)

    logger.info(f"Request completed trace_id={trace_id} version={SERVICE_VERSION} duration={duration*1000:.0f}ms")

    return {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "status": "success",
        "duration_ms": round(duration * 1000, 2),
        "trace_id": trace_id,
        "message": "Processed by stable version"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
