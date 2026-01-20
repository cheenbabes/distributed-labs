"""
Primary - The write leader that accepts writes and broadcasts to replicas.
Demonstrates eventual consistency with configurable replication delays.
"""
import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "primary")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab14-otel-collector:4317")
REPLICA_URLS = os.getenv("REPLICA_URLS", "http://lab14-replica1:8001,http://lab14-replica2:8002").split(",")
REPLICA1_DELAY_MS = int(os.getenv("REPLICA1_DELAY_MS", "500"))
REPLICA2_DELAY_MS = int(os.getenv("REPLICA2_DELAY_MS", "1500"))

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
WRITE_COUNT = Counter(
    "primary_writes_total",
    "Total write operations",
    ["status"]
)
CURRENT_VERSION = Gauge(
    "primary_current_version",
    "Current data version on primary"
)
REPLICATION_DELAY = Histogram(
    "replication_delay_seconds",
    "Configured replication delay",
    ["replica"],
    buckets=[0.1, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0]
)
REPLICATION_SUCCESS = Counter(
    "replication_success_total",
    "Successful replications",
    ["replica"]
)
REPLICATION_FAILURE = Counter(
    "replication_failure_total",
    "Failed replications",
    ["replica"]
)

# In-memory data store
class DataStore:
    def __init__(self):
        self.data: Dict[str, any] = {}
        self.version: int = 0
        self.write_timestamp: float = 0
        self.write_id: Optional[str] = None

    def write(self, key: str, value: any) -> Dict:
        self.version += 1
        self.write_timestamp = time.time()
        self.write_id = str(uuid.uuid4())[:8]
        self.data[key] = value
        CURRENT_VERSION.set(self.version)
        logger.info(f"Write: key={key}, version={self.version}, write_id={self.write_id}")
        return {
            "version": self.version,
            "write_timestamp": self.write_timestamp,
            "write_id": self.write_id,
            "key": key,
            "value": value
        }

    def get_state(self) -> Dict:
        return {
            "data": self.data.copy(),
            "version": self.version,
            "write_timestamp": self.write_timestamp,
            "write_id": self.write_id
        }

store = DataStore()

# Replication delay configuration (can be modified at runtime)
replication_delays: Dict[str, int] = {}

def init_replication_delays():
    """Initialize replication delays from environment."""
    for i, url in enumerate(REPLICA_URLS):
        if i == 0:
            replication_delays[url] = REPLICA1_DELAY_MS
        elif i == 1:
            replication_delays[url] = REPLICA2_DELAY_MS
        else:
            replication_delays[url] = 1000  # Default 1s for additional replicas

# Request/Response models
class WriteRequest(BaseModel):
    key: str
    value: any

class WriteResponse(BaseModel):
    version: int
    write_timestamp: float
    write_id: str
    key: str
    value: any
    replication_started: bool

class ReplicationDelayConfig(BaseModel):
    replica_url: str
    delay_ms: int

class AllDelaysConfig(BaseModel):
    delays: Dict[str, int]


async def replicate_to_replica(url: str, state: Dict, delay_ms: int):
    """Replicate state to a single replica with artificial delay."""
    replica_name = url.split("/")[-1].split(":")[0]

    with tracer.as_current_span(f"replicate-to-{replica_name}") as span:
        span.set_attribute("replica.url", url)
        span.set_attribute("replica.delay_ms", delay_ms)
        span.set_attribute("data.version", state["version"])

        # Artificial replication delay
        await asyncio.sleep(delay_ms / 1000.0)
        REPLICATION_DELAY.labels(replica=replica_name).observe(delay_ms / 1000.0)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{url}/replicate",
                    json=state,
                    headers={"X-Replication-Delay-Ms": str(delay_ms)}
                )
                if response.status_code == 200:
                    REPLICATION_SUCCESS.labels(replica=replica_name).inc()
                    logger.info(f"Replication to {replica_name} succeeded: version={state['version']}")
                else:
                    REPLICATION_FAILURE.labels(replica=replica_name).inc()
                    logger.error(f"Replication to {replica_name} failed: {response.status_code}")
        except Exception as e:
            REPLICATION_FAILURE.labels(replica=replica_name).inc()
            logger.error(f"Replication to {replica_name} error: {e}")


async def broadcast_to_replicas(state: Dict):
    """Broadcast state to all replicas asynchronously with their configured delays."""
    tasks = []
    for url in REPLICA_URLS:
        url = url.strip()
        if url:
            delay_ms = replication_delays.get(url, 1000)
            tasks.append(replicate_to_replica(url, state, delay_ms))

    # Fire and forget - don't wait for replication to complete
    asyncio.gather(*tasks, return_exceptions=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_replication_delays()
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Replica URLs: {REPLICA_URLS}")
    logger.info(f"Replication delays: {replication_delays}")
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


@app.post("/data", response_model=WriteResponse)
async def write_data(request: WriteRequest):
    """
    Write data to the primary. This will be asynchronously replicated to replicas.
    """
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")
    current_span.set_attribute("write.key", request.key)

    # Write to primary
    write_result = store.write(request.key, request.value)
    WRITE_COUNT.labels(status="success").inc()

    # Start async replication (fire and forget)
    asyncio.create_task(broadcast_to_replicas(store.get_state()))

    current_span.set_attribute("write.version", write_result["version"])
    current_span.set_attribute("write.write_id", write_result["write_id"])

    logger.info(f"Write completed: trace_id={trace_id}, version={write_result['version']}")

    return WriteResponse(
        **write_result,
        replication_started=True
    )


@app.get("/data")
async def read_data():
    """
    Read current data from primary.
    Use this for read-your-writes consistency.
    """
    state = store.get_state()
    age_ms = (time.time() - state["write_timestamp"]) * 1000 if state["write_timestamp"] > 0 else 0

    return {
        "source": "primary",
        "data": state["data"],
        "version": state["version"],
        "write_id": state["write_id"],
        "write_timestamp": state["write_timestamp"],
        "data_age_ms": round(age_ms, 2)
    }


@app.get("/version")
async def get_version():
    """Get current version number."""
    return {
        "source": "primary",
        "version": store.version,
        "write_id": store.write_id,
        "write_timestamp": store.write_timestamp
    }


@app.get("/state")
async def get_full_state():
    """Get full state for debugging."""
    return {
        "source": "primary",
        **store.get_state(),
        "replication_delays": replication_delays,
        "replica_urls": REPLICA_URLS
    }


@app.get("/admin/replication-delays")
async def get_replication_delays():
    """Get current replication delay configuration."""
    return {
        "delays": replication_delays,
        "default_replica1_ms": REPLICA1_DELAY_MS,
        "default_replica2_ms": REPLICA2_DELAY_MS
    }


@app.post("/admin/replication-delay")
async def set_replication_delay(config: ReplicationDelayConfig):
    """Set replication delay for a specific replica."""
    if config.replica_url not in REPLICA_URLS and config.replica_url not in [url.strip() for url in REPLICA_URLS]:
        raise HTTPException(status_code=400, detail=f"Unknown replica: {config.replica_url}")

    replication_delays[config.replica_url] = config.delay_ms
    logger.info(f"Set replication delay for {config.replica_url} to {config.delay_ms}ms")

    return {
        "status": "updated",
        "replica_url": config.replica_url,
        "delay_ms": config.delay_ms,
        "all_delays": replication_delays
    }


@app.post("/admin/replication-delays")
async def set_all_replication_delays(config: AllDelaysConfig):
    """Set replication delays for all replicas at once."""
    for url, delay in config.delays.items():
        replication_delays[url] = delay

    logger.info(f"Updated all replication delays: {replication_delays}")
    return {"status": "updated", "delays": replication_delays}


@app.post("/admin/reset")
async def reset_data():
    """Reset all data and version counter."""
    store.data.clear()
    store.version = 0
    store.write_timestamp = 0
    store.write_id = None
    CURRENT_VERSION.set(0)

    # Also reset replicas
    async with httpx.AsyncClient(timeout=5.0) as client:
        for url in REPLICA_URLS:
            url = url.strip()
            if url:
                try:
                    await client.post(f"{url}/admin/reset")
                except Exception as e:
                    logger.error(f"Failed to reset {url}: {e}")

    logger.info("Data reset completed")
    return {"status": "reset", "version": 0}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
