"""
Client Gateway - Routes reads to replicas and writes to primary.
Automatically discovers current primary from failover controller.
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from typing import Any, List

import httpx
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "client-gateway")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
FAILOVER_CONTROLLER_URL = os.getenv("FAILOVER_CONTROLLER_URL", "http://lab30-failover-controller:8010")
PRIMARY_URL = os.getenv("PRIMARY_URL", "http://lab30-primary-db:8001")
REPLICA_URLS = os.getenv("REPLICA_URLS", "http://lab30-replica-1:8002,http://lab30-replica-2:8003").split(",")

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
    "gateway_requests_total",
    "Total gateway requests",
    ["operation", "target", "status"]
)
REQUEST_LATENCY = Histogram(
    "gateway_request_duration_seconds",
    "Gateway request latency",
    ["operation"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
ROUTING_DECISIONS = Counter(
    "gateway_routing_decisions_total",
    "Routing decisions made by gateway",
    ["decision"]
)
CURRENT_PRIMARY = Gauge(
    "gateway_current_primary",
    "Current primary node being used",
    ["node_url"]
)
READ_REPLICA_USED = Counter(
    "gateway_read_replica_used_total",
    "Which replica was used for read operations",
    ["replica"]
)
WRITE_ERRORS = Counter(
    "gateway_write_errors_total",
    "Total write errors by type",
    ["error_type"]
)

# Global state
redis_client: redis.Redis = None
current_primary_url = PRIMARY_URL
healthy_replicas: List[str] = []
topology_refresh_task = None


class WriteRequest(BaseModel):
    key: str
    value: Any


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, topology_refresh_task, healthy_replicas
    logger.info(f"{SERVICE_NAME} starting up")

    # Connect to Redis
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

    # Initialize healthy replicas
    healthy_replicas = REPLICA_URLS.copy()

    # Start topology refresh task
    topology_refresh_task = asyncio.create_task(refresh_topology_loop())

    yield

    # Cleanup
    if topology_refresh_task:
        topology_refresh_task.cancel()
    await redis_client.close()
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


async def refresh_topology_loop():
    """Periodically refresh cluster topology from Redis and failover controller."""
    global current_primary_url, healthy_replicas

    while True:
        try:
            # Get current primary from Redis (set by failover controller)
            stored_primary_url = await redis_client.get("cluster:current_primary_url")
            if stored_primary_url and stored_primary_url != current_primary_url:
                logger.info(f"Primary changed: {current_primary_url} -> {stored_primary_url}")
                current_primary_url = stored_primary_url
                CURRENT_PRIMARY.labels(node_url=current_primary_url).set(1)

            # Check replica health
            new_healthy = []
            for replica_url in REPLICA_URLS:
                try:
                    async with httpx.AsyncClient(timeout=2.0) as client:
                        resp = await client.get(f"{replica_url}/health")
                        if resp.status_code == 200:
                            new_healthy.append(replica_url)
                except Exception:
                    pass

            healthy_replicas = new_healthy if new_healthy else REPLICA_URLS

            await asyncio.sleep(2)  # Refresh every 2 seconds

        except Exception as e:
            logger.error(f"Topology refresh error: {e}")
            await asyncio.sleep(5)


async def get_current_primary() -> str:
    """Get the current primary URL, checking Redis first."""
    global current_primary_url

    try:
        stored_url = await redis_client.get("cluster:current_primary_url")
        if stored_url:
            current_primary_url = stored_url
    except Exception:
        pass

    return current_primary_url


def select_replica() -> str:
    """Select a healthy replica for read operations using round-robin or random."""
    if healthy_replicas:
        selected = random.choice(healthy_replicas)
        READ_REPLICA_USED.labels(replica=selected.split("/")[-1].split(":")[0]).inc()
        return selected
    return REPLICA_URLS[0]  # Fallback


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "current_primary": current_primary_url,
        "healthy_replicas": len(healthy_replicas)
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
async def status():
    """Detailed gateway status."""
    primary = await get_current_primary()
    cluster_primary = await redis_client.get("cluster:current_primary")

    return {
        "service": SERVICE_NAME,
        "current_primary_url": primary,
        "cluster_primary_id": cluster_primary,
        "healthy_replicas": healthy_replicas,
        "all_replicas": REPLICA_URLS,
        "failover_controller": FAILOVER_CONTROLLER_URL
    }


@app.post("/write")
async def write(request: WriteRequest):
    """
    Write data - routes to current primary.
    Handles failover automatically.
    """
    start_time = time.time()

    with tracer.start_as_current_span("gateway_write") as span:
        span.set_attribute("key", request.key)

        primary = await get_current_primary()
        span.set_attribute("target_primary", primary)

        ROUTING_DECISIONS.labels(decision="write_to_primary").inc()

        # Try to write to primary
        retries = 0
        max_retries = 3
        last_error = None

        while retries < max_retries:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.post(
                        f"{primary}/write",
                        json={"key": request.key, "value": request.value}
                    )

                    if response.status_code == 200:
                        result = response.json()
                        duration = time.time() - start_time

                        REQUEST_COUNT.labels(
                            operation="write",
                            target=primary.split("/")[-1].split(":")[0],
                            status="success"
                        ).inc()
                        REQUEST_LATENCY.labels(operation="write").observe(duration)

                        span.set_attribute("sequence", result.get("sequence", 0))

                        return {
                            "status": "ok",
                            "key": request.key,
                            "routed_to": primary,
                            "result": result,
                            "gateway_duration_ms": round(duration * 1000, 2)
                        }
                    elif response.status_code == 400:
                        # Node says it's not the primary, refresh topology
                        logger.warning(f"Node {primary} rejected write - not primary")
                        WRITE_ERRORS.labels(error_type="not_primary").inc()
                        primary = await get_current_primary()
                        retries += 1
                    else:
                        last_error = f"HTTP {response.status_code}: {response.text}"
                        retries += 1

            except httpx.TimeoutException:
                WRITE_ERRORS.labels(error_type="timeout").inc()
                last_error = "Request timeout"
                retries += 1
                # Refresh primary on timeout (might have failed over)
                await asyncio.sleep(0.5)
                primary = await get_current_primary()

            except httpx.ConnectError:
                WRITE_ERRORS.labels(error_type="connection_error").inc()
                last_error = "Connection error"
                retries += 1
                # Primary might be down, refresh topology
                await asyncio.sleep(0.5)
                primary = await get_current_primary()

            except Exception as e:
                WRITE_ERRORS.labels(error_type="unknown").inc()
                last_error = str(e)
                retries += 1

        # All retries failed
        duration = time.time() - start_time
        REQUEST_COUNT.labels(
            operation="write",
            target="unknown",
            status="error"
        ).inc()
        REQUEST_LATENCY.labels(operation="write").observe(duration)

        span.set_attribute("error", True)
        span.set_attribute("error_message", last_error)

        raise HTTPException(
            status_code=503,
            detail=f"Write failed after {max_retries} retries: {last_error}"
        )


@app.get("/read/{key}")
async def read(key: str, use_primary: bool = False):
    """
    Read data - routes to replica by default, or primary if specified.
    """
    start_time = time.time()

    with tracer.start_as_current_span("gateway_read") as span:
        span.set_attribute("key", key)
        span.set_attribute("use_primary", use_primary)

        if use_primary:
            target = await get_current_primary()
            ROUTING_DECISIONS.labels(decision="read_from_primary").inc()
        else:
            target = select_replica()
            ROUTING_DECISIONS.labels(decision="read_from_replica").inc()

        span.set_attribute("target", target)

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{target}/read/{key}")

                duration = time.time() - start_time

                if response.status_code == 200:
                    result = response.json()
                    REQUEST_COUNT.labels(
                        operation="read",
                        target=target.split("/")[-1].split(":")[0],
                        status="success"
                    ).inc()
                    REQUEST_LATENCY.labels(operation="read").observe(duration)

                    return {
                        "status": "ok",
                        "key": key,
                        "routed_to": target,
                        "is_primary": use_primary,
                        "result": result,
                        "gateway_duration_ms": round(duration * 1000, 2)
                    }
                elif response.status_code == 404:
                    REQUEST_COUNT.labels(
                        operation="read",
                        target=target.split("/")[-1].split(":")[0],
                        status="not_found"
                    ).inc()
                    raise HTTPException(status_code=404, detail=f"Key not found: {key}")
                else:
                    # Try another replica on error
                    if not use_primary and len(healthy_replicas) > 1:
                        other_replicas = [r for r in healthy_replicas if r != target]
                        if other_replicas:
                            target = random.choice(other_replicas)
                            response = await client.get(f"{target}/read/{key}")
                            if response.status_code == 200:
                                result = response.json()
                                REQUEST_COUNT.labels(
                                    operation="read",
                                    target=target.split("/")[-1].split(":")[0],
                                    status="success_retry"
                                ).inc()
                                return {
                                    "status": "ok",
                                    "key": key,
                                    "routed_to": target,
                                    "result": result,
                                    "gateway_duration_ms": round((time.time() - start_time) * 1000, 2)
                                }

                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"Read failed: {response.text}"
                    )

        except HTTPException:
            raise
        except Exception as e:
            REQUEST_COUNT.labels(
                operation="read",
                target=target.split("/")[-1].split(":")[0],
                status="error"
            ).inc()
            raise HTTPException(status_code=503, detail=f"Read failed: {str(e)}")


@app.get("/cluster/status")
async def cluster_status():
    """Get overall cluster status from failover controller."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{FAILOVER_CONTROLLER_URL}/status")
            if response.status_code == 200:
                return response.json()
            else:
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to get cluster status"
                )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Cluster status unavailable: {str(e)}")


@app.get("/cluster/topology")
async def cluster_topology():
    """Get cluster topology."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{FAILOVER_CONTROLLER_URL}/cluster/topology")
            if response.status_code == 200:
                return response.json()
    except Exception:
        pass

    # Fallback to local knowledge
    return {
        "primary": {"url": current_primary_url},
        "replicas": [{"url": r} for r in REPLICA_URLS]
    }


@app.post("/api/data")
async def write_data(request: WriteRequest):
    """Alias for /write for cleaner API."""
    return await write(request)


@app.get("/api/data/{key}")
async def read_data(key: str, consistent: bool = False):
    """
    Alias for /read with consistent read option.
    consistent=true reads from primary for strong consistency.
    """
    return await read(key, use_primary=consistent)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
