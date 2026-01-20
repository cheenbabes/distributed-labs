"""
CRDT Replica Service - Implements G-Counter, PN-Counter, and LWW-Register CRDTs.
Each replica maintains its own state that can be merged with other replicas.
"""
import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
REPLICA_ID = os.getenv("REPLICA_ID", "replica-1")
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", f"crdt-{REPLICA_ID}")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
SYNC_ENABLED = os.getenv("SYNC_ENABLED", "true").lower() == "true"

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(SERVICE_NAME)

# OpenTelemetry setup
resource = Resource.create({"service.name": SERVICE_NAME, "replica.id": REPLICA_ID})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# Prometheus metrics
GCOUNTER_VALUE = Gauge(
    "crdt_gcounter_value",
    "Current G-Counter total value",
    ["replica_id", "counter_name"]
)
PNCOUNTER_VALUE = Gauge(
    "crdt_pncounter_value",
    "Current PN-Counter total value",
    ["replica_id", "counter_name"]
)
LWW_REGISTER_UPDATES = Counter(
    "crdt_lww_register_updates_total",
    "Total LWW-Register updates",
    ["replica_id", "register_name"]
)
MERGE_OPERATIONS = Counter(
    "crdt_merge_operations_total",
    "Total merge operations performed",
    ["replica_id", "crdt_type"]
)


# =============================================================================
# CRDT Implementations
# =============================================================================

class GCounter:
    """
    Grow-only Counter (G-Counter) CRDT.

    Each replica maintains a map of {replica_id: count}.
    The value is the sum of all counts.
    Only increment operations are allowed.

    Merge: Take the max of each replica's count.
    """

    def __init__(self, name: str):
        self.name = name
        self.counts: Dict[str, int] = {}

    def increment(self, replica_id: str, amount: int = 1) -> int:
        """Increment the counter for the given replica."""
        if amount < 0:
            raise ValueError("G-Counter can only increment (positive values)")
        self.counts[replica_id] = self.counts.get(replica_id, 0) + amount
        return self.value()

    def value(self) -> int:
        """Get the total value across all replicas."""
        return sum(self.counts.values())

    def merge(self, other_counts: Dict[str, int]) -> int:
        """Merge with another G-Counter state. Take max of each replica's count."""
        for replica_id, count in other_counts.items():
            self.counts[replica_id] = max(self.counts.get(replica_id, 0), count)
        return self.value()

    def state(self) -> Dict[str, int]:
        """Get the internal state for syncing."""
        return self.counts.copy()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": "g-counter",
            "value": self.value(),
            "state": self.state()
        }


class PNCounter:
    """
    Positive-Negative Counter (PN-Counter) CRDT.

    Composed of two G-Counters: one for increments (P), one for decrements (N).
    The value is P - N.

    Merge: Merge both P and N G-Counters independently.
    """

    def __init__(self, name: str):
        self.name = name
        self.p_counts: Dict[str, int] = {}  # Positive (increments)
        self.n_counts: Dict[str, int] = {}  # Negative (decrements)

    def increment(self, replica_id: str, amount: int = 1) -> int:
        """Increment the counter."""
        if amount < 0:
            raise ValueError("Use decrement for negative values")
        self.p_counts[replica_id] = self.p_counts.get(replica_id, 0) + amount
        return self.value()

    def decrement(self, replica_id: str, amount: int = 1) -> int:
        """Decrement the counter."""
        if amount < 0:
            raise ValueError("Amount must be positive")
        self.n_counts[replica_id] = self.n_counts.get(replica_id, 0) + amount
        return self.value()

    def value(self) -> int:
        """Get the total value (P - N)."""
        p_total = sum(self.p_counts.values())
        n_total = sum(self.n_counts.values())
        return p_total - n_total

    def merge(self, other_p: Dict[str, int], other_n: Dict[str, int]) -> int:
        """Merge with another PN-Counter state."""
        for replica_id, count in other_p.items():
            self.p_counts[replica_id] = max(self.p_counts.get(replica_id, 0), count)
        for replica_id, count in other_n.items():
            self.n_counts[replica_id] = max(self.n_counts.get(replica_id, 0), count)
        return self.value()

    def state(self) -> Dict[str, Dict[str, int]]:
        """Get the internal state for syncing."""
        return {
            "p": self.p_counts.copy(),
            "n": self.n_counts.copy()
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": "pn-counter",
            "value": self.value(),
            "state": self.state()
        }


class LWWRegister:
    """
    Last-Write-Wins Register (LWW-Register) CRDT.

    Stores a single value with a timestamp.
    On conflict, the value with the higher timestamp wins.

    Uses Lamport timestamps with replica ID as tiebreaker.
    """

    def __init__(self, name: str):
        self.name = name
        self.value_data: Optional[Any] = None
        self.timestamp: float = 0.0
        self.replica_id: str = ""

    def set(self, value: Any, replica_id: str, timestamp: Optional[float] = None) -> Dict[str, Any]:
        """Set the register value with a timestamp."""
        ts = timestamp if timestamp is not None else time.time()

        # Only update if timestamp is newer, or same timestamp but higher replica_id
        if (ts > self.timestamp or
            (ts == self.timestamp and replica_id > self.replica_id)):
            self.value_data = value
            self.timestamp = ts
            self.replica_id = replica_id

        return self.get()

    def get(self) -> Dict[str, Any]:
        """Get the current value and metadata."""
        return {
            "value": self.value_data,
            "timestamp": self.timestamp,
            "replica_id": self.replica_id
        }

    def merge(self, other_value: Any, other_timestamp: float, other_replica_id: str) -> Dict[str, Any]:
        """Merge with another LWW-Register state."""
        # Apply same logic as set - higher timestamp wins
        if (other_timestamp > self.timestamp or
            (other_timestamp == self.timestamp and other_replica_id > self.replica_id)):
            self.value_data = other_value
            self.timestamp = other_timestamp
            self.replica_id = other_replica_id
        return self.get()

    def state(self) -> Dict[str, Any]:
        """Get the internal state for syncing."""
        return {
            "value": self.value_data,
            "timestamp": self.timestamp,
            "replica_id": self.replica_id
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": "lww-register",
            "current": self.get(),
            "state": self.state()
        }


# =============================================================================
# Storage
# =============================================================================

class CRDTStore:
    """In-memory store for all CRDTs on this replica."""

    def __init__(self, replica_id: str):
        self.replica_id = replica_id
        self.g_counters: Dict[str, GCounter] = {}
        self.pn_counters: Dict[str, PNCounter] = {}
        self.lww_registers: Dict[str, LWWRegister] = {}

    def get_or_create_gcounter(self, name: str) -> GCounter:
        if name not in self.g_counters:
            self.g_counters[name] = GCounter(name)
        return self.g_counters[name]

    def get_or_create_pncounter(self, name: str) -> PNCounter:
        if name not in self.pn_counters:
            self.pn_counters[name] = PNCounter(name)
        return self.pn_counters[name]

    def get_or_create_lww_register(self, name: str) -> LWWRegister:
        if name not in self.lww_registers:
            self.lww_registers[name] = LWWRegister(name)
        return self.lww_registers[name]

    def get_all_state(self) -> Dict[str, Any]:
        """Get the complete state of all CRDTs for syncing."""
        return {
            "replica_id": self.replica_id,
            "g_counters": {name: c.state() for name, c in self.g_counters.items()},
            "pn_counters": {name: c.state() for name, c in self.pn_counters.items()},
            "lww_registers": {name: r.state() for name, r in self.lww_registers.items()}
        }


# Global store
store = CRDTStore(REPLICA_ID)


# =============================================================================
# Pydantic Models
# =============================================================================

class IncrementRequest(BaseModel):
    amount: int = 1

class DecrementRequest(BaseModel):
    amount: int = 1

class SetValueRequest(BaseModel):
    value: Any
    timestamp: Optional[float] = None

class MergeGCounterRequest(BaseModel):
    state: Dict[str, int]

class MergePNCounterRequest(BaseModel):
    p_state: Dict[str, int]
    n_state: Dict[str, int]

class MergeLWWRegisterRequest(BaseModel):
    value: Any
    timestamp: float
    replica_id: str

class FullStateRequest(BaseModel):
    g_counters: Dict[str, Dict[str, int]] = {}
    pn_counters: Dict[str, Dict[str, Dict[str, int]]] = {}
    lww_registers: Dict[str, Dict[str, Any]] = {}

class NetworkPartitionRequest(BaseModel):
    enabled: bool


# =============================================================================
# Network Partition Simulation
# =============================================================================

is_partitioned = False


# =============================================================================
# FastAPI App
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} (Replica: {REPLICA_ID}) starting up")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=f"CRDT Replica - {REPLICA_ID}", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "replica_id": REPLICA_ID,
        "service": SERVICE_NAME,
        "partitioned": is_partitioned
    }


@app.get("/metrics")
async def metrics():
    # Update gauge metrics
    for name, counter in store.g_counters.items():
        GCOUNTER_VALUE.labels(replica_id=REPLICA_ID, counter_name=name).set(counter.value())
    for name, counter in store.pn_counters.items():
        PNCOUNTER_VALUE.labels(replica_id=REPLICA_ID, counter_name=name).set(counter.value())

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# =============================================================================
# G-Counter Endpoints
# =============================================================================

@app.post("/gcounter/{name}/increment")
async def gcounter_increment(name: str, request: IncrementRequest):
    """Increment a G-Counter."""
    with tracer.start_as_current_span("gcounter_increment") as span:
        span.set_attribute("counter.name", name)
        span.set_attribute("replica.id", REPLICA_ID)
        span.set_attribute("increment.amount", request.amount)

        counter = store.get_or_create_gcounter(name)
        new_value = counter.increment(REPLICA_ID, request.amount)

        span.set_attribute("counter.new_value", new_value)
        logger.info(f"G-Counter '{name}' incremented by {request.amount}, new value: {new_value}")

        GCOUNTER_VALUE.labels(replica_id=REPLICA_ID, counter_name=name).set(new_value)

        return {
            "replica_id": REPLICA_ID,
            "counter": name,
            "operation": "increment",
            "amount": request.amount,
            "value": new_value,
            "state": counter.state()
        }


@app.get("/gcounter/{name}")
async def gcounter_get(name: str):
    """Get a G-Counter value."""
    counter = store.get_or_create_gcounter(name)
    return counter.to_dict()


@app.post("/gcounter/{name}/merge")
async def gcounter_merge(name: str, request: MergeGCounterRequest):
    """Merge remote G-Counter state."""
    if is_partitioned:
        raise HTTPException(status_code=503, detail="Network partitioned - cannot merge")

    with tracer.start_as_current_span("gcounter_merge") as span:
        span.set_attribute("counter.name", name)
        span.set_attribute("replica.id", REPLICA_ID)

        counter = store.get_or_create_gcounter(name)
        old_value = counter.value()
        new_value = counter.merge(request.state)

        span.set_attribute("counter.old_value", old_value)
        span.set_attribute("counter.new_value", new_value)

        MERGE_OPERATIONS.labels(replica_id=REPLICA_ID, crdt_type="g-counter").inc()
        logger.info(f"G-Counter '{name}' merged: {old_value} -> {new_value}")

        return {
            "replica_id": REPLICA_ID,
            "counter": name,
            "old_value": old_value,
            "new_value": new_value,
            "state": counter.state()
        }


# =============================================================================
# PN-Counter Endpoints
# =============================================================================

@app.post("/pncounter/{name}/increment")
async def pncounter_increment(name: str, request: IncrementRequest):
    """Increment a PN-Counter."""
    with tracer.start_as_current_span("pncounter_increment") as span:
        span.set_attribute("counter.name", name)
        span.set_attribute("replica.id", REPLICA_ID)
        span.set_attribute("increment.amount", request.amount)

        counter = store.get_or_create_pncounter(name)
        new_value = counter.increment(REPLICA_ID, request.amount)

        span.set_attribute("counter.new_value", new_value)
        logger.info(f"PN-Counter '{name}' incremented by {request.amount}, new value: {new_value}")

        PNCOUNTER_VALUE.labels(replica_id=REPLICA_ID, counter_name=name).set(new_value)

        return {
            "replica_id": REPLICA_ID,
            "counter": name,
            "operation": "increment",
            "amount": request.amount,
            "value": new_value,
            "state": counter.state()
        }


@app.post("/pncounter/{name}/decrement")
async def pncounter_decrement(name: str, request: DecrementRequest):
    """Decrement a PN-Counter."""
    with tracer.start_as_current_span("pncounter_decrement") as span:
        span.set_attribute("counter.name", name)
        span.set_attribute("replica.id", REPLICA_ID)
        span.set_attribute("decrement.amount", request.amount)

        counter = store.get_or_create_pncounter(name)
        new_value = counter.decrement(REPLICA_ID, request.amount)

        span.set_attribute("counter.new_value", new_value)
        logger.info(f"PN-Counter '{name}' decremented by {request.amount}, new value: {new_value}")

        PNCOUNTER_VALUE.labels(replica_id=REPLICA_ID, counter_name=name).set(new_value)

        return {
            "replica_id": REPLICA_ID,
            "counter": name,
            "operation": "decrement",
            "amount": request.amount,
            "value": new_value,
            "state": counter.state()
        }


@app.get("/pncounter/{name}")
async def pncounter_get(name: str):
    """Get a PN-Counter value."""
    counter = store.get_or_create_pncounter(name)
    return counter.to_dict()


@app.post("/pncounter/{name}/merge")
async def pncounter_merge(name: str, request: MergePNCounterRequest):
    """Merge remote PN-Counter state."""
    if is_partitioned:
        raise HTTPException(status_code=503, detail="Network partitioned - cannot merge")

    with tracer.start_as_current_span("pncounter_merge") as span:
        span.set_attribute("counter.name", name)
        span.set_attribute("replica.id", REPLICA_ID)

        counter = store.get_or_create_pncounter(name)
        old_value = counter.value()
        new_value = counter.merge(request.p_state, request.n_state)

        span.set_attribute("counter.old_value", old_value)
        span.set_attribute("counter.new_value", new_value)

        MERGE_OPERATIONS.labels(replica_id=REPLICA_ID, crdt_type="pn-counter").inc()
        logger.info(f"PN-Counter '{name}' merged: {old_value} -> {new_value}")

        return {
            "replica_id": REPLICA_ID,
            "counter": name,
            "old_value": old_value,
            "new_value": new_value,
            "state": counter.state()
        }


# =============================================================================
# LWW-Register Endpoints
# =============================================================================

@app.post("/lww/{name}/set")
async def lww_set(name: str, request: SetValueRequest):
    """Set a LWW-Register value."""
    with tracer.start_as_current_span("lww_set") as span:
        span.set_attribute("register.name", name)
        span.set_attribute("replica.id", REPLICA_ID)

        register = store.get_or_create_lww_register(name)
        result = register.set(request.value, REPLICA_ID, request.timestamp)

        span.set_attribute("register.value", str(request.value))
        span.set_attribute("register.timestamp", result["timestamp"])

        LWW_REGISTER_UPDATES.labels(replica_id=REPLICA_ID, register_name=name).inc()
        logger.info(f"LWW-Register '{name}' set to '{request.value}' at {result['timestamp']}")

        return {
            "replica_id": REPLICA_ID,
            "register": name,
            "operation": "set",
            "current": result,
            "state": register.state()
        }


@app.get("/lww/{name}")
async def lww_get(name: str):
    """Get a LWW-Register value."""
    register = store.get_or_create_lww_register(name)
    return register.to_dict()


@app.post("/lww/{name}/merge")
async def lww_merge(name: str, request: MergeLWWRegisterRequest):
    """Merge remote LWW-Register state."""
    if is_partitioned:
        raise HTTPException(status_code=503, detail="Network partitioned - cannot merge")

    with tracer.start_as_current_span("lww_merge") as span:
        span.set_attribute("register.name", name)
        span.set_attribute("replica.id", REPLICA_ID)

        register = store.get_or_create_lww_register(name)
        old_state = register.get()
        new_state = register.merge(request.value, request.timestamp, request.replica_id)

        MERGE_OPERATIONS.labels(replica_id=REPLICA_ID, crdt_type="lww-register").inc()
        logger.info(f"LWW-Register '{name}' merged: {old_state} -> {new_state}")

        return {
            "replica_id": REPLICA_ID,
            "register": name,
            "old_state": old_state,
            "new_state": new_state,
            "state": register.state()
        }


# =============================================================================
# State & Sync Endpoints
# =============================================================================

@app.get("/state")
async def get_state():
    """Get the complete state of all CRDTs on this replica."""
    return store.get_all_state()


@app.post("/state/merge")
async def merge_full_state(request: FullStateRequest):
    """Merge a complete state from another replica."""
    if is_partitioned:
        raise HTTPException(status_code=503, detail="Network partitioned - cannot merge")

    with tracer.start_as_current_span("full_state_merge") as span:
        span.set_attribute("replica.id", REPLICA_ID)

        merged_counts = {"g_counter": 0, "pn_counter": 0, "lww_register": 0}

        # Merge G-Counters
        for name, state in request.g_counters.items():
            counter = store.get_or_create_gcounter(name)
            counter.merge(state)
            merged_counts["g_counter"] += 1

        # Merge PN-Counters
        for name, state in request.pn_counters.items():
            counter = store.get_or_create_pncounter(name)
            counter.merge(state.get("p", {}), state.get("n", {}))
            merged_counts["pn_counter"] += 1

        # Merge LWW-Registers
        for name, state in request.lww_registers.items():
            register = store.get_or_create_lww_register(name)
            register.merge(
                state.get("value"),
                state.get("timestamp", 0.0),
                state.get("replica_id", "")
            )
            merged_counts["lww_register"] += 1

        logger.info(f"Full state merge completed: {merged_counts}")

        return {
            "replica_id": REPLICA_ID,
            "merged_counts": merged_counts,
            "current_state": store.get_all_state()
        }


# =============================================================================
# Network Partition Simulation
# =============================================================================

@app.get("/admin/partition")
async def get_partition_status():
    """Get current network partition status."""
    return {"partitioned": is_partitioned, "replica_id": REPLICA_ID}


@app.post("/admin/partition")
async def set_partition_status(request: NetworkPartitionRequest):
    """Enable or disable network partition simulation."""
    global is_partitioned
    is_partitioned = request.enabled
    logger.warning(f"Network partition {'ENABLED' if is_partitioned else 'DISABLED'} for {REPLICA_ID}")
    return {"partitioned": is_partitioned, "replica_id": REPLICA_ID}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
