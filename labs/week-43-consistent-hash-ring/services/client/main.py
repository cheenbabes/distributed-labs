"""
Client Service - Simulates client traffic and provides utilities for testing.

This service provides:
- Batch key generation for testing
- Key distribution analysis
- Migration simulation utilities
"""
import asyncio
import hashlib
import logging
import os
import random
import string
import time
from contextlib import asynccontextmanager
from typing import Optional

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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "client")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://ring-coordinator:8080")

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
KEYS_GENERATED = Counter(
    "client_keys_generated_total",
    "Total keys generated"
)
STORE_REQUESTS = Counter(
    "client_store_requests_total",
    "Total store requests",
    ["result"]
)
GET_REQUESTS = Counter(
    "client_get_requests_total",
    "Total get requests",
    ["result"]
)
REQUEST_LATENCY = Histogram(
    "client_request_duration_seconds",
    "Client request latency",
    ["operation"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)


def generate_random_key(prefix: str = "", length: int = 10) -> str:
    """Generate a random key."""
    random_part = ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))
    return f"{prefix}{random_part}" if prefix else random_part


def generate_random_value(length: int = 50) -> str:
    """Generate a random value."""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


class GenerateKeysRequest(BaseModel):
    count: int = 100
    prefix: str = ""
    key_length: int = 10
    value_length: int = 50
    store: bool = True  # Whether to store the keys or just generate


class BatchGetRequest(BaseModel):
    keys: list[str]


class MigrationTestRequest(BaseModel):
    key_count: int = 1000
    node_to_remove: Optional[str] = None
    node_to_add: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Coordinator URL: {COORDINATOR_URL}")
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


@app.post("/generate-keys")
async def generate_keys(request: GenerateKeysRequest):
    """Generate and optionally store a batch of random keys."""
    with tracer.start_as_current_span("generate_keys") as span:
        span.set_attribute("count", request.count)
        span.set_attribute("store", request.store)

        start_time = time.time()
        keys_generated = []
        distribution = {}
        errors = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            for i in range(request.count):
                key = generate_random_key(request.prefix, request.key_length)
                value = generate_random_value(request.value_length)

                KEYS_GENERATED.inc()
                keys_generated.append(key)

                if request.store:
                    try:
                        response = await client.post(
                            f"{COORDINATOR_URL}/keys",
                            json={"key": key, "value": value}
                        )
                        response.raise_for_status()
                        result = response.json()

                        node = result.get("routed_to", "unknown")
                        distribution[node] = distribution.get(node, 0) + 1
                        STORE_REQUESTS.labels(result="success").inc()

                    except Exception as e:
                        errors.append({"key": key, "error": str(e)})
                        STORE_REQUESTS.labels(result="error").inc()

        duration = time.time() - start_time
        REQUEST_LATENCY.labels(operation="generate_batch").observe(duration)

        return {
            "keys_generated": len(keys_generated),
            "keys_stored": request.count - len(errors) if request.store else 0,
            "distribution": distribution,
            "errors": errors[:10],  # Limit errors in response
            "duration_seconds": duration
        }


@app.post("/batch-get")
async def batch_get(request: BatchGetRequest):
    """Get multiple keys in batch."""
    with tracer.start_as_current_span("batch_get") as span:
        span.set_attribute("key_count", len(request.keys))

        start_time = time.time()
        results = []
        hits = 0
        misses = 0

        async with httpx.AsyncClient(timeout=30.0) as client:
            for key in request.keys:
                try:
                    response = await client.get(f"{COORDINATOR_URL}/keys/{key}")
                    if response.status_code == 200:
                        results.append(response.json())
                        hits += 1
                        GET_REQUESTS.labels(result="hit").inc()
                    else:
                        misses += 1
                        GET_REQUESTS.labels(result="miss").inc()
                except Exception as e:
                    misses += 1
                    GET_REQUESTS.labels(result="error").inc()

        duration = time.time() - start_time
        REQUEST_LATENCY.labels(operation="batch_get").observe(duration)

        return {
            "total": len(request.keys),
            "hits": hits,
            "misses": misses,
            "hit_rate": hits / len(request.keys) if request.keys else 0,
            "results": results[:50],  # Limit results in response
            "duration_seconds": duration
        }


@app.get("/ring-status")
async def ring_status():
    """Get current ring status from coordinator."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            ring_response = await client.get(f"{COORDINATOR_URL}/ring")
            distribution_response = await client.get(f"{COORDINATOR_URL}/ring/distribution")

            return {
                "ring": ring_response.json(),
                "distribution": distribution_response.json()
            }
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Coordinator unavailable: {e}")


@app.post("/simulate-traffic")
async def simulate_traffic(
    duration_seconds: int = 30,
    requests_per_second: int = 10,
    read_ratio: float = 0.7
):
    """Simulate mixed read/write traffic."""
    with tracer.start_as_current_span("simulate_traffic") as span:
        span.set_attribute("duration_seconds", duration_seconds)
        span.set_attribute("requests_per_second", requests_per_second)
        span.set_attribute("read_ratio", read_ratio)

        start_time = time.time()
        stored_keys = []
        stats = {
            "writes": {"success": 0, "error": 0},
            "reads": {"hit": 0, "miss": 0, "error": 0}
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            end_time = start_time + duration_seconds
            interval = 1.0 / requests_per_second

            while time.time() < end_time:
                op_start = time.time()

                # Decide read or write
                if random.random() < read_ratio and stored_keys:
                    # Read operation
                    key = random.choice(stored_keys)
                    try:
                        response = await client.get(f"{COORDINATOR_URL}/keys/{key}")
                        if response.status_code == 200:
                            stats["reads"]["hit"] += 1
                        else:
                            stats["reads"]["miss"] += 1
                    except Exception:
                        stats["reads"]["error"] += 1
                else:
                    # Write operation
                    key = generate_random_key("traffic_", 8)
                    value = generate_random_value(100)
                    try:
                        response = await client.post(
                            f"{COORDINATOR_URL}/keys",
                            json={"key": key, "value": value}
                        )
                        if response.status_code == 200:
                            stored_keys.append(key)
                            stats["writes"]["success"] += 1
                        else:
                            stats["writes"]["error"] += 1
                    except Exception:
                        stats["writes"]["error"] += 1

                # Maintain rate
                elapsed = time.time() - op_start
                if elapsed < interval:
                    await asyncio.sleep(interval - elapsed)

        actual_duration = time.time() - start_time
        total_ops = (
            stats["writes"]["success"] + stats["writes"]["error"] +
            stats["reads"]["hit"] + stats["reads"]["miss"] + stats["reads"]["error"]
        )

        return {
            "duration_seconds": actual_duration,
            "total_operations": total_ops,
            "actual_ops_per_second": total_ops / actual_duration,
            "stats": stats,
            "keys_created": len(stored_keys)
        }


@app.post("/test-migration")
async def test_migration(request: MigrationTestRequest):
    """
    Test key migration by:
    1. Storing a set of keys
    2. Recording their distribution
    3. Adding or removing a node
    4. Checking which keys moved
    """
    with tracer.start_as_current_span("test_migration") as span:
        span.set_attribute("key_count", request.key_count)

        results = {
            "phase1_store": {},
            "phase2_topology_change": {},
            "phase3_verify": {},
            "migration_analysis": {}
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            # Phase 1: Store keys
            logger.info(f"Phase 1: Storing {request.key_count} keys")
            key_to_node = {}

            for i in range(request.key_count):
                key = f"migration_test_{i}"
                value = f"value_{i}"

                try:
                    response = await client.post(
                        f"{COORDINATOR_URL}/keys",
                        json={"key": key, "value": value}
                    )
                    response.raise_for_status()
                    result = response.json()
                    key_to_node[key] = result.get("routed_to", "unknown")
                except Exception as e:
                    logger.error(f"Failed to store key {key}: {e}")

            # Record initial distribution
            initial_distribution = {}
            for node in key_to_node.values():
                initial_distribution[node] = initial_distribution.get(node, 0) + 1

            results["phase1_store"] = {
                "keys_stored": len(key_to_node),
                "distribution": initial_distribution
            }

            # Phase 2: Topology change
            logger.info("Phase 2: Changing topology")

            if request.node_to_remove:
                try:
                    response = await client.delete(
                        f"{COORDINATOR_URL}/ring/nodes/{request.node_to_remove}"
                    )
                    results["phase2_topology_change"]["removed"] = response.json()
                except Exception as e:
                    results["phase2_topology_change"]["remove_error"] = str(e)

            if request.node_to_add:
                try:
                    # Add new node with address based on naming convention
                    node_num = request.node_to_add.split("-")[-1]
                    address = f"http://storage-node-{node_num}:8080"
                    response = await client.post(
                        f"{COORDINATOR_URL}/ring/nodes",
                        json={"node_id": request.node_to_add, "address": address}
                    )
                    results["phase2_topology_change"]["added"] = response.json()
                except Exception as e:
                    results["phase2_topology_change"]["add_error"] = str(e)

            # Phase 3: Verify new distribution
            logger.info("Phase 3: Verifying new key distribution")
            new_key_to_node = {}
            keys_moved = 0
            movement_details = []

            for key in key_to_node.keys():
                try:
                    response = await client.get(f"{COORDINATOR_URL}/lookup/{key}")
                    response.raise_for_status()
                    result = response.json()
                    new_node = result.get("target_node", {}).get("node_id", "unknown")
                    new_key_to_node[key] = new_node

                    if new_node != key_to_node[key]:
                        keys_moved += 1
                        movement_details.append({
                            "key": key,
                            "from": key_to_node[key],
                            "to": new_node
                        })
                except Exception as e:
                    logger.error(f"Failed to lookup key {key}: {e}")

            # Record new distribution
            new_distribution = {}
            for node in new_key_to_node.values():
                new_distribution[node] = new_distribution.get(node, 0) + 1

            results["phase3_verify"] = {
                "distribution": new_distribution
            }

            # Migration analysis
            total_keys = len(key_to_node)
            ideal_movement = 0
            if request.node_to_add and not request.node_to_remove:
                # Adding a node: ideal is K/N where N is new node count
                new_node_count = len(new_distribution)
                ideal_movement = total_keys / new_node_count
            elif request.node_to_remove and not request.node_to_add:
                # Removing a node: keys from removed node should move
                removed_node_keys = initial_distribution.get(request.node_to_remove, 0)
                ideal_movement = removed_node_keys

            results["migration_analysis"] = {
                "total_keys": total_keys,
                "keys_moved": keys_moved,
                "movement_percentage": (keys_moved / total_keys * 100) if total_keys else 0,
                "ideal_movement": ideal_movement,
                "movement_details": movement_details[:20]  # Limit for response
            }

        return results


@app.get("/analyze-hash-distribution")
async def analyze_hash_distribution(sample_size: int = 1000, prefix: str = ""):
    """
    Analyze how uniformly keys would be distributed without actually storing them.
    This helps understand the quality of the hash function and virtual node setup.
    """
    with tracer.start_as_current_span("analyze_hash_distribution") as span:
        span.set_attribute("sample_size", sample_size)

        async with httpx.AsyncClient(timeout=60.0) as client:
            distribution = {}

            for i in range(sample_size):
                key = f"{prefix}sample_key_{i}"

                try:
                    response = await client.get(f"{COORDINATOR_URL}/lookup/{key}")
                    response.raise_for_status()
                    result = response.json()
                    node = result.get("target_node", {}).get("node_id", "unknown")
                    distribution[node] = distribution.get(node, 0) + 1
                except Exception as e:
                    logger.error(f"Lookup failed for {key}: {e}")

            # Calculate statistics
            if distribution:
                values = list(distribution.values())
                total = sum(values)
                mean = total / len(values)
                variance = sum((v - mean) ** 2 for v in values) / len(values)
                std_dev = variance ** 0.5

                ideal = total / len(distribution)
                max_deviation = max(abs(v - ideal) for v in values)

                return {
                    "sample_size": sample_size,
                    "node_count": len(distribution),
                    "distribution": distribution,
                    "statistics": {
                        "mean_keys_per_node": mean,
                        "std_deviation": std_dev,
                        "coefficient_of_variation": std_dev / mean if mean else 0,
                        "ideal_keys_per_node": ideal,
                        "max_deviation_from_ideal": max_deviation,
                        "max_deviation_percentage": (max_deviation / ideal * 100) if ideal else 0
                    }
                }
            else:
                return {"error": "No nodes available"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090)
