"""
Service B - Intermediate service that tracks and propagates timeout budgets.

Demonstrates:
1. Receiving budget from upstream via headers
2. Checking if budget is exhausted before doing work
3. Propagating remaining budget to downstream
4. Early termination to avoid wasted work
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
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "service-b")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
DOWNSTREAM_URL = os.getenv("DOWNSTREAM_URL", "http://service-c:8003")
PROCESSING_TIME_MS = int(os.getenv("PROCESSING_TIME_MS", "150"))
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

# Instrument httpx
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

# Runtime configuration
config = {
    "processing_time_ms": PROCESSING_TIME_MS,
    "mode": BUDGET_MODE
}


class ServiceConfig(BaseModel):
    processing_time_ms: int = PROCESSING_TIME_MS
    mode: str = BUDGET_MODE


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Downstream URL: {DOWNSTREAM_URL}")
    logger.info(f"Processing time: {PROCESSING_TIME_MS}ms")
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
    config["mode"] = new_config.mode
    logger.info(f"Config updated: {config}")
    return config


@app.get("/process")
async def process(request: Request):
    """Process request with budget tracking."""
    start_time = time.time()
    start_time_ms = int(start_time * 1000)

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Extract budget from headers
    budget_header = request.headers.get(BUDGET_HEADER)
    deadline_header = request.headers.get(DEADLINE_HEADER)

    received_budget = int(budget_header) if budget_header else None
    deadline_timestamp = int(deadline_header) if deadline_header else None

    current_span.set_attribute("budget.received_ms", received_budget or -1)
    current_span.set_attribute("budget.mode", config["mode"])

    if received_budget:
        BUDGET_RECEIVED.labels(service=SERVICE_NAME).observe(received_budget)
        logger.info(f"Received budget trace_id={trace_id} budget={received_budget}ms")

    # Check if budget is already exhausted on arrival
    if config["mode"] == "propagate" and received_budget is not None and received_budget <= 0:
        BUDGET_EXHAUSTED.labels(service=SERVICE_NAME, phase="on_arrival").inc()
        EARLY_TERMINATION.labels(service=SERVICE_NAME).inc()
        WORK_SKIPPED.labels(service=SERVICE_NAME).inc(config["processing_time_ms"])

        current_span.set_attribute("budget.exhausted", True)
        current_span.set_attribute("budget.exhausted_phase", "on_arrival")
        current_span.set_attribute("work.skipped_ms", config["processing_time_ms"])

        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/process",
            status="504"
        ).inc()

        raise HTTPException(
            status_code=504,
            detail={
                "error": "timeout_budget_exhausted",
                "message": "Budget exhausted on arrival - skipping work",
                "service": SERVICE_NAME,
                "received_budget_ms": received_budget,
                "work_skipped_ms": config["processing_time_ms"],
                "trace_id": trace_id
            }
        )

    # Simulate local processing
    processing_time = config["processing_time_ms"]
    await asyncio.sleep(processing_time / 1000.0)

    # Calculate remaining budget after processing
    elapsed_ms = int((time.time() - start_time) * 1000)

    if received_budget is not None:
        remaining_budget = received_budget - elapsed_ms
    else:
        remaining_budget = None

    if remaining_budget is not None:
        BUDGET_REMAINING.labels(service=SERVICE_NAME, phase="after_processing").observe(remaining_budget)
        current_span.set_attribute("budget.remaining_after_processing_ms", remaining_budget)

    current_span.set_attribute("budget.local_processing_ms", processing_time)

    # Check if budget exhausted before downstream call
    if config["mode"] == "propagate" and remaining_budget is not None and remaining_budget <= 0:
        BUDGET_EXHAUSTED.labels(service=SERVICE_NAME, phase="before_downstream").inc()
        EARLY_TERMINATION.labels(service=SERVICE_NAME).inc()
        current_span.set_attribute("budget.exhausted", True)
        current_span.set_attribute("budget.exhausted_phase", "before_downstream")

        duration = time.time() - start_time
        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/process",
            status="504"
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/process"
        ).observe(duration)

        raise HTTPException(
            status_code=504,
            detail={
                "error": "timeout_budget_exhausted",
                "message": "Budget exhausted after processing - skipping downstream",
                "service": SERVICE_NAME,
                "received_budget_ms": received_budget,
                "elapsed_ms": elapsed_ms,
                "remaining_ms": remaining_budget,
                "trace_id": trace_id
            }
        )

    # Call downstream with propagated budget
    downstream_result = None
    downstream_error = None

    try:
        if config["mode"] == "propagate" and remaining_budget is not None:
            headers = {
                BUDGET_HEADER: str(remaining_budget),
                DEADLINE_HEADER: str(deadline_timestamp) if deadline_timestamp else str(start_time_ms + remaining_budget)
            }
            timeout = remaining_budget / 1000.0
        else:
            headers = {}
            timeout = 30.0

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                f"{DOWNSTREAM_URL}/process",
                headers=headers
            )
            downstream_result = response.json()

    except httpx.TimeoutException:
        downstream_error = "timeout"
        current_span.set_attribute("downstream.error", "timeout")
    except Exception as e:
        downstream_error = str(e)
        current_span.set_attribute("downstream.error", str(e))

    duration = time.time() - start_time
    final_elapsed_ms = int(duration * 1000)

    if received_budget is not None:
        final_remaining = received_budget - final_elapsed_ms
        BUDGET_REMAINING.labels(service=SERVICE_NAME, phase="final").observe(max(0, final_remaining))
        current_span.set_attribute("budget.final_remaining_ms", final_remaining)
    else:
        final_remaining = None

    status = "200" if downstream_result else "504"
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

    if downstream_error:
        raise HTTPException(
            status_code=504,
            detail={
                "error": "downstream_failed",
                "message": f"Downstream call failed: {downstream_error}",
                "service": SERVICE_NAME,
                "received_budget_ms": received_budget,
                "elapsed_ms": final_elapsed_ms,
                "remaining_ms": final_remaining,
                "trace_id": trace_id
            }
        )

    logger.info(f"Completed trace_id={trace_id} duration={final_elapsed_ms}ms remaining={final_remaining}ms")

    return {
        "service": SERVICE_NAME,
        "budget": {
            "received_ms": received_budget,
            "elapsed_ms": final_elapsed_ms,
            "remaining_ms": final_remaining,
            "exhausted": final_remaining is not None and final_remaining <= 0
        },
        "local_processing_ms": processing_time,
        "mode": config["mode"],
        "trace_id": trace_id,
        "chain": downstream_result
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
