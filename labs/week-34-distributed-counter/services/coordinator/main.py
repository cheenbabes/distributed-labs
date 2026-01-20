"""
Coordinator Service - Global view and orchestration for distributed counters.

This service provides:
- Aggregated view of all regional counters
- Global counter queries (read from all regions)
- Partition management across regions
- Comparison metrics between eventual and consistent counters
"""
import asyncio
import logging
import os
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
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from pydantic import BaseModel

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "coordinator")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
REGION_ENDPOINTS = os.getenv("REGION_ENDPOINTS", "").split(",") if os.getenv("REGION_ENDPOINTS") else []

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
GLOBAL_COUNT = Gauge(
    "coordinator_global_count",
    "Global aggregated count across all regions",
    ["counter_name"]
)
REGION_COUNT = Gauge(
    "coordinator_region_count",
    "Count from each region",
    ["counter_name", "region"]
)
CONVERGENCE_LAG = Gauge(
    "coordinator_convergence_lag",
    "Difference between highest and lowest regional counts",
    ["counter_name"]
)
QUERY_LATENCY = Histogram(
    "coordinator_query_latency_seconds",
    "Latency for global queries",
    ["operation"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
HEALTHY_REGIONS = Gauge(
    "coordinator_healthy_regions",
    "Number of healthy regions"
)


class PartitionRequest(BaseModel):
    """Request to set partition for a region."""
    region: str
    enabled: bool


class GlobalIncrementRequest(BaseModel):
    """Request to increment across regions."""
    counter_name: str = "views"
    amount: int = 1


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Region endpoints: {REGION_ENDPOINTS}")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title="Distributed Counter Coordinator", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


async def query_region(endpoint: str, path: str) -> Optional[Dict]:
    """Query a single region endpoint."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"http://{endpoint}{path}")
            if response.status_code == 200:
                return response.json()
    except Exception as e:
        logger.warning(f"Failed to query {endpoint}: {e}")
    return None


async def post_region(endpoint: str, path: str, data: Dict) -> Optional[Dict]:
    """POST to a single region endpoint."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"http://{endpoint}{path}",
                json=data
            )
            if response.status_code == 200:
                return response.json()
    except Exception as e:
        logger.warning(f"Failed to post to {endpoint}: {e}")
    return None


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/global/counter/{counter_name}")
async def get_global_counter(counter_name: str):
    """
    Get global view of a counter across all regions.

    This queries all regions and provides:
    - Each region's view of the count
    - The maximum count seen (eventually consistent global value)
    - Convergence status (whether regions have synced)
    """
    start_time = time.time()

    current_span = trace.get_current_span()
    current_span.set_attribute("counter.name", counter_name)

    # Query all regions in parallel
    tasks = [
        query_region(endpoint, f"/counter/{counter_name}")
        for endpoint in REGION_ENDPOINTS if endpoint
    ]
    results = await asyncio.gather(*tasks)

    regions = {}
    total_counts = []
    healthy_count = 0

    for endpoint, result in zip(REGION_ENDPOINTS, results):
        if result:
            region = result.get("region", endpoint)
            regions[region] = {
                "local_count": result.get("local_count", 0),
                "total_count": result.get("total_count", 0),
                "nodes": result.get("nodes", {})
            }
            total_counts.append(result.get("total_count", 0))
            healthy_count += 1

            # Update per-region metric
            REGION_COUNT.labels(
                counter_name=counter_name, region=region
            ).set(result.get("total_count", 0))

    HEALTHY_REGIONS.set(healthy_count)

    # Calculate global metrics
    if total_counts:
        max_count = max(total_counts)
        min_count = min(total_counts)
        convergence_lag = max_count - min_count
        converged = convergence_lag == 0
    else:
        max_count = 0
        min_count = 0
        convergence_lag = 0
        converged = True

    # Update metrics
    GLOBAL_COUNT.labels(counter_name=counter_name).set(max_count)
    CONVERGENCE_LAG.labels(counter_name=counter_name).set(convergence_lag)

    duration = time.time() - start_time
    QUERY_LATENCY.labels(operation="get_global_counter").observe(duration)

    current_span.set_attribute("counter.global_count", max_count)
    current_span.set_attribute("counter.converged", converged)
    current_span.set_attribute("counter.convergence_lag", convergence_lag)

    return {
        "counter": counter_name,
        "global_count": max_count,
        "regions": regions,
        "convergence": {
            "converged": converged,
            "lag": convergence_lag,
            "max_count": max_count,
            "min_count": min_count
        },
        "healthy_regions": healthy_count,
        "total_regions": len(REGION_ENDPOINTS),
        "query_time_ms": round(duration * 1000, 2)
    }


@app.get("/global/counters")
async def get_all_global_counters():
    """Get all counters from all regions."""
    start_time = time.time()

    # Query all regions
    tasks = [
        query_region(endpoint, "/counters")
        for endpoint in REGION_ENDPOINTS if endpoint
    ]
    results = await asyncio.gather(*tasks)

    regions = {}
    all_counter_names = set()

    for endpoint, result in zip(REGION_ENDPOINTS, results):
        if result:
            region = result.get("region", endpoint)
            regions[region] = result
            if "counters" in result:
                all_counter_names.update(result["counters"].keys())

    duration = time.time() - start_time

    return {
        "regions": regions,
        "counter_names": list(all_counter_names),
        "healthy_regions": len([r for r in results if r]),
        "total_regions": len(REGION_ENDPOINTS),
        "query_time_ms": round(duration * 1000, 2)
    }


@app.get("/global/status")
async def get_global_status():
    """Get health and partition status of all regions."""
    tasks = []
    for endpoint in REGION_ENDPOINTS:
        if endpoint:
            tasks.append(asyncio.gather(
                query_region(endpoint, "/health"),
                query_region(endpoint, "/admin/partition")
            ))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    regions = {}
    for endpoint, result in zip(REGION_ENDPOINTS, results):
        if isinstance(result, Exception):
            regions[endpoint] = {"status": "error", "error": str(result)}
        elif result:
            health, partition = result
            if health and partition:
                regions[health.get("region", endpoint)] = {
                    "status": "healthy",
                    "node_id": health.get("node_id"),
                    "network_partition": partition.get("network_partition", False)
                }
            else:
                regions[endpoint] = {"status": "unhealthy"}

    return {
        "regions": regions,
        "healthy_count": sum(1 for r in regions.values() if r.get("status") == "healthy"),
        "partitioned_count": sum(1 for r in regions.values() if r.get("network_partition", False))
    }


@app.post("/global/partition")
async def set_global_partition(request: PartitionRequest):
    """Set partition status for a specific region."""
    current_span = trace.get_current_span()
    current_span.set_attribute("partition.region", request.region)
    current_span.set_attribute("partition.enabled", request.enabled)

    # Find the region endpoint
    target_endpoint = None
    for endpoint in REGION_ENDPOINTS:
        result = await query_region(endpoint, "/health")
        if result and result.get("region") == request.region:
            target_endpoint = endpoint
            break

    if not target_endpoint:
        raise HTTPException(status_code=404, detail=f"Region {request.region} not found")

    result = await post_region(
        target_endpoint,
        "/admin/partition",
        {"enabled": request.enabled}
    )

    if result:
        return {
            "status": "success",
            "region": request.region,
            "network_partition": request.enabled
        }
    else:
        raise HTTPException(status_code=500, detail="Failed to set partition")


@app.post("/global/partition/all")
async def set_all_partitions(enabled: bool = True):
    """Enable or disable partition on all regions."""
    tasks = [
        post_region(endpoint, "/admin/partition", {"enabled": enabled})
        for endpoint in REGION_ENDPOINTS if endpoint
    ]
    results = await asyncio.gather(*tasks)

    return {
        "status": "success",
        "partition_enabled": enabled,
        "regions_updated": sum(1 for r in results if r)
    }


@app.post("/global/force-sync")
async def force_global_sync():
    """Force all regions to sync immediately."""
    tasks = [
        post_region(endpoint, "/admin/force-sync", {})
        for endpoint in REGION_ENDPOINTS if endpoint
    ]
    results = await asyncio.gather(*tasks)

    return {
        "status": "sync_initiated",
        "regions_synced": sum(1 for r in results if r)
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
