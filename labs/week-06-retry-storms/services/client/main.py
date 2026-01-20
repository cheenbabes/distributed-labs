"""
Client Service - Gateway that calls backend with configurable retry strategies.

Demonstrates:
- Naive retries (immediate, 3 attempts) - amplifies failures
- Exponential backoff (1s, 2s, 4s) - gives time to recover
- Jitter (backoff + random delay) - prevents synchronized retry waves
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from enum import Enum
from typing import Optional

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
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "client")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8001")
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
BASE_BACKOFF_MS = int(os.getenv("BASE_BACKOFF_MS", "1000"))

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
RETRY_ATTEMPTS = Counter(
    "retry_attempts_total",
    "Total retry attempts",
    ["service", "strategy", "attempt"]
)
RETRY_STRATEGY_GAUGE = Gauge(
    "retry_strategy_active",
    "Currently active retry strategy",
    ["strategy"]
)
BACKEND_CALLS = Counter(
    "backend_calls_total",
    "Total calls to backend service",
    ["service", "result"]
)


class RetryStrategy(str, Enum):
    NAIVE = "naive"
    BACKOFF = "backoff"
    JITTER = "jitter"


class StrategyRequest(BaseModel):
    strategy: RetryStrategy


# Global state for retry strategy
current_strategy = RetryStrategy.NAIVE


def get_retry_delay(strategy: RetryStrategy, attempt: int) -> float:
    """
    Calculate retry delay based on strategy.

    - naive: 0 seconds (immediate retry)
    - backoff: exponential (1s, 2s, 4s, ...)
    - jitter: backoff + random(0, backoff)
    """
    if strategy == RetryStrategy.NAIVE:
        return 0.0

    # Exponential backoff: base * 2^attempt
    base_delay = (BASE_BACKOFF_MS / 1000.0) * (2 ** attempt)

    if strategy == RetryStrategy.BACKOFF:
        return base_delay

    if strategy == RetryStrategy.JITTER:
        # Full jitter: random between 0 and base_delay
        return random.uniform(0, base_delay)

    return 0.0


async def call_backend_with_retry(
    client: httpx.AsyncClient,
    strategy: RetryStrategy,
    trace_id: str
) -> dict:
    """
    Call backend service with configurable retry strategy.
    """
    last_error: Optional[Exception] = None

    for attempt in range(MAX_RETRIES + 1):  # +1 for initial attempt
        try:
            # Record the attempt
            if attempt > 0:
                RETRY_ATTEMPTS.labels(
                    service=SERVICE_NAME,
                    strategy=strategy.value,
                    attempt=str(attempt)
                ).inc()

                # Calculate delay
                delay = get_retry_delay(strategy, attempt - 1)
                if delay > 0:
                    logger.info(
                        f"Retry {attempt}/{MAX_RETRIES} after {delay:.2f}s "
                        f"(strategy={strategy.value}) trace_id={trace_id}"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.info(
                        f"Retry {attempt}/{MAX_RETRIES} immediately "
                        f"(strategy={strategy.value}) trace_id={trace_id}"
                    )

            # Make the request
            BACKEND_CALLS.labels(service=SERVICE_NAME, result="attempt").inc()

            response = await client.get(
                f"{BACKEND_URL}/process",
                timeout=5.0
            )

            if response.status_code == 200:
                BACKEND_CALLS.labels(service=SERVICE_NAME, result="success").inc()
                return {
                    "success": True,
                    "attempts": attempt + 1,
                    "strategy": strategy.value,
                    "data": response.json()
                }
            elif response.status_code >= 500:
                # Server error - should retry
                BACKEND_CALLS.labels(service=SERVICE_NAME, result="server_error").inc()
                last_error = HTTPException(
                    status_code=response.status_code,
                    detail=f"Backend returned {response.status_code}"
                )
                logger.warning(
                    f"Backend error {response.status_code} "
                    f"attempt={attempt + 1} trace_id={trace_id}"
                )
            else:
                # Client error - don't retry
                BACKEND_CALLS.labels(service=SERVICE_NAME, result="client_error").inc()
                return {
                    "success": False,
                    "attempts": attempt + 1,
                    "strategy": strategy.value,
                    "error": f"Backend returned {response.status_code}"
                }

        except httpx.TimeoutException as e:
            BACKEND_CALLS.labels(service=SERVICE_NAME, result="timeout").inc()
            last_error = e
            logger.warning(f"Backend timeout attempt={attempt + 1} trace_id={trace_id}")

        except httpx.ConnectError as e:
            BACKEND_CALLS.labels(service=SERVICE_NAME, result="connect_error").inc()
            last_error = e
            logger.warning(f"Backend connect error attempt={attempt + 1} trace_id={trace_id}")

        except Exception as e:
            BACKEND_CALLS.labels(service=SERVICE_NAME, result="error").inc()
            last_error = e
            logger.error(f"Unexpected error: {e} attempt={attempt + 1} trace_id={trace_id}")

    # All retries exhausted
    BACKEND_CALLS.labels(service=SERVICE_NAME, result="exhausted").inc()
    return {
        "success": False,
        "attempts": MAX_RETRIES + 1,
        "strategy": strategy.value,
        "error": f"All {MAX_RETRIES + 1} attempts failed: {str(last_error)}"
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    global current_strategy
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Backend URL: {BACKEND_URL}")
    logger.info(f"Max retries: {MAX_RETRIES}")
    logger.info(f"Base backoff: {BASE_BACKOFF_MS}ms")
    logger.info(f"Initial strategy: {current_strategy.value}")

    # Set initial strategy gauge
    for strategy in RetryStrategy:
        RETRY_STRATEGY_GAUGE.labels(strategy=strategy.value).set(
            1 if strategy == current_strategy else 0
        )

    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "retry_strategy": current_strategy.value,
        "max_retries": MAX_RETRIES
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/admin/strategy")
async def get_strategy():
    """Get current retry strategy."""
    return {
        "strategy": current_strategy.value,
        "max_retries": MAX_RETRIES,
        "base_backoff_ms": BASE_BACKOFF_MS
    }


@app.post("/admin/strategy")
async def set_strategy(request: StrategyRequest):
    """Set retry strategy."""
    global current_strategy
    old_strategy = current_strategy
    current_strategy = request.strategy

    # Update gauges
    for strategy in RetryStrategy:
        RETRY_STRATEGY_GAUGE.labels(strategy=strategy.value).set(
            1 if strategy == current_strategy else 0
        )

    logger.info(f"Retry strategy changed: {old_strategy.value} -> {current_strategy.value}")

    return {
        "message": f"Strategy changed from {old_strategy.value} to {current_strategy.value}",
        "strategy": current_strategy.value,
        "max_retries": MAX_RETRIES,
        "base_backoff_ms": BASE_BACKOFF_MS
    }


@app.get("/process")
async def process(request: Request):
    """
    Main endpoint - calls backend with current retry strategy.
    """
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    current_span.set_attribute("retry.strategy", current_strategy.value)
    current_span.set_attribute("retry.max_retries", MAX_RETRIES)

    logger.info(f"Processing request strategy={current_strategy.value} trace_id={trace_id}")

    async with httpx.AsyncClient() as client:
        result = await call_backend_with_retry(client, current_strategy, trace_id)

    duration = time.time() - start_time
    status = "200" if result["success"] else "502"

    # Record metrics
    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/process",
        status=status
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/process"
    ).observe(duration)

    current_span.set_attribute("retry.attempts", result["attempts"])
    current_span.set_attribute("retry.success", result["success"])

    response_data = {
        "service": SERVICE_NAME,
        "trace_id": trace_id,
        "duration_ms": round(duration * 1000, 2),
        "retry_strategy": current_strategy.value,
        "attempts": result["attempts"],
        "success": result["success"]
    }

    if result["success"]:
        response_data["backend"] = result.get("data")
    else:
        response_data["error"] = result.get("error")

    logger.info(
        f"Request completed success={result['success']} "
        f"attempts={result['attempts']} duration={duration*1000:.0f}ms "
        f"trace_id={trace_id}"
    )

    if not result["success"]:
        raise HTTPException(status_code=502, detail=response_data)

    return response_data


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
