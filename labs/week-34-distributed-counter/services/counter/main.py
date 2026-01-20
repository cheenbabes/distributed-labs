"""
Distributed Counter Service - CRDT-based G-Counter implementation.

This service demonstrates Netflix-style distributed counting using CRDTs
(Conflict-free Replicated Data Types). Each region maintains its own
counter state and periodically syncs with peers.

Key concepts:
- G-Counter: A grow-only counter that can only increment
- Each node maintains a vector of counts indexed by node ID
- Merge operation: take max of each node's count
- Query operation: sum all counts in the vector
"""
import asyncio
import logging
import os
import time
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import httpx
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "counter")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
REGION = os.getenv("REGION", "unknown")
NODE_ID = os.getenv("NODE_ID", "node-1")
SERVICE_PORT = int(os.getenv("SERVICE_PORT", "8001"))
PEER_ENDPOINTS = os.getenv("PEER_ENDPOINTS", "").split(",") if os.getenv("PEER_ENDPOINTS") else []
SYNC_INTERVAL_MS = int(os.getenv("SYNC_INTERVAL_MS", "5000"))

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(SERVICE_NAME)

# OpenTelemetry setup
resource = Resource.create({"service.name": SERVICE_NAME, "region": REGION})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# Instrument httpx for trace propagation
HTTPXClientInstrumentor().instrument()

# Prometheus metrics
LOCAL_COUNT = Gauge(
    "counter_local_count",
    "Local count for this node",
    ["service", "region", "node_id"]
)
SYNCED_COUNT = Gauge(
    "counter_synced_count",
    "Total synced count (sum of all nodes)",
    ["service", "region"]
)
SYNC_LAG_MS = Gauge(
    "counter_sync_lag_ms",
    "Time since last successful sync",
    ["service", "region"]
)
CONFLICTS_RESOLVED = Counter(
    "counter_conflicts_resolved_total",
    "Number of merge conflicts resolved",
    ["service", "region"]
)
INCREMENT_COUNT = Counter(
    "counter_increments_total",
    "Total increments performed",
    ["service", "region", "counter_name"]
)
SYNC_OPERATIONS = Counter(
    "counter_sync_operations_total",
    "Total sync operations",
    ["service", "region", "status"]
)
SYNC_LATENCY = Histogram(
    "counter_sync_latency_seconds",
    "Sync operation latency",
    ["service", "region"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)


class GCounterState(BaseModel):
    """Serializable G-Counter state for network transfer."""
    counts: Dict[str, Dict[str, int]]  # counter_name -> {node_id -> count}
    node_id: str
    timestamp: float


class IncrementRequest(BaseModel):
    """Request to increment a counter."""
    counter_name: str = "views"
    amount: int = 1


class PartitionRequest(BaseModel):
    """Request to simulate network partition."""
    enabled: bool


@dataclass
class GCounter:
    """
    G-Counter (Grow-only Counter) CRDT implementation.

    A G-Counter is a CRDT that supports only increment operations.
    Each node maintains a map of node_id -> count. To get the total,
    sum all counts. To merge, take the max of each node's count.

    This guarantees eventual consistency: all replicas will converge
    to the same value once they sync, regardless of message ordering.
    """
    node_id: str
    counts: Dict[str, int] = field(default_factory=dict)

    def increment(self, amount: int = 1) -> int:
        """Increment this node's count. Returns new local count."""
        if self.node_id not in self.counts:
            self.counts[self.node_id] = 0
        self.counts[self.node_id] += amount
        return self.counts[self.node_id]

    def local_value(self) -> int:
        """Get this node's local count."""
        return self.counts.get(self.node_id, 0)

    def value(self) -> int:
        """Get the total count (sum of all nodes)."""
        return sum(self.counts.values())

    def merge(self, other_counts: Dict[str, int]) -> int:
        """
        Merge another node's state into ours.

        For each node, take the maximum count seen.
        Returns the number of conflicts resolved (where values differed).
        """
        conflicts = 0
        for node_id, count in other_counts.items():
            if node_id in self.counts:
                if self.counts[node_id] != count:
                    conflicts += 1
                self.counts[node_id] = max(self.counts[node_id], count)
            else:
                self.counts[node_id] = count
        return conflicts

    def to_dict(self) -> Dict[str, int]:
        """Export state for network transfer."""
        return dict(self.counts)


class DistributedCounterStore:
    """
    Manages multiple named G-Counters with background sync.

    This simulates how Netflix might count video views across regions:
    - Each region increments its local counter instantly (low latency)
    - Counters sync periodically to share state
    - During partitions, local counting continues
    - After partition heals, counters merge and converge
    """

    def __init__(self, node_id: str, peers: List[str]):
        self.node_id = node_id
        self.peers = [p.strip() for p in peers if p.strip()]
        self.counters: Dict[str, GCounter] = {}
        self.lock = threading.Lock()
        self.last_sync_time: float = time.time()
        self.network_partition: bool = False
        self._sync_task: Optional[asyncio.Task] = None

    def get_or_create_counter(self, name: str) -> GCounter:
        """Get a counter by name, creating if it doesn't exist."""
        with self.lock:
            if name not in self.counters:
                self.counters[name] = GCounter(node_id=self.node_id)
            return self.counters[name]

    def increment(self, name: str, amount: int = 1) -> Dict:
        """Increment a counter and return current state."""
        counter = self.get_or_create_counter(name)
        with self.lock:
            local_value = counter.increment(amount)
            total_value = counter.value()

        # Update metrics
        LOCAL_COUNT.labels(
            service=SERVICE_NAME, region=REGION, node_id=self.node_id
        ).set(local_value)
        SYNCED_COUNT.labels(
            service=SERVICE_NAME, region=REGION
        ).set(total_value)
        INCREMENT_COUNT.labels(
            service=SERVICE_NAME, region=REGION, counter_name=name
        ).inc(amount)

        return {
            "counter": name,
            "local_count": local_value,
            "total_count": total_value,
            "node_id": self.node_id,
            "region": REGION
        }

    def get_state(self) -> GCounterState:
        """Get full state for sync."""
        with self.lock:
            counts = {name: counter.to_dict() for name, counter in self.counters.items()}
        return GCounterState(
            counts=counts,
            node_id=self.node_id,
            timestamp=time.time()
        )

    def merge_state(self, remote_state: GCounterState) -> int:
        """Merge remote state into local state. Returns conflicts resolved."""
        total_conflicts = 0
        with self.lock:
            for counter_name, remote_counts in remote_state.counts.items():
                counter = self.get_or_create_counter(counter_name)
                conflicts = counter.merge(remote_counts)
                total_conflicts += conflicts

                # Update metrics after merge
                SYNCED_COUNT.labels(
                    service=SERVICE_NAME, region=REGION
                ).set(counter.value())

        if total_conflicts > 0:
            CONFLICTS_RESOLVED.labels(
                service=SERVICE_NAME, region=REGION
            ).inc(total_conflicts)

        return total_conflicts

    def get_all_counters(self) -> Dict:
        """Get status of all counters."""
        with self.lock:
            result = {}
            for name, counter in self.counters.items():
                result[name] = {
                    "local_count": counter.local_value(),
                    "total_count": counter.value(),
                    "nodes": counter.to_dict()
                }
            return result

    async def sync_with_peers(self):
        """Sync state with all peer nodes."""
        if self.network_partition:
            logger.warning("Network partition active - skipping sync")
            SYNC_OPERATIONS.labels(
                service=SERVICE_NAME, region=REGION, status="partition"
            ).inc()
            return

        start_time = time.time()
        my_state = self.get_state()

        for peer in self.peers:
            if not peer:
                continue
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    # Send our state and receive peer's state
                    url = f"http://{peer}/sync"
                    response = await client.post(
                        url,
                        json=my_state.model_dump()
                    )
                    if response.status_code == 200:
                        remote_state = GCounterState(**response.json())
                        conflicts = self.merge_state(remote_state)
                        logger.info(
                            f"Synced with {peer}: merged {conflicts} conflicts"
                        )
                        SYNC_OPERATIONS.labels(
                            service=SERVICE_NAME, region=REGION, status="success"
                        ).inc()
                    else:
                        logger.warning(f"Sync failed with {peer}: {response.status_code}")
                        SYNC_OPERATIONS.labels(
                            service=SERVICE_NAME, region=REGION, status="error"
                        ).inc()
            except Exception as e:
                logger.error(f"Failed to sync with {peer}: {e}")
                SYNC_OPERATIONS.labels(
                    service=SERVICE_NAME, region=REGION, status="error"
                ).inc()

        self.last_sync_time = time.time()
        sync_duration = time.time() - start_time

        SYNC_LATENCY.labels(
            service=SERVICE_NAME, region=REGION
        ).observe(sync_duration)
        SYNC_LAG_MS.labels(
            service=SERVICE_NAME, region=REGION
        ).set(0)  # Reset lag after successful sync

    async def background_sync_loop(self):
        """Background task that periodically syncs with peers."""
        while True:
            try:
                await asyncio.sleep(SYNC_INTERVAL_MS / 1000.0)

                # Update sync lag metric
                lag_ms = (time.time() - self.last_sync_time) * 1000
                SYNC_LAG_MS.labels(
                    service=SERVICE_NAME, region=REGION
                ).set(lag_ms)

                with tracer.start_as_current_span("background_sync") as span:
                    span.set_attribute("region", REGION)
                    span.set_attribute("node_id", self.node_id)
                    span.set_attribute("partition_active", self.network_partition)
                    await self.sync_with_peers()
            except asyncio.CancelledError:
                logger.info("Background sync loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in background sync: {e}")


# Global counter store
counter_store: Optional[DistributedCounterStore] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global counter_store

    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Region: {REGION}, Node ID: {NODE_ID}")
    logger.info(f"Peers: {PEER_ENDPOINTS}")
    logger.info(f"Sync interval: {SYNC_INTERVAL_MS}ms")

    # Initialize counter store
    counter_store = DistributedCounterStore(NODE_ID, PEER_ENDPOINTS)

    # Start background sync task
    counter_store._sync_task = asyncio.create_task(
        counter_store.background_sync_loop()
    )

    yield

    # Cleanup
    if counter_store._sync_task:
        counter_store._sync_task.cancel()
        try:
            await counter_store._sync_task
        except asyncio.CancelledError:
            pass

    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=f"Distributed Counter - {REGION}", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "region": REGION,
        "node_id": NODE_ID
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/increment")
async def increment(request: IncrementRequest):
    """
    Increment a counter. This is the main write operation.

    - Increments happen locally with no coordination (fast!)
    - State syncs to peers in the background
    - Returns both local and total counts
    """
    current_span = trace.get_current_span()
    current_span.set_attribute("counter.name", request.counter_name)
    current_span.set_attribute("counter.amount", request.amount)
    current_span.set_attribute("region", REGION)

    result = counter_store.increment(request.counter_name, request.amount)

    current_span.set_attribute("counter.local_count", result["local_count"])
    current_span.set_attribute("counter.total_count", result["total_count"])

    return result


@app.get("/counter/{counter_name}")
async def get_counter(counter_name: str):
    """Get the current value of a counter."""
    counter = counter_store.get_or_create_counter(counter_name)
    return {
        "counter": counter_name,
        "local_count": counter.local_value(),
        "total_count": counter.value(),
        "nodes": counter.to_dict(),
        "region": REGION,
        "node_id": NODE_ID
    }


@app.get("/counters")
async def get_all_counters():
    """Get all counters and their states."""
    return {
        "region": REGION,
        "node_id": NODE_ID,
        "network_partition": counter_store.network_partition,
        "last_sync_time": counter_store.last_sync_time,
        "counters": counter_store.get_all_counters()
    }


@app.post("/sync")
async def sync(state: GCounterState):
    """
    Receive sync request from peer.

    This endpoint:
    1. Merges the incoming state into our local state
    2. Returns our current state for the peer to merge
    """
    current_span = trace.get_current_span()
    current_span.set_attribute("sync.from_node", state.node_id)
    current_span.set_attribute("sync.counter_count", len(state.counts))

    # Merge incoming state
    conflicts = counter_store.merge_state(state)
    current_span.set_attribute("sync.conflicts_resolved", conflicts)

    # Return our state for the peer
    return counter_store.get_state().model_dump()


@app.post("/admin/partition")
async def set_partition(request: PartitionRequest):
    """
    Simulate network partition.

    When enabled, this node stops syncing with peers but continues
    accepting local increments. This demonstrates:
    - Availability during partition (AP in CAP theorem)
    - Local counting continues
    - Eventually converges when partition heals
    """
    counter_store.network_partition = request.enabled
    status = "enabled" if request.enabled else "disabled"
    logger.warning(f"Network partition {status} for {NODE_ID}")

    return {
        "network_partition": counter_store.network_partition,
        "region": REGION,
        "node_id": NODE_ID,
        "message": f"Network partition {status}"
    }


@app.get("/admin/partition")
async def get_partition():
    """Get current partition status."""
    return {
        "network_partition": counter_store.network_partition,
        "region": REGION,
        "node_id": NODE_ID
    }


@app.post("/admin/force-sync")
async def force_sync():
    """Force an immediate sync with all peers."""
    if counter_store.network_partition:
        raise HTTPException(
            status_code=503,
            detail="Cannot sync - network partition is active"
        )

    await counter_store.sync_with_peers()
    return {
        "status": "sync_completed",
        "region": REGION,
        "node_id": NODE_ID,
        "counters": counter_store.get_all_counters()
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
