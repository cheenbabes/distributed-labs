"""
Backend Service - Target service that can simulate failures.

Demonstrates:
- Controlled failure injection via /admin/fail endpoint
- Metrics showing request amplification during failures
- Recovery after failure window ends
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "backend")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
LATENCY_MIN_MS = int(os.getenv("LATENCY_MIN_MS", "10"))
LATENCY_MAX_MS = int(os.getenv("LATENCY_MAX_MS", "50"))

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
FAILURE_ACTIVE = Gauge(
    "failure_injection_active",
    "Whether failure injection is currently active",
    ["service"]
)
FAILURE_REQUESTS = Counter(
    "failure_requests_total",
    "Requests that failed due to injection",
    ["service"]
)
REQUESTS_DURING_FAILURE = Counter(
    "requests_during_failure_total",
    "Total requests received during failure window",
    ["service"]
)


class FailureRequest(BaseModel):
    duration_seconds: int = 10
    failure_rate: float = 1.0  # 0.0 to 1.0


# Global state for failure injection
failure_end_time: Optional[datetime] = None
failure_rate: float = 1.0


def is_failure_active() -> bool:
    """Check if failure injection is currently active."""
    global failure_end_time
    if failure_end_time is None:
        return False
    return datetime.now() < failure_end_time


def should_fail() -> bool:
    """Determine if this request should fail based on failure state."""
    if not is_failure_active():
        return False
    return random.random() < failure_rate


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Base latency: {LATENCY_MIN_MS}-{LATENCY_MAX_MS}ms")
    FAILURE_ACTIVE.labels(service=SERVICE_NAME).set(0)
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "failure_active": is_failure_active()
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/admin/status")
async def get_status():
    """Get current failure injection status."""
    active = is_failure_active()
    remaining = 0
    if active and failure_end_time:
        remaining = max(0, (failure_end_time - datetime.now()).total_seconds())

    return {
        "failure_active": active,
        "failure_rate": failure_rate if active else 0,
        "remaining_seconds": round(remaining, 1),
        "failure_end_time": failure_end_time.isoformat() if failure_end_time else None
    }


@app.post("/admin/fail")
async def trigger_failure(request: FailureRequest):
    """
    Trigger a failure for the specified duration.

    During failure:
    - All (or percentage of) requests to /process will return 503
    - This simulates a service outage

    Args:
        duration_seconds: How long the failure should last
        failure_rate: Percentage of requests to fail (0.0 to 1.0)
    """
    global failure_end_time, failure_rate

    failure_end_time = datetime.now() + timedelta(seconds=request.duration_seconds)
    failure_rate = max(0.0, min(1.0, request.failure_rate))

    FAILURE_ACTIVE.labels(service=SERVICE_NAME).set(1)

    logger.warning(
        f"FAILURE INJECTION STARTED: duration={request.duration_seconds}s "
        f"rate={failure_rate*100:.0f}%"
    )

    # Schedule gauge reset
    async def reset_gauge():
        await asyncio.sleep(request.duration_seconds)
        if not is_failure_active():
            FAILURE_ACTIVE.labels(service=SERVICE_NAME).set(0)
            logger.info("FAILURE INJECTION ENDED")

    asyncio.create_task(reset_gauge())

    return {
        "message": f"Failure triggered for {request.duration_seconds} seconds",
        "failure_rate": failure_rate,
        "failure_end_time": failure_end_time.isoformat()
    }


@app.post("/admin/recover")
async def recover():
    """Immediately end failure injection."""
    global failure_end_time

    was_active = is_failure_active()
    failure_end_time = None
    FAILURE_ACTIVE.labels(service=SERVICE_NAME).set(0)

    logger.info("FAILURE INJECTION MANUALLY ENDED")

    return {
        "message": "Failure injection ended",
        "was_active": was_active
    }


@app.get("/process")
async def process(request: Request):
    """
    Main processing endpoint.

    - Returns 503 if failure is active
    - Otherwise simulates work with random latency
    """
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Check failure state
    if is_failure_active():
        REQUESTS_DURING_FAILURE.labels(service=SERVICE_NAME).inc()

        if should_fail():
            # Record failure metrics
            duration = time.time() - start_time
            REQUEST_COUNT.labels(
                service=SERVICE_NAME,
                method="GET",
                endpoint="/process",
                status="503"
            ).inc()
            REQUEST_LATENCY.labels(
                service=SERVICE_NAME,
                method="GET",
                endpoint="/process"
            ).observe(duration)
            FAILURE_REQUESTS.labels(service=SERVICE_NAME).inc()

            current_span.set_attribute("failure.injected", True)
            logger.warning(f"Request failed (injection active) trace_id={trace_id}")

            raise HTTPException(
                status_code=503,
                detail={
                    "error": "Service temporarily unavailable",
                    "service": SERVICE_NAME,
                    "trace_id": trace_id,
                    "failure_injected": True
                }
            )

    # Normal processing - simulate work
    latency_ms = random.randint(LATENCY_MIN_MS, LATENCY_MAX_MS)
    await asyncio.sleep(latency_ms / 1000.0)

    duration = time.time() - start_time

    # Record success metrics
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

    current_span.set_attribute("processing.latency_ms", latency_ms)
    current_span.set_attribute("failure.injected", False)

    logger.debug(f"Request processed latency={latency_ms}ms trace_id={trace_id}")

    return {
        "service": SERVICE_NAME,
        "trace_id": trace_id,
        "processing_ms": latency_ms,
        "total_duration_ms": round(duration * 1000, 2),
        "timestamp": datetime.now().isoformat()
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
