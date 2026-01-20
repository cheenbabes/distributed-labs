"""
Coordinator - Implements quorum-based reads and writes across replica nodes.

This service is the brain of the quorum system, implementing:
- Configurable N, R, W parameters
- Strict vs Sloppy quorum modes
- Version-based conflict resolution
- Parallel replica communication
"""
import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "coordinator")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
COORDINATOR_PORT = int(os.getenv("COORDINATOR_PORT", "8000"))

# Quorum configuration
REPLICA_COUNT = int(os.getenv("REPLICA_COUNT", "5"))
REPLICA_URLS = os.getenv("REPLICA_URLS", "").split(",")
READ_QUORUM = int(os.getenv("READ_QUORUM", "3"))
WRITE_QUORUM = int(os.getenv("WRITE_QUORUM", "3"))
QUORUM_MODE = os.getenv("QUORUM_MODE", "strict")  # strict or sloppy
TIMEOUT_MS = int(os.getenv("TIMEOUT_MS", "5000"))

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
    "coordinator_requests_total",
    "Total coordinator requests",
    ["operation", "status", "quorum_met"]
)
QUORUM_LATENCY = Histogram(
    "coordinator_quorum_latency_seconds",
    "Time to achieve quorum",
    ["operation"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
REPLICA_RESPONSES = Counter(
    "coordinator_replica_responses_total",
    "Replica response counts",
    ["replica_id", "operation", "status"]
)
QUORUM_CONFIG = Gauge(
    "coordinator_quorum_config",
    "Current quorum configuration",
    ["parameter"]
)
ACTIVE_REPLICAS = Gauge(
    "coordinator_active_replicas",
    "Number of currently active replicas"
)
READ_CONFLICTS = Counter(
    "coordinator_read_conflicts_total",
    "Number of read conflicts detected (different versions)"
)


class QuorumMode(str, Enum):
    STRICT = "strict"
    SLOPPY = "sloppy"


@dataclass
class QuorumConfig:
    n: int = REPLICA_COUNT
    r: int = READ_QUORUM
    w: int = WRITE_QUORUM
    mode: QuorumMode = QuorumMode(QUORUM_MODE)
    timeout_ms: int = TIMEOUT_MS

    def is_consistent(self) -> bool:
        """Check if R + W > N (strong consistency)"""
        return self.r + self.w > self.n


# Global quorum config (can be modified at runtime)
quorum_config = QuorumConfig()


class WriteRequest(BaseModel):
    key: str
    value: str


class WriteResponse(BaseModel):
    key: str
    version: int
    replicas_written: int
    quorum_met: bool
    write_latency_ms: float
    trace_id: str


class ReadResponse(BaseModel):
    key: str
    value: Optional[str]
    version: Optional[int]
    replicas_read: int
    quorum_met: bool
    read_latency_ms: float
    had_conflicts: bool
    trace_id: str


class QuorumConfigRequest(BaseModel):
    r: Optional[int] = None
    w: Optional[int] = None
    mode: Optional[str] = None
    timeout_ms: Optional[int] = None


class QuorumConfigResponse(BaseModel):
    n: int
    r: int
    w: int
    mode: str
    timeout_ms: int
    is_consistent: bool
    consistency_formula: str


@dataclass
class ReplicaResult:
    replica_id: int
    success: bool
    value: Optional[str] = None
    version: Optional[int] = None
    latency_ms: float = 0.0
    error: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Quorum config: N={quorum_config.n}, R={quorum_config.r}, W={quorum_config.w}, mode={quorum_config.mode}")
    logger.info(f"Replicas: {REPLICA_URLS}")

    # Set initial metrics
    QUORUM_CONFIG.labels(parameter="N").set(quorum_config.n)
    QUORUM_CONFIG.labels(parameter="R").set(quorum_config.r)
    QUORUM_CONFIG.labels(parameter="W").set(quorum_config.w)

    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


async def write_to_replica(client: httpx.AsyncClient, replica_url: str, replica_id: int, key: str, value: str, version: int) -> ReplicaResult:
    """Write to a single replica."""
    start_time = time.time()
    try:
        response = await client.post(
            f"{replica_url}/write",
            json={"key": key, "value": value, "version": version},
            timeout=quorum_config.timeout_ms / 1000.0
        )
        latency_ms = (time.time() - start_time) * 1000

        if response.status_code == 200:
            REPLICA_RESPONSES.labels(replica_id=str(replica_id), operation="write", status="success").inc()
            return ReplicaResult(replica_id=replica_id, success=True, version=version, latency_ms=latency_ms)
        else:
            REPLICA_RESPONSES.labels(replica_id=str(replica_id), operation="write", status="error").inc()
            return ReplicaResult(replica_id=replica_id, success=False, error=f"HTTP {response.status_code}", latency_ms=latency_ms)
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        REPLICA_RESPONSES.labels(replica_id=str(replica_id), operation="write", status="timeout").inc()
        return ReplicaResult(replica_id=replica_id, success=False, error=str(e), latency_ms=latency_ms)


async def read_from_replica(client: httpx.AsyncClient, replica_url: str, replica_id: int, key: str) -> ReplicaResult:
    """Read from a single replica."""
    start_time = time.time()
    try:
        response = await client.get(
            f"{replica_url}/read/{key}",
            timeout=quorum_config.timeout_ms / 1000.0
        )
        latency_ms = (time.time() - start_time) * 1000

        if response.status_code == 200:
            data = response.json()
            REPLICA_RESPONSES.labels(replica_id=str(replica_id), operation="read", status="success").inc()
            return ReplicaResult(
                replica_id=replica_id,
                success=True,
                value=data.get("value"),
                version=data.get("version"),
                latency_ms=latency_ms
            )
        elif response.status_code == 404:
            REPLICA_RESPONSES.labels(replica_id=str(replica_id), operation="read", status="not_found").inc()
            return ReplicaResult(replica_id=replica_id, success=True, value=None, version=None, latency_ms=latency_ms)
        else:
            REPLICA_RESPONSES.labels(replica_id=str(replica_id), operation="read", status="error").inc()
            return ReplicaResult(replica_id=replica_id, success=False, error=f"HTTP {response.status_code}", latency_ms=latency_ms)
    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        REPLICA_RESPONSES.labels(replica_id=str(replica_id), operation="read", status="timeout").inc()
        return ReplicaResult(replica_id=replica_id, success=False, error=str(e), latency_ms=latency_ms)


def resolve_read_conflicts(results: list[ReplicaResult]) -> tuple[Optional[str], Optional[int], bool]:
    """
    Resolve conflicts by returning the value with the highest version.
    Returns (value, version, had_conflicts)
    """
    successful_reads = [r for r in results if r.success and r.value is not None]

    if not successful_reads:
        return None, None, False

    # Check for conflicts (different versions)
    versions = set(r.version for r in successful_reads if r.version is not None)
    had_conflicts = len(versions) > 1

    if had_conflicts:
        READ_CONFLICTS.inc()
        logger.warning(f"Read conflict detected: versions {versions}")

    # Return highest version
    best_result = max(successful_reads, key=lambda r: r.version or 0)
    return best_result.value, best_result.version, had_conflicts


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/config")
async def get_config() -> QuorumConfigResponse:
    """Get current quorum configuration."""
    return QuorumConfigResponse(
        n=quorum_config.n,
        r=quorum_config.r,
        w=quorum_config.w,
        mode=quorum_config.mode.value,
        timeout_ms=quorum_config.timeout_ms,
        is_consistent=quorum_config.is_consistent(),
        consistency_formula=f"R({quorum_config.r}) + W({quorum_config.w}) = {quorum_config.r + quorum_config.w} {'>' if quorum_config.is_consistent() else '<='} N({quorum_config.n})"
    )


@app.post("/config")
async def update_config(config: QuorumConfigRequest) -> QuorumConfigResponse:
    """Update quorum configuration at runtime."""
    if config.r is not None:
        if config.r < 1 or config.r > quorum_config.n:
            raise HTTPException(status_code=400, detail=f"R must be between 1 and N({quorum_config.n})")
        quorum_config.r = config.r
        QUORUM_CONFIG.labels(parameter="R").set(config.r)

    if config.w is not None:
        if config.w < 1 or config.w > quorum_config.n:
            raise HTTPException(status_code=400, detail=f"W must be between 1 and N({quorum_config.n})")
        quorum_config.w = config.w
        QUORUM_CONFIG.labels(parameter="W").set(config.w)

    if config.mode is not None:
        if config.mode not in ["strict", "sloppy"]:
            raise HTTPException(status_code=400, detail="Mode must be 'strict' or 'sloppy'")
        quorum_config.mode = QuorumMode(config.mode)

    if config.timeout_ms is not None:
        quorum_config.timeout_ms = config.timeout_ms

    logger.info(f"Config updated: R={quorum_config.r}, W={quorum_config.w}, mode={quorum_config.mode}")

    return await get_config()


@app.post("/write")
async def write(request: WriteRequest) -> WriteResponse:
    """
    Write a value with quorum.

    In strict mode: Must write to exactly W replicas from the original N
    In sloppy mode: Can write to any W available replicas (hinted handoff style)
    """
    start_time = time.time()
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Generate version (timestamp-based for simplicity)
    version = int(time.time() * 1000000)

    current_span.set_attribute("quorum.operation", "write")
    current_span.set_attribute("quorum.n", quorum_config.n)
    current_span.set_attribute("quorum.w", quorum_config.w)
    current_span.set_attribute("quorum.mode", quorum_config.mode.value)
    current_span.set_attribute("key", request.key)

    async with httpx.AsyncClient() as client:
        # Send writes to all replicas in parallel
        tasks = [
            write_to_replica(client, url, i + 1, request.key, request.value, version)
            for i, url in enumerate(REPLICA_URLS)
            if url
        ]

        results = await asyncio.gather(*tasks)

    # Count successful writes
    successful_writes = [r for r in results if r.success]
    replicas_written = len(successful_writes)

    # Check if quorum met
    if quorum_config.mode == QuorumMode.STRICT:
        quorum_met = replicas_written >= quorum_config.w
    else:
        # Sloppy quorum: just need W successful writes
        quorum_met = replicas_written >= quorum_config.w

    latency_ms = (time.time() - start_time) * 1000

    # Record metrics
    QUORUM_LATENCY.labels(operation="write").observe(latency_ms / 1000.0)
    REQUEST_COUNT.labels(operation="write", status="success" if quorum_met else "quorum_failed", quorum_met=str(quorum_met)).inc()
    ACTIVE_REPLICAS.set(replicas_written)

    current_span.set_attribute("quorum.replicas_written", replicas_written)
    current_span.set_attribute("quorum.met", quorum_met)

    logger.info(f"Write key={request.key} version={version} replicas={replicas_written}/{quorum_config.w} quorum_met={quorum_met}")

    if not quorum_met and quorum_config.mode == QuorumMode.STRICT:
        raise HTTPException(
            status_code=503,
            detail=f"Write quorum not met: only {replicas_written}/{quorum_config.w} replicas responded"
        )

    return WriteResponse(
        key=request.key,
        version=version,
        replicas_written=replicas_written,
        quorum_met=quorum_met,
        write_latency_ms=round(latency_ms, 2),
        trace_id=trace_id
    )


@app.get("/read/{key}")
async def read(key: str) -> ReadResponse:
    """
    Read a value with quorum.

    In strict mode: Must read from exactly R replicas from the original N
    In sloppy mode: Can read from any R available replicas

    Conflict resolution: Returns the value with the highest version
    """
    start_time = time.time()
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    current_span.set_attribute("quorum.operation", "read")
    current_span.set_attribute("quorum.n", quorum_config.n)
    current_span.set_attribute("quorum.r", quorum_config.r)
    current_span.set_attribute("quorum.mode", quorum_config.mode.value)
    current_span.set_attribute("key", key)

    async with httpx.AsyncClient() as client:
        # Send reads to all replicas in parallel
        tasks = [
            read_from_replica(client, url, i + 1, key)
            for i, url in enumerate(REPLICA_URLS)
            if url
        ]

        results = await asyncio.gather(*tasks)

    # Count successful reads
    successful_reads = [r for r in results if r.success]
    replicas_read = len(successful_reads)

    # Check if quorum met
    if quorum_config.mode == QuorumMode.STRICT:
        quorum_met = replicas_read >= quorum_config.r
    else:
        quorum_met = replicas_read >= quorum_config.r

    # Resolve conflicts and get the best value
    value, version, had_conflicts = resolve_read_conflicts(results)

    latency_ms = (time.time() - start_time) * 1000

    # Record metrics
    QUORUM_LATENCY.labels(operation="read").observe(latency_ms / 1000.0)
    REQUEST_COUNT.labels(operation="read", status="success" if quorum_met else "quorum_failed", quorum_met=str(quorum_met)).inc()

    current_span.set_attribute("quorum.replicas_read", replicas_read)
    current_span.set_attribute("quorum.met", quorum_met)
    current_span.set_attribute("quorum.had_conflicts", had_conflicts)

    logger.info(f"Read key={key} version={version} replicas={replicas_read}/{quorum_config.r} quorum_met={quorum_met} conflicts={had_conflicts}")

    if not quorum_met and quorum_config.mode == QuorumMode.STRICT:
        raise HTTPException(
            status_code=503,
            detail=f"Read quorum not met: only {replicas_read}/{quorum_config.r} replicas responded"
        )

    return ReadResponse(
        key=key,
        value=value,
        version=version,
        replicas_read=replicas_read,
        quorum_met=quorum_met,
        read_latency_ms=round(latency_ms, 2),
        had_conflicts=had_conflicts,
        trace_id=trace_id
    )


@app.get("/replicas/status")
async def replica_status():
    """Check the status of all replicas."""
    async with httpx.AsyncClient() as client:
        results = []
        for i, url in enumerate(REPLICA_URLS):
            if not url:
                continue
            replica_id = i + 1
            try:
                start = time.time()
                response = await client.get(f"{url}/health", timeout=2.0)
                latency = (time.time() - start) * 1000
                if response.status_code == 200:
                    data = response.json()
                    results.append({
                        "replica_id": replica_id,
                        "url": url,
                        "status": "healthy",
                        "latency_ms": round(latency, 2),
                        "data": data
                    })
                else:
                    results.append({
                        "replica_id": replica_id,
                        "url": url,
                        "status": "unhealthy",
                        "error": f"HTTP {response.status_code}"
                    })
            except Exception as e:
                results.append({
                    "replica_id": replica_id,
                    "url": url,
                    "status": "unreachable",
                    "error": str(e)
                })

        healthy = len([r for r in results if r["status"] == "healthy"])
        ACTIVE_REPLICAS.set(healthy)

        return {
            "total_replicas": quorum_config.n,
            "healthy_replicas": healthy,
            "can_read": healthy >= quorum_config.r,
            "can_write": healthy >= quorum_config.w,
            "replicas": results
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=COORDINATOR_PORT)
