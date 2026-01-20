"""
Two-Phase Commit Coordinator - Manages distributed transactions.

This coordinator demonstrates the 2PC protocol and its blocking nature.
It can be configured to fail at various points to demonstrate why 2PC is problematic.
"""
import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
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
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from pydantic import BaseModel


# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "coordinator")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
PARTICIPANT_URLS = [
    os.getenv("PARTICIPANT_A_URL", "http://participant-a:8001"),
    os.getenv("PARTICIPANT_B_URL", "http://participant-b:8002"),
    os.getenv("PARTICIPANT_C_URL", "http://participant-c:8003"),
]
PREPARE_TIMEOUT_MS = int(os.getenv("PREPARE_TIMEOUT_MS", "5000"))
COMMIT_TIMEOUT_MS = int(os.getenv("COMMIT_TIMEOUT_MS", "5000"))

# Failure injection
FAIL_AFTER_PREPARE = os.getenv("FAIL_AFTER_PREPARE", "false").lower() == "true"
FAIL_BEFORE_COMMIT = os.getenv("FAIL_BEFORE_COMMIT", "false").lower() == "true"

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
TRANSACTION_COUNT = Counter(
    "twopc_transactions_total",
    "Total 2PC transactions",
    ["status"]  # committed, aborted, blocked
)
PREPARE_LATENCY = Histogram(
    "twopc_prepare_duration_seconds",
    "2PC prepare phase latency",
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)
COMMIT_LATENCY = Histogram(
    "twopc_commit_duration_seconds",
    "2PC commit phase latency",
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)
BLOCKED_TRANSACTIONS = Gauge(
    "twopc_blocked_transactions",
    "Number of transactions in blocking state"
)
PARTICIPANT_VOTE_COUNT = Counter(
    "twopc_participant_votes_total",
    "Participant votes during prepare",
    ["participant", "vote"]  # yes, no, timeout
)


class TransactionState(str, Enum):
    INITIAL = "initial"
    PREPARING = "preparing"
    PREPARED = "prepared"  # All voted yes
    COMMITTING = "committing"
    COMMITTED = "committed"
    ABORTING = "aborting"
    ABORTED = "aborted"
    BLOCKED = "blocked"  # Coordinator failed, participants uncertain


@dataclass
class ParticipantState:
    url: str
    vote: Optional[str] = None  # "yes", "no", or None (timeout)
    committed: bool = False
    aborted: bool = False


@dataclass
class Transaction:
    id: str
    state: TransactionState
    participants: List[ParticipantState]
    data: dict
    created_at: float = field(default_factory=time.time)
    prepared_at: Optional[float] = None
    completed_at: Optional[float] = None


# Transaction log (simulated durable storage)
transaction_log: Dict[str, Transaction] = {}

# Runtime state for failure injection
runtime_config = {
    "fail_after_prepare": FAIL_AFTER_PREPARE,
    "fail_before_commit": FAIL_BEFORE_COMMIT,
    "block_duration_ms": 0,  # How long to block between prepare and commit
}


class TransactionRequest(BaseModel):
    data: dict


class FailureConfig(BaseModel):
    fail_after_prepare: Optional[bool] = None
    fail_before_commit: Optional[bool] = None
    block_duration_ms: Optional[int] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Participants: {PARTICIPANT_URLS}")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title="2PC Coordinator", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/transaction")
async def create_transaction(request: TransactionRequest):
    """
    Execute a distributed transaction using Two-Phase Commit.

    Phase 1 (Prepare): Ask all participants if they can commit
    Phase 2 (Commit/Abort): Tell all participants to commit or abort based on votes
    """
    txn_id = str(uuid.uuid4())

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")
    current_span.set_attribute("transaction.id", txn_id)

    # Initialize transaction
    txn = Transaction(
        id=txn_id,
        state=TransactionState.INITIAL,
        participants=[ParticipantState(url=url) for url in PARTICIPANT_URLS],
        data=request.data
    )
    transaction_log[txn_id] = txn

    logger.info(f"Starting transaction {txn_id} with data: {request.data}")

    try:
        # ===== PHASE 1: PREPARE =====
        with tracer.start_as_current_span("prepare_phase") as prepare_span:
            prepare_span.set_attribute("transaction.id", txn_id)
            prepare_span.set_attribute("participant.count", len(txn.participants))

            txn.state = TransactionState.PREPARING
            prepare_start = time.time()

            all_voted_yes = await prepare_phase(txn)

            prepare_duration = time.time() - prepare_start
            PREPARE_LATENCY.observe(prepare_duration)
            prepare_span.set_attribute("prepare.duration_ms", prepare_duration * 1000)
            prepare_span.set_attribute("prepare.all_voted_yes", all_voted_yes)

            if all_voted_yes:
                txn.state = TransactionState.PREPARED
                txn.prepared_at = time.time()
                logger.info(f"Transaction {txn_id}: All participants voted YES")
            else:
                txn.state = TransactionState.ABORTING
                logger.info(f"Transaction {txn_id}: At least one participant voted NO or timed out")

        # Check for coordinator failure simulation AFTER prepare
        if runtime_config["fail_after_prepare"] and txn.state == TransactionState.PREPARED:
            BLOCKED_TRANSACTIONS.inc()
            txn.state = TransactionState.BLOCKED
            TRANSACTION_COUNT.labels(status="blocked").inc()
            logger.warning(f"Transaction {txn_id}: Coordinator FAILED after prepare! Participants are BLOCKED!")
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "coordinator_failed_after_prepare",
                    "transaction_id": txn_id,
                    "message": "Coordinator crashed after all participants voted YES but before sending COMMIT. "
                              "All participants are now BLOCKED, holding locks, unable to proceed!",
                    "participants_state": "uncertain - holding locks"
                }
            )

        # Simulate blocking delay
        if runtime_config["block_duration_ms"] > 0 and txn.state == TransactionState.PREPARED:
            logger.warning(f"Transaction {txn_id}: Simulating {runtime_config['block_duration_ms']}ms blocking delay")
            await asyncio.sleep(runtime_config["block_duration_ms"] / 1000.0)

        # Check for coordinator failure simulation BEFORE commit
        if runtime_config["fail_before_commit"] and txn.state == TransactionState.PREPARED:
            BLOCKED_TRANSACTIONS.inc()
            txn.state = TransactionState.BLOCKED
            TRANSACTION_COUNT.labels(status="blocked").inc()
            logger.warning(f"Transaction {txn_id}: Coordinator FAILED before commit!")
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "coordinator_failed_before_commit",
                    "transaction_id": txn_id,
                    "message": "Coordinator failed just before sending COMMIT. Participants are BLOCKED!",
                    "participants_state": "uncertain - holding locks"
                }
            )

        # ===== PHASE 2: COMMIT or ABORT =====
        with tracer.start_as_current_span("decision_phase") as decision_span:
            decision_span.set_attribute("transaction.id", txn_id)

            commit_start = time.time()

            if txn.state == TransactionState.PREPARED:
                # All voted yes - COMMIT
                decision_span.set_attribute("decision", "commit")
                txn.state = TransactionState.COMMITTING
                await commit_phase(txn)
                txn.state = TransactionState.COMMITTED
                txn.completed_at = time.time()
                TRANSACTION_COUNT.labels(status="committed").inc()
                logger.info(f"Transaction {txn_id}: COMMITTED")
            else:
                # At least one voted no or timeout - ABORT
                decision_span.set_attribute("decision", "abort")
                txn.state = TransactionState.ABORTING
                await abort_phase(txn)
                txn.state = TransactionState.ABORTED
                txn.completed_at = time.time()
                TRANSACTION_COUNT.labels(status="aborted").inc()
                logger.info(f"Transaction {txn_id}: ABORTED")

            commit_duration = time.time() - commit_start
            COMMIT_LATENCY.observe(commit_duration)
            decision_span.set_attribute("decision.duration_ms", commit_duration * 1000)

        total_duration = (txn.completed_at - txn.created_at) * 1000

        return {
            "transaction_id": txn_id,
            "trace_id": trace_id,
            "status": txn.state.value,
            "total_duration_ms": round(total_duration, 2),
            "participants": [
                {
                    "url": p.url,
                    "vote": p.vote,
                    "committed": p.committed,
                    "aborted": p.aborted
                }
                for p in txn.participants
            ],
            "data": txn.data
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Transaction {txn_id} failed: {e}")
        txn.state = TransactionState.ABORTED
        TRANSACTION_COUNT.labels(status="aborted").inc()
        raise HTTPException(status_code=500, detail=str(e))


async def prepare_phase(txn: Transaction) -> bool:
    """
    Phase 1: Ask all participants to prepare.
    Returns True if all voted YES, False otherwise.
    """
    async def prepare_participant(participant: ParticipantState) -> bool:
        try:
            async with httpx.AsyncClient(timeout=PREPARE_TIMEOUT_MS / 1000.0) as client:
                response = await client.post(
                    f"{participant.url}/prepare",
                    json={"transaction_id": txn.id, "data": txn.data}
                )
                result = response.json()
                participant.vote = result.get("vote", "no")

                participant_name = participant.url.split("/")[-1].split(":")[0]
                PARTICIPANT_VOTE_COUNT.labels(
                    participant=participant_name,
                    vote=participant.vote
                ).inc()

                logger.info(f"Transaction {txn.id}: {participant.url} voted {participant.vote}")
                return participant.vote == "yes"

        except httpx.TimeoutException:
            participant.vote = None
            participant_name = participant.url.split("/")[-1].split(":")[0]
            PARTICIPANT_VOTE_COUNT.labels(participant=participant_name, vote="timeout").inc()
            logger.warning(f"Transaction {txn.id}: {participant.url} TIMEOUT during prepare")
            return False
        except Exception as e:
            participant.vote = None
            logger.error(f"Transaction {txn.id}: {participant.url} error during prepare: {e}")
            return False

    # Ask all participants in parallel
    results = await asyncio.gather(*[prepare_participant(p) for p in txn.participants])
    return all(results)


async def commit_phase(txn: Transaction):
    """
    Phase 2a: Tell all participants to commit.
    """
    async def commit_participant(participant: ParticipantState):
        try:
            async with httpx.AsyncClient(timeout=COMMIT_TIMEOUT_MS / 1000.0) as client:
                response = await client.post(
                    f"{participant.url}/commit",
                    json={"transaction_id": txn.id}
                )
                result = response.json()
                participant.committed = result.get("committed", False)
                logger.info(f"Transaction {txn.id}: {participant.url} committed: {participant.committed}")
        except Exception as e:
            logger.error(f"Transaction {txn.id}: {participant.url} error during commit: {e}")
            # In 2PC, we must keep retrying commit - this is why it's blocking!

    await asyncio.gather(*[commit_participant(p) for p in txn.participants])


async def abort_phase(txn: Transaction):
    """
    Phase 2b: Tell all participants to abort.
    """
    async def abort_participant(participant: ParticipantState):
        try:
            async with httpx.AsyncClient(timeout=COMMIT_TIMEOUT_MS / 1000.0) as client:
                response = await client.post(
                    f"{participant.url}/abort",
                    json={"transaction_id": txn.id}
                )
                result = response.json()
                participant.aborted = result.get("aborted", False)
                logger.info(f"Transaction {txn.id}: {participant.url} aborted: {participant.aborted}")
        except Exception as e:
            logger.error(f"Transaction {txn.id}: {participant.url} error during abort: {e}")

    await asyncio.gather(*[abort_participant(p) for p in txn.participants])


@app.get("/transactions")
async def list_transactions():
    """List all transactions and their states."""
    return {
        "transactions": [
            {
                "id": txn.id,
                "state": txn.state.value,
                "created_at": txn.created_at,
                "completed_at": txn.completed_at,
                "data": txn.data
            }
            for txn in transaction_log.values()
        ]
    }


@app.get("/transactions/{txn_id}")
async def get_transaction(txn_id: str):
    """Get details of a specific transaction."""
    if txn_id not in transaction_log:
        raise HTTPException(status_code=404, detail="Transaction not found")

    txn = transaction_log[txn_id]
    return {
        "id": txn.id,
        "state": txn.state.value,
        "created_at": txn.created_at,
        "prepared_at": txn.prepared_at,
        "completed_at": txn.completed_at,
        "participants": [
            {
                "url": p.url,
                "vote": p.vote,
                "committed": p.committed,
                "aborted": p.aborted
            }
            for p in txn.participants
        ],
        "data": txn.data
    }


@app.get("/admin/config")
async def get_config():
    """Get current failure injection configuration."""
    return runtime_config


@app.post("/admin/config")
async def set_config(config: FailureConfig):
    """Set failure injection configuration."""
    if config.fail_after_prepare is not None:
        runtime_config["fail_after_prepare"] = config.fail_after_prepare
    if config.fail_before_commit is not None:
        runtime_config["fail_before_commit"] = config.fail_before_commit
    if config.block_duration_ms is not None:
        runtime_config["block_duration_ms"] = config.block_duration_ms

    logger.info(f"Updated config: {runtime_config}")
    return runtime_config


@app.post("/admin/reset")
async def reset_state():
    """Reset all state and metrics."""
    global transaction_log
    transaction_log = {}
    BLOCKED_TRANSACTIONS.set(0)
    runtime_config["fail_after_prepare"] = False
    runtime_config["fail_before_commit"] = False
    runtime_config["block_duration_ms"] = 0
    logger.info("State reset")
    return {"status": "reset"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
