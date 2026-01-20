"""
Two-Phase Commit Participant - Simulates a database that participates in 2PC.

Each participant maintains a "prepared" state where it has taken locks
and is ready to commit but waiting for the coordinator's decision.
This is the blocking state that makes 2PC problematic.
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from pydantic import BaseModel


# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "participant")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
SERVICE_PORT = int(os.getenv("SERVICE_PORT", "8001"))
PARTICIPANT_ID = os.getenv("PARTICIPANT_ID", "participant")

# Failure injection
FAIL_PREPARE_PROBABILITY = float(os.getenv("FAIL_PREPARE_PROBABILITY", "0"))
FAIL_COMMIT_PROBABILITY = float(os.getenv("FAIL_COMMIT_PROBABILITY", "0"))
PREPARE_LATENCY_MS = int(os.getenv("PREPARE_LATENCY_MS", "50"))
COMMIT_LATENCY_MS = int(os.getenv("COMMIT_LATENCY_MS", "30"))

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
PREPARE_COUNT = Counter(
    "participant_prepare_total",
    "Total prepare requests",
    ["participant", "result"]  # success, failure
)
COMMIT_COUNT = Counter(
    "participant_commit_total",
    "Total commit requests",
    ["participant", "result"]
)
ABORT_COUNT = Counter(
    "participant_abort_total",
    "Total abort requests",
    ["participant"]
)
LOCKED_TRANSACTIONS = Gauge(
    "participant_locked_transactions",
    "Transactions in prepared (locked) state",
    ["participant"]
)
PREPARE_LATENCY = Histogram(
    "participant_prepare_duration_seconds",
    "Prepare phase latency",
    ["participant"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5]
)


class TransactionState(str, Enum):
    NONE = "none"
    PREPARED = "prepared"  # Holding locks, waiting for decision
    COMMITTED = "committed"
    ABORTED = "aborted"


@dataclass
class LocalTransaction:
    id: str
    state: TransactionState
    data: dict
    prepared_at: Optional[float] = None
    completed_at: Optional[float] = None


# Local transaction log (simulated durable storage)
local_transactions: Dict[str, LocalTransaction] = {}

# Runtime configuration for failure injection
runtime_config = {
    "fail_prepare_probability": FAIL_PREPARE_PROBABILITY,
    "fail_commit_probability": FAIL_COMMIT_PROBABILITY,
    "prepare_latency_ms": PREPARE_LATENCY_MS,
    "commit_latency_ms": COMMIT_LATENCY_MS,
    "freeze": False,  # Simulate being frozen/unresponsive
}


class PrepareRequest(BaseModel):
    transaction_id: str
    data: dict


class CommitRequest(BaseModel):
    transaction_id: str


class AbortRequest(BaseModel):
    transaction_id: str


class FailureConfig(BaseModel):
    fail_prepare_probability: Optional[float] = None
    fail_commit_probability: Optional[float] = None
    prepare_latency_ms: Optional[int] = None
    commit_latency_ms: Optional[int] = None
    freeze: Optional[bool] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} ({PARTICIPANT_ID}) starting up on port {SERVICE_PORT}")
    yield
    logger.info(f"{SERVICE_NAME} ({PARTICIPANT_ID}) shutting down")


app = FastAPI(title=f"2PC Participant - {PARTICIPANT_ID}", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME, "participant_id": PARTICIPANT_ID}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/prepare")
async def prepare(request: PrepareRequest):
    """
    Phase 1: Prepare to commit.

    The participant must:
    1. Check if it can commit the transaction
    2. Take locks on the affected resources
    3. Write to durable storage (WAL) that it voted YES
    4. Wait for the coordinator's decision

    If anything fails, vote NO.
    """
    current_span = trace.get_current_span()
    current_span.set_attribute("transaction.id", request.transaction_id)
    current_span.set_attribute("participant.id", PARTICIPANT_ID)

    # Check if frozen (simulating unresponsive participant)
    if runtime_config["freeze"]:
        logger.warning(f"Transaction {request.transaction_id}: Participant is FROZEN, not responding")
        await asyncio.sleep(300)  # 5 minute timeout to simulate complete unresponsiveness

    start_time = time.time()

    # Simulate prepare latency (acquiring locks, writing WAL, etc.)
    latency_ms = runtime_config["prepare_latency_ms"]
    await asyncio.sleep(latency_ms / 1000.0)

    # Check for injected failure
    if random.random() < runtime_config["fail_prepare_probability"]:
        logger.warning(f"Transaction {request.transaction_id}: Voting NO (injected failure)")
        PREPARE_COUNT.labels(participant=PARTICIPANT_ID, result="failure").inc()
        duration = time.time() - start_time
        PREPARE_LATENCY.labels(participant=PARTICIPANT_ID).observe(duration)
        return {
            "transaction_id": request.transaction_id,
            "participant": PARTICIPANT_ID,
            "vote": "no",
            "reason": "injected_failure"
        }

    # Create local transaction in PREPARED state
    txn = LocalTransaction(
        id=request.transaction_id,
        state=TransactionState.PREPARED,
        data=request.data,
        prepared_at=time.time()
    )
    local_transactions[request.transaction_id] = txn

    # This is the critical state: we've voted YES and are now BLOCKING
    # We're holding locks and waiting for the coordinator
    LOCKED_TRANSACTIONS.labels(participant=PARTICIPANT_ID).inc()

    logger.info(f"Transaction {request.transaction_id}: Voting YES, now in PREPARED state (holding locks)")

    PREPARE_COUNT.labels(participant=PARTICIPANT_ID, result="success").inc()
    duration = time.time() - start_time
    PREPARE_LATENCY.labels(participant=PARTICIPANT_ID).observe(duration)

    current_span.set_attribute("vote", "yes")
    current_span.set_attribute("prepare.duration_ms", duration * 1000)

    return {
        "transaction_id": request.transaction_id,
        "participant": PARTICIPANT_ID,
        "vote": "yes",
        "locked_at": txn.prepared_at
    }


@app.post("/commit")
async def commit(request: CommitRequest):
    """
    Phase 2a: Commit the transaction.

    The participant must:
    1. Make the changes permanent
    2. Release all locks
    """
    current_span = trace.get_current_span()
    current_span.set_attribute("transaction.id", request.transaction_id)
    current_span.set_attribute("participant.id", PARTICIPANT_ID)

    if runtime_config["freeze"]:
        logger.warning(f"Transaction {request.transaction_id}: Participant is FROZEN, not responding to commit")
        await asyncio.sleep(300)

    txn = local_transactions.get(request.transaction_id)
    if not txn:
        logger.warning(f"Transaction {request.transaction_id}: Not found locally, assuming already committed")
        return {
            "transaction_id": request.transaction_id,
            "participant": PARTICIPANT_ID,
            "committed": True,
            "note": "transaction_not_found_assuming_committed"
        }

    if txn.state != TransactionState.PREPARED:
        logger.warning(f"Transaction {request.transaction_id}: Cannot commit, state is {txn.state}")
        return {
            "transaction_id": request.transaction_id,
            "participant": PARTICIPANT_ID,
            "committed": False,
            "reason": f"invalid_state_{txn.state}"
        }

    # Check for injected commit failure
    if random.random() < runtime_config["fail_commit_probability"]:
        logger.error(f"Transaction {request.transaction_id}: Commit FAILED (injected)")
        COMMIT_COUNT.labels(participant=PARTICIPANT_ID, result="failure").inc()
        return {
            "transaction_id": request.transaction_id,
            "participant": PARTICIPANT_ID,
            "committed": False,
            "reason": "injected_failure"
        }

    # Simulate commit latency
    latency_ms = runtime_config["commit_latency_ms"]
    await asyncio.sleep(latency_ms / 1000.0)

    # Commit the transaction
    txn.state = TransactionState.COMMITTED
    txn.completed_at = time.time()

    # Release locks
    LOCKED_TRANSACTIONS.labels(participant=PARTICIPANT_ID).dec()

    lock_duration = (txn.completed_at - txn.prepared_at) * 1000 if txn.prepared_at else 0
    logger.info(f"Transaction {request.transaction_id}: COMMITTED (locks held for {lock_duration:.0f}ms)")

    COMMIT_COUNT.labels(participant=PARTICIPANT_ID, result="success").inc()
    current_span.set_attribute("lock_duration_ms", lock_duration)

    return {
        "transaction_id": request.transaction_id,
        "participant": PARTICIPANT_ID,
        "committed": True,
        "lock_duration_ms": round(lock_duration, 2)
    }


@app.post("/abort")
async def abort(request: AbortRequest):
    """
    Phase 2b: Abort the transaction.

    The participant must:
    1. Rollback any changes
    2. Release all locks
    """
    current_span = trace.get_current_span()
    current_span.set_attribute("transaction.id", request.transaction_id)
    current_span.set_attribute("participant.id", PARTICIPANT_ID)

    if runtime_config["freeze"]:
        logger.warning(f"Transaction {request.transaction_id}: Participant is FROZEN, not responding to abort")
        await asyncio.sleep(300)

    txn = local_transactions.get(request.transaction_id)
    if not txn:
        logger.info(f"Transaction {request.transaction_id}: Not found locally, nothing to abort")
        return {
            "transaction_id": request.transaction_id,
            "participant": PARTICIPANT_ID,
            "aborted": True,
            "note": "transaction_not_found"
        }

    # Abort and release locks
    was_prepared = txn.state == TransactionState.PREPARED
    txn.state = TransactionState.ABORTED
    txn.completed_at = time.time()

    if was_prepared:
        LOCKED_TRANSACTIONS.labels(participant=PARTICIPANT_ID).dec()

    lock_duration = (txn.completed_at - txn.prepared_at) * 1000 if txn.prepared_at else 0
    logger.info(f"Transaction {request.transaction_id}: ABORTED (locks held for {lock_duration:.0f}ms)")

    ABORT_COUNT.labels(participant=PARTICIPANT_ID).inc()

    return {
        "transaction_id": request.transaction_id,
        "participant": PARTICIPANT_ID,
        "aborted": True,
        "lock_duration_ms": round(lock_duration, 2)
    }


@app.get("/transactions")
async def list_transactions():
    """List all local transactions."""
    return {
        "participant": PARTICIPANT_ID,
        "transactions": [
            {
                "id": txn.id,
                "state": txn.state.value,
                "prepared_at": txn.prepared_at,
                "completed_at": txn.completed_at,
                "data": txn.data
            }
            for txn in local_transactions.values()
        ]
    }


@app.get("/blocked")
async def get_blocked_transactions():
    """Get transactions that are in PREPARED state (blocking)."""
    blocked = [
        {
            "id": txn.id,
            "prepared_at": txn.prepared_at,
            "waiting_seconds": time.time() - txn.prepared_at if txn.prepared_at else 0,
            "data": txn.data
        }
        for txn in local_transactions.values()
        if txn.state == TransactionState.PREPARED
    ]
    return {
        "participant": PARTICIPANT_ID,
        "blocked_count": len(blocked),
        "blocked_transactions": blocked
    }


@app.get("/admin/config")
async def get_config():
    """Get current configuration."""
    return {
        "participant": PARTICIPANT_ID,
        **runtime_config
    }


@app.post("/admin/config")
async def set_config(config: FailureConfig):
    """Set failure injection configuration."""
    if config.fail_prepare_probability is not None:
        runtime_config["fail_prepare_probability"] = config.fail_prepare_probability
    if config.fail_commit_probability is not None:
        runtime_config["fail_commit_probability"] = config.fail_commit_probability
    if config.prepare_latency_ms is not None:
        runtime_config["prepare_latency_ms"] = config.prepare_latency_ms
    if config.commit_latency_ms is not None:
        runtime_config["commit_latency_ms"] = config.commit_latency_ms
    if config.freeze is not None:
        runtime_config["freeze"] = config.freeze

    logger.info(f"Updated config: {runtime_config}")
    return {"participant": PARTICIPANT_ID, **runtime_config}


@app.post("/admin/reset")
async def reset_state():
    """Reset all state."""
    global local_transactions
    local_transactions = {}
    LOCKED_TRANSACTIONS.labels(participant=PARTICIPANT_ID).set(0)
    runtime_config["fail_prepare_probability"] = 0
    runtime_config["fail_commit_probability"] = 0
    runtime_config["prepare_latency_ms"] = PREPARE_LATENCY_MS
    runtime_config["commit_latency_ms"] = COMMIT_LATENCY_MS
    runtime_config["freeze"] = False
    logger.info("State reset")
    return {"status": "reset", "participant": PARTICIPANT_ID}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
