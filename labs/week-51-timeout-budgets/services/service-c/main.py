"""
Service C - Leaf service with configurable slow mode for demonstrating timeout budgets.

This is the last service in the chain. It demonstrates:
1. Receiving and honoring budget from upstream
2. Early termination when budget is exhausted
3. Configurable slow mode to simulate slow downstream/database
4. Avoiding wasted work when deadline is already passed
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "service-c")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
PROCESSING_TIME_MS = int(os.getenv("PROCESSING_TIME_MS", "200"))
SLOW_MODE = os.getenv("SLOW_MODE", "false").lower() == "true"
SLOW_MODE_MS = int(os.getenv("SLOW_MODE_MS", "800"))
BUDGET_MODE = os.getenv("BUDGET_MODE", "propagate")

# Budget header names
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
BUDGET_RECEIVED = Histogram(
    "timeout_budget_received_ms",
    "Timeout budget received from upstream",
    ["service"],
    buckets=[0, 50, 100, 250, 500, 1000, 2000]
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
WORK_SKIPPED = Counter(
    "work_skipped_ms_total",
    "Milliseconds of work skipped due to early termination",
    ["service"]
)
SLOW_MODE_COUNTER = Counter(
    "slow_mode_requests_total",
    "Number of requests processed in slow mode",
    ["service"]
)

# Runtime configuration
config = {
    "processing_time_ms": PROCESSING_TIME_MS,
    "slow_mode": SLOW_MODE,
    "slow_mode_ms": SLOW_MODE_MS,
    "mode": BUDGET_MODE
}


class ServiceConfig(BaseModel):
    processing_time_ms: int = PROCESSING_TIME_MS
    slow_mode: bool = SLOW_MODE
    slow_mode_ms: int = SLOW_MODE_MS
    mode: str = BUDGET_MODE


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up (leaf service)")
    logger.info(f"Processing time: {PROCESSING_TIME_MS}ms")
    logger.info(f"Slow mode: {SLOW_MODE} ({SLOW_MODE_MS}ms)")
    logger.info(f"Budget mode: {BUDGET_MODE}")
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
    return config


@app.post("/admin/config")
async def set_config(new_config: ServiceConfig):
    config["processing_time_ms"] = new_config.processing_time_ms
    config["slow_mode"] = new_config.slow_mode
    config["slow_mode_ms"] = new_config.slow_mode_ms
    config["mode"] = new_config.mode
    logger.info(f"Config updated: {config}")
    return config


@app.post("/admin/slow-mode")
async def set_slow_mode(enabled: bool, ms: int = 800):
    """Quick toggle for slow mode - useful for demos."""
    config["slow_mode"] = enabled
    config["slow_mode_ms"] = ms
    logger.info(f"Slow mode: enabled={enabled}, ms={ms}")
    return {"slow_mode": enabled, "slow_mode_ms": ms}


@app.get("/process")
async def process(request: Request):
    """Process request - this is the leaf service."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Extract budget from headers
    budget_header = request.headers.get(BUDGET_HEADER)
    deadline_header = request.headers.get(DEADLINE_HEADER)

    received_budget = int(budget_header) if budget_header else None
    deadline_timestamp = int(deadline_header) if deadline_header else None

    current_span.set_attribute("budget.received_ms", received_budget or -1)
    current_span.set_attribute("budget.mode", config["mode"])
    current_span.set_attribute("slow_mode.enabled", config["slow_mode"])

    if received_budget:
        BUDGET_RECEIVED.labels(service=SERVICE_NAME).observe(received_budget)
        logger.info(f"Received budget trace_id={trace_id} budget={received_budget}ms")

    # Calculate total work to be done
    base_processing = config["processing_time_ms"]
    slow_mode_addition = config["slow_mode_ms"] if config["slow_mode"] else 0
    total_work_ms = base_processing + slow_mode_addition

    current_span.set_attribute("work.base_ms", base_processing)
    current_span.set_attribute("work.slow_mode_ms", slow_mode_addition)
    current_span.set_attribute("work.total_ms", total_work_ms)

    # Check if budget is already exhausted on arrival
    if config["mode"] == "propagate" and received_budget is not None and received_budget <= 0:
        BUDGET_EXHAUSTED.labels(service=SERVICE_NAME, phase="on_arrival").inc()
        EARLY_TERMINATION.labels(service=SERVICE_NAME).inc()
        WORK_SKIPPED.labels(service=SERVICE_NAME).inc(total_work_ms)

        current_span.set_attribute("budget.exhausted", True)
        current_span.set_attribute("budget.exhausted_phase", "on_arrival")
        current_span.set_attribute("work.skipped_ms", total_work_ms)

        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/process",
            status="504"
        ).inc()

        logger.warning(f"Budget exhausted on arrival trace_id={trace_id} skipped={total_work_ms}ms")

        raise HTTPException(
            status_code=504,
            detail={
                "error": "timeout_budget_exhausted",
                "message": "Budget exhausted on arrival - skipping all work",
                "service": SERVICE_NAME,
                "received_budget_ms": received_budget,
                "work_skipped_ms": total_work_ms,
                "trace_id": trace_id
            }
        )

    # Check if budget is insufficient for the work ahead
    if config["mode"] == "propagate" and received_budget is not None:
        if received_budget < total_work_ms:
            # We have some budget but not enough - do partial work
            logger.info(f"Partial budget trace_id={trace_id} budget={received_budget}ms work={total_work_ms}ms")
            current_span.set_attribute("work.partial", True)

    # Track if slow mode is active
    if config["slow_mode"]:
        SLOW_MODE_COUNTER.labels(service=SERVICE_NAME).inc()

    # Simulate local processing (this is the actual work)
    await asyncio.sleep(total_work_ms / 1000.0)

    # Calculate final remaining budget
    elapsed_ms = int((time.time() - start_time) * 1000)

    if received_budget is not None:
        remaining_budget = received_budget - elapsed_ms
        BUDGET_REMAINING.labels(service=SERVICE_NAME, phase="final").observe(max(0, remaining_budget))
        current_span.set_attribute("budget.final_remaining_ms", remaining_budget)

        if remaining_budget < 0:
            # We exceeded the budget but completed the work (would timeout upstream)
            current_span.set_attribute("budget.exceeded", True)
            current_span.set_attribute("budget.exceeded_by_ms", abs(remaining_budget))
    else:
        remaining_budget = None

    duration = time.time() - start_time

    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/process",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/process"
    ).observe(duration)

    logger.info(f"Completed trace_id={trace_id} duration={elapsed_ms}ms remaining={remaining_budget}ms")

    return {
        "service": SERVICE_NAME,
        "budget": {
            "received_ms": received_budget,
            "elapsed_ms": elapsed_ms,
            "remaining_ms": remaining_budget,
            "exhausted": remaining_budget is not None and remaining_budget <= 0
        },
        "work": {
            "base_processing_ms": base_processing,
            "slow_mode_ms": slow_mode_addition,
            "total_ms": total_work_ms
        },
        "slow_mode": config["slow_mode"],
        "mode": config["mode"],
        "trace_id": trace_id
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
