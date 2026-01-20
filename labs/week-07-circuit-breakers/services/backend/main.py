"""
Backend Service - A service that can be put into failure mode via admin endpoint.

This service simulates a backend that can fail on demand, allowing us to
demonstrate circuit breaker behavior in the client service.
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from enum import Enum

from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "backend")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
LATENCY_MIN_MS = int(os.getenv("LATENCY_MIN_MS", "10"))
LATENCY_MAX_MS = int(os.getenv("LATENCY_MAX_MS", "50"))
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

# Prometheus metrics
REQUEST_COUNT = Counter(
    "backend_requests_total",
    "Total backend requests",
    ["status"]
)
REQUEST_LATENCY = Histogram(
    "backend_request_duration_seconds",
    "Backend request latency",
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
FAILURE_MODE = Gauge(
    "backend_failure_mode",
    "Backend failure mode (0=healthy, 1=failing)"
)


class FailureMode(str, Enum):
    HEALTHY = "healthy"
    FAILING = "failing"
    SLOW = "slow"
    PARTIAL = "partial"  # Fails ~50% of the time


class FailureConfig(BaseModel):
    mode: FailureMode = FailureMode.HEALTHY
    error_rate: float = 1.0  # For PARTIAL mode, probability of failure
    slow_latency_ms: int = 5000  # For SLOW mode


# Runtime failure configuration
failure_config = {
    "mode": FailureMode.HEALTHY,
    "error_rate": 1.0,
    "slow_latency_ms": 5000
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up on port {PORT}")
    logger.info(f"Latency range: {LATENCY_MIN_MS}-{LATENCY_MAX_MS}ms")
    FAILURE_MODE.set(0)
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    """Health check - always returns healthy regardless of failure mode."""
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/admin/failure")
async def get_failure_config():
    """Get current failure configuration."""
    return {
        "mode": failure_config["mode"].value,
        "error_rate": failure_config["error_rate"],
        "slow_latency_ms": failure_config["slow_latency_ms"]
    }


@app.post("/admin/failure")
async def set_failure_config(config: FailureConfig):
    """Set failure configuration at runtime."""
    failure_config["mode"] = config.mode
    failure_config["error_rate"] = config.error_rate
    failure_config["slow_latency_ms"] = config.slow_latency_ms

    # Update metric
    if config.mode == FailureMode.HEALTHY:
        FAILURE_MODE.set(0)
    else:
        FAILURE_MODE.set(1)

    logger.info(f"Failure mode updated: {config.mode.value}")
    return {
        "mode": failure_config["mode"].value,
        "error_rate": failure_config["error_rate"],
        "slow_latency_ms": failure_config["slow_latency_ms"]
    }


@app.get("/api/data")
async def get_data():
    """Main API endpoint - behavior depends on failure mode."""
    start_time = time.time()
    current_span = trace.get_current_span()

    mode = failure_config["mode"]
    current_span.set_attribute("failure_mode", mode.value)

    # Simulate base processing time
    base_latency_ms = random.randint(LATENCY_MIN_MS, LATENCY_MAX_MS)

    # Handle different failure modes
    if mode == FailureMode.FAILING:
        REQUEST_COUNT.labels(status="error").inc()
        current_span.set_attribute("error", True)
        logger.warning("Request failed: Failure mode is FAILING")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    elif mode == FailureMode.SLOW:
        slow_latency_ms = failure_config["slow_latency_ms"]
        total_latency = base_latency_ms + slow_latency_ms
        await asyncio.sleep(total_latency / 1000.0)
        current_span.set_attribute("slow_mode", True)
        current_span.set_attribute("latency_ms", total_latency)

    elif mode == FailureMode.PARTIAL:
        if random.random() < failure_config["error_rate"]:
            REQUEST_COUNT.labels(status="error").inc()
            current_span.set_attribute("error", True)
            logger.warning("Request failed: Partial failure triggered")
            raise HTTPException(status_code=503, detail="Service temporarily unavailable")
        await asyncio.sleep(base_latency_ms / 1000.0)

    else:  # HEALTHY
        await asyncio.sleep(base_latency_ms / 1000.0)

    duration = time.time() - start_time
    REQUEST_COUNT.labels(status="success").inc()
    REQUEST_LATENCY.observe(duration)

    return {
        "service": SERVICE_NAME,
        "status": "success",
        "data": {
            "id": random.randint(1000, 9999),
            "value": f"response-{int(time.time())}",
            "latency_ms": round(duration * 1000, 2)
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
