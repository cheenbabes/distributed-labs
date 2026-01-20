"""
Sync Service - Handles state synchronization between CRDT replicas.
Implements anti-entropy protocol for eventual consistency.
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Dict, List, Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "sync-service")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
SYNC_INTERVAL_MS = int(os.getenv("SYNC_INTERVAL_MS", "5000"))

# Replica configuration - comma-separated list of replica URLs
REPLICA_URLS = os.getenv("REPLICA_URLS", "http://replica-1:8001,http://replica-2:8002,http://replica-3:8003").split(",")

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
SYNC_CYCLES = Counter(
    "crdt_sync_cycles_total",
    "Total sync cycles completed",
    ["status"]
)
SYNC_DURATION = Histogram(
    "crdt_sync_duration_seconds",
    "Duration of sync cycles",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)
REPLICA_STATES_FETCHED = Counter(
    "crdt_replica_states_fetched_total",
    "Total replica states fetched",
    ["replica"]
)
REPLICA_MERGES = Counter(
    "crdt_replica_merges_total",
    "Total merge operations sent to replicas",
    ["replica", "status"]
)
SYNC_ENABLED_GAUGE = Gauge(
    "crdt_sync_enabled",
    "Whether automatic sync is enabled"
)
REPLICAS_REACHABLE = Gauge(
    "crdt_replicas_reachable",
    "Number of reachable replicas"
)


# =============================================================================
# State
# =============================================================================

sync_enabled = True
sync_task: Optional[asyncio.Task] = None
last_sync_time: float = 0
last_sync_status: str = "never"
sync_error_count = 0


# =============================================================================
# Pydantic Models
# =============================================================================

class SyncConfigRequest(BaseModel):
    enabled: bool


class ManualSyncRequest(BaseModel):
    replicas: Optional[List[str]] = None  # If None, sync all


# =============================================================================
# Sync Logic
# =============================================================================

async def fetch_replica_state(client: httpx.AsyncClient, replica_url: str) -> Optional[Dict[str, Any]]:
    """Fetch the complete state from a replica."""
    try:
        response = await client.get(f"{replica_url}/state", timeout=5.0)
        if response.status_code == 200:
            REPLICA_STATES_FETCHED.labels(replica=replica_url).inc()
            return response.json()
        elif response.status_code == 503:
            logger.warning(f"Replica {replica_url} is partitioned, skipping")
            return None
        else:
            logger.error(f"Failed to fetch state from {replica_url}: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Error fetching state from {replica_url}: {e}")
        return None


async def push_state_to_replica(client: httpx.AsyncClient, replica_url: str, state: Dict[str, Any]) -> bool:
    """Push merged state to a replica."""
    try:
        response = await client.post(
            f"{replica_url}/state/merge",
            json={
                "g_counters": state.get("g_counters", {}),
                "pn_counters": state.get("pn_counters", {}),
                "lww_registers": state.get("lww_registers", {})
            },
            timeout=5.0
        )
        if response.status_code == 200:
            REPLICA_MERGES.labels(replica=replica_url, status="success").inc()
            return True
        elif response.status_code == 503:
            logger.warning(f"Replica {replica_url} is partitioned, cannot push state")
            REPLICA_MERGES.labels(replica=replica_url, status="partitioned").inc()
            return False
        else:
            logger.error(f"Failed to push state to {replica_url}: {response.status_code}")
            REPLICA_MERGES.labels(replica=replica_url, status="error").inc()
            return False
    except Exception as e:
        logger.error(f"Error pushing state to {replica_url}: {e}")
        REPLICA_MERGES.labels(replica=replica_url, status="error").inc()
        return False


def merge_gcounter_states(states: List[Dict[str, int]]) -> Dict[str, int]:
    """Merge multiple G-Counter states - take max of each replica's count."""
    merged = {}
    for state in states:
        for replica_id, count in state.items():
            merged[replica_id] = max(merged.get(replica_id, 0), count)
    return merged


def merge_pncounter_states(states: List[Dict[str, Dict[str, int]]]) -> Dict[str, Dict[str, int]]:
    """Merge multiple PN-Counter states."""
    merged_p = {}
    merged_n = {}
    for state in states:
        for replica_id, count in state.get("p", {}).items():
            merged_p[replica_id] = max(merged_p.get(replica_id, 0), count)
        for replica_id, count in state.get("n", {}).items():
            merged_n[replica_id] = max(merged_n.get(replica_id, 0), count)
    return {"p": merged_p, "n": merged_n}


def merge_lww_register_states(states: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge multiple LWW-Register states - highest timestamp wins."""
    if not states:
        return {"value": None, "timestamp": 0.0, "replica_id": ""}

    winner = states[0]
    for state in states[1:]:
        if (state.get("timestamp", 0) > winner.get("timestamp", 0) or
            (state.get("timestamp", 0) == winner.get("timestamp", 0) and
             state.get("replica_id", "") > winner.get("replica_id", ""))):
            winner = state
    return winner


def compute_merged_state(replica_states: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute the merged state from all replicas."""
    merged = {
        "g_counters": {},
        "pn_counters": {},
        "lww_registers": {}
    }

    # Collect all CRDT names
    all_gcounters = set()
    all_pncounters = set()
    all_lww_registers = set()

    for state in replica_states:
        all_gcounters.update(state.get("g_counters", {}).keys())
        all_pncounters.update(state.get("pn_counters", {}).keys())
        all_lww_registers.update(state.get("lww_registers", {}).keys())

    # Merge G-Counters
    for name in all_gcounters:
        states_to_merge = [
            state.get("g_counters", {}).get(name, {})
            for state in replica_states
            if name in state.get("g_counters", {})
        ]
        merged["g_counters"][name] = merge_gcounter_states(states_to_merge)

    # Merge PN-Counters
    for name in all_pncounters:
        states_to_merge = [
            state.get("pn_counters", {}).get(name, {})
            for state in replica_states
            if name in state.get("pn_counters", {})
        ]
        merged["pn_counters"][name] = merge_pncounter_states(states_to_merge)

    # Merge LWW-Registers
    for name in all_lww_registers:
        states_to_merge = [
            state.get("lww_registers", {}).get(name, {})
            for state in replica_states
            if name in state.get("lww_registers", {})
        ]
        merged["lww_registers"][name] = merge_lww_register_states(states_to_merge)

    return merged


async def perform_sync(replica_urls: Optional[List[str]] = None) -> Dict[str, Any]:
    """Perform a full sync cycle across all replicas."""
    global last_sync_time, last_sync_status, sync_error_count

    with tracer.start_as_current_span("sync_cycle") as span:
        start_time = time.time()
        urls = replica_urls or REPLICA_URLS

        span.set_attribute("replica.count", len(urls))

        # Step 1: Fetch state from all replicas
        replica_states = []
        reachable_count = 0

        async with httpx.AsyncClient() as client:
            fetch_tasks = [fetch_replica_state(client, url) for url in urls]
            results = await asyncio.gather(*fetch_tasks)

            for url, state in zip(urls, results):
                if state is not None:
                    replica_states.append(state)
                    reachable_count += 1
                    span.add_event(f"Fetched state from {url}")

        REPLICAS_REACHABLE.set(reachable_count)
        span.set_attribute("replicas.reachable", reachable_count)

        if len(replica_states) < 2:
            logger.warning(f"Not enough replicas reachable for sync ({len(replica_states)})")
            last_sync_status = "insufficient_replicas"
            SYNC_CYCLES.labels(status="insufficient_replicas").inc()
            return {
                "status": "insufficient_replicas",
                "reachable": reachable_count,
                "required": 2
            }

        # Step 2: Compute merged state
        merged_state = compute_merged_state(replica_states)
        span.add_event("Computed merged state")

        # Step 3: Push merged state to all replicas
        push_results = {}
        async with httpx.AsyncClient() as client:
            for url in urls:
                success = await push_state_to_replica(client, url, merged_state)
                push_results[url] = "success" if success else "failed"
                if success:
                    span.add_event(f"Pushed state to {url}")

        # Record metrics
        duration = time.time() - start_time
        SYNC_DURATION.observe(duration)
        last_sync_time = time.time()

        success_count = sum(1 for v in push_results.values() if v == "success")
        if success_count == len(urls):
            last_sync_status = "success"
            SYNC_CYCLES.labels(status="success").inc()
            sync_error_count = 0
        else:
            last_sync_status = "partial"
            SYNC_CYCLES.labels(status="partial").inc()
            sync_error_count += 1

        span.set_attribute("sync.duration_ms", duration * 1000)
        span.set_attribute("sync.status", last_sync_status)

        logger.info(f"Sync cycle completed: {last_sync_status}, duration={duration*1000:.0f}ms")

        return {
            "status": last_sync_status,
            "duration_ms": round(duration * 1000, 2),
            "replicas_reachable": reachable_count,
            "push_results": push_results,
            "merged_state_summary": {
                "g_counters": len(merged_state["g_counters"]),
                "pn_counters": len(merged_state["pn_counters"]),
                "lww_registers": len(merged_state["lww_registers"])
            }
        }


async def sync_loop():
    """Background task that performs periodic sync."""
    global sync_enabled

    logger.info(f"Starting sync loop with interval {SYNC_INTERVAL_MS}ms")

    while sync_enabled:
        try:
            await perform_sync()
        except Exception as e:
            logger.error(f"Error in sync loop: {e}")
            SYNC_CYCLES.labels(status="error").inc()

        await asyncio.sleep(SYNC_INTERVAL_MS / 1000.0)

    logger.info("Sync loop stopped")


# =============================================================================
# FastAPI App
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global sync_task

    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Replicas: {REPLICA_URLS}")
    logger.info(f"Sync interval: {SYNC_INTERVAL_MS}ms")

    # Start background sync task
    if sync_enabled:
        sync_task = asyncio.create_task(sync_loop())
        SYNC_ENABLED_GAUGE.set(1)

    yield

    # Stop background sync
    global sync_enabled
    sync_enabled = False
    if sync_task:
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass

    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title="CRDT Sync Service", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "sync_enabled": sync_enabled,
        "last_sync_time": last_sync_time,
        "last_sync_status": last_sync_status
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
async def get_status():
    """Get detailed sync status."""
    return {
        "sync_enabled": sync_enabled,
        "sync_interval_ms": SYNC_INTERVAL_MS,
        "last_sync_time": last_sync_time,
        "last_sync_status": last_sync_status,
        "sync_error_count": sync_error_count,
        "configured_replicas": REPLICA_URLS
    }


@app.post("/sync")
async def trigger_sync(request: Optional[ManualSyncRequest] = None):
    """Manually trigger a sync cycle."""
    with tracer.start_as_current_span("manual_sync") as span:
        span.set_attribute("trigger", "manual")

        replicas = request.replicas if request else None
        result = await perform_sync(replicas)

        return {
            "triggered": True,
            "result": result
        }


@app.post("/config/sync")
async def configure_sync(request: SyncConfigRequest):
    """Enable or disable automatic sync."""
    global sync_enabled, sync_task

    if request.enabled and not sync_enabled:
        # Start sync
        sync_enabled = True
        sync_task = asyncio.create_task(sync_loop())
        SYNC_ENABLED_GAUGE.set(1)
        logger.info("Automatic sync enabled")
    elif not request.enabled and sync_enabled:
        # Stop sync
        sync_enabled = False
        if sync_task:
            sync_task.cancel()
            try:
                await sync_task
            except asyncio.CancelledError:
                pass
            sync_task = None
        SYNC_ENABLED_GAUGE.set(0)
        logger.info("Automatic sync disabled")

    return {
        "sync_enabled": sync_enabled,
        "message": f"Sync {'enabled' if sync_enabled else 'disabled'}"
    }


@app.get("/replicas")
async def list_replicas():
    """List configured replicas and their current health."""
    replica_health = {}

    async with httpx.AsyncClient() as client:
        for url in REPLICA_URLS:
            try:
                response = await client.get(f"{url}/health", timeout=2.0)
                if response.status_code == 200:
                    replica_health[url] = response.json()
                else:
                    replica_health[url] = {"status": "error", "code": response.status_code}
            except Exception as e:
                replica_health[url] = {"status": "unreachable", "error": str(e)}

    return {
        "replicas": REPLICA_URLS,
        "health": replica_health
    }


@app.get("/state/merged")
async def get_merged_state():
    """Get the current merged state across all replicas (without pushing)."""
    with tracer.start_as_current_span("get_merged_state") as span:
        replica_states = []

        async with httpx.AsyncClient() as client:
            for url in REPLICA_URLS:
                state = await fetch_replica_state(client, url)
                if state:
                    replica_states.append(state)

        if not replica_states:
            raise HTTPException(status_code=503, detail="No replicas reachable")

        merged = compute_merged_state(replica_states)

        # Calculate values
        result = {
            "replicas_sampled": len(replica_states),
            "g_counters": {},
            "pn_counters": {},
            "lww_registers": {}
        }

        for name, state in merged["g_counters"].items():
            result["g_counters"][name] = {
                "value": sum(state.values()),
                "state": state
            }

        for name, state in merged["pn_counters"].items():
            p_total = sum(state.get("p", {}).values())
            n_total = sum(state.get("n", {}).values())
            result["pn_counters"][name] = {
                "value": p_total - n_total,
                "state": state
            }

        for name, state in merged["lww_registers"].items():
            result["lww_registers"][name] = state

        return result


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8010"))
    uvicorn.run(app, host="0.0.0.0", port=port)
