"""
Client - Service that reads from replicas and detects staleness/inconsistency.
Periodically polls all replicas and compares their versions to detect divergence.
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "client")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab14-otel-collector:4317")
PRIMARY_URL = os.getenv("PRIMARY_URL", "http://lab14-primary:8000")
REPLICA_URLS = os.getenv("REPLICA_URLS", "http://lab14-replica1:8001,http://lab14-replica2:8002").split(",")
POLL_INTERVAL_MS = int(os.getenv("POLL_INTERVAL_MS", "500"))

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
STALE_READS = Counter(
    "client_stale_reads_total",
    "Total stale reads detected",
    ["source"]
)
FRESH_READS = Counter(
    "client_fresh_reads_total",
    "Total fresh reads detected",
    ["source"]
)
CONSISTENCY_LAG_MS = Gauge(
    "client_consistency_lag_ms",
    "Time lag between primary and replica versions",
    ["replica"]
)
VERSION_DIVERGENCE = Gauge(
    "client_version_divergence",
    "Difference between primary version and replica version",
    ["replica"]
)
ALL_CONSISTENT = Gauge(
    "client_all_replicas_consistent",
    "1 if all replicas match primary, 0 otherwise"
)
CONSISTENCY_WINDOW_MS = Histogram(
    "client_consistency_window_ms",
    "Time until all replicas became consistent after a write",
    buckets=[100, 250, 500, 1000, 1500, 2000, 3000, 5000, 10000]
)
POLL_DURATION = Histogram(
    "client_poll_duration_seconds",
    "Time to poll all replicas",
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5]
)

# State tracking
class ConsistencyState:
    def __init__(self):
        self.primary_version: int = 0
        self.primary_write_id: Optional[str] = None
        self.primary_write_timestamp: float = 0
        self.replica_states: Dict[str, Dict] = {}
        self.last_poll_time: float = 0
        self.all_consistent: bool = True
        self.consistency_achieved_at: Optional[float] = None
        self.waiting_for_consistency_since: Optional[float] = None

    def update_primary(self, state: Dict):
        old_version = self.primary_version
        self.primary_version = state.get("version", 0)
        self.primary_write_id = state.get("write_id")
        self.primary_write_timestamp = state.get("write_timestamp", 0)

        # If version increased, start timing consistency window
        if self.primary_version > old_version:
            self.waiting_for_consistency_since = time.time()
            self.consistency_achieved_at = None

    def update_replica(self, replica_id: str, state: Dict):
        self.replica_states[replica_id] = state
        self.last_poll_time = time.time()

    def check_consistency(self) -> Dict:
        """Check if all replicas are consistent with primary."""
        consistent_count = 0
        total_replicas = len(self.replica_states)

        for replica_id, state in self.replica_states.items():
            replica_version = state.get("version", 0)
            divergence = self.primary_version - replica_version

            VERSION_DIVERGENCE.labels(replica=replica_id).set(divergence)

            if replica_version == self.primary_version:
                consistent_count += 1
                CONSISTENCY_LAG_MS.labels(replica=replica_id).set(0)
            else:
                # Calculate lag based on when primary wrote vs now
                if self.primary_write_timestamp > 0:
                    lag_ms = (time.time() - self.primary_write_timestamp) * 1000
                    CONSISTENCY_LAG_MS.labels(replica=replica_id).set(lag_ms)

        self.all_consistent = (consistent_count == total_replicas and total_replicas > 0)
        ALL_CONSISTENT.set(1 if self.all_consistent else 0)

        # Track consistency window
        if self.all_consistent and self.waiting_for_consistency_since:
            consistency_window = (time.time() - self.waiting_for_consistency_since) * 1000
            CONSISTENCY_WINDOW_MS.observe(consistency_window)
            self.consistency_achieved_at = time.time()
            self.waiting_for_consistency_since = None
            logger.info(f"All replicas consistent! Window: {consistency_window:.0f}ms")

        return {
            "all_consistent": self.all_consistent,
            "consistent_count": consistent_count,
            "total_replicas": total_replicas,
            "primary_version": self.primary_version
        }

state = ConsistencyState()
polling_task: Optional[asyncio.Task] = None


async def poll_all_sources():
    """Poll primary and all replicas to check versions."""
    with tracer.start_as_current_span("poll-consistency") as span:
        start_time = time.time()

        async with httpx.AsyncClient(timeout=5.0) as client:
            # Poll primary
            try:
                resp = await client.get(f"{PRIMARY_URL}/version")
                if resp.status_code == 200:
                    state.update_primary(resp.json())
            except Exception as e:
                logger.error(f"Failed to poll primary: {e}")

            # Poll all replicas in parallel
            tasks = []
            for url in REPLICA_URLS:
                url = url.strip()
                if url:
                    tasks.append(poll_replica(client, url))

            await asyncio.gather(*tasks, return_exceptions=True)

        duration = time.time() - start_time
        POLL_DURATION.observe(duration)

        consistency = state.check_consistency()
        span.set_attribute("consistency.all_consistent", consistency["all_consistent"])
        span.set_attribute("consistency.primary_version", consistency["primary_version"])


async def poll_replica(client: httpx.AsyncClient, url: str):
    """Poll a single replica."""
    try:
        resp = await client.get(f"{url}/version")
        if resp.status_code == 200:
            data = resp.json()
            replica_id = data.get("source", url)
            state.update_replica(replica_id, data)

            # Track stale vs fresh reads
            if data.get("version", 0) < state.primary_version:
                STALE_READS.labels(source=replica_id).inc()
            else:
                FRESH_READS.labels(source=replica_id).inc()
    except Exception as e:
        logger.error(f"Failed to poll {url}: {e}")


async def polling_loop():
    """Background task that continuously polls all sources."""
    logger.info(f"Starting polling loop (interval: {POLL_INTERVAL_MS}ms)")
    while True:
        try:
            await poll_all_sources()
        except Exception as e:
            logger.error(f"Polling error: {e}")
        await asyncio.sleep(POLL_INTERVAL_MS / 1000.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global polling_task
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Primary URL: {PRIMARY_URL}")
    logger.info(f"Replica URLs: {REPLICA_URLS}")

    # Start background polling
    polling_task = asyncio.create_task(polling_loop())

    yield

    # Stop polling
    if polling_task:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass

    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
async def get_status():
    """Get current consistency status across all replicas."""
    consistency = state.check_consistency()

    replica_details = []
    for replica_id, replica_state in state.replica_states.items():
        replica_version = replica_state.get("version", 0)
        is_consistent = replica_version == state.primary_version
        lag_ms = replica_state.get("data_age_ms", 0)

        replica_details.append({
            "replica_id": replica_id,
            "version": replica_version,
            "is_consistent": is_consistent,
            "version_behind": state.primary_version - replica_version,
            "data_age_ms": lag_ms
        })

    return {
        "primary_version": state.primary_version,
        "primary_write_id": state.primary_write_id,
        "all_consistent": state.all_consistent,
        "replicas": replica_details,
        "last_poll_time": state.last_poll_time,
        "waiting_for_consistency": state.waiting_for_consistency_since is not None
    }


@app.get("/read-compare")
async def read_and_compare():
    """
    Read from all sources and compare their data.
    This is the key operation to observe eventual consistency.
    """
    current_span = trace.get_current_span()

    results = {"primary": None, "replicas": {}}

    async with httpx.AsyncClient(timeout=5.0) as client:
        # Read from primary
        try:
            resp = await client.get(f"{PRIMARY_URL}/data")
            if resp.status_code == 200:
                results["primary"] = resp.json()
        except Exception as e:
            results["primary"] = {"error": str(e)}

        # Read from all replicas
        for url in REPLICA_URLS:
            url = url.strip()
            if url:
                replica_name = url.split("/")[-1].split(":")[0]
                try:
                    resp = await client.get(f"{url}/data")
                    if resp.status_code == 200:
                        results["replicas"][replica_name] = resp.json()
                except Exception as e:
                    results["replicas"][replica_name] = {"error": str(e)}

    # Analyze consistency
    primary_version = results["primary"].get("version", 0) if results["primary"] else 0
    stale_replicas = []
    consistent_replicas = []

    for replica_name, replica_data in results["replicas"].items():
        if "error" in replica_data:
            continue
        replica_version = replica_data.get("version", 0)
        if replica_version < primary_version:
            stale_replicas.append({
                "name": replica_name,
                "version": replica_version,
                "behind_by": primary_version - replica_version
            })
        else:
            consistent_replicas.append(replica_name)

    current_span.set_attribute("stale_replica_count", len(stale_replicas))
    current_span.set_attribute("consistent_replica_count", len(consistent_replicas))

    return {
        "primary": results["primary"],
        "replicas": results["replicas"],
        "analysis": {
            "primary_version": primary_version,
            "stale_replicas": stale_replicas,
            "consistent_replicas": consistent_replicas,
            "all_consistent": len(stale_replicas) == 0
        }
    }


class WriteRequest(BaseModel):
    key: str
    value: any


@app.post("/write-and-read")
async def write_and_read(request: WriteRequest):
    """
    Write to primary, then immediately read from all replicas.
    This demonstrates read-after-write inconsistency.
    """
    current_span = trace.get_current_span()
    write_time = time.time()

    async with httpx.AsyncClient(timeout=5.0) as client:
        # Write to primary
        write_response = await client.post(
            f"{PRIMARY_URL}/data",
            json={"key": request.key, "value": request.value}
        )
        write_result = write_response.json()
        write_version = write_result.get("version", 0)

        current_span.set_attribute("write.version", write_version)

        # Immediately read from all replicas
        replica_reads = {}
        for url in REPLICA_URLS:
            url = url.strip()
            if url:
                replica_name = url.split("/")[-1].split(":")[0]
                try:
                    resp = await client.get(f"{url}/data")
                    if resp.status_code == 200:
                        data = resp.json()
                        replica_reads[replica_name] = {
                            "version": data.get("version", 0),
                            "has_new_data": data.get("version", 0) >= write_version,
                            "data_age_ms": data.get("data_age_ms", 0)
                        }
                except Exception as e:
                    replica_reads[replica_name] = {"error": str(e)}

    # Count stale reads
    stale_count = sum(
        1 for r in replica_reads.values()
        if "error" not in r and not r.get("has_new_data", False)
    )

    return {
        "write_result": write_result,
        "immediate_reads": replica_reads,
        "stale_read_count": stale_count,
        "total_replicas": len(replica_reads),
        "elapsed_ms": round((time.time() - write_time) * 1000, 2),
        "note": "Stale reads are expected - this demonstrates eventual consistency!"
    }


@app.post("/write-and-wait")
async def write_and_wait(request: WriteRequest, timeout_ms: int = 5000):
    """
    Write to primary, then wait until all replicas are consistent.
    This demonstrates read-your-writes consistency by waiting.
    """
    current_span = trace.get_current_span()
    write_time = time.time()
    deadline = write_time + (timeout_ms / 1000.0)

    async with httpx.AsyncClient(timeout=5.0) as client:
        # Write to primary
        write_response = await client.post(
            f"{PRIMARY_URL}/data",
            json={"key": request.key, "value": request.value}
        )
        write_result = write_response.json()
        write_version = write_result.get("version", 0)

        current_span.set_attribute("write.version", write_version)

        # Wait for all replicas to catch up
        attempts = 0
        while time.time() < deadline:
            attempts += 1
            all_consistent = True
            replica_versions = {}

            for url in REPLICA_URLS:
                url = url.strip()
                if url:
                    replica_name = url.split("/")[-1].split(":")[0]
                    try:
                        resp = await client.get(f"{url}/version")
                        if resp.status_code == 200:
                            data = resp.json()
                            version = data.get("version", 0)
                            replica_versions[replica_name] = version
                            if version < write_version:
                                all_consistent = False
                    except Exception as e:
                        all_consistent = False
                        replica_versions[replica_name] = f"error: {e}"

            if all_consistent:
                consistency_time = (time.time() - write_time) * 1000
                current_span.set_attribute("consistency.time_ms", consistency_time)
                return {
                    "write_result": write_result,
                    "consistency_achieved": True,
                    "consistency_time_ms": round(consistency_time, 2),
                    "attempts": attempts,
                    "final_versions": replica_versions
                }

            await asyncio.sleep(0.05)  # 50ms between checks

    # Timeout
    return {
        "write_result": write_result,
        "consistency_achieved": False,
        "timeout_ms": timeout_ms,
        "attempts": attempts,
        "final_versions": replica_versions,
        "note": "Timed out waiting for consistency"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
