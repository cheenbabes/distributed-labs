"""
Gateway - Entry point service that routes requests to the application service.
Provides a unified API and aggregates metrics.
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "gateway")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
APP_URL = os.getenv("APP_URL", "http://app:8001")

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
    "gateway_requests_total",
    "Total HTTP requests to gateway",
    ["method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "gateway_request_duration_seconds",
    "Gateway request latency",
    ["method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
UPSTREAM_ERRORS = Counter(
    "gateway_upstream_errors_total",
    "Errors from upstream services",
    ["service", "error_type"]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Application URL: {APP_URL}")
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


@app.get("/api/data")
async def get_data(request: Request):
    """Main API endpoint - fetches data through the application layer."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{APP_URL}/data")
            response.raise_for_status()
            result = response.json()
    except httpx.RequestError as e:
        UPSTREAM_ERRORS.labels(service="app", error_type="connection").inc()
        logger.error(f"Connection error to app service: {e}")
        raise HTTPException(status_code=503, detail="Application service unavailable")
    except httpx.HTTPStatusError as e:
        UPSTREAM_ERRORS.labels(service="app", error_type="http_error").inc()
        logger.error(f"HTTP error from app service: {e}")
        raise HTTPException(status_code=502, detail="Application service error")

    duration = time.time() - start_time

    # Record metrics
    REQUEST_COUNT.labels(method="GET", endpoint="/api/data", status="200").inc()
    REQUEST_LATENCY.labels(method="GET", endpoint="/api/data").observe(duration)

    logger.info(f"Request completed trace_id={trace_id} duration={duration*1000:.0f}ms cache_hit={result.get('cache_hit')}")

    return {
        "service": SERVICE_NAME,
        "trace_id": trace_id,
        "total_latency_ms": round(duration * 1000, 2),
        **result
    }


@app.get("/api/stats")
async def get_stats(request: Request):
    """Get cache and database statistics."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{APP_URL}/stats")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(status_code=503, detail="Unable to fetch stats")


@app.post("/api/admin/clear-cache")
async def clear_cache():
    """Clear the application cache."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{APP_URL}/admin/clear-cache")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Error clearing cache: {e}")
        raise HTTPException(status_code=503, detail="Unable to clear cache")


@app.post("/api/admin/fill-cache")
async def fill_cache(count: int = 1000):
    """Fill cache with test data to simulate memory pressure."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{APP_URL}/admin/fill-cache?count={count}")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Error filling cache: {e}")
        raise HTTPException(status_code=503, detail="Unable to fill cache")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
