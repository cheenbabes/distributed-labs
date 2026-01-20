"""
Bank API - Demonstrates race conditions in financial transfers.

This service intentionally contains a check-then-act race condition
in the transfer endpoint. The vulnerability can be exploited when
concurrent transfers exceed the account balance.
"""
import asyncio
import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# =============================================================================
# Configuration
# =============================================================================

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "bank-api")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab21-otel-collector:4317")

# Debug delay widens the race window
DEBUG_DELAY_ENABLED = os.getenv("DEBUG_DELAY_ENABLED", "false").lower() == "true"
DEBUG_DELAY_MS = int(os.getenv("DEBUG_DELAY_MS", "100"))

# Barrier synchronization for controlled reproduction
BARRIER_ENABLED = os.getenv("BARRIER_ENABLED", "false").lower() == "true"
BARRIER_COUNT = int(os.getenv("BARRIER_COUNT", "2"))

# Fix mode: use proper locking
USE_TRANSACTIONS = os.getenv("USE_TRANSACTIONS", "false").lower() == "true"

# =============================================================================
# Logging
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d %(levelname)s [%(name)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(SERVICE_NAME)

# =============================================================================
# OpenTelemetry Setup
# =============================================================================

resource = Resource.create({"service.name": SERVICE_NAME})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# =============================================================================
# Prometheus Metrics
# =============================================================================

TRANSFERS_TOTAL = Counter(
    "transfers_total",
    "Total transfer attempts",
    ["status", "service"]
)

RACE_CONDITION_HITS = Counter(
    "race_condition_hits_total",
    "Number of times race condition was detected (negative balance)",
    ["service"]
)

DOUBLE_SPEND_DETECTED = Counter(
    "double_spend_detected_total",
    "Number of double-spend events detected",
    ["service"]
)

ACCOUNT_BALANCE = Gauge(
    "account_balance",
    "Current account balance",
    ["account_id", "service"]
)

TRANSFER_LATENCY = Histogram(
    "transfer_duration_seconds",
    "Transfer operation latency",
    ["service"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

CONCURRENT_TRANSFERS = Gauge(
    "concurrent_transfers",
    "Number of transfers currently in progress",
    ["service"]
)

BARRIER_WAITING = Gauge(
    "barrier_waiting_count",
    "Number of requests waiting at barrier",
    ["service"]
)

# =============================================================================
# Data Models
# =============================================================================

class TransferRequest(BaseModel):
    from_account: str
    to_account: str
    amount: float


class DebugSettings(BaseModel):
    delay_enabled: bool
    delay_ms: int
    barrier_enabled: bool
    barrier_count: int
    use_transactions: bool


class TransactionLog(BaseModel):
    id: str
    timestamp: str
    from_account: str
    to_account: str
    amount: float
    from_balance_before: float
    from_balance_after: float
    to_balance_before: float
    to_balance_after: float
    status: str
    thread_id: str
    duration_ms: float


@dataclass
class Account:
    id: str
    balance: float
    lock: threading.Lock = field(default_factory=threading.Lock)


# =============================================================================
# In-Memory Database (simulates persistent storage)
# =============================================================================

class BankDatabase:
    def __init__(self):
        self.accounts: Dict[str, Account] = {}
        self.transaction_log: List[TransactionLog] = []
        self.global_lock = threading.Lock()
        self._init_default_accounts()

    def _init_default_accounts(self):
        """Initialize default accounts for the demo."""
        self.accounts["alice"] = Account(id="alice", balance=1000.0)
        self.accounts["bob"] = Account(id="bob", balance=500.0)
        self.accounts["charlie"] = Account(id="charlie", balance=200.0)

        # Update metrics
        for acc_id, acc in self.accounts.items():
            ACCOUNT_BALANCE.labels(account_id=acc_id, service=SERVICE_NAME).set(acc.balance)

    def reset(self):
        """Reset all accounts to initial state."""
        self._init_default_accounts()
        self.transaction_log.clear()
        logger.info("Database reset to initial state")

    def get_balance(self, account_id: str) -> float:
        """Get current balance for an account."""
        if account_id not in self.accounts:
            raise ValueError(f"Account {account_id} not found")
        return self.accounts[account_id].balance

    def get_account(self, account_id: str) -> Account:
        """Get account object."""
        if account_id not in self.accounts:
            raise ValueError(f"Account {account_id} not found")
        return self.accounts[account_id]


db = BankDatabase()

# =============================================================================
# Barrier Synchronization for Controlled Race Reproduction
# =============================================================================

class TransferBarrier:
    """
    A barrier that forces concurrent transfers to execute simultaneously.
    When enabled, transfers wait until barrier_count requests arrive,
    then all proceed at once - guaranteeing the race condition triggers.
    """
    def __init__(self):
        self.waiting = 0
        self.condition = threading.Condition()
        self.enabled = BARRIER_ENABLED
        self.count = BARRIER_COUNT

    def configure(self, enabled: bool, count: int):
        with self.condition:
            self.enabled = enabled
            self.count = count
            self.waiting = 0
            self.condition.notify_all()

    async def wait(self, transfer_id: str):
        """
        Wait at the barrier until enough concurrent requests arrive.
        Uses threading primitives wrapped in asyncio for demo purposes.
        """
        if not self.enabled:
            return

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_wait, transfer_id)

    def _sync_wait(self, transfer_id: str):
        with self.condition:
            self.waiting += 1
            BARRIER_WAITING.labels(service=SERVICE_NAME).set(self.waiting)
            logger.info(f"[{transfer_id}] Waiting at barrier ({self.waiting}/{self.count})")

            if self.waiting >= self.count:
                logger.warning(f"[{transfer_id}] BARRIER RELEASED - All {self.count} transfers proceed NOW!")
                self.waiting = 0
                BARRIER_WAITING.labels(service=SERVICE_NAME).set(0)
                self.condition.notify_all()
            else:
                # Wait until enough requests arrive
                self.condition.wait(timeout=30.0)

            logger.info(f"[{transfer_id}] Proceeding past barrier")


barrier = TransferBarrier()

# =============================================================================
# Transfer Logic - THE RACE CONDITION IS HERE
# =============================================================================

async def transfer_vulnerable(
    from_account: str,
    to_account: str,
    amount: float,
    transfer_id: str,
    span
) -> TransactionLog:
    """
    VULNERABLE: Check-then-act race condition.

    The bug: We read the balance, check if sufficient, then update.
    Between check and update, another thread can also read the same
    balance, leading to double-spending.

    Timeline showing the bug:

    Thread A                          Thread B
    --------                          --------
    read balance (1000)
                                      read balance (1000)  <- SAME VALUE!
    check: 1000 >= 800? YES
                                      check: 1000 >= 800? YES
    deduct: balance = 200
                                      deduct: balance = 200  <- WRONG!

    Result: Two $800 transfers succeeded from a $1000 account!
    """
    start_time = time.time()
    thread_id = threading.current_thread().name

    # Get account objects
    from_acc = db.get_account(from_account)
    to_acc = db.get_account(to_account)

    # Record balances BEFORE the operation
    from_balance_before = from_acc.balance
    to_balance_before = to_acc.balance

    span.set_attribute("transfer.from_account", from_account)
    span.set_attribute("transfer.to_account", to_account)
    span.set_attribute("transfer.amount", amount)
    span.set_attribute("transfer.from_balance_before", from_balance_before)
    span.set_attribute("transfer.thread_id", thread_id)

    logger.info(f"[{transfer_id}] STEP 1: Read balance for {from_account} = ${from_balance_before:.2f}")

    # =========================================================================
    # THE RACE WINDOW STARTS HERE
    # =========================================================================

    # Wait at barrier if enabled (for controlled reproduction)
    await barrier.wait(transfer_id)

    # Debug delay: artificially widen the race window
    global DEBUG_DELAY_ENABLED, DEBUG_DELAY_MS
    if DEBUG_DELAY_ENABLED:
        logger.warning(f"[{transfer_id}] DEBUG DELAY: Sleeping {DEBUG_DELAY_MS}ms to widen race window...")
        await asyncio.sleep(DEBUG_DELAY_MS / 1000.0)

    # VULNERABLE CHECK: Read balance (may be stale!)
    current_balance = from_acc.balance
    logger.info(f"[{transfer_id}] STEP 2: Check if ${current_balance:.2f} >= ${amount:.2f}")

    if current_balance < amount:
        logger.warning(f"[{transfer_id}] INSUFFICIENT FUNDS: {from_account} has ${current_balance:.2f}, needs ${amount:.2f}")
        TRANSFERS_TOTAL.labels(status="insufficient_funds", service=SERVICE_NAME).inc()

        duration = (time.time() - start_time) * 1000
        return TransactionLog(
            id=transfer_id,
            timestamp=datetime.utcnow().isoformat(),
            from_account=from_account,
            to_account=to_account,
            amount=amount,
            from_balance_before=from_balance_before,
            from_balance_after=from_acc.balance,
            to_balance_before=to_balance_before,
            to_balance_after=to_acc.balance,
            status="failed_insufficient_funds",
            thread_id=thread_id,
            duration_ms=duration
        )

    # Another debug delay after check, before update
    if DEBUG_DELAY_ENABLED:
        logger.warning(f"[{transfer_id}] DEBUG DELAY: Post-check delay {DEBUG_DELAY_MS}ms...")
        await asyncio.sleep(DEBUG_DELAY_MS / 1000.0)

    # VULNERABLE UPDATE: Deduct from source
    logger.info(f"[{transfer_id}] STEP 3: Deducting ${amount:.2f} from {from_account}")
    from_acc.balance -= amount

    # Small delay between operations (realistic)
    await asyncio.sleep(0.001)

    # Add to destination
    logger.info(f"[{transfer_id}] STEP 4: Adding ${amount:.2f} to {to_account}")
    to_acc.balance += amount

    # =========================================================================
    # THE RACE WINDOW ENDS HERE
    # =========================================================================

    # Check for race condition: did we go negative?
    if from_acc.balance < 0:
        logger.error(f"[{transfer_id}] RACE CONDITION DETECTED! {from_account} balance is NEGATIVE: ${from_acc.balance:.2f}")
        RACE_CONDITION_HITS.labels(service=SERVICE_NAME).inc()
        DOUBLE_SPEND_DETECTED.labels(service=SERVICE_NAME).inc()
        span.set_attribute("race_condition.detected", True)
        span.set_attribute("race_condition.negative_balance", from_acc.balance)

    # Update metrics
    ACCOUNT_BALANCE.labels(account_id=from_account, service=SERVICE_NAME).set(from_acc.balance)
    ACCOUNT_BALANCE.labels(account_id=to_account, service=SERVICE_NAME).set(to_acc.balance)
    TRANSFERS_TOTAL.labels(status="success", service=SERVICE_NAME).inc()

    duration = (time.time() - start_time) * 1000
    TRANSFER_LATENCY.labels(service=SERVICE_NAME).observe(duration / 1000.0)

    logger.info(f"[{transfer_id}] COMPLETE: {from_account} ${from_balance_before:.2f} -> ${from_acc.balance:.2f}, {to_account} ${to_balance_before:.2f} -> ${to_acc.balance:.2f}")

    span.set_attribute("transfer.from_balance_after", from_acc.balance)
    span.set_attribute("transfer.to_balance_after", to_acc.balance)
    span.set_attribute("transfer.duration_ms", duration)

    return TransactionLog(
        id=transfer_id,
        timestamp=datetime.utcnow().isoformat(),
        from_account=from_account,
        to_account=to_account,
        amount=amount,
        from_balance_before=from_balance_before,
        from_balance_after=from_acc.balance,
        to_balance_before=to_balance_before,
        to_balance_after=to_acc.balance,
        status="success",
        thread_id=thread_id,
        duration_ms=duration
    )


async def transfer_safe(
    from_account: str,
    to_account: str,
    amount: float,
    transfer_id: str,
    span
) -> TransactionLog:
    """
    SAFE: Uses locking to prevent race conditions.

    This is the "fix" - we acquire locks on both accounts before
    reading or modifying balances.
    """
    start_time = time.time()
    thread_id = threading.current_thread().name

    # Get account objects
    from_acc = db.get_account(from_account)
    to_acc = db.get_account(to_account)

    # CRITICAL: Lock ordering prevents deadlocks
    # Always lock accounts in alphabetical order
    first_lock, second_lock = (from_acc.lock, to_acc.lock) if from_account < to_account else (to_acc.lock, from_acc.lock)

    span.set_attribute("transfer.from_account", from_account)
    span.set_attribute("transfer.to_account", to_account)
    span.set_attribute("transfer.amount", amount)
    span.set_attribute("transfer.thread_id", thread_id)
    span.set_attribute("transfer.using_locks", True)

    logger.info(f"[{transfer_id}] SAFE MODE: Acquiring locks for {from_account} and {to_account}")

    # Wait at barrier if enabled
    await barrier.wait(transfer_id)

    # Acquire locks (using run_in_executor for async compatibility)
    loop = asyncio.get_event_loop()

    def do_transfer_with_locks():
        with first_lock:
            with second_lock:
                # Now we have exclusive access to both accounts
                from_balance_before = from_acc.balance
                to_balance_before = to_acc.balance

                logger.info(f"[{transfer_id}] SAFE: Locks acquired, checking balance...")

                if from_acc.balance < amount:
                    logger.warning(f"[{transfer_id}] INSUFFICIENT FUNDS (safe check)")
                    return {
                        "status": "failed_insufficient_funds",
                        "from_balance_before": from_balance_before,
                        "from_balance_after": from_acc.balance,
                        "to_balance_before": to_balance_before,
                        "to_balance_after": to_acc.balance
                    }

                # Apply debug delay even in safe mode (to show it doesn't matter)
                if DEBUG_DELAY_ENABLED:
                    logger.info(f"[{transfer_id}] SAFE: Debug delay active but we're protected by locks")
                    time.sleep(DEBUG_DELAY_MS / 1000.0)

                # Perform transfer atomically
                from_acc.balance -= amount
                to_acc.balance += amount

                logger.info(f"[{transfer_id}] SAFE: Transfer complete within lock")

                return {
                    "status": "success",
                    "from_balance_before": from_balance_before,
                    "from_balance_after": from_acc.balance,
                    "to_balance_before": to_balance_before,
                    "to_balance_after": to_acc.balance
                }

    result = await loop.run_in_executor(None, do_transfer_with_locks)

    duration = (time.time() - start_time) * 1000

    if result["status"] == "success":
        TRANSFERS_TOTAL.labels(status="success", service=SERVICE_NAME).inc()
        ACCOUNT_BALANCE.labels(account_id=from_account, service=SERVICE_NAME).set(result["from_balance_after"])
        ACCOUNT_BALANCE.labels(account_id=to_account, service=SERVICE_NAME).set(result["to_balance_after"])
    else:
        TRANSFERS_TOTAL.labels(status="insufficient_funds", service=SERVICE_NAME).inc()

    TRANSFER_LATENCY.labels(service=SERVICE_NAME).observe(duration / 1000.0)

    span.set_attribute("transfer.from_balance_after", result["from_balance_after"])
    span.set_attribute("transfer.to_balance_after", result["to_balance_after"])
    span.set_attribute("transfer.duration_ms", duration)

    return TransactionLog(
        id=transfer_id,
        timestamp=datetime.utcnow().isoformat(),
        from_account=from_account,
        to_account=to_account,
        amount=amount,
        from_balance_before=result["from_balance_before"],
        from_balance_after=result["from_balance_after"],
        to_balance_before=result["to_balance_before"],
        to_balance_after=result["to_balance_after"],
        status=result["status"],
        thread_id=thread_id,
        duration_ms=duration
    )


# =============================================================================
# FastAPI Application
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Debug delay: {DEBUG_DELAY_ENABLED} ({DEBUG_DELAY_MS}ms)")
    logger.info(f"Barrier: {BARRIER_ENABLED} (count={BARRIER_COUNT})")
    logger.info(f"Safe mode (transactions): {USE_TRANSACTIONS}")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(
    title="Bank API - Race Condition Demo",
    description="Demonstrates check-then-act race conditions in financial transfers",
    lifespan=lifespan
)
FastAPIInstrumentor.instrument_app(app)

# Track concurrent transfers
transfer_semaphore = asyncio.Semaphore(100)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/accounts")
async def list_accounts():
    """List all accounts and their balances."""
    return {
        "accounts": [
            {"id": acc.id, "balance": acc.balance}
            for acc in db.accounts.values()
        ]
    }


@app.get("/accounts/{account_id}")
async def get_account(account_id: str):
    """Get a specific account's balance."""
    try:
        balance = db.get_balance(account_id)
        return {"id": account_id, "balance": balance}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/transfer")
async def transfer(request: TransferRequest):
    """
    Execute a transfer between accounts.

    WARNING: This endpoint is vulnerable to race conditions unless
    USE_TRANSACTIONS is enabled.
    """
    transfer_id = str(uuid.uuid4())[:8]

    with tracer.start_as_current_span("transfer") as span:
        span.set_attribute("transfer.id", transfer_id)

        CONCURRENT_TRANSFERS.labels(service=SERVICE_NAME).inc()

        try:
            async with transfer_semaphore:
                global USE_TRANSACTIONS
                if USE_TRANSACTIONS:
                    result = await transfer_safe(
                        request.from_account,
                        request.to_account,
                        request.amount,
                        transfer_id,
                        span
                    )
                else:
                    result = await transfer_vulnerable(
                        request.from_account,
                        request.to_account,
                        request.amount,
                        transfer_id,
                        span
                    )

                db.transaction_log.append(result)
                return result

        finally:
            CONCURRENT_TRANSFERS.labels(service=SERVICE_NAME).dec()


@app.get("/transactions")
async def list_transactions(limit: int = 20):
    """List recent transactions."""
    return {"transactions": db.transaction_log[-limit:]}


@app.post("/reset")
async def reset_database():
    """Reset all accounts to initial state."""
    db.reset()
    return {"status": "reset", "accounts": [
        {"id": acc.id, "balance": acc.balance}
        for acc in db.accounts.values()
    ]}


# =============================================================================
# Admin/Debug Endpoints
# =============================================================================

@app.get("/admin/settings")
async def get_settings():
    """Get current debug settings."""
    return DebugSettings(
        delay_enabled=DEBUG_DELAY_ENABLED,
        delay_ms=DEBUG_DELAY_MS,
        barrier_enabled=barrier.enabled,
        barrier_count=barrier.count,
        use_transactions=USE_TRANSACTIONS
    )


@app.post("/admin/settings")
async def update_settings(settings: DebugSettings):
    """Update debug settings."""
    global DEBUG_DELAY_ENABLED, DEBUG_DELAY_MS, USE_TRANSACTIONS

    DEBUG_DELAY_ENABLED = settings.delay_enabled
    DEBUG_DELAY_MS = settings.delay_ms
    USE_TRANSACTIONS = settings.use_transactions

    barrier.configure(settings.barrier_enabled, settings.barrier_count)

    logger.info(f"Settings updated: delay={DEBUG_DELAY_ENABLED}({DEBUG_DELAY_MS}ms), "
                f"barrier={settings.barrier_enabled}({settings.barrier_count}), "
                f"transactions={USE_TRANSACTIONS}")

    return settings


@app.post("/admin/delay")
async def set_delay(enabled: bool = True, ms: int = 100):
    """Quick endpoint to enable/disable debug delay."""
    global DEBUG_DELAY_ENABLED, DEBUG_DELAY_MS
    DEBUG_DELAY_ENABLED = enabled
    DEBUG_DELAY_MS = ms
    logger.info(f"Debug delay: {enabled} ({ms}ms)")
    return {"delay_enabled": enabled, "delay_ms": ms}


@app.post("/admin/barrier")
async def set_barrier(enabled: bool = True, count: int = 2):
    """Quick endpoint to configure barrier synchronization."""
    barrier.configure(enabled, count)
    logger.info(f"Barrier: {enabled} (count={count})")
    return {"barrier_enabled": enabled, "barrier_count": count}


@app.post("/admin/transactions")
async def set_transactions(enabled: bool = True):
    """Quick endpoint to enable/disable safe transaction mode."""
    global USE_TRANSACTIONS
    USE_TRANSACTIONS = enabled
    logger.info(f"Safe transactions: {enabled}")
    return {"use_transactions": enabled}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
