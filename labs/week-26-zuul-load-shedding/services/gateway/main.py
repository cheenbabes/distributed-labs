"""
Gateway Service - Netflix Zuul-style load shedding implementation.

This gateway demonstrates various load shedding strategies:
1. Concurrency limit (max in-flight requests)
2. Latency-based shedding (shed when p99 exceeds threshold)
3. Error-rate-based shedding (shed when errors exceed threshold)
4. Adaptive concurrency (Vegas algorithm style)
5. Priority-based shedding (VIP vs normal traffic)
"""
import asyncio
import logging
import os
import time
import random
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import httpx
from fastapi import FastAPI, Request, HTTPException, Header
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
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8001")

# Load shedding configuration (can be overridden via API)
LOAD_SHEDDING_ENABLED = os.getenv("LOAD_SHEDDING_ENABLED", "false").lower() == "true"
MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_CONCURRENT_REQUESTS", "50"))
LATENCY_THRESHOLD_MS = int(os.getenv("LATENCY_THRESHOLD_MS", "500"))
ERROR_RATE_THRESHOLD = float(os.getenv("ERROR_RATE_THRESHOLD", "0.1"))
ADAPTIVE_ENABLED = os.getenv("ADAPTIVE_ENABLED", "false").lower() == "true"
PRIORITY_ENABLED = os.getenv("PRIORITY_ENABLED", "false").lower() == "true"

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


class ShedReason(str, Enum):
    NONE = "none"
    CONCURRENCY = "concurrency_limit"
    LATENCY = "high_latency"
    ERROR_RATE = "high_error_rate"
    ADAPTIVE = "adaptive_limit"


class Priority(str, Enum):
    VIP = "vip"
    NORMAL = "normal"


# Prometheus metrics
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "endpoint", "status", "priority"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["service", "method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)
REQUESTS_SHED = Counter(
    "requests_shed_total",
    "Total requests shed (rejected)",
    ["service", "reason", "priority"]
)
CONCURRENT_REQUESTS = Gauge(
    "concurrent_requests",
    "Current number of in-flight requests",
    ["service"]
)
ADAPTIVE_LIMIT = Gauge(
    "adaptive_concurrency_limit",
    "Current adaptive concurrency limit",
    ["service"]
)
LATENCY_P99 = Gauge(
    "latency_p99_ms",
    "Current p99 latency in milliseconds",
    ["service"]
)
ERROR_RATE = Gauge(
    "error_rate",
    "Current error rate (0-1)",
    ["service"]
)


@dataclass
class LoadSheddingConfig:
    """Configuration for load shedding behavior."""
    enabled: bool = LOAD_SHEDDING_ENABLED
    max_concurrent: int = MAX_CONCURRENT_REQUESTS
    latency_threshold_ms: int = LATENCY_THRESHOLD_MS
    error_rate_threshold: float = ERROR_RATE_THRESHOLD
    adaptive_enabled: bool = ADAPTIVE_ENABLED
    priority_enabled: bool = PRIORITY_ENABLED
    # Adaptive algorithm parameters (Vegas-style)
    adaptive_min_limit: int = 10
    adaptive_max_limit: int = 200
    adaptive_smoothing: float = 0.2
    # Strategy toggles
    concurrency_limit_enabled: bool = True
    latency_shedding_enabled: bool = False
    error_rate_shedding_enabled: bool = False


@dataclass
class LoadSheddingState:
    """Runtime state for load shedding decisions."""
    current_concurrent: int = 0
    adaptive_limit: float = 50.0
    latencies: deque = field(default_factory=lambda: deque(maxlen=100))
    errors: deque = field(default_factory=lambda: deque(maxlen=100))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def get_p99_latency_ms(self) -> float:
        """Calculate p99 latency from recent requests."""
        if len(self.latencies) < 10:
            return 0.0
        sorted_latencies = sorted(self.latencies)
        p99_idx = int(len(sorted_latencies) * 0.99)
        return sorted_latencies[p99_idx]

    def get_error_rate(self) -> float:
        """Calculate error rate from recent requests."""
        if len(self.errors) < 10:
            return 0.0
        return sum(self.errors) / len(self.errors)

    def record_request(self, latency_ms: float, is_error: bool):
        """Record a completed request for statistics."""
        self.latencies.append(latency_ms)
        self.errors.append(1 if is_error else 0)


# Global state
config = LoadSheddingConfig()
state = LoadSheddingState()


def should_shed(priority: Priority) -> tuple[bool, ShedReason]:
    """
    Determine if a request should be shed based on current conditions.
    Returns (should_shed, reason).
    """
    if not config.enabled:
        return False, ShedReason.NONE

    # VIP traffic gets through unless we're critically overloaded
    vip_multiplier = 2.0 if priority == Priority.VIP and config.priority_enabled else 1.0

    # 1. Check concurrency limit
    if config.concurrency_limit_enabled:
        effective_limit = config.max_concurrent * vip_multiplier
        if config.adaptive_enabled:
            effective_limit = min(effective_limit, state.adaptive_limit * vip_multiplier)

        if state.current_concurrent >= effective_limit:
            return True, ShedReason.CONCURRENCY if not config.adaptive_enabled else ShedReason.ADAPTIVE

    # 2. Check latency-based shedding
    if config.latency_shedding_enabled:
        p99 = state.get_p99_latency_ms()
        LATENCY_P99.labels(service=SERVICE_NAME).set(p99)

        # For VIP, allow 2x the latency threshold
        effective_threshold = config.latency_threshold_ms * vip_multiplier
        if p99 > effective_threshold:
            return True, ShedReason.LATENCY

    # 3. Check error-rate-based shedding
    if config.error_rate_shedding_enabled:
        error_rate = state.get_error_rate()
        ERROR_RATE.labels(service=SERVICE_NAME).set(error_rate)

        # For VIP, allow 2x the error rate threshold
        effective_threshold = config.error_rate_threshold * vip_multiplier
        if error_rate > effective_threshold:
            return True, ShedReason.ERROR_RATE

    return False, ShedReason.NONE


def update_adaptive_limit(latency_ms: float):
    """
    Update adaptive concurrency limit using Vegas algorithm style.

    The idea is to increase the limit when latency is low (we have headroom)
    and decrease it when latency is high (we're overloaded).
    """
    if not config.adaptive_enabled:
        return

    target_latency = config.latency_threshold_ms * 0.5  # Target 50% of threshold

    # Calculate gradient: negative means we should increase, positive means decrease
    gradient = (latency_ms - target_latency) / target_latency

    # Apply smoothed update
    if gradient < 0:
        # Latency is low, increase limit
        new_limit = state.adaptive_limit * (1 + config.adaptive_smoothing * abs(gradient))
    else:
        # Latency is high, decrease limit
        new_limit = state.adaptive_limit * (1 - config.adaptive_smoothing * min(gradient, 0.5))

    # Clamp to bounds
    state.adaptive_limit = max(
        config.adaptive_min_limit,
        min(config.adaptive_max_limit, new_limit)
    )

    ADAPTIVE_LIMIT.labels(service=SERVICE_NAME).set(state.adaptive_limit)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Backend URL: {BACKEND_URL}")
    logger.info(f"Load shedding enabled: {config.enabled}")
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
    """Get current load shedding configuration."""
    return {
        "enabled": config.enabled,
        "max_concurrent": config.max_concurrent,
        "latency_threshold_ms": config.latency_threshold_ms,
        "error_rate_threshold": config.error_rate_threshold,
        "adaptive_enabled": config.adaptive_enabled,
        "priority_enabled": config.priority_enabled,
        "concurrency_limit_enabled": config.concurrency_limit_enabled,
        "latency_shedding_enabled": config.latency_shedding_enabled,
        "error_rate_shedding_enabled": config.error_rate_shedding_enabled,
        "adaptive_min_limit": config.adaptive_min_limit,
        "adaptive_max_limit": config.adaptive_max_limit,
    }


@app.post("/config")
async def update_config(new_config: dict):
    """Update load shedding configuration dynamically."""
    global config

    if "enabled" in new_config:
        config.enabled = new_config["enabled"]
    if "max_concurrent" in new_config:
        config.max_concurrent = new_config["max_concurrent"]
    if "latency_threshold_ms" in new_config:
        config.latency_threshold_ms = new_config["latency_threshold_ms"]
    if "error_rate_threshold" in new_config:
        config.error_rate_threshold = new_config["error_rate_threshold"]
    if "adaptive_enabled" in new_config:
        config.adaptive_enabled = new_config["adaptive_enabled"]
    if "priority_enabled" in new_config:
        config.priority_enabled = new_config["priority_enabled"]
    if "concurrency_limit_enabled" in new_config:
        config.concurrency_limit_enabled = new_config["concurrency_limit_enabled"]
    if "latency_shedding_enabled" in new_config:
        config.latency_shedding_enabled = new_config["latency_shedding_enabled"]
    if "error_rate_shedding_enabled" in new_config:
        config.error_rate_shedding_enabled = new_config["error_rate_shedding_enabled"]

    logger.info(f"Config updated: {new_config}")
    return {"status": "updated", "config": await get_config()}


@app.get("/stats")
async def get_stats():
    """Get current load shedding statistics."""
    return {
        "current_concurrent": state.current_concurrent,
        "adaptive_limit": state.adaptive_limit,
        "p99_latency_ms": state.get_p99_latency_ms(),
        "error_rate": state.get_error_rate(),
        "recent_latencies_count": len(state.latencies),
        "recent_errors_count": len(state.errors),
    }


@app.get("/api/process")
async def api_process(
    request: Request,
    x_priority: Optional[str] = Header(None, alias="X-Priority")
):
    """
    Main API endpoint - proxies to backend with load shedding.

    Use X-Priority: vip header for VIP traffic (when priority_enabled=true).
    """
    start_time = time.time()

    # Determine request priority
    priority = Priority.VIP if x_priority and x_priority.lower() == "vip" else Priority.NORMAL

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Check if we should shed this request
    shed, reason = should_shed(priority)

    if shed:
        REQUESTS_SHED.labels(
            service=SERVICE_NAME,
            reason=reason.value,
            priority=priority.value
        ).inc()

        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/api/process",
            status="503",
            priority=priority.value
        ).inc()

        current_span.set_attribute("load_shed", True)
        current_span.set_attribute("shed_reason", reason.value)

        logger.warning(f"Request shed: reason={reason.value}, priority={priority.value}, trace_id={trace_id}")

        raise HTTPException(
            status_code=503,
            detail={
                "error": "Service temporarily unavailable due to load shedding",
                "reason": reason.value,
                "trace_id": trace_id,
            }
        )

    # Track concurrent requests
    async with state.lock:
        state.current_concurrent += 1
    CONCURRENT_REQUESTS.labels(service=SERVICE_NAME).set(state.current_concurrent)

    try:
        # Call backend service
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{BACKEND_URL}/process")
            is_error = response.status_code >= 500

            if response.status_code >= 400:
                backend_result = {"error": response.text, "status_code": response.status_code}
            else:
                backend_result = response.json()

        duration = time.time() - start_time
        latency_ms = duration * 1000

        # Record metrics
        state.record_request(latency_ms, is_error)
        update_adaptive_limit(latency_ms)

        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/api/process",
            status=str(response.status_code),
            priority=priority.value
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/api/process"
        ).observe(duration)

        current_span.set_attribute("load_shed", False)
        current_span.set_attribute("priority", priority.value)
        current_span.set_attribute("latency_ms", latency_ms)

        logger.info(f"Request completed trace_id={trace_id} duration={latency_ms:.0f}ms priority={priority.value}")

        return {
            "service": SERVICE_NAME,
            "total_duration_ms": round(latency_ms, 2),
            "trace_id": trace_id,
            "priority": priority.value,
            "load_shedding": {
                "active": config.enabled,
                "current_concurrent": state.current_concurrent,
                "adaptive_limit": state.adaptive_limit if config.adaptive_enabled else None,
            },
            "backend": backend_result
        }

    except httpx.TimeoutException:
        duration = time.time() - start_time
        state.record_request(duration * 1000, is_error=True)

        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/api/process",
            status="504",
            priority=priority.value
        ).inc()

        raise HTTPException(
            status_code=504,
            detail={"error": "Backend timeout", "trace_id": trace_id}
        )

    except Exception as e:
        duration = time.time() - start_time
        state.record_request(duration * 1000, is_error=True)

        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/api/process",
            status="500",
            priority=priority.value
        ).inc()

        logger.error(f"Request failed: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": str(e), "trace_id": trace_id}
        )

    finally:
        async with state.lock:
            state.current_concurrent -= 1
        CONCURRENT_REQUESTS.labels(service=SERVICE_NAME).set(state.current_concurrent)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
