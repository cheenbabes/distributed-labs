"""
Inventory Service - Leaf service with limited concurrency (10 concurrent requests).

This is the leaf service in the chain that can be slowed down or made to fail
to demonstrate cascading failure patterns.
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
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "inventory")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
LATENCY_MIN_MS = int(os.getenv("LATENCY_MIN_MS", "20"))
LATENCY_MAX_MS = int(os.getenv("LATENCY_MAX_MS", "50"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "10"))
TIMEOUT_SECONDS = float(os.getenv("TIMEOUT_SECONDS", "5.0"))

# Admin configuration state (mutable at runtime)
admin_config = {
    "slow_enabled": os.getenv("SLOW_ENABLED", "false").lower() == "true",
    "slow_latency_ms": int(os.getenv("SLOW_LATENCY_MS", "2000")),
    "fail_enabled": os.getenv("FAIL_ENABLED", "false").lower() == "true",
    "fail_rate": float(os.getenv("FAIL_RATE", "1.0")),  # 0.0 to 1.0
}

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

# Concurrency control
semaphore = asyncio.Semaphore(MAX_CONCURRENT)
active_requests = 0
queued_requests = 0

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
ACTIVE_REQUESTS = Gauge(
    "active_requests",
    "Number of requests currently being processed",
    ["service"]
)
QUEUED_REQUESTS = Gauge(
    "queued_requests",
    "Number of requests waiting in queue",
    ["service"]
)
INJECTED_FAILURES = Counter(
    "injected_failures_total",
    "Total number of injected failures",
    ["service"]
)
REJECTIONS_TOTAL = Counter(
    "rejections_total",
    "Total number of rejected requests due to queue overflow",
    ["service"]
)


class SlowConfig(BaseModel):
    enabled: bool
    latency_ms: int = 2000


class FailConfig(BaseModel):
    enabled: bool
    rate: float = 1.0  # 0.0 to 1.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Latency range: {LATENCY_MIN_MS}-{LATENCY_MAX_MS}ms")
    logger.info(f"Max concurrent requests: {MAX_CONCURRENT}")
    logger.info(f"Slow injection: {'enabled' if admin_config['slow_enabled'] else 'disabled'}")
    logger.info(f"Fail injection: {'enabled' if admin_config['fail_enabled'] else 'disabled'}")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "active_requests": active_requests,
        "max_concurrent": MAX_CONCURRENT
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
async def status():
    """Get current service status including concurrency metrics."""
    return {
        "service": SERVICE_NAME,
        "active_requests": active_requests,
        "queued_requests": queued_requests,
        "max_concurrent": MAX_CONCURRENT,
        "available_slots": MAX_CONCURRENT - active_requests,
        "admin_config": admin_config
    }


# ============ Admin Endpoints ============

@app.get("/admin/slow")
async def get_slow_config():
    """Get current slow injection configuration."""
    return {
        "enabled": admin_config["slow_enabled"],
        "latency_ms": admin_config["slow_latency_ms"]
    }


@app.post("/admin/slow")
async def set_slow_config(config: SlowConfig):
    """Enable/disable slow mode with configurable latency."""
    admin_config["slow_enabled"] = config.enabled
    admin_config["slow_latency_ms"] = config.latency_ms
    logger.info(f"Slow injection updated: enabled={config.enabled}, latency_ms={config.latency_ms}")
    return {
        "enabled": admin_config["slow_enabled"],
        "latency_ms": admin_config["slow_latency_ms"]
    }


@app.get("/admin/fail")
async def get_fail_config():
    """Get current fail injection configuration."""
    return {
        "enabled": admin_config["fail_enabled"],
        "rate": admin_config["fail_rate"]
    }


@app.post("/admin/fail")
async def set_fail_config(config: FailConfig):
    """Enable/disable failure injection with configurable rate."""
    admin_config["fail_enabled"] = config.enabled
    admin_config["fail_rate"] = max(0.0, min(1.0, config.rate))
    logger.info(f"Fail injection updated: enabled={config.enabled}, rate={admin_config['fail_rate']}")
    return {
        "enabled": admin_config["fail_enabled"],
        "rate": admin_config["fail_rate"]
    }


@app.post("/admin/reset")
async def reset_config():
    """Reset all admin configurations to default (healthy) state."""
    admin_config["slow_enabled"] = False
    admin_config["slow_latency_ms"] = 2000
    admin_config["fail_enabled"] = False
    admin_config["fail_rate"] = 1.0
    logger.info("Admin config reset to defaults")
    return {"status": "reset", "config": admin_config}


# ============ Main Endpoint ============

@app.get("/check")
async def check_inventory(request: Request):
    """Check inventory availability - this is the leaf service."""
    global active_requests, queued_requests

    start_time = time.time()
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Track queued requests
    queued_requests += 1
    QUEUED_REQUESTS.labels(service=SERVICE_NAME).set(queued_requests)

    try:
        # Acquire semaphore with timeout
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            queued_requests -= 1
            QUEUED_REQUESTS.labels(service=SERVICE_NAME).set(queued_requests)
            REJECTIONS_TOTAL.labels(service=SERVICE_NAME).inc()
            REQUEST_COUNT.labels(
                service=SERVICE_NAME,
                method="GET",
                endpoint="/check",
                status="503"
            ).inc()
            logger.warning(f"Request rejected - queue timeout trace_id={trace_id}")
            raise HTTPException(status_code=503, detail="Service overloaded - queue timeout")

        queued_requests -= 1
        QUEUED_REQUESTS.labels(service=SERVICE_NAME).set(queued_requests)

        active_requests += 1
        ACTIVE_REQUESTS.labels(service=SERVICE_NAME).set(active_requests)
        current_span.set_attribute("active_requests", active_requests)
        current_span.set_attribute("max_concurrent", MAX_CONCURRENT)

        try:
            # Check for failure injection
            if admin_config["fail_enabled"]:
                if random.random() < admin_config["fail_rate"]:
                    INJECTED_FAILURES.labels(service=SERVICE_NAME).inc()
                    REQUEST_COUNT.labels(
                        service=SERVICE_NAME,
                        method="GET",
                        endpoint="/check",
                        status="500"
                    ).inc()
                    current_span.set_attribute("injected_failure", True)
                    logger.warning(f"Injected failure trace_id={trace_id}")
                    raise HTTPException(status_code=500, detail="Injected failure - inventory check failed")

            # Add base latency (simulates real work)
            base_latency_ms = random.randint(LATENCY_MIN_MS, LATENCY_MAX_MS)

            # Add injected latency if enabled
            injected_latency_ms = 0
            if admin_config["slow_enabled"]:
                injected_latency_ms = admin_config["slow_latency_ms"]
                current_span.set_attribute("injected_latency_ms", injected_latency_ms)
                current_span.set_attribute("slow_injection_enabled", True)

            total_latency_ms = base_latency_ms + injected_latency_ms

            # Actually sleep
            await asyncio.sleep(total_latency_ms / 1000.0)

            # Record span attributes
            current_span.set_attribute("base_latency_ms", base_latency_ms)
            current_span.set_attribute("total_latency_ms", total_latency_ms)

            duration = time.time() - start_time

            # Record metrics
            REQUEST_COUNT.labels(
                service=SERVICE_NAME,
                method="GET",
                endpoint="/check",
                status="200"
            ).inc()
            REQUEST_LATENCY.labels(
                service=SERVICE_NAME,
                method="GET",
                endpoint="/check"
            ).observe(duration)

            logger.info(f"Inventory check completed trace_id={trace_id} latency={total_latency_ms}ms")

            return {
                "service": SERVICE_NAME,
                "available": True,
                "stock": random.randint(10, 100),
                "latency_ms": total_latency_ms,
                "base_latency_ms": base_latency_ms,
                "injected_latency_ms": injected_latency_ms,
                "trace_id": trace_id
            }
        finally:
            active_requests -= 1
            ACTIVE_REQUESTS.labels(service=SERVICE_NAME).set(active_requests)
            semaphore.release()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/check",
            status="500"
        ).inc()
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
