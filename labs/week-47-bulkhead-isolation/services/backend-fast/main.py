"""
Fast Backend Service - Responds quickly with minimal latency.
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
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "backend-fast")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
LATENCY_MIN_MS = int(os.getenv("LATENCY_MIN_MS", "10"))
LATENCY_MAX_MS = int(os.getenv("LATENCY_MAX_MS", "30"))

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
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Latency range: {LATENCY_MIN_MS}-{LATENCY_MAX_MS}ms")
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


@app.get("/process")
async def process(request: Request):
    """Process request with minimal latency."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Simulate fast processing
    latency_ms = random.randint(LATENCY_MIN_MS, LATENCY_MAX_MS)
    await asyncio.sleep(latency_ms / 1000.0)

    duration = time.time() - start_time

    current_span.set_attribute("backend.latency_ms", latency_ms)
    current_span.set_attribute("backend.type", "fast")

    REQUEST_COUNT.labels(service=SERVICE_NAME, status="success").inc()
    REQUEST_LATENCY.labels(service=SERVICE_NAME).observe(duration)

    logger.info(f"Request processed trace_id={trace_id} latency={latency_ms}ms")

    return {
        "service": SERVICE_NAME,
        "type": "fast",
        "latency_ms": latency_ms,
        "trace_id": trace_id
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
