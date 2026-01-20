"""
Frontend Service - Entry point with limited concurrency (50 concurrent requests).

This service demonstrates how limited concurrency can cause cascading failures
when downstream services slow down.
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "frontend")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
DOWNSTREAM_URL = os.getenv("DOWNSTREAM_URL", "http://api:8001")
LATENCY_MIN_MS = int(os.getenv("LATENCY_MIN_MS", "5"))
LATENCY_MAX_MS = int(os.getenv("LATENCY_MAX_MS", "15"))
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "50"))
TIMEOUT_SECONDS = float(os.getenv("TIMEOUT_SECONDS", "10.0"))

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
TIMEOUTS_TOTAL = Counter(
    "timeouts_total",
    "Total number of timeout errors",
    ["service", "downstream"]
)
REJECTIONS_TOTAL = Counter(
    "rejections_total",
    "Total number of rejected requests due to queue overflow",
    ["service"]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Downstream URL: {DOWNSTREAM_URL}")
    logger.info(f"Max concurrent requests: {MAX_CONCURRENT}")
    logger.info(f"Timeout: {TIMEOUT_SECONDS}s")
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
        "timeout_seconds": TIMEOUT_SECONDS
    }


@app.get("/api/order")
async def api_order(request: Request):
    """Main API endpoint - processes an order through the chain."""
    global active_requests, queued_requests

    start_time = time.time()
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Track queued requests
    queued_requests += 1
    QUEUED_REQUESTS.labels(service=SERVICE_NAME).set(queued_requests)

    try:
        # Acquire semaphore with timeout to prevent indefinite waiting
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            queued_requests -= 1
            QUEUED_REQUESTS.labels(service=SERVICE_NAME).set(queued_requests)
            REJECTIONS_TOTAL.labels(service=SERVICE_NAME).inc()
            REQUEST_COUNT.labels(
                service=SERVICE_NAME,
                method="GET",
                endpoint="/api/order",
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
            # Simulate frontend processing time
            latency_ms = random.randint(LATENCY_MIN_MS, LATENCY_MAX_MS)
            await asyncio.sleep(latency_ms / 1000.0)
            current_span.set_attribute("frontend_latency_ms", latency_ms)

            # Call downstream service with timeout
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                try:
                    response = await client.get(f"{DOWNSTREAM_URL}/process")
                    response.raise_for_status()
                    downstream_result = response.json()
                except httpx.TimeoutException:
                    TIMEOUTS_TOTAL.labels(service=SERVICE_NAME, downstream="api").inc()
                    REQUEST_COUNT.labels(
                        service=SERVICE_NAME,
                        method="GET",
                        endpoint="/api/order",
                        status="504"
                    ).inc()
                    logger.error(f"Downstream timeout trace_id={trace_id}")
                    raise HTTPException(status_code=504, detail="Downstream service timeout")
                except httpx.HTTPStatusError as e:
                    REQUEST_COUNT.labels(
                        service=SERVICE_NAME,
                        method="GET",
                        endpoint="/api/order",
                        status=str(e.response.status_code)
                    ).inc()
                    raise HTTPException(status_code=e.response.status_code, detail="Downstream error")

            duration = time.time() - start_time

            # Record metrics
            REQUEST_COUNT.labels(
                service=SERVICE_NAME,
                method="GET",
                endpoint="/api/order",
                status="200"
            ).inc()
            REQUEST_LATENCY.labels(
                service=SERVICE_NAME,
                method="GET",
                endpoint="/api/order"
            ).observe(duration)

            logger.info(f"Request completed trace_id={trace_id} duration={duration*1000:.0f}ms")

            return {
                "service": SERVICE_NAME,
                "total_duration_ms": round(duration * 1000, 2),
                "frontend_latency_ms": latency_ms,
                "trace_id": trace_id,
                "chain": downstream_result
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
            endpoint="/api/order",
            status="500"
        ).inc()
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
