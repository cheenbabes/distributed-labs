"""
Rate Limiter Service - Production-grade rate limiting microservice.

Features:
- Multiple algorithms (token bucket, sliding window)
- REST and gRPC APIs
- Dynamic configuration per client/resource
- Redis backend for distributed state
- Full observability with OTEL and Prometheus
"""
import asyncio
import logging
import os
import time
import threading
from concurrent import futures
from contextlib import asynccontextmanager
from typing import Optional

import grpc
import redis
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from algorithms import TokenBucketLimiter, SlidingWindowLimiter, SlidingWindowCounterLimiter, RateLimitResult
from config_store import ConfigStore, RateLimitConfig
import metrics

# Import generated gRPC code
import ratelimiter_pb2
import ratelimiter_pb2_grpc

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "rate-limiter")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

DEFAULT_RATE_LIMIT = int(os.getenv("DEFAULT_RATE_LIMIT", "100"))
DEFAULT_WINDOW_SECONDS = int(os.getenv("DEFAULT_WINDOW_SECONDS", "60"))
DEFAULT_BUCKET_CAPACITY = int(os.getenv("DEFAULT_BUCKET_CAPACITY", "100"))
DEFAULT_REFILL_RATE = float(os.getenv("DEFAULT_REFILL_RATE", "10"))

GRPC_PORT = int(os.getenv("GRPC_PORT", "50051"))

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

# Instrument Redis
RedisInstrumentor().instrument()

# Redis connection
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# Initialize stores
config_store = ConfigStore(redis_client)

# Rate limiters cache (by algorithm)
rate_limiters = {}


def get_rate_limiter(config: RateLimitConfig):
    """Get or create a rate limiter for the given configuration."""
    key = f"{config.algorithm}:{config.limit}:{config.window_seconds}:{config.refill_rate}"

    if key not in rate_limiters:
        if config.algorithm == "token_bucket":
            rate_limiters[key] = TokenBucketLimiter(
                redis_client,
                capacity=config.limit,
                refill_rate=config.refill_rate
            )
        elif config.algorithm == "sliding_window":
            rate_limiters[key] = SlidingWindowLimiter(
                redis_client,
                limit=config.limit,
                window_seconds=config.window_seconds
            )
        elif config.algorithm == "sliding_window_counter":
            rate_limiters[key] = SlidingWindowCounterLimiter(
                redis_client,
                limit=config.limit,
                window_seconds=config.window_seconds
            )
        else:
            raise ValueError(f"Unknown algorithm: {config.algorithm}")

    return rate_limiters[key]


def check_rate_limit(client_id: str, resource: str, cost: int = 1) -> RateLimitResult:
    """Check rate limit for a client/resource."""
    with tracer.start_as_current_span("check_rate_limit") as span:
        span.set_attribute("client_id", client_id)
        span.set_attribute("resource", resource)
        span.set_attribute("cost", cost)

        # Get configuration
        config = config_store.get_config(client_id, resource)
        span.set_attribute("algorithm", config.algorithm)
        span.set_attribute("limit", config.limit)

        if not config.enabled:
            # Rate limiting disabled for this client
            return RateLimitResult(
                allowed=True,
                limit=0,
                remaining=0,
                reset_at=0,
                algorithm="disabled",
                retry_after=0
            )

        # Get appropriate rate limiter
        limiter = get_rate_limiter(config)

        # Create unique key for this client/resource
        key = f"{client_id}:{resource}"

        # Measure check latency
        start_time = time.time()
        result = limiter.check(key, cost)
        duration = time.time() - start_time

        # Record metrics
        metrics.CHECK_LATENCY.labels(algorithm=config.algorithm).observe(duration)
        metrics.record_decision(
            client_id=client_id,
            resource=resource,
            algorithm=config.algorithm,
            allowed=result.allowed,
            remaining=result.remaining,
            limit=result.limit
        )

        span.set_attribute("allowed", result.allowed)
        span.set_attribute("remaining", result.remaining)

        return result


# ===========================================
# REST API Models
# ===========================================

class CheckRequest(BaseModel):
    client_id: str = Field(..., description="Unique identifier for the client")
    resource: str = Field(default="*", description="Resource being accessed")
    cost: int = Field(default=1, ge=1, description="Cost of this request")


class CheckResponse(BaseModel):
    allowed: bool
    limit: int
    remaining: int
    reset_at: int
    algorithm: str
    retry_after: int


class ConfigRequest(BaseModel):
    client_id: str
    resource: str = "*"
    algorithm: str = Field(default="token_bucket", pattern="^(token_bucket|sliding_window|sliding_window_counter)$")
    limit: int = Field(default=100, ge=1)
    window_seconds: int = Field(default=60, ge=1)
    refill_rate: float = Field(default=10.0, ge=0.1)
    enabled: bool = True


class ConfigResponse(BaseModel):
    client_id: str
    resource: str
    algorithm: str
    limit: int
    window_seconds: int
    refill_rate: float
    enabled: bool


class UsageResponse(BaseModel):
    client_id: str
    resource: str
    current_usage: int
    limit: int
    total_requests: int
    allowed_requests: int
    denied_requests: int


# ===========================================
# REST API
# ===========================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Redis URL: {REDIS_URL}")

    # Initialize default configuration
    config_store.set_default_config(
        algorithm="token_bucket",
        limit=DEFAULT_BUCKET_CAPACITY,
        window_seconds=DEFAULT_WINDOW_SECONDS,
        refill_rate=DEFAULT_REFILL_RATE
    )

    # Start gRPC server in background thread
    grpc_thread = threading.Thread(target=serve_grpc, daemon=True)
    grpc_thread.start()
    logger.info(f"gRPC server started on port {GRPC_PORT}")

    yield

    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(
    title="Rate Limiter Service",
    description="Production-grade rate limiting microservice with REST and gRPC APIs",
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Instrument FastAPI
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    """Health check endpoint."""
    try:
        redis_client.ping()
        metrics.REDIS_HEALTH.set(1)
        return {"status": "ok", "service": SERVICE_NAME, "redis": "connected"}
    except Exception as e:
        metrics.REDIS_HEALTH.set(0)
        return {"status": "degraded", "service": SERVICE_NAME, "redis": str(e)}


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/v1/check", response_model=CheckResponse)
async def check_limit(request: CheckRequest):
    """
    Check if a request should be rate limited.

    Returns rate limit decision with remaining quota and reset time.
    """
    start_time = time.time()

    result = check_rate_limit(
        client_id=request.client_id,
        resource=request.resource,
        cost=request.cost
    )

    duration = time.time() - start_time
    status = "200" if result.allowed else "429"

    metrics.HTTP_REQUEST_COUNT.labels(
        method="POST",
        endpoint="/v1/check",
        status=status
    ).inc()
    metrics.HTTP_REQUEST_LATENCY.labels(
        method="POST",
        endpoint="/v1/check"
    ).observe(duration)

    return CheckResponse(
        allowed=result.allowed,
        limit=result.limit,
        remaining=result.remaining,
        reset_at=result.reset_at,
        algorithm=result.algorithm,
        retry_after=result.retry_after
    )


@app.get("/v1/config/{client_id}", response_model=ConfigResponse)
async def get_config(
    client_id: str,
    resource: str = Query(default="*", description="Resource path")
):
    """Get rate limit configuration for a client/resource."""
    config = config_store.get_config(client_id, resource)
    return ConfigResponse(**config.to_dict())


@app.put("/v1/config", response_model=ConfigResponse)
async def update_config(request: ConfigRequest):
    """
    Create or update rate limit configuration.

    Changes take effect immediately for new requests.
    """
    config = RateLimitConfig(
        client_id=request.client_id,
        resource=request.resource,
        algorithm=request.algorithm,
        limit=request.limit,
        window_seconds=request.window_seconds,
        refill_rate=request.refill_rate,
        enabled=request.enabled
    )

    result = config_store.set_config(config)
    metrics.record_config_update(config.client_id, config.resource, "update")

    return ConfigResponse(**result.to_dict())


@app.delete("/v1/config/{client_id}")
async def delete_config(
    client_id: str,
    resource: str = Query(default="*", description="Resource path")
):
    """Delete rate limit configuration (revert to default)."""
    success = config_store.delete_config(client_id, resource)
    if success:
        metrics.record_config_update(client_id, resource, "delete")
        return {"status": "deleted", "client_id": client_id, "resource": resource}
    raise HTTPException(status_code=404, detail="Configuration not found")


@app.get("/v1/configs", response_model=list[ConfigResponse])
async def list_configs(
    client_id: Optional[str] = Query(default=None, description="Filter by client ID")
):
    """List all rate limit configurations."""
    configs = config_store.list_configs(client_id)
    return [ConfigResponse(**c.to_dict()) for c in configs]


@app.get("/v1/usage/{client_id}", response_model=UsageResponse)
async def get_usage(
    client_id: str,
    resource: str = Query(default="*", description="Resource path")
):
    """Get current usage statistics for a client."""
    config = config_store.get_config(client_id, resource)
    limiter = get_rate_limiter(config)

    key = f"{client_id}:{resource}"
    current_usage, limit = limiter.get_usage(key)

    # Get metrics from Prometheus (simplified - in production you'd query Prometheus)
    return UsageResponse(
        client_id=client_id,
        resource=resource,
        current_usage=current_usage,
        limit=limit,
        total_requests=0,  # Would come from metrics aggregation
        allowed_requests=0,
        denied_requests=0
    )


# ===========================================
# gRPC Service
# ===========================================

class RateLimiterServicer(ratelimiter_pb2_grpc.RateLimiterServiceServicer):
    """gRPC implementation of the rate limiter service."""

    def CheckRateLimit(self, request, context):
        """Check if a request should be rate limited."""
        start_time = time.time()

        try:
            result = check_rate_limit(
                client_id=request.client_id,
                resource=request.resource or "*",
                cost=request.cost or 1
            )

            response = ratelimiter_pb2.RateLimitResponse(
                allowed=result.allowed,
                limit=result.limit,
                remaining=result.remaining,
                reset_at=result.reset_at,
                algorithm=result.algorithm,
                retry_after=result.retry_after
            )

            duration = time.time() - start_time
            metrics.GRPC_REQUEST_COUNT.labels(
                method="CheckRateLimit",
                status="OK"
            ).inc()
            metrics.GRPC_REQUEST_LATENCY.labels(
                method="CheckRateLimit"
            ).observe(duration)

            return response

        except Exception as e:
            metrics.GRPC_REQUEST_COUNT.labels(
                method="CheckRateLimit",
                status="ERROR"
            ).inc()
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return ratelimiter_pb2.RateLimitResponse()

    def GetConfig(self, request, context):
        """Get rate limit configuration."""
        config = config_store.get_config(
            request.client_id,
            request.resource or "*"
        )

        return ratelimiter_pb2.RateLimitConfig(
            client_id=config.client_id,
            resource=config.resource,
            algorithm=config.algorithm,
            limit=config.limit,
            window_seconds=config.window_seconds,
            refill_rate=config.refill_rate,
            enabled=config.enabled
        )

    def UpdateConfig(self, request, context):
        """Update rate limit configuration."""
        config = RateLimitConfig(
            client_id=request.config.client_id,
            resource=request.config.resource or "*",
            algorithm=request.config.algorithm or "token_bucket",
            limit=request.config.limit or 100,
            window_seconds=request.config.window_seconds or 60,
            refill_rate=request.config.refill_rate or 10.0,
            enabled=request.config.enabled
        )

        result = config_store.set_config(config)
        metrics.record_config_update(config.client_id, config.resource, "update")

        return ratelimiter_pb2.RateLimitConfig(
            client_id=result.client_id,
            resource=result.resource,
            algorithm=result.algorithm,
            limit=result.limit,
            window_seconds=result.window_seconds,
            refill_rate=result.refill_rate,
            enabled=result.enabled
        )

    def GetUsage(self, request, context):
        """Get usage statistics."""
        config = config_store.get_config(
            request.client_id,
            request.resource or "*"
        )
        limiter = get_rate_limiter(config)

        key = f"{request.client_id}:{request.resource or '*'}"
        current_usage, limit = limiter.get_usage(key)

        return ratelimiter_pb2.UsageResponse(
            client_id=request.client_id,
            resource=request.resource or "*",
            total_requests=0,
            allowed_requests=0,
            denied_requests=0,
            current_usage=current_usage,
            limit=limit
        )


def serve_grpc():
    """Start the gRPC server."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    ratelimiter_pb2_grpc.add_RateLimiterServiceServicer_to_server(
        RateLimiterServicer(), server
    )
    server.add_insecure_port(f"[::]:{GRPC_PORT}")
    server.start()
    logger.info(f"gRPC server listening on port {GRPC_PORT}")
    server.wait_for_termination()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
