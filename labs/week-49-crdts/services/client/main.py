"""
Client Service - Provides an API for concurrent updates to CRDTs.
Simulates distributed clients making concurrent writes.
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from typing import Dict, List, Any, Optional

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
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "crdt-client")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

# Replica configuration
REPLICA_URLS = os.getenv("REPLICA_URLS", "http://replica-1:8001,http://replica-2:8002,http://replica-3:8003").split(",")
SYNC_URL = os.getenv("SYNC_URL", "http://sync-service:8010")

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
CLIENT_OPERATIONS = Counter(
    "crdt_client_operations_total",
    "Total client operations",
    ["operation", "crdt_type", "status"]
)
OPERATION_DURATION = Histogram(
    "crdt_client_operation_duration_seconds",
    "Duration of client operations",
    ["operation", "crdt_type"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
CONCURRENT_TEST_RUNS = Counter(
    "crdt_concurrent_test_runs_total",
    "Total concurrent test runs",
    ["test_type", "status"]
)


# =============================================================================
# Pydantic Models
# =============================================================================

class GCounterRequest(BaseModel):
    name: str
    amount: int = 1


class PNCounterRequest(BaseModel):
    name: str
    amount: int = 1


class LWWRegisterRequest(BaseModel):
    name: str
    value: Any


class ConcurrentTestRequest(BaseModel):
    crdt_type: str  # "gcounter", "pncounter", "lww"
    name: str
    operations_per_replica: int = 10
    operation_delay_ms: int = 50


class PartitionTestRequest(BaseModel):
    crdt_type: str
    name: str
    partition_replica: str  # Which replica to partition (e.g., "replica-1")
    operations_during_partition: int = 5


# =============================================================================
# Helper Functions
# =============================================================================

def get_replica_url(replica_id: str) -> str:
    """Get URL for a replica by ID."""
    for url in REPLICA_URLS:
        if replica_id in url:
            return url
    raise ValueError(f"Unknown replica: {replica_id}")


async def send_to_random_replica(client: httpx.AsyncClient, path: str, method: str = "GET", json_data: dict = None) -> dict:
    """Send a request to a random replica."""
    replica_url = random.choice(REPLICA_URLS)
    if method == "GET":
        response = await client.get(f"{replica_url}{path}", timeout=5.0)
    else:
        response = await client.post(f"{replica_url}{path}", json=json_data, timeout=5.0)

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    return response.json()


async def send_to_all_replicas(client: httpx.AsyncClient, path: str, method: str = "GET", json_data: dict = None) -> Dict[str, Any]:
    """Send a request to all replicas and collect responses."""
    results = {}
    for url in REPLICA_URLS:
        try:
            if method == "GET":
                response = await client.get(f"{url}{path}", timeout=5.0)
            else:
                response = await client.post(f"{url}{path}", json=json_data, timeout=5.0)

            if response.status_code == 200:
                results[url] = response.json()
            else:
                results[url] = {"error": response.status_code}
        except Exception as e:
            results[url] = {"error": str(e)}
    return results


# =============================================================================
# FastAPI App
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Replicas: {REPLICA_URLS}")
    logger.info(f"Sync service: {SYNC_URL}")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title="CRDT Client Service", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# =============================================================================
# Simple Operations (Random Replica)
# =============================================================================

@app.post("/gcounter/increment")
async def increment_gcounter(request: GCounterRequest):
    """Increment a G-Counter on a random replica."""
    with tracer.start_as_current_span("client_gcounter_increment") as span:
        start_time = time.time()

        async with httpx.AsyncClient() as client:
            replica_url = random.choice(REPLICA_URLS)
            span.set_attribute("target.replica", replica_url)

            response = await client.post(
                f"{replica_url}/gcounter/{request.name}/increment",
                json={"amount": request.amount},
                timeout=5.0
            )

        duration = time.time() - start_time
        OPERATION_DURATION.labels(operation="increment", crdt_type="gcounter").observe(duration)

        if response.status_code == 200:
            CLIENT_OPERATIONS.labels(operation="increment", crdt_type="gcounter", status="success").inc()
            return response.json()
        else:
            CLIENT_OPERATIONS.labels(operation="increment", crdt_type="gcounter", status="error").inc()
            raise HTTPException(status_code=response.status_code, detail=response.text)


@app.get("/gcounter/{name}")
async def get_gcounter(name: str):
    """Get a G-Counter value from all replicas."""
    async with httpx.AsyncClient() as client:
        results = await send_to_all_replicas(client, f"/gcounter/{name}")

    # Calculate expected merged value
    all_states = []
    for url, result in results.items():
        if "state" in result:
            all_states.append(result["state"])

    merged_value = 0
    if all_states:
        merged = {}
        for state in all_states:
            for replica_id, count in state.items():
                merged[replica_id] = max(merged.get(replica_id, 0), count)
        merged_value = sum(merged.values())

    return {
        "name": name,
        "type": "gcounter",
        "replicas": results,
        "expected_merged_value": merged_value
    }


@app.post("/pncounter/increment")
async def increment_pncounter(request: PNCounterRequest):
    """Increment a PN-Counter on a random replica."""
    with tracer.start_as_current_span("client_pncounter_increment") as span:
        start_time = time.time()

        async with httpx.AsyncClient() as client:
            replica_url = random.choice(REPLICA_URLS)
            span.set_attribute("target.replica", replica_url)

            response = await client.post(
                f"{replica_url}/pncounter/{request.name}/increment",
                json={"amount": request.amount},
                timeout=5.0
            )

        duration = time.time() - start_time
        OPERATION_DURATION.labels(operation="increment", crdt_type="pncounter").observe(duration)

        if response.status_code == 200:
            CLIENT_OPERATIONS.labels(operation="increment", crdt_type="pncounter", status="success").inc()
            return response.json()
        else:
            CLIENT_OPERATIONS.labels(operation="increment", crdt_type="pncounter", status="error").inc()
            raise HTTPException(status_code=response.status_code, detail=response.text)


@app.post("/pncounter/decrement")
async def decrement_pncounter(request: PNCounterRequest):
    """Decrement a PN-Counter on a random replica."""
    with tracer.start_as_current_span("client_pncounter_decrement") as span:
        start_time = time.time()

        async with httpx.AsyncClient() as client:
            replica_url = random.choice(REPLICA_URLS)
            span.set_attribute("target.replica", replica_url)

            response = await client.post(
                f"{replica_url}/pncounter/{request.name}/decrement",
                json={"amount": request.amount},
                timeout=5.0
            )

        duration = time.time() - start_time
        OPERATION_DURATION.labels(operation="decrement", crdt_type="pncounter").observe(duration)

        if response.status_code == 200:
            CLIENT_OPERATIONS.labels(operation="decrement", crdt_type="pncounter", status="success").inc()
            return response.json()
        else:
            CLIENT_OPERATIONS.labels(operation="decrement", crdt_type="pncounter", status="error").inc()
            raise HTTPException(status_code=response.status_code, detail=response.text)


@app.get("/pncounter/{name}")
async def get_pncounter(name: str):
    """Get a PN-Counter value from all replicas."""
    async with httpx.AsyncClient() as client:
        results = await send_to_all_replicas(client, f"/pncounter/{name}")

    # Calculate expected merged value
    all_states = []
    for url, result in results.items():
        if "state" in result:
            all_states.append(result["state"])

    merged_value = 0
    if all_states:
        merged_p = {}
        merged_n = {}
        for state in all_states:
            for replica_id, count in state.get("p", {}).items():
                merged_p[replica_id] = max(merged_p.get(replica_id, 0), count)
            for replica_id, count in state.get("n", {}).items():
                merged_n[replica_id] = max(merged_n.get(replica_id, 0), count)
        merged_value = sum(merged_p.values()) - sum(merged_n.values())

    return {
        "name": name,
        "type": "pncounter",
        "replicas": results,
        "expected_merged_value": merged_value
    }


@app.post("/lww/set")
async def set_lww_register(request: LWWRegisterRequest):
    """Set a LWW-Register value on a random replica."""
    with tracer.start_as_current_span("client_lww_set") as span:
        start_time = time.time()

        async with httpx.AsyncClient() as client:
            replica_url = random.choice(REPLICA_URLS)
            span.set_attribute("target.replica", replica_url)

            response = await client.post(
                f"{replica_url}/lww/{request.name}/set",
                json={"value": request.value},
                timeout=5.0
            )

        duration = time.time() - start_time
        OPERATION_DURATION.labels(operation="set", crdt_type="lww").observe(duration)

        if response.status_code == 200:
            CLIENT_OPERATIONS.labels(operation="set", crdt_type="lww", status="success").inc()
            return response.json()
        else:
            CLIENT_OPERATIONS.labels(operation="set", crdt_type="lww", status="error").inc()
            raise HTTPException(status_code=response.status_code, detail=response.text)


@app.get("/lww/{name}")
async def get_lww_register(name: str):
    """Get a LWW-Register value from all replicas."""
    async with httpx.AsyncClient() as client:
        results = await send_to_all_replicas(client, f"/lww/{name}")

    # Find the winning value
    winner = None
    for url, result in results.items():
        if "current" in result:
            current = result["current"]
            if winner is None:
                winner = current
            elif (current.get("timestamp", 0) > winner.get("timestamp", 0) or
                  (current.get("timestamp", 0) == winner.get("timestamp", 0) and
                   current.get("replica_id", "") > winner.get("replica_id", ""))):
                winner = current

    return {
        "name": name,
        "type": "lww-register",
        "replicas": results,
        "expected_winner": winner
    }


# =============================================================================
# Concurrent Test Endpoints
# =============================================================================

@app.post("/test/concurrent")
async def run_concurrent_test(request: ConcurrentTestRequest):
    """Run a concurrent update test across all replicas."""
    with tracer.start_as_current_span("concurrent_test") as span:
        span.set_attribute("test.crdt_type", request.crdt_type)
        span.set_attribute("test.name", request.name)
        span.set_attribute("test.operations_per_replica", request.operations_per_replica)

        start_time = time.time()
        results = {"operations": [], "errors": []}

        async def run_replica_operations(client: httpx.AsyncClient, replica_url: str):
            """Run operations on a single replica."""
            replica_id = replica_url.split("/")[-1].replace(":8001", "").replace(":8002", "").replace(":8003", "")
            ops = []

            for i in range(request.operations_per_replica):
                try:
                    if request.crdt_type == "gcounter":
                        response = await client.post(
                            f"{replica_url}/gcounter/{request.name}/increment",
                            json={"amount": 1},
                            timeout=5.0
                        )
                    elif request.crdt_type == "pncounter":
                        # Alternate between increment and decrement
                        if i % 2 == 0:
                            response = await client.post(
                                f"{replica_url}/pncounter/{request.name}/increment",
                                json={"amount": 1},
                                timeout=5.0
                            )
                        else:
                            response = await client.post(
                                f"{replica_url}/pncounter/{request.name}/decrement",
                                json={"amount": 1},
                                timeout=5.0
                            )
                    elif request.crdt_type == "lww":
                        response = await client.post(
                            f"{replica_url}/lww/{request.name}/set",
                            json={"value": f"{replica_id}-write-{i}"},
                            timeout=5.0
                        )
                    else:
                        raise ValueError(f"Unknown CRDT type: {request.crdt_type}")

                    if response.status_code == 200:
                        ops.append({
                            "replica": replica_url,
                            "operation": i,
                            "result": response.json()
                        })
                    else:
                        results["errors"].append({
                            "replica": replica_url,
                            "operation": i,
                            "error": response.status_code
                        })

                except Exception as e:
                    results["errors"].append({
                        "replica": replica_url,
                        "operation": i,
                        "error": str(e)
                    })

                # Small delay between operations
                await asyncio.sleep(request.operation_delay_ms / 1000.0)

            return ops

        # Run operations on all replicas concurrently
        async with httpx.AsyncClient() as client:
            tasks = [run_replica_operations(client, url) for url in REPLICA_URLS]
            all_ops = await asyncio.gather(*tasks)

            for ops in all_ops:
                results["operations"].extend(ops)

        # Wait for sync to propagate
        await asyncio.sleep(0.5)

        # Get final state from all replicas
        async with httpx.AsyncClient() as client:
            if request.crdt_type == "gcounter":
                final_state = await send_to_all_replicas(client, f"/gcounter/{request.name}")
            elif request.crdt_type == "pncounter":
                final_state = await send_to_all_replicas(client, f"/pncounter/{request.name}")
            elif request.crdt_type == "lww":
                final_state = await send_to_all_replicas(client, f"/lww/{request.name}")

        duration = time.time() - start_time

        # Check consistency
        values = []
        for url, state in final_state.items():
            if "value" in state:
                values.append(state["value"])
            elif "current" in state:
                values.append(state["current"])

        is_consistent = len(set(str(v) for v in values)) <= 1

        status = "success" if is_consistent else "inconsistent"
        CONCURRENT_TEST_RUNS.labels(test_type=request.crdt_type, status=status).inc()

        return {
            "test_type": request.crdt_type,
            "crdt_name": request.name,
            "duration_seconds": round(duration, 3),
            "operations_executed": len(results["operations"]),
            "errors": len(results["errors"]),
            "final_state": final_state,
            "is_consistent": is_consistent,
            "note": "Trigger sync to converge if not consistent" if not is_consistent else "All replicas have converged"
        }


@app.post("/test/partition")
async def run_partition_test(request: PartitionTestRequest):
    """Test behavior during a network partition."""
    with tracer.start_as_current_span("partition_test") as span:
        span.set_attribute("test.crdt_type", request.crdt_type)
        span.set_attribute("test.partition_replica", request.partition_replica)

        partition_url = None
        for url in REPLICA_URLS:
            if request.partition_replica in url:
                partition_url = url
                break

        if not partition_url:
            raise HTTPException(status_code=400, detail=f"Unknown replica: {request.partition_replica}")

        results = {
            "phase1_before_partition": {},
            "phase2_during_partition": {},
            "phase3_after_heal": {},
            "phase4_after_sync": {}
        }

        async with httpx.AsyncClient() as client:
            # Phase 1: Get initial state
            if request.crdt_type == "gcounter":
                results["phase1_before_partition"] = await send_to_all_replicas(
                    client, f"/gcounter/{request.name}"
                )
            elif request.crdt_type == "pncounter":
                results["phase1_before_partition"] = await send_to_all_replicas(
                    client, f"/pncounter/{request.name}"
                )
            elif request.crdt_type == "lww":
                results["phase1_before_partition"] = await send_to_all_replicas(
                    client, f"/lww/{request.name}"
                )

            # Phase 2: Enable partition on target replica
            await client.post(
                f"{partition_url}/admin/partition",
                json={"enabled": True},
                timeout=5.0
            )
            span.add_event(f"Partitioned {partition_url}")

            # Make operations on all replicas during partition
            for i in range(request.operations_during_partition):
                for url in REPLICA_URLS:
                    try:
                        if request.crdt_type == "gcounter":
                            await client.post(
                                f"{url}/gcounter/{request.name}/increment",
                                json={"amount": 1},
                                timeout=2.0
                            )
                        elif request.crdt_type == "pncounter":
                            await client.post(
                                f"{url}/pncounter/{request.name}/increment",
                                json={"amount": 1},
                                timeout=2.0
                            )
                        elif request.crdt_type == "lww":
                            replica_id = url.split("//")[1].split(":")[0]
                            await client.post(
                                f"{url}/lww/{request.name}/set",
                                json={"value": f"{replica_id}-partition-{i}"},
                                timeout=2.0
                            )
                    except Exception:
                        pass  # Expected for partitioned replica during sync

            # Get state during partition
            if request.crdt_type == "gcounter":
                results["phase2_during_partition"] = await send_to_all_replicas(
                    client, f"/gcounter/{request.name}"
                )
            elif request.crdt_type == "pncounter":
                results["phase2_during_partition"] = await send_to_all_replicas(
                    client, f"/pncounter/{request.name}"
                )
            elif request.crdt_type == "lww":
                results["phase2_during_partition"] = await send_to_all_replicas(
                    client, f"/lww/{request.name}"
                )

            # Phase 3: Heal partition
            await client.post(
                f"{partition_url}/admin/partition",
                json={"enabled": False},
                timeout=5.0
            )
            span.add_event(f"Healed partition on {partition_url}")

            # Get state immediately after heal
            if request.crdt_type == "gcounter":
                results["phase3_after_heal"] = await send_to_all_replicas(
                    client, f"/gcounter/{request.name}"
                )
            elif request.crdt_type == "pncounter":
                results["phase3_after_heal"] = await send_to_all_replicas(
                    client, f"/pncounter/{request.name}"
                )
            elif request.crdt_type == "lww":
                results["phase3_after_heal"] = await send_to_all_replicas(
                    client, f"/lww/{request.name}"
                )

            # Phase 4: Trigger manual sync and check convergence
            try:
                await client.post(f"{SYNC_URL}/sync", timeout=10.0)
            except Exception as e:
                logger.warning(f"Sync failed: {e}")

            await asyncio.sleep(1.0)  # Wait for sync to complete

            if request.crdt_type == "gcounter":
                results["phase4_after_sync"] = await send_to_all_replicas(
                    client, f"/gcounter/{request.name}"
                )
            elif request.crdt_type == "pncounter":
                results["phase4_after_sync"] = await send_to_all_replicas(
                    client, f"/pncounter/{request.name}"
                )
            elif request.crdt_type == "lww":
                results["phase4_after_sync"] = await send_to_all_replicas(
                    client, f"/lww/{request.name}"
                )

        return {
            "test_type": "partition",
            "crdt_type": request.crdt_type,
            "partitioned_replica": partition_url,
            "results": results,
            "explanation": {
                "phase1": "Initial state before partition",
                "phase2": "State after operations during partition (diverged)",
                "phase3": "State immediately after partition healed",
                "phase4": "State after sync (should be converged)"
            }
        }


@app.post("/sync/trigger")
async def trigger_sync():
    """Trigger a manual sync."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(f"{SYNC_URL}/sync", timeout=10.0)
            return response.json()
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Sync service unreachable: {e}")


@app.get("/state/all")
async def get_all_state():
    """Get the current state from all replicas."""
    async with httpx.AsyncClient() as client:
        results = {}
        for url in REPLICA_URLS:
            try:
                response = await client.get(f"{url}/state", timeout=5.0)
                if response.status_code == 200:
                    results[url] = response.json()
                else:
                    results[url] = {"error": response.status_code}
            except Exception as e:
                results[url] = {"error": str(e)}
        return results


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8020"))
    uvicorn.run(app, host="0.0.0.0", port=port)
