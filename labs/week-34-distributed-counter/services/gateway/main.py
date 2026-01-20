"""
Gateway Service - Load balancer and API entry point for distributed counters.

This simulates how Netflix routes view count increments:
- Routes to nearest (random) region for increments
- Provides unified API for counter operations
- Demonstrates round-robin vs locality-aware routing
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from pydantic import BaseModel

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "gateway")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
COUNTER_ENDPOINTS = os.getenv("COUNTER_ENDPOINTS", "").split(",") if os.getenv("COUNTER_ENDPOINTS") else []
COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://coordinator:8000")

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
    "gateway_requests_total",
    "Total gateway requests",
    ["method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "gateway_request_duration_seconds",
    "Gateway request latency",
    ["method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
ROUTED_TO_REGION = Counter(
    "gateway_routed_to_region_total",
    "Requests routed to each region",
    ["region"]
)
EVENTUAL_VS_CONSISTENT = Histogram(
    "gateway_consistency_comparison_seconds",
    "Latency comparison eventual vs consistent",
    ["consistency_type"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5]
)


class IncrementRequest(BaseModel):
    """Request to increment a counter."""
    counter_name: str = "views"
    amount: int = 1
    routing: str = "random"  # random, round-robin, or specific region


class CompareRequest(BaseModel):
    """Request to compare eventual vs consistent counters."""
    counter_name: str = "views"
    iterations: int = 10


# Round-robin state
round_robin_index = 0


def get_next_endpoint() -> str:
    """Get next endpoint using round-robin."""
    global round_robin_index
    if not COUNTER_ENDPOINTS:
        raise HTTPException(status_code=503, detail="No counter endpoints configured")
    endpoint = COUNTER_ENDPOINTS[round_robin_index % len(COUNTER_ENDPOINTS)]
    round_robin_index += 1
    return endpoint


def get_random_endpoint() -> str:
    """Get a random endpoint."""
    if not COUNTER_ENDPOINTS:
        raise HTTPException(status_code=503, detail="No counter endpoints configured")
    return random.choice(COUNTER_ENDPOINTS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Counter endpoints: {COUNTER_ENDPOINTS}")
    logger.info(f"Coordinator URL: {COORDINATOR_URL}")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title="Distributed Counter Gateway", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/increment")
async def increment(request: IncrementRequest):
    """
    Increment a counter via eventual consistency (CRDT).

    This routes to a single region which increments locally.
    The count will sync to other regions in the background.

    Routing options:
    - random: Pick a random region (default, simulates CDN routing)
    - round-robin: Distribute evenly across regions
    """
    start_time = time.time()

    current_span = trace.get_current_span()
    current_span.set_attribute("counter.name", request.counter_name)
    current_span.set_attribute("counter.routing", request.routing)

    # Select endpoint based on routing strategy
    if request.routing == "round-robin":
        endpoint = get_next_endpoint()
    else:  # random
        endpoint = get_random_endpoint()

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"http://{endpoint}/increment",
                json={
                    "counter_name": request.counter_name,
                    "amount": request.amount
                }
            )
            result = response.json()

        duration = time.time() - start_time

        # Update metrics
        region = result.get("region", "unknown")
        ROUTED_TO_REGION.labels(region=region).inc()
        REQUEST_COUNT.labels(method="POST", endpoint="/api/increment", status="200").inc()
        REQUEST_LATENCY.labels(method="POST", endpoint="/api/increment").observe(duration)
        EVENTUAL_VS_CONSISTENT.labels(consistency_type="eventual").observe(duration)

        current_span.set_attribute("counter.routed_to", region)
        current_span.set_attribute("counter.local_count", result.get("local_count", 0))

        return {
            **result,
            "routing": request.routing,
            "routed_to_endpoint": endpoint,
            "latency_ms": round(duration * 1000, 2)
        }

    except Exception as e:
        REQUEST_COUNT.labels(method="POST", endpoint="/api/increment", status="error").inc()
        raise HTTPException(status_code=503, detail=f"Failed to increment: {e}")


@app.get("/api/counter/{counter_name}")
async def get_counter(counter_name: str):
    """Get global view of a counter (queries coordinator)."""
    start_time = time.time()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{COORDINATOR_URL}/global/counter/{counter_name}"
            )
            result = response.json()

        duration = time.time() - start_time
        REQUEST_COUNT.labels(method="GET", endpoint="/api/counter", status="200").inc()
        REQUEST_LATENCY.labels(method="GET", endpoint="/api/counter").observe(duration)

        return {
            **result,
            "gateway_latency_ms": round(duration * 1000, 2)
        }

    except Exception as e:
        REQUEST_COUNT.labels(method="GET", endpoint="/api/counter", status="error").inc()
        raise HTTPException(status_code=503, detail=f"Failed to get counter: {e}")


@app.get("/api/counters")
async def get_all_counters():
    """Get all counters from coordinator."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{COORDINATOR_URL}/global/counters")
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to get counters: {e}")


@app.get("/api/status")
async def get_status():
    """Get global status including partition info."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{COORDINATOR_URL}/global/status")
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to get status: {e}")


@app.post("/api/compare")
async def compare_consistency(request: CompareRequest):
    """
    Compare eventual consistency vs hypothetical strong consistency.

    This demonstrates the latency trade-off:
    - Eventual: Single region write, fast but may read stale
    - Strong: Would need to write to all regions, slower but consistent

    For demo purposes, we simulate strong consistency by writing
    to all regions and waiting for all to respond.
    """
    eventual_times = []
    strong_times = []

    for i in range(request.iterations):
        # Eventual consistency: single region write
        start = time.time()
        async with httpx.AsyncClient(timeout=5.0) as client:
            endpoint = get_random_endpoint()
            await client.post(
                f"http://{endpoint}/increment",
                json={"counter_name": f"{request.counter_name}_eventual", "amount": 1}
            )
        eventual_times.append(time.time() - start)

        # Simulated strong consistency: write to ALL regions
        start = time.time()
        async with httpx.AsyncClient(timeout=5.0) as client:
            tasks = [
                client.post(
                    f"http://{ep}/increment",
                    json={"counter_name": f"{request.counter_name}_strong", "amount": 1}
                )
                for ep in COUNTER_ENDPOINTS if ep
            ]
            await asyncio.gather(*tasks)
        strong_times.append(time.time() - start)

    # Calculate statistics
    def stats(times):
        if not times:
            return {"avg": 0, "min": 0, "max": 0, "p50": 0, "p99": 0}
        times = sorted(times)
        return {
            "avg_ms": round(sum(times) / len(times) * 1000, 2),
            "min_ms": round(min(times) * 1000, 2),
            "max_ms": round(max(times) * 1000, 2),
            "p50_ms": round(times[len(times) // 2] * 1000, 2),
            "p99_ms": round(times[int(len(times) * 0.99)] * 1000, 2)
        }

    eventual_stats = stats(eventual_times)
    strong_stats = stats(strong_times)

    return {
        "iterations": request.iterations,
        "eventual_consistency": {
            "description": "Single region write, background sync",
            **eventual_stats
        },
        "strong_consistency": {
            "description": "Write to all regions, wait for all",
            **strong_stats
        },
        "speedup": round(strong_stats["avg_ms"] / eventual_stats["avg_ms"], 2) if eventual_stats["avg_ms"] > 0 else 0,
        "conclusion": f"Eventual consistency is ~{round(strong_stats['avg_ms'] / eventual_stats['avg_ms'], 1)}x faster" if eventual_stats["avg_ms"] > 0 else "N/A"
    }


@app.post("/admin/partition/{region}")
async def set_partition(region: str, enabled: bool = True):
    """Set partition status for a region."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{COORDINATOR_URL}/global/partition",
                json={"region": region, "enabled": enabled}
            )
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to set partition: {e}")


@app.post("/admin/partition/all")
async def set_all_partitions(enabled: bool = True):
    """Set partition on all regions."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{COORDINATOR_URL}/global/partition/all",
                params={"enabled": enabled}
            )
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to set partitions: {e}")


@app.post("/admin/force-sync")
async def force_sync():
    """Force all regions to sync."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{COORDINATOR_URL}/global/force-sync")
            return response.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to force sync: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
