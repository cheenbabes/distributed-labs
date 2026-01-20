"""
Blue-Green Deployment App - Can be deployed as either blue or green version.
This simulates a web service that returns data and exposes version information.
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, Response
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "app")
APP_VERSION = os.getenv("APP_VERSION", "v1")
APP_COLOR = os.getenv("APP_COLOR", "blue")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
LATENCY_MIN_MS = int(os.getenv("LATENCY_MIN_MS", "10"))
LATENCY_MAX_MS = int(os.getenv("LATENCY_MAX_MS", "50"))
PORT = int(os.getenv("PORT", "8080"))

# Feature flag for simulating bugs
SIMULATE_BUG = os.getenv("SIMULATE_BUG", "false").lower() == "true"
BUG_ERROR_RATE = float(os.getenv("BUG_ERROR_RATE", "0.5"))

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(SERVICE_NAME)

# OpenTelemetry setup
resource = Resource.create({
    "service.name": f"{SERVICE_NAME}-{APP_COLOR}",
    "service.version": APP_VERSION,
    "deployment.color": APP_COLOR
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
    ["service", "version", "color", "method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["service", "version", "color", "method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
ERROR_COUNT = Counter(
    "http_errors_total",
    "Total HTTP errors",
    ["service", "version", "color", "error_type"]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} ({APP_COLOR}) version {APP_VERSION} starting up on port {PORT}")
    yield
    logger.info(f"{SERVICE_NAME} ({APP_COLOR}) shutting down")


app = FastAPI(title=f"{SERVICE_NAME}-{APP_COLOR}", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.middleware("http")
async def add_version_header(request: Request, call_next):
    """Add version and color headers to all responses."""
    response = await call_next(request)
    response.headers["X-App-Version"] = APP_VERSION
    response.headers["X-App-Color"] = APP_COLOR
    response.headers["X-Served-By"] = f"{APP_COLOR}-{APP_VERSION}"
    return response


@app.get("/health")
async def health():
    """Health check endpoint for load balancer."""
    # If bug is simulated, sometimes fail health checks
    if SIMULATE_BUG and random.random() < 0.3:
        return Response(
            content='{"status": "unhealthy", "reason": "simulated bug"}',
            status_code=503,
            media_type="application/json"
        )
    return {
        "status": "healthy",
        "service": SERVICE_NAME,
        "version": APP_VERSION,
        "color": APP_COLOR,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/version")
async def version():
    """Return version information."""
    return {
        "service": SERVICE_NAME,
        "version": APP_VERSION,
        "color": APP_COLOR,
        "build_time": os.getenv("BUILD_TIME", "unknown"),
        "git_sha": os.getenv("GIT_SHA", "unknown")
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/data")
async def api_data(request: Request):
    """Main API endpoint that returns mock data."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Add deployment info to span
    current_span.set_attribute("app.version", APP_VERSION)
    current_span.set_attribute("app.color", APP_COLOR)
    current_span.set_attribute("deployment.environment", APP_COLOR)

    # Simulate processing time
    latency_ms = random.randint(LATENCY_MIN_MS, LATENCY_MAX_MS)
    await asyncio.sleep(latency_ms / 1000.0)
    current_span.set_attribute("processing_latency_ms", latency_ms)

    # Simulate bug if enabled
    if SIMULATE_BUG and random.random() < BUG_ERROR_RATE:
        ERROR_COUNT.labels(
            service=SERVICE_NAME,
            version=APP_VERSION,
            color=APP_COLOR,
            error_type="simulated_bug"
        ).inc()
        current_span.set_attribute("error", True)
        current_span.set_attribute("error.type", "simulated_bug")

        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            version=APP_VERSION,
            color=APP_COLOR,
            method="GET",
            endpoint="/api/data",
            status="500"
        ).inc()

        return Response(
            content='{"error": "Internal server error", "reason": "Bug in version ' + APP_VERSION + '"}',
            status_code=500,
            media_type="application/json"
        )

    duration = time.time() - start_time

    # Record metrics
    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        version=APP_VERSION,
        color=APP_COLOR,
        method="GET",
        endpoint="/api/data",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        version=APP_VERSION,
        color=APP_COLOR,
        method="GET",
        endpoint="/api/data"
    ).observe(duration)

    logger.info(f"Request completed color={APP_COLOR} version={APP_VERSION} trace_id={trace_id} duration={duration*1000:.0f}ms")

    # Generate mock data based on version
    data_items = [
        {"id": 1, "name": "Item Alpha", "status": "active"},
        {"id": 2, "name": "Item Beta", "status": "pending"},
        {"id": 3, "name": "Item Gamma", "status": "active"},
    ]

    # v2 adds extra data field
    if APP_VERSION == "v2":
        for item in data_items:
            item["updated_at"] = datetime.utcnow().isoformat()
            item["version_added"] = "v2"

    return {
        "service": SERVICE_NAME,
        "version": APP_VERSION,
        "color": APP_COLOR,
        "trace_id": trace_id,
        "processing_time_ms": round(duration * 1000, 2),
        "data": data_items,
        "metadata": {
            "served_by": f"{APP_COLOR}-{APP_VERSION}",
            "timestamp": datetime.utcnow().isoformat()
        }
    }


@app.post("/admin/bug")
async def set_bug(enabled: bool = True, error_rate: float = 0.5):
    """Enable or disable bug simulation (for testing rollback)."""
    global SIMULATE_BUG, BUG_ERROR_RATE
    SIMULATE_BUG = enabled
    BUG_ERROR_RATE = error_rate
    logger.warning(f"Bug simulation set: enabled={enabled}, error_rate={error_rate}")
    return {
        "simulate_bug": SIMULATE_BUG,
        "error_rate": BUG_ERROR_RATE,
        "color": APP_COLOR,
        "version": APP_VERSION
    }


@app.get("/admin/bug")
async def get_bug():
    """Get current bug simulation status."""
    return {
        "simulate_bug": SIMULATE_BUG,
        "error_rate": BUG_ERROR_RATE,
        "color": APP_COLOR,
        "version": APP_VERSION
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
