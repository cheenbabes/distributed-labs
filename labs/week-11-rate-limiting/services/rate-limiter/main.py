"""
Rate Limiter Service - Demonstrates four fundamental rate limiting algorithms.

Algorithms implemented:
1. Fixed Window - Simple counter that resets at fixed intervals
2. Sliding Window Log - Tracks timestamps of all requests
3. Token Bucket - Tokens replenish at fixed rate, bursts allowed up to bucket size
4. Leaky Bucket - Requests processed at constant rate, queue overflow rejected
"""
import asyncio
import logging
import os
import time
from abc import ABC, abstractmethod
from collections import deque
from contextlib import asynccontextmanager
from enum import Enum
from threading import Lock
from typing import Dict, Optional, Tuple

from fastapi import FastAPI, Request, Response
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import JSONResponse

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "rate-limiter")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
RATE_LIMIT = int(os.getenv("RATE_LIMIT", "10"))  # requests per second
BUCKET_SIZE = int(os.getenv("BUCKET_SIZE", "10"))  # for token/leaky bucket

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
RATE_LIMIT_ALLOWED = Counter(
    "rate_limit_allowed_total",
    "Total requests allowed by rate limiter",
    ["algorithm"]
)
RATE_LIMIT_REJECTED = Counter(
    "rate_limit_rejected_total",
    "Total requests rejected by rate limiter",
    ["algorithm"]
)
RATE_LIMIT_REMAINING = Gauge(
    "rate_limit_remaining",
    "Current remaining requests allowed",
    ["algorithm"]
)
RATE_LIMIT_BUCKET_TOKENS = Gauge(
    "rate_limit_bucket_tokens",
    "Current tokens in bucket (token bucket only)",
    ["algorithm"]
)
RATE_LIMIT_QUEUE_SIZE = Gauge(
    "rate_limit_queue_size",
    "Current queue size (leaky bucket only)",
    ["algorithm"]
)


class AlgorithmType(str, Enum):
    FIXED_WINDOW = "fixed_window"
    SLIDING_WINDOW = "sliding_window"
    TOKEN_BUCKET = "token_bucket"
    LEAKY_BUCKET = "leaky_bucket"


class RateLimitResult(BaseModel):
    allowed: bool
    algorithm: str
    limit: int
    remaining: int
    reset_at: Optional[float] = None
    retry_after: Optional[float] = None
    details: Optional[Dict] = None


class RateLimiter(ABC):
    """Abstract base class for rate limiters."""

    def __init__(self, rate: int, window_seconds: float = 1.0):
        self.rate = rate
        self.window_seconds = window_seconds
        self.lock = Lock()

    @abstractmethod
    def try_acquire(self) -> RateLimitResult:
        """Try to acquire permission for a request. Returns result with details."""
        pass

    @abstractmethod
    def get_stats(self) -> Dict:
        """Get current state of the rate limiter."""
        pass

    @abstractmethod
    def reset(self) -> None:
        """Reset the rate limiter state."""
        pass


class FixedWindowRateLimiter(RateLimiter):
    """
    Fixed Window Rate Limiter

    Divides time into fixed windows and counts requests per window.
    Simple but can allow bursts at window boundaries (2x limit briefly).

    Pros: Simple, low memory
    Cons: Boundary burst problem
    """

    def __init__(self, rate: int, window_seconds: float = 1.0):
        super().__init__(rate, window_seconds)
        self.window_start = time.time()
        self.request_count = 0

    def try_acquire(self) -> RateLimitResult:
        with self.lock:
            now = time.time()

            # Check if we're in a new window
            if now - self.window_start >= self.window_seconds:
                self.window_start = now
                self.request_count = 0

            remaining = self.rate - self.request_count
            reset_at = self.window_start + self.window_seconds

            if self.request_count < self.rate:
                self.request_count += 1
                remaining = self.rate - self.request_count
                RATE_LIMIT_REMAINING.labels(algorithm="fixed_window").set(remaining)
                return RateLimitResult(
                    allowed=True,
                    algorithm="fixed_window",
                    limit=self.rate,
                    remaining=remaining,
                    reset_at=reset_at,
                    details={
                        "window_start": self.window_start,
                        "request_count": self.request_count
                    }
                )
            else:
                retry_after = reset_at - now
                return RateLimitResult(
                    allowed=False,
                    algorithm="fixed_window",
                    limit=self.rate,
                    remaining=0,
                    reset_at=reset_at,
                    retry_after=retry_after,
                    details={
                        "window_start": self.window_start,
                        "request_count": self.request_count
                    }
                )

    def get_stats(self) -> Dict:
        with self.lock:
            now = time.time()
            window_remaining = max(0, (self.window_start + self.window_seconds) - now)
            return {
                "algorithm": "fixed_window",
                "rate": self.rate,
                "window_seconds": self.window_seconds,
                "current_count": self.request_count,
                "remaining": max(0, self.rate - self.request_count),
                "window_remaining_seconds": window_remaining,
                "window_start": self.window_start
            }

    def reset(self) -> None:
        with self.lock:
            self.window_start = time.time()
            self.request_count = 0


class SlidingWindowLogRateLimiter(RateLimiter):
    """
    Sliding Window Log Rate Limiter

    Keeps a log of all request timestamps within the window.
    Most accurate but uses more memory.

    Pros: Accurate, no boundary issues
    Cons: Higher memory usage (O(n) where n = requests)
    """

    def __init__(self, rate: int, window_seconds: float = 1.0):
        super().__init__(rate, window_seconds)
        self.request_log: deque = deque()

    def try_acquire(self) -> RateLimitResult:
        with self.lock:
            now = time.time()
            window_start = now - self.window_seconds

            # Remove expired entries
            while self.request_log and self.request_log[0] <= window_start:
                self.request_log.popleft()

            current_count = len(self.request_log)
            remaining = self.rate - current_count

            if current_count < self.rate:
                self.request_log.append(now)
                remaining = self.rate - len(self.request_log)
                RATE_LIMIT_REMAINING.labels(algorithm="sliding_window").set(remaining)

                # Calculate when the oldest request will expire
                reset_at = self.request_log[0] + self.window_seconds if self.request_log else now + self.window_seconds

                return RateLimitResult(
                    allowed=True,
                    algorithm="sliding_window",
                    limit=self.rate,
                    remaining=remaining,
                    reset_at=reset_at,
                    details={
                        "log_size": len(self.request_log),
                        "window_start": window_start
                    }
                )
            else:
                # Calculate when the oldest request will expire
                oldest_request = self.request_log[0]
                reset_at = oldest_request + self.window_seconds
                retry_after = reset_at - now

                return RateLimitResult(
                    allowed=False,
                    algorithm="sliding_window",
                    limit=self.rate,
                    remaining=0,
                    reset_at=reset_at,
                    retry_after=retry_after,
                    details={
                        "log_size": len(self.request_log),
                        "oldest_request": oldest_request
                    }
                )

    def get_stats(self) -> Dict:
        with self.lock:
            now = time.time()
            window_start = now - self.window_seconds

            # Count valid entries
            valid_count = sum(1 for ts in self.request_log if ts > window_start)

            return {
                "algorithm": "sliding_window",
                "rate": self.rate,
                "window_seconds": self.window_seconds,
                "current_count": valid_count,
                "remaining": max(0, self.rate - valid_count),
                "log_size": len(self.request_log)
            }

    def reset(self) -> None:
        with self.lock:
            self.request_log.clear()


class TokenBucketRateLimiter(RateLimiter):
    """
    Token Bucket Rate Limiter

    Tokens are added at a fixed rate. Each request consumes one token.
    Allows bursts up to bucket capacity.

    Pros: Allows controlled bursts, smooth average rate
    Cons: Slightly more complex
    """

    def __init__(self, rate: int, bucket_size: int = None, window_seconds: float = 1.0):
        super().__init__(rate, window_seconds)
        self.bucket_size = bucket_size or rate  # Max tokens
        self.tokens = float(self.bucket_size)  # Start full
        self.last_refill = time.time()
        self.refill_rate = rate / window_seconds  # Tokens per second

    def _refill(self) -> None:
        """Add tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill
        tokens_to_add = elapsed * self.refill_rate
        self.tokens = min(self.bucket_size, self.tokens + tokens_to_add)
        self.last_refill = now

    def try_acquire(self) -> RateLimitResult:
        with self.lock:
            self._refill()

            RATE_LIMIT_BUCKET_TOKENS.labels(algorithm="token_bucket").set(self.tokens)

            if self.tokens >= 1:
                self.tokens -= 1
                remaining = int(self.tokens)
                RATE_LIMIT_REMAINING.labels(algorithm="token_bucket").set(remaining)

                # Calculate when bucket will be full again
                time_to_full = (self.bucket_size - self.tokens) / self.refill_rate

                return RateLimitResult(
                    allowed=True,
                    algorithm="token_bucket",
                    limit=self.rate,
                    remaining=remaining,
                    reset_at=time.time() + time_to_full,
                    details={
                        "tokens": self.tokens,
                        "bucket_size": self.bucket_size,
                        "refill_rate": self.refill_rate
                    }
                )
            else:
                # Calculate when next token will be available
                time_until_token = (1 - self.tokens) / self.refill_rate

                return RateLimitResult(
                    allowed=False,
                    algorithm="token_bucket",
                    limit=self.rate,
                    remaining=0,
                    reset_at=time.time() + time_until_token,
                    retry_after=time_until_token,
                    details={
                        "tokens": self.tokens,
                        "bucket_size": self.bucket_size,
                        "refill_rate": self.refill_rate
                    }
                )

    def get_stats(self) -> Dict:
        with self.lock:
            self._refill()
            return {
                "algorithm": "token_bucket",
                "rate": self.rate,
                "bucket_size": self.bucket_size,
                "current_tokens": self.tokens,
                "remaining": int(self.tokens),
                "refill_rate_per_second": self.refill_rate
            }

    def reset(self) -> None:
        with self.lock:
            self.tokens = float(self.bucket_size)
            self.last_refill = time.time()


class LeakyBucketRateLimiter(RateLimiter):
    """
    Leaky Bucket Rate Limiter

    Requests enter a queue (bucket) and are processed at a fixed rate.
    If bucket overflows, requests are rejected.
    Provides very smooth output rate.

    Pros: Smooth, predictable output rate
    Cons: All requests experience some delay
    """

    def __init__(self, rate: int, bucket_size: int = None, window_seconds: float = 1.0):
        super().__init__(rate, window_seconds)
        self.bucket_size = bucket_size or rate  # Max queue size
        self.queue_size = 0  # Current items in bucket
        self.last_leak = time.time()
        self.leak_rate = rate / window_seconds  # Items processed per second

    def _leak(self) -> None:
        """Remove items from bucket based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_leak
        leaked = elapsed * self.leak_rate
        self.queue_size = max(0, self.queue_size - leaked)
        self.last_leak = now

    def try_acquire(self) -> RateLimitResult:
        with self.lock:
            self._leak()

            RATE_LIMIT_QUEUE_SIZE.labels(algorithm="leaky_bucket").set(self.queue_size)

            if self.queue_size < self.bucket_size:
                self.queue_size += 1
                remaining = int(self.bucket_size - self.queue_size)
                RATE_LIMIT_REMAINING.labels(algorithm="leaky_bucket").set(remaining)

                # Calculate when bucket will be empty
                time_to_empty = self.queue_size / self.leak_rate

                return RateLimitResult(
                    allowed=True,
                    algorithm="leaky_bucket",
                    limit=self.rate,
                    remaining=remaining,
                    reset_at=time.time() + time_to_empty,
                    details={
                        "queue_size": self.queue_size,
                        "bucket_size": self.bucket_size,
                        "leak_rate": self.leak_rate
                    }
                )
            else:
                # Calculate when space will be available
                time_until_space = 1 / self.leak_rate

                return RateLimitResult(
                    allowed=False,
                    algorithm="leaky_bucket",
                    limit=self.rate,
                    remaining=0,
                    reset_at=time.time() + time_until_space,
                    retry_after=time_until_space,
                    details={
                        "queue_size": self.queue_size,
                        "bucket_size": self.bucket_size,
                        "leak_rate": self.leak_rate
                    }
                )

    def get_stats(self) -> Dict:
        with self.lock:
            self._leak()
            return {
                "algorithm": "leaky_bucket",
                "rate": self.rate,
                "bucket_size": self.bucket_size,
                "current_queue_size": self.queue_size,
                "remaining": int(max(0, self.bucket_size - self.queue_size)),
                "leak_rate_per_second": self.leak_rate
            }

    def reset(self) -> None:
        with self.lock:
            self.queue_size = 0
            self.last_leak = time.time()


# Create all rate limiters
rate_limiters: Dict[AlgorithmType, RateLimiter] = {
    AlgorithmType.FIXED_WINDOW: FixedWindowRateLimiter(RATE_LIMIT),
    AlgorithmType.SLIDING_WINDOW: SlidingWindowLogRateLimiter(RATE_LIMIT),
    AlgorithmType.TOKEN_BUCKET: TokenBucketRateLimiter(RATE_LIMIT, BUCKET_SIZE),
    AlgorithmType.LEAKY_BUCKET: LeakyBucketRateLimiter(RATE_LIMIT, BUCKET_SIZE),
}

# Current active algorithm
current_algorithm = AlgorithmType.FIXED_WINDOW


class AlgorithmConfig(BaseModel):
    algorithm: AlgorithmType


class StatsResponse(BaseModel):
    current_algorithm: str
    stats: Dict
    all_algorithms: Dict


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Rate limit: {RATE_LIMIT} requests/second")
    logger.info(f"Bucket size: {BUCKET_SIZE}")
    logger.info(f"Active algorithm: {current_algorithm.value}")
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


@app.get("/api/request")
async def api_request(request: Request):
    """
    The rate-limited endpoint.
    Returns 200 if allowed, 429 if rate limited.
    """
    global current_algorithm

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    limiter = rate_limiters[current_algorithm]
    result = limiter.try_acquire()

    # Record metrics
    if result.allowed:
        RATE_LIMIT_ALLOWED.labels(algorithm=result.algorithm).inc()
    else:
        RATE_LIMIT_REJECTED.labels(algorithm=result.algorithm).inc()

    # Add tracing attributes
    current_span.set_attribute("rate_limit.algorithm", result.algorithm)
    current_span.set_attribute("rate_limit.allowed", result.allowed)
    current_span.set_attribute("rate_limit.remaining", result.remaining)

    # Build response headers
    headers = {
        "X-RateLimit-Limit": str(result.limit),
        "X-RateLimit-Remaining": str(result.remaining),
        "X-RateLimit-Algorithm": result.algorithm,
    }

    if result.reset_at:
        headers["X-RateLimit-Reset"] = str(int(result.reset_at))

    if result.allowed:
        logger.debug(f"Request allowed: algorithm={result.algorithm}, remaining={result.remaining}")
        return JSONResponse(
            status_code=200,
            headers=headers,
            content={
                "status": "ok",
                "message": "Request processed successfully",
                "algorithm": result.algorithm,
                "remaining": result.remaining,
                "trace_id": trace_id
            }
        )
    else:
        headers["Retry-After"] = str(int(result.retry_after) + 1) if result.retry_after else "1"
        logger.info(f"Request rejected: algorithm={result.algorithm}, retry_after={result.retry_after:.3f}s")
        return JSONResponse(
            status_code=429,
            headers=headers,
            content={
                "status": "rate_limited",
                "message": "Too Many Requests",
                "algorithm": result.algorithm,
                "retry_after": result.retry_after,
                "trace_id": trace_id
            }
        )


@app.post("/admin/algorithm")
async def set_algorithm(config: AlgorithmConfig):
    """Switch the active rate limiting algorithm."""
    global current_algorithm
    old_algorithm = current_algorithm
    current_algorithm = config.algorithm
    logger.info(f"Algorithm switched: {old_algorithm.value} -> {current_algorithm.value}")
    return {
        "previous": old_algorithm.value,
        "current": current_algorithm.value,
        "message": f"Switched to {current_algorithm.value}"
    }


@app.get("/admin/algorithm")
async def get_algorithm():
    """Get the current active algorithm."""
    return {
        "current": current_algorithm.value,
        "available": [a.value for a in AlgorithmType]
    }


@app.get("/admin/stats")
async def get_stats():
    """Get current rate limiter state for all algorithms."""
    all_stats = {algo.value: limiter.get_stats() for algo, limiter in rate_limiters.items()}
    return {
        "current_algorithm": current_algorithm.value,
        "stats": rate_limiters[current_algorithm].get_stats(),
        "all_algorithms": all_stats
    }


@app.post("/admin/reset")
async def reset_all():
    """Reset all rate limiter states."""
    for limiter in rate_limiters.values():
        limiter.reset()
    logger.info("All rate limiters reset")
    return {"status": "ok", "message": "All rate limiters reset"}


@app.post("/admin/reset/{algorithm}")
async def reset_algorithm(algorithm: AlgorithmType):
    """Reset a specific rate limiter."""
    rate_limiters[algorithm].reset()
    logger.info(f"Rate limiter reset: {algorithm.value}")
    return {"status": "ok", "message": f"{algorithm.value} rate limiter reset"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
