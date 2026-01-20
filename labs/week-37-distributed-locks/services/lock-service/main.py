"""
Lock Service - Distributed lock management using Redlock algorithm.

Implements the Redlock algorithm for distributed locks across multiple Redis instances.
This service provides APIs for acquiring, releasing, and extending locks.
"""
import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "lock-service")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
REDIS_NODES = os.getenv("REDIS_NODES", "redis-1:6379,redis-2:6379,redis-3:6379").split(",")
LOCK_TTL_MS = int(os.getenv("LOCK_TTL_MS", "10000"))
LOCK_RETRY_DELAY_MS = int(os.getenv("LOCK_RETRY_DELAY_MS", "200"))
LOCK_RETRY_COUNT = int(os.getenv("LOCK_RETRY_COUNT", "3"))
# Clock drift factor (as per Redlock spec)
CLOCK_DRIFT_FACTOR = 0.01

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
LOCK_ACQUIRE_TOTAL = Counter(
    "lock_acquire_total",
    "Total lock acquisition attempts",
    ["resource", "result"]
)
LOCK_RELEASE_TOTAL = Counter(
    "lock_release_total",
    "Total lock release attempts",
    ["resource", "result"]
)
LOCK_ACQUIRE_DURATION = Histogram(
    "lock_acquire_duration_seconds",
    "Lock acquisition latency",
    ["resource"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
ACTIVE_LOCKS = Gauge(
    "active_locks",
    "Currently held locks",
    ["resource"]
)
REDIS_NODE_HEALTHY = Gauge(
    "redis_node_healthy",
    "Health status of Redis nodes",
    ["node"]
)
LOCK_CONTENTION = Counter(
    "lock_contention_total",
    "Lock contention events (failed to acquire due to existing lock)",
    ["resource"]
)
REDIS_OPERATION_ERRORS = Counter(
    "redis_operation_errors_total",
    "Redis operation errors",
    ["node", "operation"]
)

# Lua scripts for atomic operations
LOCK_SCRIPT = """
if redis.call("exists", KEYS[1]) == 0 then
    redis.call("set", KEYS[1], ARGV[1], "PX", ARGV[2])
    return 1
end
return 0
"""

UNLOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""

EXTEND_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("pexpire", KEYS[1], ARGV[2])
end
return 0
"""


class LockRequest(BaseModel):
    resource: str
    ttl_ms: Optional[int] = None
    client_id: Optional[str] = None


class LockResponse(BaseModel):
    acquired: bool
    resource: str
    lock_id: str
    valid_until_ms: int
    redis_nodes_locked: int
    message: str


class UnlockRequest(BaseModel):
    resource: str
    lock_id: str


class ExtendRequest(BaseModel):
    resource: str
    lock_id: str
    ttl_ms: Optional[int] = None


# Global state
redis_clients: list[redis.Redis] = []
active_locks: dict[str, dict] = {}
# Mode: "redlock" (multiple Redis) or "single" (one Redis)
lock_mode = "redlock"


async def create_redis_clients():
    """Create Redis client connections."""
    global redis_clients
    for node in REDIS_NODES:
        host, port = node.split(":")
        client = redis.Redis(host=host, port=int(port), decode_responses=True)
        redis_clients.append(client)
        logger.info(f"Created Redis client for {node}")


async def close_redis_clients():
    """Close all Redis connections."""
    for client in redis_clients:
        await client.close()


async def check_redis_health():
    """Check health of all Redis nodes and update metrics."""
    healthy_count = 0
    for i, client in enumerate(redis_clients):
        node_name = REDIS_NODES[i]
        try:
            await client.ping()
            REDIS_NODE_HEALTHY.labels(node=node_name).set(1)
            healthy_count += 1
        except Exception as e:
            REDIS_NODE_HEALTHY.labels(node=node_name).set(0)
            logger.warning(f"Redis node {node_name} unhealthy: {e}")
    return healthy_count


async def acquire_lock_on_instance(
    client: redis.Redis,
    resource: str,
    lock_id: str,
    ttl_ms: int,
    node_name: str
) -> bool:
    """Try to acquire lock on a single Redis instance."""
    try:
        result = await client.eval(LOCK_SCRIPT, 1, resource, lock_id, ttl_ms)
        return result == 1
    except Exception as e:
        REDIS_OPERATION_ERRORS.labels(node=node_name, operation="acquire").inc()
        logger.error(f"Error acquiring lock on {node_name}: {e}")
        return False


async def release_lock_on_instance(
    client: redis.Redis,
    resource: str,
    lock_id: str,
    node_name: str
) -> bool:
    """Release lock on a single Redis instance."""
    try:
        result = await client.eval(UNLOCK_SCRIPT, 1, resource, lock_id)
        return result == 1
    except Exception as e:
        REDIS_OPERATION_ERRORS.labels(node=node_name, operation="release").inc()
        logger.error(f"Error releasing lock on {node_name}: {e}")
        return False


async def extend_lock_on_instance(
    client: redis.Redis,
    resource: str,
    lock_id: str,
    ttl_ms: int,
    node_name: str
) -> bool:
    """Extend lock TTL on a single Redis instance."""
    try:
        result = await client.eval(EXTEND_SCRIPT, 1, resource, lock_id, ttl_ms)
        return result == 1
    except Exception as e:
        REDIS_OPERATION_ERRORS.labels(node=node_name, operation="extend").inc()
        logger.error(f"Error extending lock on {node_name}: {e}")
        return False


async def redlock_acquire(resource: str, lock_id: str, ttl_ms: int) -> tuple[bool, int, int]:
    """
    Implement Redlock algorithm for acquiring a distributed lock.

    Returns: (success, nodes_locked, validity_time_ms)
    """
    n = len(redis_clients)
    quorum = n // 2 + 1

    # Drift time calculation as per Redlock spec
    drift = int(ttl_ms * CLOCK_DRIFT_FACTOR) + 2

    start_time = time.time() * 1000

    # Try to acquire lock on all instances
    tasks = []
    for i, client in enumerate(redis_clients):
        task = acquire_lock_on_instance(client, resource, lock_id, ttl_ms, REDIS_NODES[i])
        tasks.append(task)

    results = await asyncio.gather(*tasks)
    nodes_locked = sum(results)

    # Calculate elapsed time
    elapsed_ms = (time.time() * 1000) - start_time

    # Calculate validity time
    validity_time = ttl_ms - elapsed_ms - drift

    # Check if we have quorum and validity time is positive
    if nodes_locked >= quorum and validity_time > 0:
        logger.info(f"Redlock acquired: {resource} with {nodes_locked}/{n} nodes, validity={validity_time:.0f}ms")
        return True, nodes_locked, int(validity_time)
    else:
        # Failed to acquire, release all locks
        logger.warning(f"Redlock failed: {resource} got {nodes_locked}/{n} nodes (need {quorum})")
        await redlock_release(resource, lock_id)
        if nodes_locked > 0:
            LOCK_CONTENTION.labels(resource=resource).inc()
        return False, nodes_locked, 0


async def redlock_release(resource: str, lock_id: str) -> int:
    """Release lock from all Redis instances."""
    tasks = []
    for i, client in enumerate(redis_clients):
        task = release_lock_on_instance(client, resource, lock_id, REDIS_NODES[i])
        tasks.append(task)

    results = await asyncio.gather(*tasks)
    nodes_released = sum(results)
    return nodes_released


async def single_redis_acquire(resource: str, lock_id: str, ttl_ms: int) -> tuple[bool, int, int]:
    """Acquire lock using only the first Redis instance (for comparison)."""
    if not redis_clients:
        return False, 0, 0

    start_time = time.time() * 1000
    success = await acquire_lock_on_instance(
        redis_clients[0], resource, lock_id, ttl_ms, REDIS_NODES[0]
    )
    elapsed_ms = (time.time() * 1000) - start_time
    validity_time = ttl_ms - elapsed_ms if success else 0

    return success, 1 if success else 0, int(validity_time)


async def single_redis_release(resource: str, lock_id: str) -> int:
    """Release lock from first Redis instance only."""
    if not redis_clients:
        return 0

    success = await release_lock_on_instance(
        redis_clients[0], resource, lock_id, REDIS_NODES[0]
    )
    return 1 if success else 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Redis nodes: {REDIS_NODES}")
    logger.info(f"Lock TTL: {LOCK_TTL_MS}ms")
    await create_redis_clients()
    yield
    logger.info(f"{SERVICE_NAME} shutting down")
    await close_redis_clients()


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    healthy_nodes = await check_redis_health()
    status = "ok" if healthy_nodes >= len(redis_clients) // 2 + 1 else "degraded"
    return {
        "status": status,
        "service": SERVICE_NAME,
        "redis_nodes_healthy": healthy_nodes,
        "redis_nodes_total": len(redis_clients),
        "lock_mode": lock_mode
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/lock/acquire", response_model=LockResponse)
async def acquire_lock(request: LockRequest):
    """
    Acquire a distributed lock using Redlock algorithm.

    The lock is acquired if:
    - We can set the lock on a majority of Redis instances (quorum)
    - The total acquisition time is less than the lock TTL
    """
    with tracer.start_as_current_span("acquire_lock") as span:
        resource = request.resource
        ttl_ms = request.ttl_ms or LOCK_TTL_MS
        lock_id = request.client_id or str(uuid.uuid4())

        span.set_attribute("lock.resource", resource)
        span.set_attribute("lock.ttl_ms", ttl_ms)
        span.set_attribute("lock.mode", lock_mode)

        start_time = time.time()

        # Retry logic
        for attempt in range(LOCK_RETRY_COUNT):
            if lock_mode == "redlock":
                success, nodes_locked, validity_time = await redlock_acquire(
                    resource, lock_id, ttl_ms
                )
            else:
                success, nodes_locked, validity_time = await single_redis_acquire(
                    resource, lock_id, ttl_ms
                )

            if success:
                break

            if attempt < LOCK_RETRY_COUNT - 1:
                await asyncio.sleep(LOCK_RETRY_DELAY_MS / 1000.0)

        duration = time.time() - start_time
        LOCK_ACQUIRE_DURATION.labels(resource=resource).observe(duration)

        if success:
            LOCK_ACQUIRE_TOTAL.labels(resource=resource, result="success").inc()
            ACTIVE_LOCKS.labels(resource=resource).inc()

            valid_until = int(time.time() * 1000) + validity_time
            active_locks[f"{resource}:{lock_id}"] = {
                "resource": resource,
                "lock_id": lock_id,
                "valid_until": valid_until
            }

            span.set_attribute("lock.acquired", True)
            span.set_attribute("lock.nodes_locked", nodes_locked)

            return LockResponse(
                acquired=True,
                resource=resource,
                lock_id=lock_id,
                valid_until_ms=valid_until,
                redis_nodes_locked=nodes_locked,
                message=f"Lock acquired on {nodes_locked} nodes, valid for {validity_time}ms"
            )
        else:
            LOCK_ACQUIRE_TOTAL.labels(resource=resource, result="failed").inc()
            span.set_attribute("lock.acquired", False)

            return LockResponse(
                acquired=False,
                resource=resource,
                lock_id=lock_id,
                valid_until_ms=0,
                redis_nodes_locked=nodes_locked,
                message=f"Failed to acquire lock after {LOCK_RETRY_COUNT} attempts"
            )


@app.post("/lock/release")
async def release_lock(request: UnlockRequest):
    """Release a distributed lock."""
    with tracer.start_as_current_span("release_lock") as span:
        resource = request.resource
        lock_id = request.lock_id

        span.set_attribute("lock.resource", resource)
        span.set_attribute("lock.mode", lock_mode)

        if lock_mode == "redlock":
            nodes_released = await redlock_release(resource, lock_id)
        else:
            nodes_released = await single_redis_release(resource, lock_id)

        lock_key = f"{resource}:{lock_id}"
        if lock_key in active_locks:
            del active_locks[lock_key]
            ACTIVE_LOCKS.labels(resource=resource).dec()

        success = nodes_released > 0
        result = "success" if success else "failed"
        LOCK_RELEASE_TOTAL.labels(resource=resource, result=result).inc()

        span.set_attribute("lock.released", success)
        span.set_attribute("lock.nodes_released", nodes_released)

        return {
            "released": success,
            "resource": resource,
            "lock_id": lock_id,
            "nodes_released": nodes_released,
            "message": f"Lock released from {nodes_released} nodes"
        }


@app.post("/lock/extend")
async def extend_lock(request: ExtendRequest):
    """Extend the TTL of an existing lock."""
    with tracer.start_as_current_span("extend_lock") as span:
        resource = request.resource
        lock_id = request.lock_id
        ttl_ms = request.ttl_ms or LOCK_TTL_MS

        span.set_attribute("lock.resource", resource)
        span.set_attribute("lock.ttl_ms", ttl_ms)

        # Extend on all instances
        tasks = []
        for i, client in enumerate(redis_clients):
            task = extend_lock_on_instance(client, resource, lock_id, ttl_ms, REDIS_NODES[i])
            tasks.append(task)

        results = await asyncio.gather(*tasks)
        nodes_extended = sum(results)

        quorum = len(redis_clients) // 2 + 1
        success = nodes_extended >= quorum

        if success:
            valid_until = int(time.time() * 1000) + ttl_ms
            lock_key = f"{resource}:{lock_id}"
            if lock_key in active_locks:
                active_locks[lock_key]["valid_until"] = valid_until

        span.set_attribute("lock.extended", success)
        span.set_attribute("lock.nodes_extended", nodes_extended)

        return {
            "extended": success,
            "resource": resource,
            "lock_id": lock_id,
            "nodes_extended": nodes_extended,
            "valid_until_ms": int(time.time() * 1000) + ttl_ms if success else 0,
            "message": f"Lock extended on {nodes_extended} nodes"
        }


@app.get("/lock/status/{resource}")
async def lock_status(resource: str):
    """Check current lock status for a resource."""
    with tracer.start_as_current_span("lock_status") as span:
        span.set_attribute("lock.resource", resource)

        # Check each Redis instance
        lock_info = []
        for i, client in enumerate(redis_clients):
            try:
                value = await client.get(resource)
                ttl = await client.pttl(resource) if value else -1
                lock_info.append({
                    "node": REDIS_NODES[i],
                    "locked": value is not None,
                    "lock_id": value,
                    "ttl_ms": ttl
                })
            except Exception as e:
                lock_info.append({
                    "node": REDIS_NODES[i],
                    "error": str(e)
                })

        return {
            "resource": resource,
            "nodes": lock_info,
            "lock_mode": lock_mode
        }


@app.get("/locks/active")
async def list_active_locks():
    """List all currently tracked active locks."""
    return {
        "count": len(active_locks),
        "locks": list(active_locks.values()),
        "lock_mode": lock_mode
    }


@app.post("/admin/mode")
async def set_lock_mode(mode: str):
    """
    Switch between lock modes for demonstration purposes.

    - "redlock": Use full Redlock algorithm (multiple Redis)
    - "single": Use only first Redis instance
    """
    global lock_mode
    if mode not in ["redlock", "single"]:
        raise HTTPException(status_code=400, detail="Mode must be 'redlock' or 'single'")

    old_mode = lock_mode
    lock_mode = mode
    logger.info(f"Lock mode changed from {old_mode} to {mode}")

    return {
        "previous_mode": old_mode,
        "current_mode": lock_mode,
        "message": f"Lock mode changed to {mode}"
    }


@app.get("/admin/mode")
async def get_lock_mode():
    """Get current lock mode."""
    return {
        "mode": lock_mode,
        "description": "redlock uses all Redis instances, single uses only the first"
    }


@app.get("/admin/redis/status")
async def redis_status():
    """Get detailed status of all Redis nodes."""
    status = []
    for i, client in enumerate(redis_clients):
        node_name = REDIS_NODES[i]
        try:
            info = await client.info("server")
            memory = await client.info("memory")
            status.append({
                "node": node_name,
                "healthy": True,
                "redis_version": info.get("redis_version"),
                "used_memory_human": memory.get("used_memory_human"),
                "connected_clients": info.get("connected_clients", "unknown")
            })
        except Exception as e:
            status.append({
                "node": node_name,
                "healthy": False,
                "error": str(e)
            })

    return {
        "nodes": status,
        "total": len(status),
        "healthy": sum(1 for s in status if s.get("healthy", False))
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
