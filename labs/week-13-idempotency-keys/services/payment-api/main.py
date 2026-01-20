"""
Payment API - Demonstrates Stripe-style idempotency key pattern.

This service shows how to implement exactly-once payment processing
using idempotency keys, even when clients retry requests.
"""
import asyncio
import hashlib
import json
import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, Header, HTTPException, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel, Field
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "payment-api")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab13-otel-collector:4317")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://payments:payments@lab13-postgres:5432/payments")
REDIS_URL = os.getenv("REDIS_URL", "redis://lab13-redis:6379/0")
IDEMPOTENCY_KEY_TTL_HOURS = int(os.getenv("IDEMPOTENCY_KEY_TTL_HOURS", "24"))
PROCESSING_MIN_MS = int(os.getenv("PROCESSING_MIN_MS", "300"))
PROCESSING_MAX_MS = int(os.getenv("PROCESSING_MAX_MS", "800"))

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
PAYMENTS_TOTAL = Counter(
    "payments_total",
    "Total payment attempts",
    ["status", "is_retry"]
)
IDEMPOTENCY_HITS = Counter(
    "idempotency_cache_hits_total",
    "Idempotency key cache hits (duplicate requests blocked)"
)
IDEMPOTENCY_CONFLICTS = Counter(
    "idempotency_conflicts_total",
    "Idempotency key conflicts (same key, different body)"
)
CONCURRENT_WAITS = Counter(
    "concurrent_request_waits_total",
    "Times a request waited for another to complete"
)
PAYMENT_PROCESSING_TIME = Histogram(
    "payment_processing_seconds",
    "Time to process a payment",
    buckets=[0.1, 0.25, 0.5, 0.75, 1.0, 2.0, 5.0]
)
ACTIVE_PROCESSING = Gauge(
    "payments_processing_active",
    "Number of payments currently being processed"
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint", "status"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

# Global connections
db_pool: Optional[asyncpg.Pool] = None
redis_client: Optional[redis.Redis] = None


class IdempotencyStatus(str, Enum):
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"


class PaymentRequest(BaseModel):
    amount: int = Field(..., gt=0, description="Amount in cents")
    currency: str = Field(default="usd", pattern="^[a-z]{3}$")
    description: Optional[str] = Field(default=None, max_length=500)
    metadata: Optional[dict] = Field(default=None)


class PaymentResponse(BaseModel):
    id: str
    amount: int
    currency: str
    description: Optional[str]
    status: str
    created_at: str
    idempotency_key: Optional[str] = None
    cached: bool = False


def compute_request_hash(request_body: dict) -> str:
    """Compute a deterministic hash of the request body."""
    canonical = json.dumps(request_body, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def get_idempotency_key_from_db(key: str) -> Optional[dict]:
    """Fetch idempotency key record from PostgreSQL."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT key, request_hash, status, response_body, created_at, expires_at
            FROM idempotency_keys
            WHERE key = $1 AND expires_at > NOW()
            """,
            key
        )
        if row:
            return dict(row)
    return None


async def create_idempotency_key(key: str, request_hash: str, ttl_hours: int) -> bool:
    """
    Attempt to create an idempotency key record.
    Returns True if created, False if already exists.
    Uses PostgreSQL's ON CONFLICT for atomicity.
    """
    async with db_pool.acquire() as conn:
        try:
            result = await conn.execute(
                """
                INSERT INTO idempotency_keys (key, request_hash, status, expires_at)
                VALUES ($1, $2, $3, NOW() + INTERVAL '%s hours')
                ON CONFLICT (key) DO NOTHING
                """ % ttl_hours,
                key, request_hash, IdempotencyStatus.PROCESSING.value
            )
            # Check if a row was inserted
            return result == "INSERT 0 1"
        except Exception as e:
            logger.error(f"Error creating idempotency key: {e}")
            return False


async def update_idempotency_key(key: str, status: IdempotencyStatus, response_body: dict):
    """Update idempotency key with result."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE idempotency_keys
            SET status = $2, response_body = $3
            WHERE key = $1
            """,
            key, status.value, json.dumps(response_body)
        )


async def acquire_processing_lock(key: str, timeout: int = 30) -> bool:
    """
    Try to acquire a distributed lock for processing this key.
    Uses Redis for fast distributed locking.
    """
    lock_key = f"idempotency_lock:{key}"
    # SET NX with expiry for atomic lock acquisition
    result = await redis_client.set(lock_key, "1", nx=True, ex=timeout)
    return result is True


async def release_processing_lock(key: str):
    """Release the distributed processing lock."""
    lock_key = f"idempotency_lock:{key}"
    await redis_client.delete(lock_key)


async def wait_for_completion(key: str, max_wait: float = 10.0) -> Optional[dict]:
    """
    Wait for another request with the same key to complete.
    Polls the database until the status changes from 'processing'.
    """
    start = time.time()
    poll_interval = 0.1  # Start with 100ms

    while time.time() - start < max_wait:
        record = await get_idempotency_key_from_db(key)
        if record and record['status'] != IdempotencyStatus.PROCESSING.value:
            return record
        await asyncio.sleep(poll_interval)
        # Exponential backoff up to 500ms
        poll_interval = min(poll_interval * 1.5, 0.5)

    return None


async def process_payment(payment: PaymentRequest) -> PaymentResponse:
    """
    Simulate payment processing with random latency.
    In a real system, this would call a payment processor.
    """
    ACTIVE_PROCESSING.inc()
    start_time = time.time()

    try:
        # Simulate processing time
        processing_time_ms = random.randint(PROCESSING_MIN_MS, PROCESSING_MAX_MS)
        await asyncio.sleep(processing_time_ms / 1000.0)

        # Simulate occasional failures (5% chance)
        if random.random() < 0.05:
            raise Exception("Payment processor temporarily unavailable")

        # Create payment record
        payment_id = f"pay_{uuid.uuid4().hex[:24]}"
        created_at = datetime.utcnow()

        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO payments (id, amount, currency, description, status, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                payment_id, payment.amount, payment.currency,
                payment.description, "succeeded", created_at
            )

        duration = time.time() - start_time
        PAYMENT_PROCESSING_TIME.observe(duration)

        current_span = trace.get_current_span()
        current_span.set_attribute("payment.id", payment_id)
        current_span.set_attribute("payment.amount", payment.amount)
        current_span.set_attribute("payment.processing_time_ms", processing_time_ms)

        return PaymentResponse(
            id=payment_id,
            amount=payment.amount,
            currency=payment.currency,
            description=payment.description,
            status="succeeded",
            created_at=created_at.isoformat() + "Z"
        )

    finally:
        ACTIVE_PROCESSING.dec()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage database and Redis connections."""
    global db_pool, redis_client

    logger.info(f"{SERVICE_NAME} starting up")

    # Connect to PostgreSQL
    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=5,
        max_size=20
    )
    logger.info("Connected to PostgreSQL")

    # Connect to Redis
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    await redis_client.ping()
    logger.info("Connected to Redis")

    yield

    # Cleanup
    logger.info(f"{SERVICE_NAME} shutting down")
    await db_pool.close()
    await redis_client.close()


app = FastAPI(
    title="Payment API",
    description="Demonstrates Stripe-style idempotency key pattern",
    lifespan=lifespan
)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/payments", response_model=PaymentResponse)
async def create_payment(
    payment: PaymentRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key")
):
    """
    Create a payment with idempotency key support.

    The Idempotency-Key header ensures that retrying the same request
    returns the same response without processing the payment twice.

    Behavior:
    - No key: Process payment normally (no idempotency protection)
    - New key: Process payment, store result with key
    - Existing key + same body: Return cached response
    - Existing key + different body: Return 409 Conflict
    - Key in processing: Wait for completion, then return result
    """
    start_time = time.time()
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # If no idempotency key, process normally (dangerous for retries!)
    if not idempotency_key:
        logger.warning(f"Payment request without idempotency key trace_id={trace_id}")
        current_span.set_attribute("idempotency.key", "none")
        current_span.set_attribute("idempotency.protected", False)

        result = await process_payment(payment)
        PAYMENTS_TOTAL.labels(status="succeeded", is_retry="false").inc()

        duration = time.time() - start_time
        REQUEST_LATENCY.labels(method="POST", endpoint="/payments", status="200").observe(duration)

        return result

    current_span.set_attribute("idempotency.key", idempotency_key)
    current_span.set_attribute("idempotency.protected", True)

    # Compute hash of request body for conflict detection
    request_hash = compute_request_hash(payment.model_dump())
    current_span.set_attribute("idempotency.request_hash", request_hash[:16])

    # Check if this key already exists
    existing_record = await get_idempotency_key_from_db(idempotency_key)

    if existing_record:
        # Key exists - check for conflict or return cached response
        if existing_record['request_hash'] != request_hash:
            # Same key, different request body = CONFLICT
            IDEMPOTENCY_CONFLICTS.inc()
            current_span.set_attribute("idempotency.conflict", True)
            logger.warning(
                f"Idempotency key conflict key={idempotency_key} trace_id={trace_id}"
            )

            duration = time.time() - start_time
            REQUEST_LATENCY.labels(method="POST", endpoint="/payments", status="409").observe(duration)

            raise HTTPException(
                status_code=409,
                detail={
                    "error": "idempotency_key_conflict",
                    "message": "This idempotency key was already used with a different request body. "
                               "Each idempotency key can only be used with one unique request.",
                    "idempotency_key": idempotency_key
                }
            )

        if existing_record['status'] == IdempotencyStatus.PROCESSING.value:
            # Another request is currently processing with this key
            # Wait for it to complete
            CONCURRENT_WAITS.inc()
            current_span.set_attribute("idempotency.waited", True)
            logger.info(
                f"Waiting for concurrent request to complete key={idempotency_key} trace_id={trace_id}"
            )

            completed_record = await wait_for_completion(idempotency_key)

            if completed_record and completed_record['response_body']:
                IDEMPOTENCY_HITS.inc()
                cached_response = json.loads(completed_record['response_body'])
                cached_response['cached'] = True
                cached_response['idempotency_key'] = idempotency_key
                PAYMENTS_TOTAL.labels(status="succeeded", is_retry="true").inc()

                duration = time.time() - start_time
                REQUEST_LATENCY.labels(method="POST", endpoint="/payments", status="200").observe(duration)

                return PaymentResponse(**cached_response)
            else:
                # Processing timed out or failed, let this request try
                logger.warning(
                    f"Concurrent request timed out, retrying key={idempotency_key} trace_id={trace_id}"
                )

        elif existing_record['status'] in [IdempotencyStatus.COMPLETE.value, IdempotencyStatus.FAILED.value]:
            # Request already completed - return cached response
            IDEMPOTENCY_HITS.inc()
            current_span.set_attribute("idempotency.cache_hit", True)
            logger.info(
                f"Returning cached response for key={idempotency_key} trace_id={trace_id}"
            )

            if existing_record['response_body']:
                cached_response = json.loads(existing_record['response_body'])
                cached_response['cached'] = True
                cached_response['idempotency_key'] = idempotency_key
                PAYMENTS_TOTAL.labels(status="succeeded", is_retry="true").inc()

                duration = time.time() - start_time
                REQUEST_LATENCY.labels(method="POST", endpoint="/payments", status="200").observe(duration)

                return PaymentResponse(**cached_response)

    # New idempotency key - try to acquire processing lock
    if not await acquire_processing_lock(idempotency_key):
        # Another request just started processing this key
        CONCURRENT_WAITS.inc()
        logger.info(
            f"Lost race for lock, waiting key={idempotency_key} trace_id={trace_id}"
        )

        completed_record = await wait_for_completion(idempotency_key)
        if completed_record and completed_record['response_body']:
            IDEMPOTENCY_HITS.inc()
            cached_response = json.loads(completed_record['response_body'])
            cached_response['cached'] = True
            cached_response['idempotency_key'] = idempotency_key

            duration = time.time() - start_time
            REQUEST_LATENCY.labels(method="POST", endpoint="/payments", status="200").observe(duration)

            return PaymentResponse(**cached_response)

    try:
        # Create idempotency key record with 'processing' status
        created = await create_idempotency_key(
            idempotency_key, request_hash, IDEMPOTENCY_KEY_TTL_HOURS
        )

        if not created:
            # Key was created by another request in the meantime
            # This is a race condition - fetch and return the result
            existing_record = await get_idempotency_key_from_db(idempotency_key)
            if existing_record and existing_record['response_body']:
                IDEMPOTENCY_HITS.inc()
                cached_response = json.loads(existing_record['response_body'])
                cached_response['cached'] = True
                cached_response['idempotency_key'] = idempotency_key

                duration = time.time() - start_time
                REQUEST_LATENCY.labels(method="POST", endpoint="/payments", status="200").observe(duration)

                return PaymentResponse(**cached_response)

        # Process the payment
        current_span.set_attribute("idempotency.new_request", True)
        logger.info(
            f"Processing new payment key={idempotency_key} amount={payment.amount} trace_id={trace_id}"
        )

        try:
            result = await process_payment(payment)
            result.idempotency_key = idempotency_key

            # Store successful response
            await update_idempotency_key(
                idempotency_key,
                IdempotencyStatus.COMPLETE,
                result.model_dump()
            )

            PAYMENTS_TOTAL.labels(status="succeeded", is_retry="false").inc()
            logger.info(
                f"Payment completed payment_id={result.id} key={idempotency_key} trace_id={trace_id}"
            )

            duration = time.time() - start_time
            REQUEST_LATENCY.labels(method="POST", endpoint="/payments", status="200").observe(duration)

            return result

        except Exception as e:
            # Store failure so retries know this failed
            await update_idempotency_key(
                idempotency_key,
                IdempotencyStatus.FAILED,
                {"error": str(e), "status": "failed"}
            )
            PAYMENTS_TOTAL.labels(status="failed", is_retry="false").inc()
            logger.error(
                f"Payment failed key={idempotency_key} error={e} trace_id={trace_id}"
            )

            duration = time.time() - start_time
            REQUEST_LATENCY.labels(method="POST", endpoint="/payments", status="500").observe(duration)

            raise HTTPException(status_code=500, detail=str(e))

    finally:
        await release_processing_lock(idempotency_key)


@app.get("/payments/{payment_id}")
async def get_payment(payment_id: str):
    """Retrieve a payment by ID."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM payments WHERE id = $1",
            payment_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="Payment not found")

        return PaymentResponse(
            id=row['id'],
            amount=row['amount'],
            currency=row['currency'],
            description=row['description'],
            status=row['status'],
            created_at=row['created_at'].isoformat() + "Z"
        )


@app.get("/idempotency-keys/{key}")
async def get_idempotency_key(key: str):
    """
    Debug endpoint to inspect an idempotency key's state.
    In production, this would be admin-only.
    """
    record = await get_idempotency_key_from_db(key)
    if not record:
        raise HTTPException(status_code=404, detail="Idempotency key not found or expired")

    return {
        "key": record['key'],
        "request_hash": record['request_hash'],
        "status": record['status'],
        "created_at": record['created_at'].isoformat() + "Z",
        "expires_at": record['expires_at'].isoformat() + "Z",
        "has_response": record['response_body'] is not None
    }


@app.delete("/idempotency-keys/{key}")
async def delete_idempotency_key(key: str):
    """
    Delete an idempotency key (for testing purposes).
    In production, keys should expire naturally.
    """
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM idempotency_keys WHERE key = $1",
            key
        )
        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="Idempotency key not found")

    return {"deleted": key}


@app.get("/admin/stats")
async def get_stats():
    """Get statistics about payments and idempotency keys."""
    async with db_pool.acquire() as conn:
        payment_count = await conn.fetchval("SELECT COUNT(*) FROM payments")
        key_count = await conn.fetchval(
            "SELECT COUNT(*) FROM idempotency_keys WHERE expires_at > NOW()"
        )
        expired_count = await conn.fetchval(
            "SELECT COUNT(*) FROM idempotency_keys WHERE expires_at <= NOW()"
        )

    return {
        "payments": {
            "total": payment_count
        },
        "idempotency_keys": {
            "active": key_count,
            "expired": expired_count,
            "ttl_hours": IDEMPOTENCY_KEY_TTL_HOURS
        }
    }


@app.post("/admin/cleanup")
async def cleanup_expired_keys():
    """Clean up expired idempotency keys."""
    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM idempotency_keys WHERE expires_at <= NOW()"
        )
        count = int(result.split()[-1]) if result else 0

    return {"deleted_keys": count}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
