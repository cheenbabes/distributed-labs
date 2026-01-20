"""
Frontend - Entry point service that demonstrates hedge requests.

This service can either:
1. Use the hedger proxy (with speculative execution)
2. Call backends directly (for comparison)
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Query
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "frontend")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
HEDGER_URL = os.getenv("HEDGER_URL", "http://hedger:8080")
DIRECT_BACKEND_URL = os.getenv("DIRECT_BACKEND_URL", "http://backend-1:8001")

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
    "frontend_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status", "mode"]
)
REQUEST_LATENCY = Histogram(
    "frontend_request_duration_seconds",
    "HTTP request latency",
    ["endpoint", "mode"],
    buckets=[0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Hedger URL: {HEDGER_URL}")
    logger.info(f"Direct Backend URL: {DIRECT_BACKEND_URL}")
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


@app.get("/api/process")
async def api_process(
    request: Request,
    mode: str = Query("hedged", description="Request mode: 'hedged' or 'direct'")
):
    """
    Main API endpoint.

    Args:
        mode: 'hedged' uses the hedger proxy, 'direct' calls backend directly
    """
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")
    request_id = f"req-{random.randint(10000, 99999)}"

    current_span.set_attribute("mode", mode)
    current_span.set_attribute("request_id", request_id)

    async with httpx.AsyncClient(timeout=30.0) as client:
        if mode == "hedged":
            # Use hedger proxy with speculative execution
            with tracer.start_as_current_span("call_hedger") as span:
                span.set_attribute("target", HEDGER_URL)
                response = await client.get(
                    f"{HEDGER_URL}/process",
                    params={"request_id": request_id}
                )
                result = response.json()
        else:
            # Direct call to backend (no hedging)
            with tracer.start_as_current_span("call_backend_direct") as span:
                span.set_attribute("target", DIRECT_BACKEND_URL)
                response = await client.get(
                    f"{DIRECT_BACKEND_URL}/process",
                    params={"request_id": request_id}
                )
                result = response.json()

    duration = time.time() - start_time

    REQUEST_COUNT.labels(
        method="GET",
        endpoint="/api/process",
        status="200",
        mode=mode
    ).inc()
    REQUEST_LATENCY.labels(
        endpoint="/api/process",
        mode=mode
    ).observe(duration)

    logger.info(f"Request completed trace_id={trace_id} mode={mode} duration={duration*1000:.0f}ms")

    return {
        "service": SERVICE_NAME,
        "mode": mode,
        "request_id": request_id,
        "trace_id": trace_id,
        "total_duration_ms": round(duration * 1000, 2),
        "downstream_result": result
    }


@app.get("/api/compare")
async def compare(request: Request, iterations: int = Query(10, ge=1, le=100)):
    """
    Compare hedged vs direct requests side by side.

    Makes the same number of requests using both modes and returns latency statistics.
    """
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    hedged_latencies = []
    direct_latencies = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Run hedged requests
        for i in range(iterations):
            start = time.time()
            request_id = f"cmp-h-{i}-{random.randint(1000, 9999)}"
            await client.get(f"{HEDGER_URL}/process", params={"request_id": request_id})
            hedged_latencies.append((time.time() - start) * 1000)

        # Run direct requests
        for i in range(iterations):
            start = time.time()
            request_id = f"cmp-d-{i}-{random.randint(1000, 9999)}"
            await client.get(f"{DIRECT_BACKEND_URL}/process", params={"request_id": request_id})
            direct_latencies.append((time.time() - start) * 1000)

    def percentile(data, p):
        sorted_data = sorted(data)
        k = (len(sorted_data) - 1) * p / 100
        f = int(k)
        c = f + 1 if f + 1 < len(sorted_data) else f
        return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)

    def stats(data):
        return {
            "min": round(min(data), 2),
            "max": round(max(data), 2),
            "avg": round(sum(data) / len(data), 2),
            "p50": round(percentile(data, 50), 2),
            "p95": round(percentile(data, 95), 2),
            "p99": round(percentile(data, 99), 2),
        }

    hedged_stats = stats(hedged_latencies)
    direct_stats = stats(direct_latencies)

    return {
        "trace_id": trace_id,
        "iterations": iterations,
        "hedged": {
            "latencies_ms": hedged_stats,
            "all_values": [round(x, 2) for x in hedged_latencies]
        },
        "direct": {
            "latencies_ms": direct_stats,
            "all_values": [round(x, 2) for x in direct_latencies]
        },
        "improvement": {
            "p50_reduction_ms": round(direct_stats["p50"] - hedged_stats["p50"], 2),
            "p95_reduction_ms": round(direct_stats["p95"] - hedged_stats["p95"], 2),
            "p99_reduction_ms": round(direct_stats["p99"] - hedged_stats["p99"], 2),
            "p99_reduction_pct": round((direct_stats["p99"] - hedged_stats["p99"]) / direct_stats["p99"] * 100, 1)
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
