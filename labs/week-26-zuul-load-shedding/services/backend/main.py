"""
Backend Service - Simulates a service with configurable capacity and latency.

This service simulates various overload conditions:
1. Configurable base latency
2. Capacity limits (starts returning errors when overloaded)
3. Gradual degradation under load
4. Random errors
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "backend")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

# Service capacity and latency configuration
BASE_LATENCY_MIN_MS = int(os.getenv("BASE_LATENCY_MIN_MS", "20"))
BASE_LATENCY_MAX_MS = int(os.getenv("BASE_LATENCY_MAX_MS", "50"))
MAX_CAPACITY = int(os.getenv("MAX_CAPACITY", "20"))  # Max concurrent requests before degradation
ERROR_RATE = float(os.getenv("ERROR_RATE", "0.0"))  # Base random error rate
OVERLOAD_LATENCY_MULTIPLIER = float(os.getenv("OVERLOAD_LATENCY_MULTIPLIER", "5.0"))

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
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)
CONCURRENT_REQUESTS = Gauge(
    "concurrent_requests",
    "Current number of in-flight requests",
    ["service"]
)
CAPACITY_UTILIZATION = Gauge(
    "capacity_utilization",
    "Current capacity utilization (0-1+)",
    ["service"]
)


@dataclass
class ServiceConfig:
    """Dynamic service configuration."""
    base_latency_min_ms: int = BASE_LATENCY_MIN_MS
    base_latency_max_ms: int = BASE_LATENCY_MAX_MS
    max_capacity: int = MAX_CAPACITY
    error_rate: float = ERROR_RATE
    overload_latency_multiplier: float = OVERLOAD_LATENCY_MULTIPLIER
    # Injected conditions
    inject_latency: bool = False
    injected_latency_ms: int = 0
    inject_errors: bool = False
    injected_error_rate: float = 0.5


@dataclass
class ServiceState:
    """Runtime state."""
    current_concurrent: int = 0
    lock: asyncio.Lock = None

    def __post_init__(self):
        self.lock = asyncio.Lock()


# Global state
config = ServiceConfig()
state = ServiceState()


def calculate_latency() -> int:
    """
    Calculate latency based on current load.
    Latency increases when approaching/exceeding capacity.
    """
    base_latency = random.randint(config.base_latency_min_ms, config.base_latency_max_ms)

    # Add injected latency if enabled
    if config.inject_latency:
        base_latency += config.injected_latency_ms

    # Calculate load factor (can exceed 1.0 when overloaded)
    load_factor = state.current_concurrent / max(config.max_capacity, 1)

    if load_factor > 0.8:
        # Exponential latency increase as we approach/exceed capacity
        multiplier = 1.0 + (load_factor - 0.8) * config.overload_latency_multiplier
        return int(base_latency * multiplier)

    return base_latency


def should_error() -> bool:
    """Determine if this request should fail based on load and config."""
    # Check injected errors
    if config.inject_errors:
        if random.random() < config.injected_error_rate:
            return True

    # Check base error rate
    if random.random() < config.error_rate:
        return True

    # Higher error rate when severely overloaded
    load_factor = state.current_concurrent / max(config.max_capacity, 1)
    if load_factor > 1.5:
        # 50% error rate when at 150%+ capacity
        if random.random() < 0.5:
            return True

    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Max capacity: {config.max_capacity}")
    logger.info(f"Base latency: {config.base_latency_min_ms}-{config.base_latency_max_ms}ms")
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


@app.get("/config")
async def get_config():
    """Get current service configuration."""
    return {
        "base_latency_min_ms": config.base_latency_min_ms,
        "base_latency_max_ms": config.base_latency_max_ms,
        "max_capacity": config.max_capacity,
        "error_rate": config.error_rate,
        "overload_latency_multiplier": config.overload_latency_multiplier,
        "inject_latency": config.inject_latency,
        "injected_latency_ms": config.injected_latency_ms,
        "inject_errors": config.inject_errors,
        "injected_error_rate": config.injected_error_rate,
    }


@app.post("/config")
async def update_config(new_config: dict):
    """Update service configuration dynamically."""
    if "base_latency_min_ms" in new_config:
        config.base_latency_min_ms = new_config["base_latency_min_ms"]
    if "base_latency_max_ms" in new_config:
        config.base_latency_max_ms = new_config["base_latency_max_ms"]
    if "max_capacity" in new_config:
        config.max_capacity = new_config["max_capacity"]
    if "error_rate" in new_config:
        config.error_rate = new_config["error_rate"]
    if "overload_latency_multiplier" in new_config:
        config.overload_latency_multiplier = new_config["overload_latency_multiplier"]
    if "inject_latency" in new_config:
        config.inject_latency = new_config["inject_latency"]
    if "injected_latency_ms" in new_config:
        config.injected_latency_ms = new_config["injected_latency_ms"]
    if "inject_errors" in new_config:
        config.inject_errors = new_config["inject_errors"]
    if "injected_error_rate" in new_config:
        config.injected_error_rate = new_config["injected_error_rate"]

    logger.info(f"Config updated: {new_config}")
    return {"status": "updated", "config": await get_config()}


@app.get("/stats")
async def get_stats():
    """Get current service statistics."""
    load_factor = state.current_concurrent / max(config.max_capacity, 1)
    return {
        "current_concurrent": state.current_concurrent,
        "max_capacity": config.max_capacity,
        "load_factor": round(load_factor, 2),
        "status": "healthy" if load_factor < 0.8 else ("degraded" if load_factor < 1.5 else "overloaded"),
    }


@app.get("/process")
async def process():
    """
    Main processing endpoint.
    Simulates work with configurable latency and capacity limits.
    """
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Track concurrent requests
    async with state.lock:
        state.current_concurrent += 1

    CONCURRENT_REQUESTS.labels(service=SERVICE_NAME).set(state.current_concurrent)
    load_factor = state.current_concurrent / max(config.max_capacity, 1)
    CAPACITY_UTILIZATION.labels(service=SERVICE_NAME).set(load_factor)

    try:
        # Check if we should return an error
        if should_error():
            REQUEST_COUNT.labels(
                service=SERVICE_NAME,
                method="GET",
                endpoint="/process",
                status="500"
            ).inc()

            current_span.set_attribute("error", True)
            current_span.set_attribute("error_type", "simulated_overload")

            logger.warning(f"Simulated error trace_id={trace_id}")

            raise HTTPException(
                status_code=500,
                detail={
                    "error": "Service overloaded",
                    "trace_id": trace_id,
                    "load_factor": load_factor,
                }
            )

        # Simulate processing time
        latency_ms = calculate_latency()
        await asyncio.sleep(latency_ms / 1000.0)

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

        current_span.set_attribute("latency_ms", latency_ms)
        current_span.set_attribute("load_factor", load_factor)
        current_span.set_attribute("concurrent_requests", state.current_concurrent)

        logger.info(f"Request completed trace_id={trace_id} latency={latency_ms}ms load={load_factor:.1%}")

        return {
            "service": SERVICE_NAME,
            "latency_ms": latency_ms,
            "duration_ms": round(duration * 1000, 2),
            "trace_id": trace_id,
            "load_factor": round(load_factor, 2),
            "concurrent_requests": state.current_concurrent,
            "status": "healthy" if load_factor < 0.8 else "degraded",
        }

    finally:
        async with state.lock:
            state.current_concurrent -= 1
        CONCURRENT_REQUESTS.labels(service=SERVICE_NAME).set(state.current_concurrent)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
