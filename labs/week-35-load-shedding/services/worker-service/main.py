"""
Worker Service - Simulates downstream processing with configurable capacity.

This service represents a backend worker that processes requests.
It can simulate overload conditions to demonstrate load shedding behavior.
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "worker-service")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab35-otel-collector:4317")

# Processing simulation
PROCESSING_TIME_MIN_MS = int(os.getenv("PROCESSING_TIME_MIN_MS", "50"))
PROCESSING_TIME_MAX_MS = int(os.getenv("PROCESSING_TIME_MAX_MS", "200"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "20"))
SIMULATE_OVERLOAD = os.getenv("SIMULATE_OVERLOAD", "false").lower() == "true"

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
CONCURRENT_REQUESTS = Gauge(
    "worker_concurrent_requests",
    "Current concurrent requests",
    ["service"]
)
WORK_COMPLETED = Counter(
    "work_completed_total",
    "Total work items completed",
    ["service", "priority"]
)
WORK_REJECTED = Counter(
    "work_rejected_total",
    "Total work items rejected due to capacity",
    ["service"]
)


class WorkerCapacity:
    """Tracks worker capacity and simulates overload conditions."""

    def __init__(self, max_concurrent: int):
        self.max_concurrent = max_concurrent
        self.current_concurrent = 0
        self.simulate_overload = SIMULATE_OVERLOAD
        self._lock = asyncio.Lock()

        # Overload simulation settings
        self.overload_latency_multiplier = 3.0
        self.overload_error_rate = 0.1

    async def acquire(self) -> bool:
        """Try to acquire a processing slot."""
        async with self._lock:
            if self.current_concurrent >= self.max_concurrent:
                return False
            self.current_concurrent += 1
            CONCURRENT_REQUESTS.labels(service=SERVICE_NAME).set(self.current_concurrent)
            return True

    async def release(self):
        """Release a processing slot."""
        async with self._lock:
            self.current_concurrent = max(0, self.current_concurrent - 1)
            CONCURRENT_REQUESTS.labels(service=SERVICE_NAME).set(self.current_concurrent)

    @property
    def utilization_percent(self) -> float:
        return (self.current_concurrent / self.max_concurrent) * 100


# Global capacity tracker
capacity = WorkerCapacity(MAX_CONCURRENT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Processing time: {PROCESSING_TIME_MIN_MS}-{PROCESSING_TIME_MAX_MS}ms")
    logger.info(f"Max concurrent: {MAX_CONCURRENT}")
    logger.info(f"Simulate overload: {SIMULATE_OVERLOAD}")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "concurrent": capacity.current_concurrent,
        "max_concurrent": capacity.max_concurrent,
        "utilization_percent": capacity.utilization_percent
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/admin/config")
async def get_config():
    """Get current worker configuration."""
    return {
        "processing_time_min_ms": PROCESSING_TIME_MIN_MS,
        "processing_time_max_ms": PROCESSING_TIME_MAX_MS,
        "max_concurrent": capacity.max_concurrent,
        "current_concurrent": capacity.current_concurrent,
        "utilization_percent": capacity.utilization_percent,
        "simulate_overload": capacity.simulate_overload
    }


@app.post("/admin/config")
async def update_config(
    max_concurrent: int = None,
    simulate_overload: bool = None
):
    """Update worker configuration at runtime."""
    if max_concurrent is not None:
        capacity.max_concurrent = max_concurrent
        logger.info(f"Max concurrent set to: {max_concurrent}")

    if simulate_overload is not None:
        capacity.simulate_overload = simulate_overload
        logger.info(f"Simulate overload: {simulate_overload}")

    return await get_config()


@app.get("/work")
async def do_work(request: Request):
    """
    Simulate work processing.

    When overloaded:
    - Response times increase
    - Error rate increases
    - Some requests rejected
    """
    start_time = time.time()
    priority = request.headers.get("X-Priority", "normal")

    current_span = trace.get_current_span()
    current_span.set_attribute("worker.priority", priority)

    # Try to acquire capacity
    if not await capacity.acquire():
        WORK_REJECTED.labels(service=SERVICE_NAME).inc()
        current_span.set_attribute("worker.rejected", True)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Worker at capacity",
                "current": capacity.current_concurrent,
                "max": capacity.max_concurrent
            }
        )

    try:
        # Calculate processing time
        base_time_ms = random.randint(PROCESSING_TIME_MIN_MS, PROCESSING_TIME_MAX_MS)

        # Apply overload simulation if enabled
        if capacity.simulate_overload:
            # Increase latency when under load
            load_factor = capacity.utilization_percent / 100
            latency_multiplier = 1.0 + (capacity.overload_latency_multiplier - 1.0) * load_factor
            processing_time_ms = int(base_time_ms * latency_multiplier)

            # Random errors under load
            if random.random() < capacity.overload_error_rate * load_factor:
                raise HTTPException(500, "Simulated overload error")
        else:
            processing_time_ms = base_time_ms

        current_span.set_attribute("worker.processing_time_ms", processing_time_ms)

        # Simulate processing
        await asyncio.sleep(processing_time_ms / 1000.0)

        duration = time.time() - start_time

        # Record metrics
        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/work",
            status="200"
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/work"
        ).observe(duration)
        WORK_COMPLETED.labels(
            service=SERVICE_NAME,
            priority=priority
        ).inc()

        return {
            "service": SERVICE_NAME,
            "status": "completed",
            "processing_time_ms": processing_time_ms,
            "duration_ms": round(duration * 1000, 2),
            "priority": priority,
            "concurrent_at_completion": capacity.current_concurrent,
            "utilization_percent": round(capacity.utilization_percent, 1)
        }

    finally:
        await capacity.release()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
