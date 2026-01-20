"""
Frontend API - Entry point service that initiates timeout budgets.

This service demonstrates deadline propagation by:
1. Setting an overall timeout budget for each request
2. Propagating remaining budget to downstream services via headers
3. Tracking budget consumption across the call chain
"""
import asyncio
import logging
import os
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
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "frontend-api")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
DOWNSTREAM_URL = os.getenv("DOWNSTREAM_URL", "http://service-a:8001")
DEFAULT_BUDGET_MS = int(os.getenv("DEFAULT_BUDGET_MS", "1000"))
PROCESSING_TIME_MS = int(os.getenv("PROCESSING_TIME_MS", "50"))

# Budget header name (like gRPC deadline propagation)
BUDGET_HEADER = "X-Timeout-Budget-Ms"
DEADLINE_HEADER = "X-Deadline-Timestamp-Ms"

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
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
BUDGET_INITIAL = Histogram(
    "timeout_budget_initial_ms",
    "Initial timeout budget in milliseconds",
    ["service"],
    buckets=[100, 250, 500, 1000, 2000, 5000]
)
BUDGET_REMAINING = Histogram(
    "timeout_budget_remaining_ms",
    "Remaining timeout budget after processing",
    ["service", "phase"],
    buckets=[0, 50, 100, 250, 500, 1000, 2000]
)
BUDGET_EXHAUSTED = Counter(
    "timeout_budget_exhausted_total",
    "Number of requests where budget was exhausted",
    ["service", "phase"]
)
EARLY_TERMINATION = Counter(
    "early_termination_total",
    "Number of requests terminated early due to budget exhaustion",
    ["service"]
)
WASTED_WORK = Counter(
    "wasted_work_ms_total",
    "Milliseconds of work that would be wasted without deadline propagation",
    ["service"]
)

# Runtime configuration
config = {
    "default_budget_ms": DEFAULT_BUDGET_MS,
    "processing_time_ms": PROCESSING_TIME_MS,
    "mode": "budget"  # "budget" or "fixed" for comparison
}


class BudgetConfig(BaseModel):
    default_budget_ms: int = DEFAULT_BUDGET_MS
    processing_time_ms: int = PROCESSING_TIME_MS
    mode: str = "budget"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Downstream URL: {DOWNSTREAM_URL}")
    logger.info(f"Default budget: {DEFAULT_BUDGET_MS}ms")
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


@app.get("/admin/config")
async def get_config():
    """Get current configuration."""
    return config


@app.post("/admin/config")
async def set_config(new_config: BudgetConfig):
    """Update configuration at runtime."""
    config["default_budget_ms"] = new_config.default_budget_ms
    config["processing_time_ms"] = new_config.processing_time_ms
    config["mode"] = new_config.mode
    logger.info(f"Config updated: {config}")
    return config


@app.get("/api/process")
async def api_process(request: Request, budget_ms: int = None):
    """
    Main API endpoint demonstrating timeout budget propagation.

    Query params:
        budget_ms: Override the default timeout budget
    """
    start_time = time.time()
    start_time_ms = int(start_time * 1000)

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Initialize the timeout budget
    initial_budget = budget_ms if budget_ms else config["default_budget_ms"]
    deadline_timestamp = start_time_ms + initial_budget

    BUDGET_INITIAL.labels(service=SERVICE_NAME).observe(initial_budget)
    current_span.set_attribute("budget.initial_ms", initial_budget)
    current_span.set_attribute("budget.deadline_timestamp", deadline_timestamp)
    current_span.set_attribute("budget.mode", config["mode"])

    logger.info(f"Request started trace_id={trace_id} budget={initial_budget}ms mode={config['mode']}")

    # Simulate local processing time
    processing_time = config["processing_time_ms"]
    await asyncio.sleep(processing_time / 1000.0)

    # Calculate remaining budget after local processing
    elapsed_ms = int((time.time() - start_time) * 1000)
    remaining_budget = initial_budget - elapsed_ms

    BUDGET_REMAINING.labels(service=SERVICE_NAME, phase="after_processing").observe(remaining_budget)
    current_span.set_attribute("budget.remaining_after_processing_ms", remaining_budget)
    current_span.set_attribute("budget.local_processing_ms", processing_time)

    # Check if budget is already exhausted
    if remaining_budget <= 0:
        BUDGET_EXHAUSTED.labels(service=SERVICE_NAME, phase="before_downstream").inc()
        EARLY_TERMINATION.labels(service=SERVICE_NAME).inc()
        current_span.set_attribute("budget.exhausted", True)
        current_span.set_attribute("budget.exhausted_phase", "before_downstream")

        duration = time.time() - start_time
        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/api/process",
            status="504"
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/api/process"
        ).observe(duration)

        raise HTTPException(
            status_code=504,
            detail={
                "error": "timeout_budget_exhausted",
                "message": "Budget exhausted before calling downstream",
                "service": SERVICE_NAME,
                "initial_budget_ms": initial_budget,
                "elapsed_ms": elapsed_ms,
                "remaining_ms": remaining_budget,
                "trace_id": trace_id
            }
        )

    # Call downstream service with budget propagation
    downstream_result = None
    downstream_error = None

    try:
        if config["mode"] == "budget":
            # Propagate remaining budget to downstream
            headers = {
                BUDGET_HEADER: str(remaining_budget),
                DEADLINE_HEADER: str(deadline_timestamp)
            }
            timeout = remaining_budget / 1000.0  # Convert to seconds
        else:
            # Fixed timeout mode (no propagation) - for comparison
            headers = {}
            timeout = 30.0  # Long fixed timeout

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                f"{DOWNSTREAM_URL}/process",
                headers=headers
            )
            downstream_result = response.json()

    except httpx.TimeoutException as e:
        downstream_error = "timeout"
        current_span.set_attribute("downstream.error", "timeout")
        logger.warning(f"Downstream timeout trace_id={trace_id}")

    except Exception as e:
        downstream_error = str(e)
        current_span.set_attribute("downstream.error", str(e))
        logger.error(f"Downstream error trace_id={trace_id}: {e}")

    duration = time.time() - start_time
    final_elapsed_ms = int(duration * 1000)
    final_remaining = initial_budget - final_elapsed_ms

    BUDGET_REMAINING.labels(service=SERVICE_NAME, phase="final").observe(max(0, final_remaining))
    current_span.set_attribute("budget.final_remaining_ms", final_remaining)

    # Record metrics
    status = "200" if downstream_result else "504"
    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/api/process",
        status=status
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/api/process"
    ).observe(duration)

    if downstream_error:
        raise HTTPException(
            status_code=504,
            detail={
                "error": "downstream_failed",
                "message": f"Downstream call failed: {downstream_error}",
                "service": SERVICE_NAME,
                "initial_budget_ms": initial_budget,
                "elapsed_ms": final_elapsed_ms,
                "remaining_ms": final_remaining,
                "mode": config["mode"],
                "trace_id": trace_id
            }
        )

    logger.info(f"Request completed trace_id={trace_id} duration={final_elapsed_ms}ms remaining={final_remaining}ms")

    return {
        "service": SERVICE_NAME,
        "mode": config["mode"],
        "budget": {
            "initial_ms": initial_budget,
            "elapsed_ms": final_elapsed_ms,
            "remaining_ms": final_remaining,
            "exhausted": final_remaining <= 0
        },
        "local_processing_ms": processing_time,
        "total_duration_ms": round(duration * 1000, 2),
        "trace_id": trace_id,
        "chain": downstream_result
    }


@app.get("/api/compare")
async def api_compare(request: Request, budget_ms: int = None):
    """
    Compare budget vs fixed timeout modes by making parallel requests.
    Useful for demonstrating the difference in behavior.
    """
    budget = budget_ms if budget_ms else config["default_budget_ms"]

    # Save current mode
    original_mode = config["mode"]

    results = {}

    # Test with budget mode
    config["mode"] = "budget"
    try:
        async with httpx.AsyncClient(timeout=budget / 1000.0 + 1.0) as client:
            response = await client.get(
                f"http://localhost:8000/api/process?budget_ms={budget}"
            )
            results["budget_mode"] = response.json()
    except Exception as e:
        results["budget_mode"] = {"error": str(e)}

    # Test with fixed mode
    config["mode"] = "fixed"
    try:
        async with httpx.AsyncClient(timeout=budget / 1000.0 + 1.0) as client:
            response = await client.get(
                f"http://localhost:8000/api/process?budget_ms={budget}"
            )
            results["fixed_mode"] = response.json()
    except Exception as e:
        results["fixed_mode"] = {"error": str(e)}

    # Restore original mode
    config["mode"] = original_mode

    return {
        "comparison": results,
        "budget_tested_ms": budget
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
