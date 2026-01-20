"""
API Gateway with Bulkhead Pattern - Isolates failures using thread pools and semaphores.

This service demonstrates:
1. Thread pool isolation (separate executors for each backend)
2. Semaphore-based isolation (limiting concurrent requests per backend)
3. How bulkheads prevent cascade failures
"""
import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
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
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "api-gateway")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

# Backend URLs
BACKEND_FAST_URL = os.getenv("BACKEND_FAST_URL", "http://backend-fast:8001")
BACKEND_SLOW_URL = os.getenv("BACKEND_SLOW_URL", "http://backend-slow:8002")
BACKEND_FLAKY_URL = os.getenv("BACKEND_FLAKY_URL", "http://backend-flaky:8003")

# Bulkhead Configuration
BULKHEAD_MODE = os.getenv("BULKHEAD_MODE", "enabled")  # enabled, disabled
THREAD_POOL_SIZE = int(os.getenv("THREAD_POOL_SIZE", "5"))
SEMAPHORE_LIMIT = int(os.getenv("SEMAPHORE_LIMIT", "10"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "5.0"))

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
    "bulkhead_requests_total",
    "Total requests per bulkhead",
    ["bulkhead", "status"]
)

BULKHEAD_ACTIVE = Gauge(
    "bulkhead_active_requests",
    "Currently active requests per bulkhead",
    ["bulkhead"]
)

BULKHEAD_REJECTED = Counter(
    "bulkhead_rejected_total",
    "Rejected requests due to bulkhead limits",
    ["bulkhead"]
)

BULKHEAD_QUEUE_TIME = Histogram(
    "bulkhead_queue_seconds",
    "Time spent waiting for bulkhead slot",
    ["bulkhead"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)

REQUEST_LATENCY = Histogram(
    "bulkhead_request_duration_seconds",
    "Request latency per bulkhead",
    ["bulkhead"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

BACKEND_LATENCY = Histogram(
    "backend_request_duration_seconds",
    "Backend request latency",
    ["backend", "status"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)


class BulkheadType(Enum):
    SEMAPHORE = "semaphore"
    THREAD_POOL = "thread_pool"


@dataclass
class BulkheadConfig:
    name: str
    url: str
    bulkhead_type: BulkheadType
    max_concurrent: int
    timeout: float


class Bulkhead:
    """
    Bulkhead implementation that provides isolation using semaphores.
    """

    def __init__(self, name: str, max_concurrent: int):
        self.name = name
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active = 0
        self._lock = asyncio.Lock()

    async def acquire(self, timeout: Optional[float] = None) -> bool:
        """Acquire a slot in the bulkhead. Returns False if timeout exceeded."""
        start_time = time.time()

        try:
            if timeout:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=timeout)
            else:
                await self._semaphore.acquire()

            queue_time = time.time() - start_time
            BULKHEAD_QUEUE_TIME.labels(bulkhead=self.name).observe(queue_time)

            async with self._lock:
                self._active += 1
                BULKHEAD_ACTIVE.labels(bulkhead=self.name).set(self._active)

            return True

        except asyncio.TimeoutError:
            BULKHEAD_REJECTED.labels(bulkhead=self.name).inc()
            return False

    async def release(self):
        """Release a slot in the bulkhead."""
        async with self._lock:
            self._active -= 1
            BULKHEAD_ACTIVE.labels(bulkhead=self.name).set(self._active)
        self._semaphore.release()

    @property
    def active_count(self) -> int:
        return self._active

    @property
    def available_count(self) -> int:
        return self.max_concurrent - self._active


class ThreadPoolBulkhead:
    """
    Bulkhead implementation using a dedicated thread pool.
    """

    def __init__(self, name: str, pool_size: int):
        self.name = name
        self.pool_size = pool_size
        self._executor = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix=f"bulkhead-{name}")
        self._active = 0
        self._lock = asyncio.Lock()

    async def execute(self, func, *args, timeout: Optional[float] = None):
        """Execute a function in the thread pool with optional timeout."""
        loop = asyncio.get_event_loop()
        start_time = time.time()

        async with self._lock:
            self._active += 1
            BULKHEAD_ACTIVE.labels(bulkhead=self.name).set(self._active)

        try:
            if timeout:
                result = await asyncio.wait_for(
                    loop.run_in_executor(self._executor, func, *args),
                    timeout=timeout
                )
            else:
                result = await loop.run_in_executor(self._executor, func, *args)

            return result

        except asyncio.TimeoutError:
            BULKHEAD_REJECTED.labels(bulkhead=self.name).inc()
            raise
        finally:
            async with self._lock:
                self._active -= 1
                BULKHEAD_ACTIVE.labels(bulkhead=self.name).set(self._active)

    def shutdown(self):
        self._executor.shutdown(wait=False)


# Global bulkheads - initialized with configurable limits
bulkheads = {}
thread_pool_bulkheads = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bulkheads, thread_pool_bulkheads

    # Initialize semaphore-based bulkheads
    bulkheads = {
        "backend-fast": Bulkhead("backend-fast", SEMAPHORE_LIMIT),
        "backend-slow": Bulkhead("backend-slow", SEMAPHORE_LIMIT),
        "backend-flaky": Bulkhead("backend-flaky", SEMAPHORE_LIMIT),
    }

    # Initialize thread pool bulkheads
    thread_pool_bulkheads = {
        "backend-fast": ThreadPoolBulkhead("tp-backend-fast", THREAD_POOL_SIZE),
        "backend-slow": ThreadPoolBulkhead("tp-backend-slow", THREAD_POOL_SIZE),
        "backend-flaky": ThreadPoolBulkhead("tp-backend-flaky", THREAD_POOL_SIZE),
    }

    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Bulkhead mode: {BULKHEAD_MODE}")
    logger.info(f"Semaphore limit: {SEMAPHORE_LIMIT}")
    logger.info(f"Thread pool size: {THREAD_POOL_SIZE}")
    logger.info(f"Request timeout: {REQUEST_TIMEOUT}s")

    yield

    # Cleanup
    for tp in thread_pool_bulkheads.values():
        tp.shutdown()

    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


async def call_backend_with_bulkhead(backend_name: str, url: str) -> dict:
    """Call a backend service using semaphore-based bulkhead."""
    bulkhead = bulkheads.get(backend_name)
    if not bulkhead:
        raise HTTPException(status_code=500, detail=f"Unknown backend: {backend_name}")

    current_span = trace.get_current_span()
    current_span.set_attribute("bulkhead.name", backend_name)
    current_span.set_attribute("bulkhead.type", "semaphore")
    current_span.set_attribute("bulkhead.max_concurrent", bulkhead.max_concurrent)
    current_span.set_attribute("bulkhead.active_before", bulkhead.active_count)

    # Try to acquire bulkhead slot
    acquired = await bulkhead.acquire(timeout=1.0)  # 1 second timeout for acquiring slot
    if not acquired:
        current_span.set_attribute("bulkhead.rejected", True)
        REQUEST_COUNT.labels(bulkhead=backend_name, status="rejected").inc()
        raise HTTPException(
            status_code=503,
            detail=f"Bulkhead {backend_name} is full. Try again later."
        )

    start_time = time.time()
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(f"{url}/process")
            duration = time.time() - start_time

            REQUEST_LATENCY.labels(bulkhead=backend_name).observe(duration)
            BACKEND_LATENCY.labels(backend=backend_name, status="success").observe(duration)
            REQUEST_COUNT.labels(bulkhead=backend_name, status="success").inc()

            current_span.set_attribute("bulkhead.duration_ms", duration * 1000)

            return response.json()

    except httpx.TimeoutException:
        duration = time.time() - start_time
        BACKEND_LATENCY.labels(backend=backend_name, status="timeout").observe(duration)
        REQUEST_COUNT.labels(bulkhead=backend_name, status="timeout").inc()
        current_span.set_attribute("bulkhead.timeout", True)
        raise HTTPException(status_code=504, detail=f"Backend {backend_name} timed out")

    except Exception as e:
        duration = time.time() - start_time
        BACKEND_LATENCY.labels(backend=backend_name, status="error").observe(duration)
        REQUEST_COUNT.labels(bulkhead=backend_name, status="error").inc()
        current_span.set_attribute("bulkhead.error", str(e))
        raise HTTPException(status_code=502, detail=f"Backend {backend_name} error: {str(e)}")

    finally:
        await bulkhead.release()


async def call_backend_without_bulkhead(backend_name: str, url: str) -> dict:
    """Call a backend service without bulkhead protection."""
    current_span = trace.get_current_span()
    current_span.set_attribute("bulkhead.enabled", False)

    start_time = time.time()
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(f"{url}/process")
            duration = time.time() - start_time

            BACKEND_LATENCY.labels(backend=backend_name, status="success").observe(duration)
            return response.json()

    except httpx.TimeoutException:
        duration = time.time() - start_time
        BACKEND_LATENCY.labels(backend=backend_name, status="timeout").observe(duration)
        raise HTTPException(status_code=504, detail=f"Backend {backend_name} timed out")

    except Exception as e:
        duration = time.time() - start_time
        BACKEND_LATENCY.labels(backend=backend_name, status="error").observe(duration)
        raise HTTPException(status_code=502, detail=f"Backend {backend_name} error: {str(e)}")


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
async def status():
    """Return current bulkhead status."""
    return {
        "bulkhead_mode": BULKHEAD_MODE,
        "semaphore_limit": SEMAPHORE_LIMIT,
        "thread_pool_size": THREAD_POOL_SIZE,
        "request_timeout": REQUEST_TIMEOUT,
        "bulkheads": {
            name: {
                "active": bh.active_count,
                "available": bh.available_count,
                "max": bh.max_concurrent
            }
            for name, bh in bulkheads.items()
        }
    }


@app.post("/admin/config")
async def update_config(request: Request):
    """Update bulkhead configuration at runtime."""
    global BULKHEAD_MODE, SEMAPHORE_LIMIT, REQUEST_TIMEOUT

    data = await request.json()

    if "mode" in data:
        BULKHEAD_MODE = data["mode"]
        logger.info(f"Bulkhead mode changed to: {BULKHEAD_MODE}")

    if "semaphore_limit" in data:
        new_limit = data["semaphore_limit"]
        # Note: Changing semaphore limit requires recreating bulkheads
        for name in bulkheads:
            bulkheads[name] = Bulkhead(name, new_limit)
        SEMAPHORE_LIMIT = new_limit
        logger.info(f"Semaphore limit changed to: {SEMAPHORE_LIMIT}")

    if "timeout" in data:
        REQUEST_TIMEOUT = float(data["timeout"])
        logger.info(f"Request timeout changed to: {REQUEST_TIMEOUT}")

    return {
        "status": "updated",
        "mode": BULKHEAD_MODE,
        "semaphore_limit": SEMAPHORE_LIMIT,
        "timeout": REQUEST_TIMEOUT
    }


@app.get("/api/fast")
async def api_fast(request: Request):
    """Call the fast backend service."""
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    if BULKHEAD_MODE == "enabled":
        result = await call_backend_with_bulkhead("backend-fast", BACKEND_FAST_URL)
    else:
        result = await call_backend_without_bulkhead("backend-fast", BACKEND_FAST_URL)

    return {
        "service": SERVICE_NAME,
        "backend": "fast",
        "trace_id": trace_id,
        "bulkhead_mode": BULKHEAD_MODE,
        "result": result
    }


@app.get("/api/slow")
async def api_slow(request: Request):
    """Call the slow backend service."""
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    if BULKHEAD_MODE == "enabled":
        result = await call_backend_with_bulkhead("backend-slow", BACKEND_SLOW_URL)
    else:
        result = await call_backend_without_bulkhead("backend-slow", BACKEND_SLOW_URL)

    return {
        "service": SERVICE_NAME,
        "backend": "slow",
        "trace_id": trace_id,
        "bulkhead_mode": BULKHEAD_MODE,
        "result": result
    }


@app.get("/api/flaky")
async def api_flaky(request: Request):
    """Call the flaky backend service."""
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    if BULKHEAD_MODE == "enabled":
        result = await call_backend_with_bulkhead("backend-flaky", BACKEND_FLAKY_URL)
    else:
        result = await call_backend_without_bulkhead("backend-flaky", BACKEND_FLAKY_URL)

    return {
        "service": SERVICE_NAME,
        "backend": "flaky",
        "trace_id": trace_id,
        "bulkhead_mode": BULKHEAD_MODE,
        "result": result
    }


@app.get("/api/all")
async def api_all(request: Request):
    """Call all backend services in parallel - demonstrates isolation."""
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    start_time = time.time()

    # Call all backends in parallel
    if BULKHEAD_MODE == "enabled":
        tasks = [
            call_backend_with_bulkhead("backend-fast", BACKEND_FAST_URL),
            call_backend_with_bulkhead("backend-slow", BACKEND_SLOW_URL),
            call_backend_with_bulkhead("backend-flaky", BACKEND_FLAKY_URL),
        ]
    else:
        tasks = [
            call_backend_without_bulkhead("backend-fast", BACKEND_FAST_URL),
            call_backend_without_bulkhead("backend-slow", BACKEND_SLOW_URL),
            call_backend_without_bulkhead("backend-flaky", BACKEND_FLAKY_URL),
        ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    duration = time.time() - start_time

    response = {
        "service": SERVICE_NAME,
        "trace_id": trace_id,
        "bulkhead_mode": BULKHEAD_MODE,
        "total_duration_ms": round(duration * 1000, 2),
        "backends": {}
    }

    for i, (name, result) in enumerate(zip(["fast", "slow", "flaky"], results)):
        if isinstance(result, Exception):
            response["backends"][name] = {
                "status": "error",
                "error": str(result)
            }
        else:
            response["backends"][name] = {
                "status": "success",
                "data": result
            }

    return response


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
