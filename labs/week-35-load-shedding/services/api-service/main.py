"""
API Service with Load Shedding Middleware

Demonstrates multiple load shedding strategies:
- Random: Randomly reject requests above threshold
- Priority: Protect VIP traffic, shed low-priority first
- LIFO: Shed oldest queued requests first (shed waiting, not working)
"""
import asyncio
import logging
import os
import random
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import httpx
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "api-service")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab35-otel-collector:4317")
WORKER_URL = os.getenv("WORKER_URL", "http://lab35-worker-service:8001")

# Load shedding configuration
SHEDDING_ENABLED = os.getenv("SHEDDING_ENABLED", "false").lower() == "true"
SHEDDING_STRATEGY = os.getenv("SHEDDING_STRATEGY", "random")  # random, priority, lifo
MAX_QUEUE_DEPTH = int(os.getenv("MAX_QUEUE_DEPTH", "100"))
MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_CONCURRENT_REQUESTS", "50"))
SHED_THRESHOLD_PERCENT = int(os.getenv("SHED_THRESHOLD_PERCENT", "80"))

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

# ===========================================
# Prometheus Metrics
# ===========================================

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

# Load shedding specific metrics
REQUESTS_SHED = Counter(
    "requests_shed_total",
    "Total requests shed (rejected)",
    ["service", "strategy", "priority"]
)
QUEUE_DEPTH = Gauge(
    "queue_depth",
    "Current request queue depth",
    ["service"]
)
CONCURRENT_REQUESTS = Gauge(
    "concurrent_requests",
    "Current number of concurrent requests being processed",
    ["service"]
)
SHED_BY_PRIORITY = Counter(
    "shed_by_priority_total",
    "Requests shed by priority level",
    ["service", "priority"]
)
SHEDDING_RATE = Gauge(
    "shedding_rate",
    "Current load shedding rate (0-1)",
    ["service"]
)
CAPACITY_UTILIZATION = Gauge(
    "capacity_utilization_percent",
    "Current capacity utilization percentage",
    ["service"]
)


# ===========================================
# Load Shedding Strategies
# ===========================================

class Priority(Enum):
    VIP = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4


@dataclass
class LoadSheddingConfig:
    enabled: bool = False
    strategy: str = "random"  # random, priority, lifo
    max_queue_depth: int = 100
    max_concurrent: int = 50
    shed_threshold_percent: int = 80


class LoadShedder:
    """
    Load shedding controller with multiple strategies.

    Strategies:
    - random: Fair, unbiased rejection when overloaded
    - priority: VIP/high-priority requests preserved
    - lifo: Newest requests served first (shed old waiting requests)
    """

    def __init__(self, config: LoadSheddingConfig):
        self.config = config
        self.current_concurrent = 0
        self.queue: deque = deque()
        self._lock = asyncio.Lock()

    @property
    def utilization_percent(self) -> float:
        """Calculate current capacity utilization."""
        return (self.current_concurrent / self.config.max_concurrent) * 100

    @property
    def should_shed_percent(self) -> float:
        """
        Calculate what percentage of requests to shed.
        Uses a progressive shedding curve.
        """
        util = self.utilization_percent
        threshold = self.config.shed_threshold_percent

        if util < threshold:
            return 0.0

        # Progressive shedding: linearly increase shed rate as we approach 100%
        # At threshold: 0%, at 100%: 100% shed rate
        overage = (util - threshold) / (100 - threshold)
        return min(overage, 1.0)

    async def should_shed(self, priority: Priority = Priority.NORMAL) -> tuple[bool, str]:
        """
        Determine if a request should be shed.
        Returns (should_shed, reason).
        """
        if not self.config.enabled:
            return False, ""

        async with self._lock:
            # Always accept if we're under threshold
            if self.utilization_percent < self.config.shed_threshold_percent:
                return False, ""

            strategy = self.config.strategy
            shed_rate = self.should_shed_percent

            # Update metrics
            SHEDDING_RATE.labels(service=SERVICE_NAME).set(shed_rate)
            CAPACITY_UTILIZATION.labels(service=SERVICE_NAME).set(self.utilization_percent)

            if strategy == "random":
                # Random shedding: fair but unbiased
                if random.random() < shed_rate:
                    return True, f"random shedding at {shed_rate*100:.1f}% rate"

            elif strategy == "priority":
                # Priority-based shedding: protect VIP traffic
                # VIP: never shed, HIGH: 25% of normal rate, NORMAL: full rate, LOW: 150% rate
                priority_multipliers = {
                    Priority.VIP: 0.0,
                    Priority.HIGH: 0.25,
                    Priority.NORMAL: 1.0,
                    Priority.LOW: 1.5
                }
                adjusted_rate = min(shed_rate * priority_multipliers[priority], 1.0)
                if random.random() < adjusted_rate:
                    return True, f"priority shedding {priority.name} at {adjusted_rate*100:.1f}% rate"

            elif strategy == "lifo":
                # LIFO shedding: shed old requests, serve new ones
                # This ensures requests don't wait forever
                # If queue is deep, shed more aggressively
                queue_pressure = len(self.queue) / self.config.max_queue_depth
                combined_rate = min(shed_rate + queue_pressure * 0.5, 1.0)
                if random.random() < combined_rate:
                    return True, f"lifo shedding at {combined_rate*100:.1f}% rate (queue: {len(self.queue)})"

            return False, ""

    async def acquire(self) -> bool:
        """Acquire a processing slot. Returns True if acquired."""
        async with self._lock:
            if self.current_concurrent >= self.config.max_concurrent:
                return False
            self.current_concurrent += 1
            CONCURRENT_REQUESTS.labels(service=SERVICE_NAME).set(self.current_concurrent)
            return True

    async def release(self):
        """Release a processing slot."""
        async with self._lock:
            self.current_concurrent = max(0, self.current_concurrent - 1)
            CONCURRENT_REQUESTS.labels(service=SERVICE_NAME).set(self.current_concurrent)

    async def enqueue(self, request_id: str, timestamp: float):
        """Add request to queue for LIFO tracking."""
        async with self._lock:
            self.queue.append((request_id, timestamp))
            # Trim queue if too long
            while len(self.queue) > self.config.max_queue_depth:
                self.queue.popleft()  # Remove oldest
            QUEUE_DEPTH.labels(service=SERVICE_NAME).set(len(self.queue))

    async def dequeue(self, request_id: str):
        """Remove request from queue."""
        async with self._lock:
            # Find and remove from queue
            for i, (rid, _) in enumerate(self.queue):
                if rid == request_id:
                    del self.queue[i]
                    break
            QUEUE_DEPTH.labels(service=SERVICE_NAME).set(len(self.queue))


# Global load shedder instance
load_shedder = LoadShedder(LoadSheddingConfig(
    enabled=SHEDDING_ENABLED,
    strategy=SHEDDING_STRATEGY,
    max_queue_depth=MAX_QUEUE_DEPTH,
    max_concurrent=MAX_CONCURRENT_REQUESTS,
    shed_threshold_percent=SHED_THRESHOLD_PERCENT
))


# ===========================================
# Load Shedding Middleware
# ===========================================

class LoadSheddingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that implements load shedding at the request level.
    Returns 503 Service Unavailable with Retry-After header when shedding.
    """

    async def dispatch(self, request: Request, call_next):
        # Skip middleware for health/metrics endpoints
        if request.url.path in ["/health", "/metrics", "/admin/config"]:
            return await call_next(request)

        # Extract priority from header
        priority_header = request.headers.get("X-Priority", "normal").lower()
        priority_map = {
            "vip": Priority.VIP,
            "high": Priority.HIGH,
            "normal": Priority.NORMAL,
            "low": Priority.LOW
        }
        priority = priority_map.get(priority_header, Priority.NORMAL)

        # Generate request ID for tracking
        request_id = f"{time.time()}-{random.randint(1000, 9999)}"

        # Check if we should shed this request
        should_shed, reason = await load_shedder.should_shed(priority)

        if should_shed:
            # Record shed metrics
            REQUESTS_SHED.labels(
                service=SERVICE_NAME,
                strategy=load_shedder.config.strategy,
                priority=priority.name
            ).inc()
            SHED_BY_PRIORITY.labels(
                service=SERVICE_NAME,
                priority=priority.name
            ).inc()

            logger.warning(f"Shedding request {request_id}: {reason}")

            # Calculate retry-after based on current load
            retry_after = int(5 + load_shedder.should_shed_percent * 10)

            return JSONResponse(
                status_code=503,
                content={
                    "error": "Service Unavailable",
                    "message": "Server is under heavy load. Please retry later.",
                    "reason": reason,
                    "retry_after": retry_after
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-Shed-Reason": reason,
                    "X-Request-Priority": priority.name
                }
            )

        # Try to acquire a processing slot
        if not await load_shedder.acquire():
            logger.warning(f"Request {request_id} rejected: max concurrent reached")
            REQUESTS_SHED.labels(
                service=SERVICE_NAME,
                strategy="capacity",
                priority=priority.name
            ).inc()
            return JSONResponse(
                status_code=503,
                content={
                    "error": "Service Unavailable",
                    "message": "Server at maximum capacity",
                    "retry_after": 10
                },
                headers={"Retry-After": "10"}
            )

        try:
            # Track in queue for LIFO strategy
            await load_shedder.enqueue(request_id, time.time())

            # Process the request
            response = await call_next(request)
            return response

        finally:
            await load_shedder.release()
            await load_shedder.dequeue(request_id)


# ===========================================
# FastAPI Application
# ===========================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Load shedding: enabled={load_shedder.config.enabled}, strategy={load_shedder.config.strategy}")
    logger.info(f"Capacity: max_concurrent={load_shedder.config.max_concurrent}, threshold={load_shedder.config.shed_threshold_percent}%")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
app.add_middleware(LoadSheddingMiddleware)
FastAPIInstrumentor.instrument_app(app)


# ===========================================
# API Endpoints
# ===========================================

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "shedding_enabled": load_shedder.config.enabled
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/admin/config")
async def get_config():
    """Get current load shedding configuration."""
    return {
        "enabled": load_shedder.config.enabled,
        "strategy": load_shedder.config.strategy,
        "max_queue_depth": load_shedder.config.max_queue_depth,
        "max_concurrent": load_shedder.config.max_concurrent,
        "shed_threshold_percent": load_shedder.config.shed_threshold_percent,
        "current_concurrent": load_shedder.current_concurrent,
        "current_queue_depth": len(load_shedder.queue),
        "utilization_percent": load_shedder.utilization_percent,
        "shedding_rate": load_shedder.should_shed_percent
    }


@app.post("/admin/config")
async def update_config(
    enabled: Optional[bool] = None,
    strategy: Optional[str] = None,
    max_concurrent: Optional[int] = None,
    shed_threshold_percent: Optional[int] = None
):
    """Update load shedding configuration at runtime."""
    if enabled is not None:
        load_shedder.config.enabled = enabled
        logger.info(f"Load shedding {'enabled' if enabled else 'disabled'}")

    if strategy is not None:
        if strategy not in ["random", "priority", "lifo"]:
            raise HTTPException(400, f"Invalid strategy: {strategy}. Use: random, priority, lifo")
        load_shedder.config.strategy = strategy
        logger.info(f"Shedding strategy set to: {strategy}")

    if max_concurrent is not None:
        load_shedder.config.max_concurrent = max_concurrent
        logger.info(f"Max concurrent set to: {max_concurrent}")

    if shed_threshold_percent is not None:
        if not 0 <= shed_threshold_percent <= 100:
            raise HTTPException(400, "shed_threshold_percent must be 0-100")
        load_shedder.config.shed_threshold_percent = shed_threshold_percent
        logger.info(f"Shed threshold set to: {shed_threshold_percent}%")

    return await get_config()


@app.get("/api/process")
async def api_process(
    request: Request,
    x_priority: Optional[str] = Header(None, alias="X-Priority")
):
    """
    Main API endpoint - processes request through worker service.

    Headers:
    - X-Priority: vip, high, normal, low (default: normal)
    """
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    priority = x_priority or "normal"
    current_span.set_attribute("request.priority", priority)

    # Call worker service
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{WORKER_URL}/work",
            headers={"X-Priority": priority}
        )
        worker_result = response.json()

    duration = time.time() - start_time

    # Record metrics
    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/api/process",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/api/process"
    ).observe(duration)

    logger.info(f"Request completed trace_id={trace_id} duration={duration*1000:.0f}ms priority={priority}")

    return {
        "service": SERVICE_NAME,
        "trace_id": trace_id,
        "duration_ms": round(duration * 1000, 2),
        "priority": priority,
        "worker_result": worker_result,
        "shedding": {
            "enabled": load_shedder.config.enabled,
            "strategy": load_shedder.config.strategy,
            "utilization_percent": round(load_shedder.utilization_percent, 1)
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
