"""
Service A - Middle tier service that calls downstream.
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "service-a")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
DOWNSTREAM_URL = os.getenv("DOWNSTREAM_URL", "http://service-b:8002")
LATENCY_MIN_MS = int(os.getenv("LATENCY_MIN_MS", "10"))
LATENCY_MAX_MS = int(os.getenv("LATENCY_MAX_MS", "30"))
PORT = int(os.getenv("PORT", "8001"))

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
    logger.info(f"{SERVICE_NAME} starting up on port {PORT}")
    logger.info(f"Downstream URL: {DOWNSTREAM_URL}")
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
    """Process request and call downstream service."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Simulate processing time
    latency_ms = random.randint(LATENCY_MIN_MS, LATENCY_MAX_MS)
    await asyncio.sleep(latency_ms / 1000.0)
    current_span.set_attribute("processing_latency_ms", latency_ms)

    # Call downstream service
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{DOWNSTREAM_URL}/process")
        downstream_result = response.json()

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

    logger.info(f"Processed trace_id={trace_id} latency={latency_ms}ms total={duration*1000:.0f}ms")

    return {
        "service": SERVICE_NAME,
        "processed": True,
        "latency_ms": latency_ms,
        "downstream": downstream_result
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
