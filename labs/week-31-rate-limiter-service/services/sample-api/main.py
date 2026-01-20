"""
Sample API Service - Demonstrates integration with the Rate Limiter Service.

This service uses the external rate limiter service to protect its endpoints.
Shows how to:
- Check rate limits before processing requests
- Include rate limit headers in responses
- Handle rate limiter service failures gracefully (fail open/closed)
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
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
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "sample-api")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
INSTANCE_ID = os.getenv("INSTANCE_ID", "api-1")
RATE_LIMITER_URL = os.getenv("RATE_LIMITER_URL", "http://rate-limiter:8000")
CLIENT_ID = os.getenv("CLIENT_ID", "sample-api")
FAILOPEN = os.getenv("FAILOPEN", "true").lower() == "true"
RATE_LIMIT_TIMEOUT = float(os.getenv("RATE_LIMIT_TIMEOUT", "0.1"))  # 100ms

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

# Instrument httpx
HTTPXClientInstrumentor().instrument()

# Prometheus metrics
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "instance", "method", "endpoint", "status"]
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["service", "instance", "method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

RATE_LIMIT_CHECK_LATENCY = Histogram(
    "rate_limit_check_duration_seconds",
    "Latency of rate limit checks",
    ["service", "instance"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25]
)

RATE_LIMIT_STATUS = Counter(
    "rate_limit_check_status_total",
    "Rate limit check outcomes",
    ["service", "instance", "status"]  # status: allowed, denied, error, failopen
)


class RateLimitClient:
    """Client for the Rate Limiter Service."""

    def __init__(self, base_url: str, client_id: str, failopen: bool = True, timeout: float = 0.1):
        self.base_url = base_url
        self.client_id = client_id
        self.failopen = failopen
        self.timeout = timeout

    async def check(
        self,
        resource: str,
        cost: int = 1,
        override_client_id: Optional[str] = None
    ) -> dict:
        """
        Check rate limit for a request.

        Returns:
            dict with keys: allowed, limit, remaining, reset_at, algorithm, retry_after, error
        """
        client_id = override_client_id or self.client_id

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                start_time = time.time()

                response = await client.post(
                    f"{self.base_url}/v1/check",
                    json={
                        "client_id": client_id,
                        "resource": resource,
                        "cost": cost
                    }
                )

                duration = time.time() - start_time
                RATE_LIMIT_CHECK_LATENCY.labels(
                    service=SERVICE_NAME,
                    instance=INSTANCE_ID
                ).observe(duration)

                if response.status_code == 200:
                    data = response.json()
                    status = "allowed" if data["allowed"] else "denied"
                    RATE_LIMIT_STATUS.labels(
                        service=SERVICE_NAME,
                        instance=INSTANCE_ID,
                        status=status
                    ).inc()
                    return {**data, "error": None}

                # Non-200 response
                logger.warning(f"Rate limiter returned {response.status_code}: {response.text}")
                raise Exception(f"Rate limiter error: {response.status_code}")

        except httpx.TimeoutException:
            logger.warning(f"Rate limiter timeout after {self.timeout}s")
            RATE_LIMIT_STATUS.labels(
                service=SERVICE_NAME,
                instance=INSTANCE_ID,
                status="timeout"
            ).inc()

            if self.failopen:
                RATE_LIMIT_STATUS.labels(
                    service=SERVICE_NAME,
                    instance=INSTANCE_ID,
                    status="failopen"
                ).inc()
                return {
                    "allowed": True,
                    "limit": 0,
                    "remaining": 0,
                    "reset_at": 0,
                    "algorithm": "failopen",
                    "retry_after": 0,
                    "error": "timeout"
                }
            raise

        except Exception as e:
            logger.error(f"Rate limiter error: {e}")
            RATE_LIMIT_STATUS.labels(
                service=SERVICE_NAME,
                instance=INSTANCE_ID,
                status="error"
            ).inc()

            if self.failopen:
                RATE_LIMIT_STATUS.labels(
                    service=SERVICE_NAME,
                    instance=INSTANCE_ID,
                    status="failopen"
                ).inc()
                return {
                    "allowed": True,
                    "limit": 0,
                    "remaining": 0,
                    "reset_at": 0,
                    "algorithm": "failopen",
                    "retry_after": 0,
                    "error": str(e)
                }
            raise


# Rate limiter client
rate_limiter = RateLimitClient(
    base_url=RATE_LIMITER_URL,
    client_id=CLIENT_ID,
    failopen=FAILOPEN,
    timeout=RATE_LIMIT_TIMEOUT
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info(f"{SERVICE_NAME} ({INSTANCE_ID}) starting up")
    logger.info(f"Rate limiter URL: {RATE_LIMITER_URL}")
    logger.info(f"Fail open: {FAILOPEN}")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(
    title=f"Sample API ({INSTANCE_ID})",
    description="Sample API demonstrating rate limiter integration",
    version="1.0.0",
    lifespan=lifespan
)

FastAPIInstrumentor.instrument_app(app)


def add_rate_limit_headers(response: JSONResponse, rate_limit_info: dict) -> JSONResponse:
    """Add rate limit headers to response."""
    if rate_limit_info.get("algorithm") != "failopen":
        response.headers["X-RateLimit-Limit"] = str(rate_limit_info.get("limit", 0))
        response.headers["X-RateLimit-Remaining"] = str(rate_limit_info.get("remaining", 0))
        response.headers["X-RateLimit-Reset"] = str(rate_limit_info.get("reset_at", 0))
        response.headers["X-RateLimit-Algorithm"] = rate_limit_info.get("algorithm", "unknown")

    if rate_limit_info.get("retry_after", 0) > 0:
        response.headers["Retry-After"] = str(rate_limit_info["retry_after"])

    return response


@app.get("/health")
async def health():
    """Health check endpoint - not rate limited."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "instance": INSTANCE_ID
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint - not rate limited."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/data")
async def get_data(
    request: Request,
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-ID")
):
    """
    Sample data endpoint - rate limited.

    The client can optionally specify their ID via X-Client-ID header
    for per-client rate limiting.
    """
    start_time = time.time()
    endpoint = "/api/data"

    # Use client-specified ID or default
    client_id = x_client_id or CLIENT_ID

    # Check rate limit
    rate_limit_info = await rate_limiter.check(
        resource=endpoint,
        override_client_id=client_id
    )

    if not rate_limit_info["allowed"]:
        # Rate limited - return 429
        duration = time.time() - start_time
        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            instance=INSTANCE_ID,
            method="GET",
            endpoint=endpoint,
            status="429"
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME,
            instance=INSTANCE_ID,
            method="GET",
            endpoint=endpoint
        ).observe(duration)

        response = JSONResponse(
            status_code=429,
            content={
                "error": "Too Many Requests",
                "retry_after": rate_limit_info.get("retry_after", 0),
                "limit": rate_limit_info.get("limit", 0),
                "remaining": 0
            }
        )
        return add_rate_limit_headers(response, rate_limit_info)

    # Process the request (simulate work)
    await asyncio.sleep(random.uniform(0.01, 0.05))

    duration = time.time() - start_time
    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        instance=INSTANCE_ID,
        method="GET",
        endpoint=endpoint,
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        instance=INSTANCE_ID,
        method="GET",
        endpoint=endpoint
    ).observe(duration)

    response = JSONResponse(
        content={
            "data": {
                "id": random.randint(1000, 9999),
                "value": f"sample-data-{random.randint(1, 100)}",
                "timestamp": int(time.time())
            },
            "instance": INSTANCE_ID,
            "client_id": client_id,
            "processing_time_ms": round((duration - RATE_LIMIT_TIMEOUT) * 1000, 2)
        }
    )
    return add_rate_limit_headers(response, rate_limit_info)


@app.post("/api/resource")
async def create_resource(
    request: Request,
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-ID")
):
    """
    Sample resource creation endpoint - rate limited with higher cost.

    Write operations often have higher cost in rate limiting.
    """
    start_time = time.time()
    endpoint = "/api/resource"

    client_id = x_client_id or CLIENT_ID

    # Check rate limit with higher cost for writes
    rate_limit_info = await rate_limiter.check(
        resource=endpoint,
        cost=5,  # Writes cost 5 tokens
        override_client_id=client_id
    )

    if not rate_limit_info["allowed"]:
        duration = time.time() - start_time
        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            instance=INSTANCE_ID,
            method="POST",
            endpoint=endpoint,
            status="429"
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME,
            instance=INSTANCE_ID,
            method="POST",
            endpoint=endpoint
        ).observe(duration)

        response = JSONResponse(
            status_code=429,
            content={
                "error": "Too Many Requests",
                "retry_after": rate_limit_info.get("retry_after", 0),
                "limit": rate_limit_info.get("limit", 0),
                "remaining": 0
            }
        )
        return add_rate_limit_headers(response, rate_limit_info)

    # Process write (simulate)
    await asyncio.sleep(random.uniform(0.05, 0.15))

    duration = time.time() - start_time
    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        instance=INSTANCE_ID,
        method="POST",
        endpoint=endpoint,
        status="201"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        instance=INSTANCE_ID,
        method="POST",
        endpoint=endpoint
    ).observe(duration)

    response = JSONResponse(
        status_code=201,
        content={
            "resource": {
                "id": random.randint(10000, 99999),
                "created_at": int(time.time())
            },
            "instance": INSTANCE_ID,
            "client_id": client_id
        }
    )
    return add_rate_limit_headers(response, rate_limit_info)


@app.get("/api/expensive")
async def expensive_operation(
    request: Request,
    x_client_id: Optional[str] = Header(default=None, alias="X-Client-ID")
):
    """
    Expensive operation endpoint - separate rate limit tier.

    Uses a different resource path for separate rate limiting.
    """
    start_time = time.time()
    endpoint = "/api/expensive"

    client_id = x_client_id or CLIENT_ID

    # Check rate limit for expensive operations (separate bucket)
    rate_limit_info = await rate_limiter.check(
        resource="expensive",  # Separate resource for expensive operations
        cost=10,
        override_client_id=client_id
    )

    if not rate_limit_info["allowed"]:
        duration = time.time() - start_time
        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            instance=INSTANCE_ID,
            method="GET",
            endpoint=endpoint,
            status="429"
        ).inc()

        response = JSONResponse(
            status_code=429,
            content={
                "error": "Too Many Requests",
                "message": "Expensive operation rate limit exceeded",
                "retry_after": rate_limit_info.get("retry_after", 0)
            }
        )
        return add_rate_limit_headers(response, rate_limit_info)

    # Simulate expensive operation
    await asyncio.sleep(random.uniform(0.2, 0.5))

    duration = time.time() - start_time
    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        instance=INSTANCE_ID,
        method="GET",
        endpoint=endpoint,
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        instance=INSTANCE_ID,
        method="GET",
        endpoint=endpoint
    ).observe(duration)

    response = JSONResponse(
        content={
            "result": {
                "computed_value": random.random() * 1000,
                "computation_time_ms": round(duration * 1000, 2)
            },
            "instance": INSTANCE_ID
        }
    )
    return add_rate_limit_headers(response, rate_limit_info)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
