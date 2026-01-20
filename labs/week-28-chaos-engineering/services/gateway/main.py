"""
Gateway Service - Entry point for the e-commerce application.

Routes requests to downstream services and integrates with chaos controller
to apply chaos based on centralized configuration.
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
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
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab28-otel-collector:4317")
ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://lab28-order-service:8001")
CHAOS_CONTROLLER_URL = os.getenv("CHAOS_CONTROLLER_URL", "http://lab28-chaos-controller:8080")

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
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)
CHAOS_INJECTED = Counter(
    "chaos_injected_total",
    "Total chaos injections applied",
    ["service", "chaos_type"]
)
ERROR_COUNT = Counter(
    "errors_total",
    "Total errors",
    ["service", "error_type"]
)

# Chaos state cache
chaos_state = {
    "chaos_enabled": False,
    "latency_ms": 0,
    "error_rate": 0.0,
    "last_fetched": 0
}


async def fetch_chaos_state():
    """Fetch current chaos state from controller."""
    global chaos_state
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{CHAOS_CONTROLLER_URL}/chaos/state/{SERVICE_NAME}")
            if response.status_code == 200:
                chaos_state = response.json()
                chaos_state["last_fetched"] = time.time()
    except Exception as e:
        logger.debug(f"Could not fetch chaos state: {e}")


async def apply_chaos():
    """Apply chaos based on current state."""
    # Fetch chaos state periodically (every 5 seconds)
    if time.time() - chaos_state.get("last_fetched", 0) > 5:
        await fetch_chaos_state()

    if not chaos_state.get("chaos_enabled", False):
        return

    # Apply latency
    latency_ms = chaos_state.get("latency_ms", 0)
    jitter_ms = chaos_state.get("jitter_ms", 0)
    if latency_ms > 0:
        actual_latency = latency_ms + random.randint(-jitter_ms, jitter_ms)
        actual_latency = max(0, actual_latency)
        await asyncio.sleep(actual_latency / 1000.0)
        CHAOS_INJECTED.labels(service=SERVICE_NAME, chaos_type="latency").inc()

    # Apply error injection
    error_rate = chaos_state.get("error_rate", 0.0)
    if error_rate > 0 and random.random() < error_rate:
        CHAOS_INJECTED.labels(service=SERVICE_NAME, chaos_type="error").inc()
        error_code = chaos_state.get("error_code", 500)
        error_message = chaos_state.get("error_message", "Chaos-induced error")
        raise HTTPException(status_code=error_code, detail=error_message)

    # Apply network partition (request dropping)
    drop_rate = chaos_state.get("drop_rate", 0.0)
    if drop_rate > 0 and random.random() < drop_rate:
        CHAOS_INJECTED.labels(service=SERVICE_NAME, chaos_type="network_partition").inc()
        raise HTTPException(status_code=503, detail="Service unavailable (simulated network partition)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Order service URL: {ORDER_SERVICE_URL}")
    logger.info(f"Chaos controller URL: {CHAOS_CONTROLLER_URL}")
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


@app.get("/chaos/state")
async def get_chaos_state():
    """Get current chaos state for this service."""
    await fetch_chaos_state()
    return chaos_state


@app.post("/api/orders")
async def create_order(request: Request):
    """Create a new order - calls order service."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Apply chaos before processing
    await apply_chaos()

    # Simulate gateway processing
    await asyncio.sleep(random.randint(5, 15) / 1000.0)

    try:
        # Get request body if present
        try:
            body = await request.json()
        except Exception:
            body = {"items": [{"product_id": "default", "quantity": 1}]}

        # Call order service
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{ORDER_SERVICE_URL}/orders",
                json=body
            )
            result = response.json()

        duration = time.time() - start_time

        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="POST",
            endpoint="/api/orders",
            status=str(response.status_code)
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME,
            method="POST",
            endpoint="/api/orders"
        ).observe(duration)

        return {
            "service": SERVICE_NAME,
            "trace_id": trace_id,
            "duration_ms": round(duration * 1000, 2),
            "order": result
        }

    except httpx.RequestError as e:
        ERROR_COUNT.labels(service=SERVICE_NAME, error_type="downstream_error").inc()
        raise HTTPException(status_code=503, detail=f"Order service unavailable: {str(e)}")


@app.get("/api/orders/{order_id}")
async def get_order(order_id: str):
    """Get order details."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Apply chaos
    await apply_chaos()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{ORDER_SERVICE_URL}/orders/{order_id}")
            result = response.json()

        duration = time.time() - start_time

        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/api/orders/{id}",
            status=str(response.status_code)
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/api/orders/{id}"
        ).observe(duration)

        return {
            "service": SERVICE_NAME,
            "trace_id": trace_id,
            "duration_ms": round(duration * 1000, 2),
            "order": result
        }

    except httpx.RequestError as e:
        ERROR_COUNT.labels(service=SERVICE_NAME, error_type="downstream_error").inc()
        raise HTTPException(status_code=503, detail=f"Order service unavailable: {str(e)}")


@app.get("/api/health-check")
async def health_check():
    """Deep health check - verifies all downstream services."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Apply chaos
    await apply_chaos()

    services_status = {}

    async with httpx.AsyncClient(timeout=5.0) as client:
        # Check order service
        try:
            response = await client.get(f"{ORDER_SERVICE_URL}/health")
            services_status["order-service"] = "healthy" if response.status_code == 200 else "unhealthy"
        except Exception:
            services_status["order-service"] = "unreachable"

        # Check chaos controller
        try:
            response = await client.get(f"{CHAOS_CONTROLLER_URL}/health")
            services_status["chaos-controller"] = "healthy" if response.status_code == 200 else "unhealthy"
        except Exception:
            services_status["chaos-controller"] = "unreachable"

    duration = time.time() - start_time
    all_healthy = all(s == "healthy" for s in services_status.values())

    return {
        "service": SERVICE_NAME,
        "trace_id": trace_id,
        "status": "healthy" if all_healthy else "degraded",
        "duration_ms": round(duration * 1000, 2),
        "downstream_services": services_status
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
