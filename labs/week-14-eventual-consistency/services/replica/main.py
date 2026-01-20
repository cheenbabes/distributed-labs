"""
Replica - Read replica that receives updates from the primary with configurable lag.
Demonstrates eventual consistency - data may be stale relative to the primary.
"""
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Dict, Optional

from fastapi import FastAPI, Request
from pydantic import BaseModel
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "replica")
REPLICA_ID = os.getenv("REPLICA_ID", "replica1")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab14-otel-collector:4317")
PORT = int(os.getenv("PORT", "8001"))

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
CURRENT_VERSION = Gauge(
    "replica_current_version",
    "Current data version on this replica",
    ["replica_id"]
)
STALENESS_MS = Gauge(
    "replica_staleness_ms",
    "How stale the data is (ms since write on primary)",
    ["replica_id"]
)
REPLICATION_RECEIVED = Counter(
    "replica_replication_received_total",
    "Total replication messages received",
    ["replica_id"]
)
REPLICATION_LAG = Histogram(
    "replica_replication_lag_seconds",
    "Time between write on primary and receive on replica",
    ["replica_id"],
    buckets=[0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0]
)
READ_COUNT = Counter(
    "replica_reads_total",
    "Total read operations",
    ["replica_id"]
)
STALE_READS = Counter(
    "replica_stale_reads_total",
    "Reads that returned potentially stale data",
    ["replica_id"]
)

# In-memory data store (replica of primary)
class ReplicaStore:
    def __init__(self, replica_id: str):
        self.replica_id = replica_id
        self.data: Dict[str, any] = {}
        self.version: int = 0
        self.write_timestamp: float = 0  # When primary wrote
        self.replicated_at: float = 0     # When we received the replication
        self.write_id: Optional[str] = None
        self.replication_delay_ms: int = 0  # Last known replication delay

    def apply_replication(self, state: Dict, replication_delay_ms: int = 0):
        """Apply replicated state from primary."""
        old_version = self.version
        self.data = state.get("data", {}).copy()
        self.version = state.get("version", 0)
        self.write_timestamp = state.get("write_timestamp", 0)
        self.write_id = state.get("write_id")
        self.replicated_at = time.time()
        self.replication_delay_ms = replication_delay_ms

        CURRENT_VERSION.labels(replica_id=self.replica_id).set(self.version)

        # Calculate replication lag
        lag = self.replicated_at - self.write_timestamp if self.write_timestamp > 0 else 0
        REPLICATION_LAG.labels(replica_id=self.replica_id).observe(lag)
        REPLICATION_RECEIVED.labels(replica_id=self.replica_id).inc()

        logger.info(
            f"Replication applied: version {old_version} -> {self.version}, "
            f"lag={lag*1000:.0f}ms, delay_config={replication_delay_ms}ms"
        )

    def get_staleness_ms(self) -> float:
        """Calculate how stale the data is since the original write."""
        if self.write_timestamp == 0:
            return 0
        return (time.time() - self.write_timestamp) * 1000

    def get_state(self) -> Dict:
        staleness = self.get_staleness_ms()
        STALENESS_MS.labels(replica_id=self.replica_id).set(staleness)
        return {
            "data": self.data.copy(),
            "version": self.version,
            "write_timestamp": self.write_timestamp,
            "write_id": self.write_id,
            "replicated_at": self.replicated_at,
            "data_age_ms": round(staleness, 2)
        }

store = ReplicaStore(REPLICA_ID)


# Request models
class ReplicationPayload(BaseModel):
    data: Dict[str, any] = {}
    version: int = 0
    write_timestamp: float = 0
    write_id: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} ({REPLICA_ID}) starting up on port {PORT}")
    yield
    logger.info(f"{SERVICE_NAME} ({REPLICA_ID}) shutting down")


app = FastAPI(title=f"{SERVICE_NAME}-{REPLICA_ID}", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME, "replica_id": REPLICA_ID}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/data")
async def read_data():
    """
    Read current data from this replica.
    Data may be stale relative to the primary!
    """
    current_span = trace.get_current_span()
    current_span.set_attribute("replica.id", REPLICA_ID)
    current_span.set_attribute("replica.version", store.version)

    READ_COUNT.labels(replica_id=REPLICA_ID).inc()

    state = store.get_state()
    staleness = state["data_age_ms"]

    # Consider data stale if it's more than 100ms old
    is_stale = staleness > 100
    if is_stale:
        STALE_READS.labels(replica_id=REPLICA_ID).inc()

    current_span.set_attribute("data.staleness_ms", staleness)
    current_span.set_attribute("data.is_stale", is_stale)

    return {
        "source": REPLICA_ID,
        "data": state["data"],
        "version": state["version"],
        "write_id": state["write_id"],
        "write_timestamp": state["write_timestamp"],
        "data_age_ms": staleness,
        "is_stale": is_stale,
        "replicated_at": state["replicated_at"]
    }


@app.get("/version")
async def get_version():
    """Get current version number on this replica."""
    staleness = store.get_staleness_ms()
    return {
        "source": REPLICA_ID,
        "version": store.version,
        "write_id": store.write_id,
        "write_timestamp": store.write_timestamp,
        "data_age_ms": round(staleness, 2)
    }


@app.get("/state")
async def get_full_state():
    """Get full state for debugging."""
    return {
        "source": REPLICA_ID,
        **store.get_state(),
        "replication_delay_ms": store.replication_delay_ms
    }


@app.post("/replicate")
async def receive_replication(payload: ReplicationPayload, request: Request):
    """
    Receive replicated state from primary.
    This endpoint is called by the primary after a write.
    """
    current_span = trace.get_current_span()
    current_span.set_attribute("replica.id", REPLICA_ID)
    current_span.set_attribute("replication.version", payload.version)

    # Get the configured delay from header (for metrics)
    delay_ms = int(request.headers.get("X-Replication-Delay-Ms", "0"))
    current_span.set_attribute("replication.delay_ms", delay_ms)

    # Apply the replication
    store.apply_replication(payload.model_dump(), delay_ms)

    return {
        "status": "replicated",
        "replica_id": REPLICA_ID,
        "version": store.version,
        "replicated_at": store.replicated_at
    }


@app.post("/admin/reset")
async def reset_data():
    """Reset all data and version counter."""
    store.data.clear()
    store.version = 0
    store.write_timestamp = 0
    store.replicated_at = 0
    store.write_id = None
    CURRENT_VERSION.labels(replica_id=REPLICA_ID).set(0)
    STALENESS_MS.labels(replica_id=REPLICA_ID).set(0)

    logger.info("Replica data reset")
    return {"status": "reset", "replica_id": REPLICA_ID, "version": 0}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
