"""
Hedger - Proxy service implementing speculative execution (hedge requests).

Hedge requests reduce tail latency by racing multiple requests:
1. Send request to primary backend
2. After HEDGE_DELAY_MS, if no response, send hedge request to another backend
3. Return first successful response, cancel the other

This trades increased load for reduced P99 latency.
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import httpx
from fastapi import FastAPI, Request
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "hedger")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
BACKEND_URLS = os.getenv("BACKEND_URLS", "http://backend-1:8001,http://backend-2:8002,http://backend-3:8003").split(",")
HEDGE_DELAY_MS = int(os.getenv("HEDGE_DELAY_MS", "50"))
HEDGE_ENABLED = os.getenv("HEDGE_ENABLED", "true").lower() == "true"
MAX_HEDGE_REQUESTS = int(os.getenv("MAX_HEDGE_REQUESTS", "2"))  # Max total requests (1 primary + hedges)

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
    "hedger_requests_total",
    "Total requests to hedger",
    ["status", "hedged"]
)
REQUEST_LATENCY = Histogram(
    "hedger_request_duration_seconds",
    "Hedger request latency",
    ["hedged"],
    buckets=[0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0]
)
HEDGE_TRIGGERED = Counter(
    "hedger_hedge_triggered_total",
    "Number of times hedge request was triggered"
)
HEDGE_WON = Counter(
    "hedger_hedge_won_total",
    "Number of times hedge request won the race"
)
PRIMARY_WON = Counter(
    "hedger_primary_won_total",
    "Number of times primary request won the race"
)
CANCELLED_REQUESTS = Counter(
    "hedger_cancelled_requests_total",
    "Number of cancelled losing requests"
)
BACKEND_LOAD = Counter(
    "hedger_backend_requests_total",
    "Total requests sent to backends",
    ["backend"]
)
LOAD_AMPLIFICATION = Gauge(
    "hedger_load_amplification_ratio",
    "Current load amplification ratio (backend_requests / user_requests)"
)
LATENCY_WITHOUT_HEDGE = Histogram(
    "hedger_latency_without_hedge_seconds",
    "Simulated latency if hedging was disabled (primary request time)",
    buckets=[0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0]
)

# Global state for config and metrics
class HedgerState:
    def __init__(self):
        self.hedge_enabled = HEDGE_ENABLED
        self.hedge_delay_ms = HEDGE_DELAY_MS
        self.total_user_requests = 0
        self.total_backend_requests = 0
        self.backend_index = 0  # Round-robin index

state = HedgerState()


class RequestResult(Enum):
    PRIMARY_WON = "primary_won"
    HEDGE_WON = "hedge_won"
    NO_HEDGE = "no_hedge"


@dataclass
class HedgeResult:
    response: dict
    result_type: RequestResult
    primary_latency_ms: float
    actual_latency_ms: float
    backend_used: str
    hedges_triggered: int
    requests_cancelled: int


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Backend URLs: {BACKEND_URLS}")
    logger.info(f"Hedge delay: {HEDGE_DELAY_MS}ms")
    logger.info(f"Hedging enabled: {HEDGE_ENABLED}")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


def get_next_backend() -> str:
    """Round-robin backend selection."""
    backend = BACKEND_URLS[state.backend_index % len(BACKEND_URLS)]
    state.backend_index += 1
    return backend


async def make_request(client: httpx.AsyncClient, url: str, request_id: str) -> tuple[dict, float]:
    """Make a request to a backend and return response with latency."""
    start = time.time()
    try:
        response = await client.get(f"{url}/process", params={"request_id": request_id})
        latency = (time.time() - start) * 1000
        return response.json(), latency
    except Exception as e:
        latency = (time.time() - start) * 1000
        logger.error(f"Request to {url} failed: {e}")
        raise


async def hedged_request(request_id: str) -> HedgeResult:
    """
    Execute a hedged request:
    1. Send primary request
    2. After hedge_delay, if not complete, send hedge request
    3. Return first successful response
    """
    state.total_user_requests += 1

    primary_backend = get_next_backend()
    hedge_backend = get_next_backend()

    primary_task = None
    hedge_task = None
    primary_latency = None
    hedges_triggered = 0
    requests_cancelled = 0

    current_span = trace.get_current_span()

    async with httpx.AsyncClient(timeout=30.0) as client:
        with tracer.start_as_current_span("primary_request") as span:
            span.set_attribute("backend", primary_backend)

            # Track backend load
            state.total_backend_requests += 1
            BACKEND_LOAD.labels(backend=primary_backend).inc()

            primary_start = time.time()
            primary_task = asyncio.create_task(
                make_request(client, primary_backend, request_id)
            )

        if state.hedge_enabled:
            try:
                # Wait for primary with timeout
                result, primary_latency = await asyncio.wait_for(
                    primary_task,
                    timeout=state.hedge_delay_ms / 1000.0
                )
                # Primary completed before hedge delay
                actual_latency = primary_latency
                PRIMARY_WON.inc()
                LATENCY_WITHOUT_HEDGE.observe(primary_latency / 1000.0)

                return HedgeResult(
                    response=result,
                    result_type=RequestResult.PRIMARY_WON,
                    primary_latency_ms=primary_latency,
                    actual_latency_ms=actual_latency,
                    backend_used=primary_backend,
                    hedges_triggered=0,
                    requests_cancelled=0
                )

            except asyncio.TimeoutError:
                # Primary didn't complete in time, trigger hedge
                HEDGE_TRIGGERED.inc()
                hedges_triggered = 1

                with tracer.start_as_current_span("hedge_request") as hedge_span:
                    hedge_span.set_attribute("backend", hedge_backend)
                    hedge_span.set_attribute("hedge_delay_ms", state.hedge_delay_ms)

                    # Track backend load for hedge
                    state.total_backend_requests += 1
                    BACKEND_LOAD.labels(backend=hedge_backend).inc()

                    hedge_task = asyncio.create_task(
                        make_request(client, hedge_backend, request_id)
                    )

                # Race primary and hedge
                done, pending = await asyncio.wait(
                    [primary_task, hedge_task],
                    return_when=asyncio.FIRST_COMPLETED
                )

                # Get the winning result
                winning_task = done.pop()
                result, latency = winning_task.result()

                # Determine who won
                if winning_task == primary_task:
                    PRIMARY_WON.inc()
                    primary_latency = latency
                    actual_latency = latency
                    backend_used = primary_backend
                    result_type = RequestResult.PRIMARY_WON
                else:
                    HEDGE_WON.inc()
                    actual_latency = state.hedge_delay_ms + latency
                    backend_used = hedge_backend
                    result_type = RequestResult.HEDGE_WON
                    # For comparison, wait for primary to complete (non-blocking)
                    try:
                        _, primary_latency = await asyncio.wait_for(primary_task, timeout=5.0)
                    except:
                        primary_latency = actual_latency  # Estimate if primary fails

                # Cancel losing request
                for task in pending:
                    task.cancel()
                    requests_cancelled += 1
                    CANCELLED_REQUESTS.inc()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                LATENCY_WITHOUT_HEDGE.observe(primary_latency / 1000.0)

                return HedgeResult(
                    response=result,
                    result_type=result_type,
                    primary_latency_ms=primary_latency,
                    actual_latency_ms=actual_latency,
                    backend_used=backend_used,
                    hedges_triggered=hedges_triggered,
                    requests_cancelled=requests_cancelled
                )
        else:
            # Hedging disabled - just wait for primary
            result, primary_latency = await primary_task
            LATENCY_WITHOUT_HEDGE.observe(primary_latency / 1000.0)

            return HedgeResult(
                response=result,
                result_type=RequestResult.NO_HEDGE,
                primary_latency_ms=primary_latency,
                actual_latency_ms=primary_latency,
                backend_used=primary_backend,
                hedges_triggered=0,
                requests_cancelled=0
            )


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    # Update load amplification gauge
    if state.total_user_requests > 0:
        ratio = state.total_backend_requests / state.total_user_requests
        LOAD_AMPLIFICATION.set(ratio)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/process")
async def process(request: Request, request_id: Optional[str] = None):
    """Main endpoint - executes hedged request to backends."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    if not request_id:
        request_id = f"req-{random.randint(10000, 99999)}"

    current_span.set_attribute("request_id", request_id)
    current_span.set_attribute("hedge_enabled", state.hedge_enabled)
    current_span.set_attribute("hedge_delay_ms", state.hedge_delay_ms)

    try:
        hedge_result = await hedged_request(request_id)

        duration = time.time() - start_time
        hedged = hedge_result.result_type != RequestResult.NO_HEDGE and hedge_result.hedges_triggered > 0

        REQUEST_COUNT.labels(status="200", hedged=str(hedged).lower()).inc()
        REQUEST_LATENCY.labels(hedged=str(hedged).lower()).observe(duration)

        current_span.set_attribute("result_type", hedge_result.result_type.value)
        current_span.set_attribute("backend_used", hedge_result.backend_used)
        current_span.set_attribute("hedges_triggered", hedge_result.hedges_triggered)
        current_span.set_attribute("requests_cancelled", hedge_result.requests_cancelled)
        current_span.set_attribute("latency_improvement_ms",
            hedge_result.primary_latency_ms - hedge_result.actual_latency_ms)

        logger.info(
            f"Request completed trace_id={trace_id} "
            f"result={hedge_result.result_type.value} "
            f"actual_latency={hedge_result.actual_latency_ms:.0f}ms "
            f"primary_latency={hedge_result.primary_latency_ms:.0f}ms "
            f"improvement={hedge_result.primary_latency_ms - hedge_result.actual_latency_ms:.0f}ms"
        )

        return {
            "service": SERVICE_NAME,
            "request_id": request_id,
            "trace_id": trace_id,
            "hedge_enabled": state.hedge_enabled,
            "hedge_delay_ms": state.hedge_delay_ms,
            "result_type": hedge_result.result_type.value,
            "backend_used": hedge_result.backend_used,
            "actual_latency_ms": round(hedge_result.actual_latency_ms, 2),
            "primary_latency_ms": round(hedge_result.primary_latency_ms, 2),
            "latency_improvement_ms": round(hedge_result.primary_latency_ms - hedge_result.actual_latency_ms, 2),
            "hedges_triggered": hedge_result.hedges_triggered,
            "requests_cancelled": hedge_result.requests_cancelled,
            "load_amplification": round(state.total_backend_requests / max(1, state.total_user_requests), 2),
            "backend_response": hedge_result.response
        }

    except Exception as e:
        duration = time.time() - start_time
        REQUEST_COUNT.labels(status="500", hedged="unknown").inc()
        logger.error(f"Request failed: {e}")
        return {"error": str(e)}


@app.get("/admin/config")
async def get_config():
    """Get current hedger configuration."""
    return {
        "hedge_enabled": state.hedge_enabled,
        "hedge_delay_ms": state.hedge_delay_ms,
        "backend_urls": BACKEND_URLS,
        "max_hedge_requests": MAX_HEDGE_REQUESTS,
        "stats": {
            "total_user_requests": state.total_user_requests,
            "total_backend_requests": state.total_backend_requests,
            "load_amplification": round(state.total_backend_requests / max(1, state.total_user_requests), 2)
        }
    }


@app.post("/admin/config")
async def update_config(request: Request):
    """Update hedger configuration."""
    data = await request.json()

    if "hedge_enabled" in data:
        state.hedge_enabled = data["hedge_enabled"]
        logger.info(f"Hedging {'enabled' if state.hedge_enabled else 'disabled'}")

    if "hedge_delay_ms" in data:
        state.hedge_delay_ms = int(data["hedge_delay_ms"])
        logger.info(f"Hedge delay set to {state.hedge_delay_ms}ms")

    return await get_config()


@app.post("/admin/reset-stats")
async def reset_stats():
    """Reset load amplification statistics."""
    state.total_user_requests = 0
    state.total_backend_requests = 0
    logger.info("Statistics reset")
    return {"message": "Statistics reset"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
