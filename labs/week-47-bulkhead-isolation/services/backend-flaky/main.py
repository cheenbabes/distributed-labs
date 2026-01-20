"""
Flaky Backend Service - Simulates an unreliable dependency.

This service can be configured to:
- Fail randomly with configurable error rate
- Return errors for a period of time
- Simulate various failure modes (timeout, 500, connection reset)
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from enum import Enum

from fastapi import FastAPI, Request, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "backend-flaky")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

# Default latency
LATENCY_MIN_MS = int(os.getenv("LATENCY_MIN_MS", "50"))
LATENCY_MAX_MS = int(os.getenv("LATENCY_MAX_MS", "150"))

# Failure configuration
ERROR_RATE = float(os.getenv("ERROR_RATE", "0.3"))  # 30% error rate by default
FAILURE_MODE = os.getenv("FAILURE_MODE", "random")  # random, always_fail, always_succeed

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

ERROR_RATE_GAUGE = Gauge(
    "backend_error_rate",
    "Configured error rate",
    ["service"]
)

FAILURE_MODE_GAUGE = Gauge(
    "backend_failure_mode",
    "Current failure mode (0=random, 1=always_fail, 2=always_succeed)",
    ["service"]
)


class FailureMode(Enum):
    RANDOM = "random"
    ALWAYS_FAIL = "always_fail"
    ALWAYS_SUCCEED = "always_succeed"


# Runtime configuration
runtime_config = {
    "error_rate": ERROR_RATE,
    "failure_mode": FAILURE_MODE,
    "latency_min_ms": LATENCY_MIN_MS,
    "latency_max_ms": LATENCY_MAX_MS,
}


def get_failure_mode_value(mode: str) -> int:
    if mode == "random":
        return 0
    elif mode == "always_fail":
        return 1
    else:
        return 2


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Error rate: {runtime_config['error_rate']}")
    logger.info(f"Failure mode: {runtime_config['failure_mode']}")
    ERROR_RATE_GAUGE.labels(service=SERVICE_NAME).set(runtime_config['error_rate'])
    FAILURE_MODE_GAUGE.labels(service=SERVICE_NAME).set(get_failure_mode_value(runtime_config['failure_mode']))
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
        "error_rate": runtime_config["error_rate"],
        "failure_mode": runtime_config["failure_mode"],
        "latency_range": f"{runtime_config['latency_min_ms']}-{runtime_config['latency_max_ms']}ms"
    }


@app.post("/admin/failure")
async def set_failure_config(request: Request):
    """Configure failure behavior."""
    data = await request.json()

    if "error_rate" in data:
        runtime_config["error_rate"] = float(data["error_rate"])
        ERROR_RATE_GAUGE.labels(service=SERVICE_NAME).set(runtime_config["error_rate"])
        logger.info(f"Error rate set to {runtime_config['error_rate']}")

    if "failure_mode" in data:
        mode = data["failure_mode"]
        if mode in ["random", "always_fail", "always_succeed"]:
            runtime_config["failure_mode"] = mode
            FAILURE_MODE_GAUGE.labels(service=SERVICE_NAME).set(get_failure_mode_value(mode))
            logger.info(f"Failure mode set to {mode}")

    return runtime_config


def should_fail() -> bool:
    """Determine if this request should fail based on configuration."""
    mode = runtime_config["failure_mode"]

    if mode == "always_fail":
        return True
    elif mode == "always_succeed":
        return False
    else:  # random
        return random.random() < runtime_config["error_rate"]


def get_failure_type() -> str:
    """Randomly select a failure type."""
    failures = ["500_error", "timeout", "bad_gateway"]
    return random.choice(failures)


@app.get("/process")
async def process(request: Request):
    """Process request with configurable failure behavior."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Simulate some processing time
    latency_ms = random.randint(
        runtime_config["latency_min_ms"],
        runtime_config["latency_max_ms"]
    )
    await asyncio.sleep(latency_ms / 1000.0)

    # Check if we should fail
    if should_fail():
        failure_type = get_failure_type()
        duration = time.time() - start_time

        current_span.set_attribute("backend.failure", True)
        current_span.set_attribute("backend.failure_type", failure_type)

        REQUEST_COUNT.labels(service=SERVICE_NAME, status="error").inc()
        REQUEST_LATENCY.labels(service=SERVICE_NAME).observe(duration)

        logger.warning(f"Request failed trace_id={trace_id} failure_type={failure_type}")

        if failure_type == "timeout":
            # Simulate timeout by sleeping longer
            await asyncio.sleep(30)  # This will likely trigger client timeout
            raise HTTPException(status_code=504, detail="Gateway Timeout")
        elif failure_type == "bad_gateway":
            raise HTTPException(status_code=502, detail="Bad Gateway")
        else:  # 500_error
            raise HTTPException(status_code=500, detail="Internal Server Error")

    # Success path
    duration = time.time() - start_time

    current_span.set_attribute("backend.latency_ms", latency_ms)
    current_span.set_attribute("backend.type", "flaky")
    current_span.set_attribute("backend.failure", False)

    REQUEST_COUNT.labels(service=SERVICE_NAME, status="success").inc()
    REQUEST_LATENCY.labels(service=SERVICE_NAME).observe(duration)

    logger.info(f"Request processed trace_id={trace_id} latency={latency_ms}ms")

    return {
        "service": SERVICE_NAME,
        "type": "flaky",
        "latency_ms": latency_ms,
        "trace_id": trace_id
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
