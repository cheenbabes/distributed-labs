"""
Failover Controller - Monitors primary health and orchestrates failover.
Implements leader election to prevent split-brain scenarios.
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
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "failover-controller")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
PRIMARY_URL = os.getenv("PRIMARY_URL", "http://lab30-primary-db:8001")
REPLICA_URLS = os.getenv("REPLICA_URLS", "http://lab30-replica-1:8002,http://lab30-replica-2:8003").split(",")
HEALTH_CHECK_INTERVAL_MS = int(os.getenv("HEALTH_CHECK_INTERVAL_MS", "1000"))
FAILOVER_THRESHOLD = int(os.getenv("FAILOVER_THRESHOLD", "3"))
LEADER_LEASE_TTL_MS = int(os.getenv("LEADER_LEASE_TTL_MS", "5000"))
CONTROLLER_ID = os.getenv("CONTROLLER_ID", f"controller-{uuid.uuid4().hex[:8]}")

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
HEALTH_CHECK_COUNT = Counter(
    "failover_health_checks_total",
    "Total health checks performed",
    ["target", "result"]
)
FAILOVER_COUNT = Counter(
    "failover_count_total",
    "Total number of failovers performed",
    ["from_node", "to_node"]
)
FAILOVER_DURATION = Histogram(
    "failover_duration_seconds",
    "Time taken to complete failover",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)
PRIMARY_HEALTH_STATUS = Gauge(
    "failover_primary_health",
    "Current primary health status (1=healthy, 0=unhealthy)"
)
CONSECUTIVE_FAILURES = Gauge(
    "failover_consecutive_failures",
    "Number of consecutive health check failures for primary"
)
SPLIT_BRAIN_DETECTED = Gauge(
    "split_brain_detected",
    "Whether a split-brain scenario was detected (1=yes, 0=no)"
)
IS_LEADER = Gauge(
    "failover_controller_is_leader",
    "Whether this controller is the leader (1=yes, 0=no)",
    ["controller_id"]
)
CURRENT_PRIMARY_GAUGE = Gauge(
    "current_primary_node",
    "Current primary node (encoded as index)",
    ["node_id"]
)
RTO_SECONDS = Gauge(
    "rto_seconds",
    "Recovery Time Objective - time from failure detection to recovery"
)
RPO_SEQUENCES = Gauge(
    "rpo_sequences_lost",
    "Recovery Point Objective - sequences potentially lost during failover"
)

# Global state
redis_client: redis.Redis = None
is_leader = False
primary_healthy = True
consecutive_failures = 0
current_primary_url = PRIMARY_URL
current_primary_id = "primary-1"
last_known_primary_sequence = 0
failover_in_progress = False
split_brain_active = False
monitoring_task = None
leader_task = None
failover_start_time: Optional[float] = None


class FailoverRequest(BaseModel):
    target_replica: str


class SplitBrainConfig(BaseModel):
    simulate: bool


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, monitoring_task, leader_task
    logger.info(f"{SERVICE_NAME} ({CONTROLLER_ID}) starting up")
    logger.info(f"Monitoring primary: {PRIMARY_URL}")
    logger.info(f"Available replicas: {REPLICA_URLS}")

    # Connect to Redis
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

    # Initialize current primary in Redis
    await redis_client.set("cluster:current_primary", "primary-1")
    await redis_client.set("cluster:current_primary_url", PRIMARY_URL)

    # Start background tasks
    leader_task = asyncio.create_task(leader_election_loop())
    monitoring_task = asyncio.create_task(health_monitoring_loop())

    # Set initial metrics
    PRIMARY_HEALTH_STATUS.set(1)
    CONSECUTIVE_FAILURES.set(0)
    SPLIT_BRAIN_DETECTED.set(0)
    IS_LEADER.labels(controller_id=CONTROLLER_ID).set(0)

    yield

    # Cleanup
    if monitoring_task:
        monitoring_task.cancel()
    if leader_task:
        leader_task.cancel()
    await redis_client.close()
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


async def leader_election_loop():
    """
    Simple leader election using Redis.
    Only the leader performs failover operations.
    """
    global is_leader

    while True:
        try:
            # Try to acquire leadership
            acquired = await redis_client.set(
                "failover:leader",
                CONTROLLER_ID,
                nx=True,
                px=LEADER_LEASE_TTL_MS
            )

            if acquired:
                is_leader = True
                IS_LEADER.labels(controller_id=CONTROLLER_ID).set(1)
            else:
                # Check if we're still the leader
                current_leader = await redis_client.get("failover:leader")
                if current_leader == CONTROLLER_ID:
                    # Refresh our lease
                    await redis_client.pexpire("failover:leader", LEADER_LEASE_TTL_MS)
                    is_leader = True
                    IS_LEADER.labels(controller_id=CONTROLLER_ID).set(1)
                else:
                    is_leader = False
                    IS_LEADER.labels(controller_id=CONTROLLER_ID).set(0)

            await asyncio.sleep(LEADER_LEASE_TTL_MS / 3000.0)  # Refresh at 1/3 of TTL

        except Exception as e:
            logger.error(f"Leader election error: {e}")
            is_leader = False
            IS_LEADER.labels(controller_id=CONTROLLER_ID).set(0)
            await asyncio.sleep(1)


async def health_monitoring_loop():
    """
    Continuously monitor primary health and trigger failover if needed.
    """
    global primary_healthy, consecutive_failures, current_primary_url
    global failover_start_time, last_known_primary_sequence

    while True:
        try:
            # Get current primary URL from Redis
            stored_url = await redis_client.get("cluster:current_primary_url")
            if stored_url:
                current_primary_url = stored_url

            # Perform health check
            healthy = await check_primary_health()

            if healthy:
                primary_healthy = True
                consecutive_failures = 0
                PRIMARY_HEALTH_STATUS.set(1)
                CONSECUTIVE_FAILURES.set(0)
                failover_start_time = None

                # Track primary sequence for RPO calculation
                try:
                    async with httpx.AsyncClient(timeout=2.0) as client:
                        resp = await client.get(f"{current_primary_url}/sequence")
                        if resp.status_code == 200:
                            last_known_primary_sequence = resp.json().get("sequence", 0)
                except Exception:
                    pass

            else:
                consecutive_failures += 1
                PRIMARY_HEALTH_STATUS.set(0)
                CONSECUTIVE_FAILURES.set(consecutive_failures)

                if failover_start_time is None:
                    failover_start_time = time.time()

                logger.warning(
                    f"Primary health check failed ({consecutive_failures}/{FAILOVER_THRESHOLD})"
                )

                # Only leader performs failover
                if is_leader and consecutive_failures >= FAILOVER_THRESHOLD and not failover_in_progress:
                    logger.error("FAILOVER THRESHOLD REACHED - Initiating automatic failover")
                    await perform_automatic_failover()

            await asyncio.sleep(HEALTH_CHECK_INTERVAL_MS / 1000.0)

        except Exception as e:
            logger.error(f"Health monitoring error: {e}")
            await asyncio.sleep(1)


async def check_primary_health() -> bool:
    """Check if the primary is healthy."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{current_primary_url}/health")
            healthy = response.status_code == 200
            HEALTH_CHECK_COUNT.labels(target="primary", result="success" if healthy else "failure").inc()
            return healthy
    except Exception as e:
        HEALTH_CHECK_COUNT.labels(target="primary", result="error").inc()
        logger.debug(f"Primary health check failed: {e}")
        return False


async def perform_automatic_failover():
    """Automatically failover to the best available replica."""
    global failover_in_progress, current_primary_url, current_primary_id
    global primary_healthy, consecutive_failures

    failover_in_progress = True
    failover_start = time.time()

    try:
        with tracer.start_as_current_span("automatic_failover") as span:
            # Find the best replica (highest sequence number, healthy)
            best_replica = None
            best_sequence = -1

            for replica_url in REPLICA_URLS:
                try:
                    async with httpx.AsyncClient(timeout=2.0) as client:
                        # Check health
                        health_resp = await client.get(f"{replica_url}/health")
                        if health_resp.status_code != 200:
                            continue

                        # Check sequence
                        seq_resp = await client.get(f"{replica_url}/sequence")
                        if seq_resp.status_code == 200:
                            seq_data = seq_resp.json()
                            sequence = seq_data.get("sequence", 0)
                            node_id = seq_data.get("node_id", "unknown")

                            if sequence > best_sequence:
                                best_sequence = sequence
                                best_replica = {
                                    "url": replica_url,
                                    "node_id": node_id,
                                    "sequence": sequence
                                }
                except Exception as e:
                    logger.warning(f"Failed to check replica {replica_url}: {e}")
                    continue

            if best_replica is None:
                logger.error("NO HEALTHY REPLICAS AVAILABLE - Failover failed!")
                span.set_attribute("failover_result", "no_healthy_replicas")
                return

            # Calculate RPO (potential data loss)
            rpo_lost = last_known_primary_sequence - best_replica["sequence"]
            RPO_SEQUENCES.set(max(0, rpo_lost))

            logger.info(f"Promoting replica: {best_replica['node_id']} (sequence: {best_replica['sequence']})")
            logger.info(f"Potential data loss: {rpo_lost} sequences")

            # Promote the replica
            async with httpx.AsyncClient(timeout=5.0) as client:
                promote_resp = await client.post(f"{best_replica['url']}/admin/promote")
                if promote_resp.status_code != 200:
                    logger.error(f"Failed to promote replica: {promote_resp.text}")
                    span.set_attribute("failover_result", "promotion_failed")
                    return

            # Update cluster state
            old_primary_id = current_primary_id
            current_primary_id = best_replica["node_id"]
            current_primary_url = best_replica["url"]

            await redis_client.set("cluster:current_primary", current_primary_id)
            await redis_client.set("cluster:current_primary_url", current_primary_url)

            # Calculate RTO
            rto = time.time() - (failover_start_time or failover_start)
            RTO_SECONDS.set(rto)

            # Record metrics
            failover_duration = time.time() - failover_start
            FAILOVER_DURATION.observe(failover_duration)
            FAILOVER_COUNT.labels(from_node=old_primary_id, to_node=current_primary_id).inc()
            CURRENT_PRIMARY_GAUGE.labels(node_id=current_primary_id).set(1)

            # Reset failure tracking
            primary_healthy = True
            consecutive_failures = 0
            PRIMARY_HEALTH_STATUS.set(1)
            CONSECUTIVE_FAILURES.set(0)

            span.set_attribute("failover_result", "success")
            span.set_attribute("new_primary", current_primary_id)
            span.set_attribute("failover_duration_ms", failover_duration * 1000)
            span.set_attribute("rto_seconds", rto)
            span.set_attribute("rpo_sequences", rpo_lost)

            logger.info(f"FAILOVER COMPLETE: New primary is {current_primary_id}")
            logger.info(f"RTO: {rto:.2f}s, RPO: {rpo_lost} sequences")

    except Exception as e:
        logger.error(f"Failover failed: {e}")
    finally:
        failover_in_progress = False


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "controller_id": CONTROLLER_ID,
        "is_leader": is_leader
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
async def status():
    """Detailed cluster status."""
    nodes = await redis_client.hgetall("cluster:nodes")
    current_primary = await redis_client.get("cluster:current_primary")
    current_leader = await redis_client.get("failover:leader")

    return {
        "controller_id": CONTROLLER_ID,
        "is_leader": is_leader,
        "current_leader": current_leader,
        "primary_healthy": primary_healthy,
        "consecutive_failures": consecutive_failures,
        "failover_threshold": FAILOVER_THRESHOLD,
        "current_primary": current_primary,
        "current_primary_url": current_primary_url,
        "failover_in_progress": failover_in_progress,
        "split_brain_detected": split_brain_active,
        "cluster_nodes": nodes,
        "monitored_replicas": REPLICA_URLS
    }


@app.get("/cluster/topology")
async def get_topology():
    """Get the current cluster topology."""
    nodes = await redis_client.hgetall("cluster:nodes")
    current_primary = await redis_client.get("cluster:current_primary")

    topology = {
        "primary": None,
        "replicas": []
    }

    for node_id, info in nodes.items():
        node_info = {"node_id": node_id, "info": info}
        if "primary=true" in info or node_id == current_primary:
            topology["primary"] = node_info
        else:
            topology["replicas"].append(node_info)

    return topology


@app.post("/failover/trigger")
async def trigger_failover(request: FailoverRequest):
    """Manually trigger failover to a specific replica."""
    global failover_in_progress, current_primary_url, current_primary_id

    if not is_leader:
        raise HTTPException(
            status_code=403,
            detail="Only the leader controller can trigger failover"
        )

    if failover_in_progress:
        raise HTTPException(
            status_code=409,
            detail="Failover already in progress"
        )

    target_url = None
    for url in REPLICA_URLS:
        if request.target_replica in url:
            target_url = url
            break

    if not target_url:
        raise HTTPException(
            status_code=404,
            detail=f"Replica not found: {request.target_replica}"
        )

    failover_in_progress = True
    failover_start = time.time()

    try:
        # Promote the specified replica
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Get replica info
            seq_resp = await client.get(f"{target_url}/sequence")
            if seq_resp.status_code != 200:
                raise HTTPException(status_code=500, detail="Failed to get replica status")

            seq_data = seq_resp.json()

            # Calculate potential data loss
            rpo_lost = last_known_primary_sequence - seq_data.get("sequence", 0)

            # Promote
            promote_resp = await client.post(f"{target_url}/admin/promote")
            if promote_resp.status_code != 200:
                raise HTTPException(status_code=500, detail="Failed to promote replica")

            promote_data = promote_resp.json()

        # Update state
        old_primary = current_primary_id
        current_primary_id = seq_data.get("node_id", request.target_replica)
        current_primary_url = target_url

        await redis_client.set("cluster:current_primary", current_primary_id)
        await redis_client.set("cluster:current_primary_url", current_primary_url)

        failover_duration = time.time() - failover_start
        FAILOVER_DURATION.observe(failover_duration)
        FAILOVER_COUNT.labels(from_node=old_primary, to_node=current_primary_id).inc()
        RPO_SEQUENCES.set(max(0, rpo_lost))

        return {
            "status": "success",
            "old_primary": old_primary,
            "new_primary": current_primary_id,
            "failover_duration_ms": round(failover_duration * 1000, 2),
            "potential_data_loss_sequences": rpo_lost
        }

    finally:
        failover_in_progress = False


@app.post("/admin/simulate-split-brain")
async def simulate_split_brain(config: SplitBrainConfig):
    """Simulate a split-brain scenario for testing."""
    global split_brain_active

    split_brain_active = config.simulate
    SPLIT_BRAIN_DETECTED.set(1 if config.simulate else 0)

    if config.simulate:
        logger.warning("SPLIT-BRAIN SIMULATION ACTIVATED")
        # In a real scenario, we'd detect multiple primaries
        # Here we'll just set the metric
    else:
        logger.info("Split-brain simulation deactivated")

    return {
        "status": "ok",
        "split_brain_simulated": split_brain_active
    }


@app.get("/admin/split-brain-status")
async def get_split_brain_status():
    """Check for split-brain scenarios."""
    # In a real system, we'd check if multiple nodes think they're primary
    nodes = await redis_client.hgetall("cluster:nodes")

    primaries = []
    for node_id, info in nodes.items():
        if "primary=true" in info:
            primaries.append(node_id)

    detected = len(primaries) > 1 or split_brain_active

    if detected and not split_brain_active:
        SPLIT_BRAIN_DETECTED.set(1)
        logger.error(f"SPLIT-BRAIN DETECTED: Multiple primaries: {primaries}")

    return {
        "split_brain_detected": detected,
        "simulated": split_brain_active,
        "primaries_found": primaries
    }


@app.post("/admin/reset-cluster")
async def reset_cluster():
    """Reset cluster state to initial configuration."""
    global current_primary_url, current_primary_id, primary_healthy
    global consecutive_failures, split_brain_active

    # Reset to original primary
    current_primary_url = PRIMARY_URL
    current_primary_id = "primary-1"
    primary_healthy = True
    consecutive_failures = 0
    split_brain_active = False

    # Update Redis
    await redis_client.set("cluster:current_primary", "primary-1")
    await redis_client.set("cluster:current_primary_url", PRIMARY_URL)

    # Demote any promoted replicas
    for replica_url in REPLICA_URLS:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.post(f"{replica_url}/admin/demote")
        except Exception:
            pass

    # Reset metrics
    PRIMARY_HEALTH_STATUS.set(1)
    CONSECUTIVE_FAILURES.set(0)
    SPLIT_BRAIN_DETECTED.set(0)

    logger.info("Cluster state reset to initial configuration")

    return {
        "status": "ok",
        "message": "Cluster reset to initial state",
        "current_primary": current_primary_id
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8010)
