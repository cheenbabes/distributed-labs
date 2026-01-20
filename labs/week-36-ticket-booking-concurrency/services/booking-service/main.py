"""
Ticket Booking Service - Demonstrates race conditions and locking strategies.

This service supports four locking strategies:
1. none - No locking, shows double-booking bug
2. pessimistic - SELECT FOR UPDATE (database row lock)
3. optimistic - Version column with retry
4. distributed - Redis distributed lock
"""
import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "booking-service")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab36-otel-collector:4317")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://booking:booking123@lab36-postgres:5432/tickets")
REDIS_URL = os.getenv("REDIS_URL", "redis://lab36-redis:6379")
LOCKING_STRATEGY = os.getenv("LOCKING_STRATEGY", "none")

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
BOOKINGS_TOTAL = Counter(
    "bookings_total",
    "Total booking attempts",
    ["strategy", "status"]
)
DOUBLE_BOOKINGS = Counter(
    "double_bookings_detected",
    "Number of double bookings detected",
    ["strategy"]
)
LOCK_CONTENTIONS = Counter(
    "lock_contentions_total",
    "Number of lock contention events",
    ["strategy"]
)
OPTIMISTIC_RETRIES = Counter(
    "optimistic_retries_total",
    "Number of optimistic locking retries",
    ["strategy"]
)
BOOKING_LATENCY = Histogram(
    "booking_duration_seconds",
    "Booking operation latency",
    ["strategy"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
AVAILABLE_SEATS = Gauge(
    "available_seats",
    "Number of available seats",
    ["event_id"]
)
CURRENT_STRATEGY = Gauge(
    "current_locking_strategy",
    "Current locking strategy (0=none, 1=pessimistic, 2=optimistic, 3=distributed)",
    []
)

# Global database pool and redis client
db_pool: Optional[asyncpg.Pool] = None
redis_client: Optional[redis.Redis] = None
current_strategy = LOCKING_STRATEGY


class BookingRequest(BaseModel):
    event_id: int
    seat_number: str
    customer_id: str


class StrategyRequest(BaseModel):
    strategy: str  # none, pessimistic, optimistic, distributed


def strategy_to_number(strategy: str) -> int:
    mapping = {"none": 0, "pessimistic": 1, "optimistic": 2, "distributed": 3}
    return mapping.get(strategy, 0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, redis_client, current_strategy

    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Locking strategy: {LOCKING_STRATEGY}")

    # Initialize database pool
    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=5,
        max_size=20
    )

    # Initialize Redis client
    redis_client = redis.from_url(REDIS_URL)

    # Set initial strategy metric
    CURRENT_STRATEGY.set(strategy_to_number(current_strategy))

    yield

    # Cleanup
    if db_pool:
        await db_pool.close()
    if redis_client:
        await redis_client.close()

    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME, "strategy": current_strategy}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/events/{event_id}")
async def get_event(event_id: int):
    """Get event details with seat availability."""
    async with db_pool.acquire() as conn:
        event = await conn.fetchrow(
            "SELECT * FROM events WHERE id = $1", event_id
        )
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

        seats = await conn.fetch(
            """
            SELECT seat_number, section, row_name, is_booked, booked_by
            FROM seats WHERE event_id = $1
            ORDER BY section, row_name, seat_number
            """,
            event_id
        )

        available = sum(1 for s in seats if not s["is_booked"])
        booked = sum(1 for s in seats if s["is_booked"])

        AVAILABLE_SEATS.labels(event_id=str(event_id)).set(available)

        return {
            "event": dict(event),
            "seats": {
                "total": len(seats),
                "available": available,
                "booked": booked,
                "details": [dict(s) for s in seats]
            }
        }


@app.get("/events/{event_id}/seats")
async def get_seats(event_id: int, available_only: bool = False):
    """Get all seats for an event."""
    async with db_pool.acquire() as conn:
        if available_only:
            seats = await conn.fetch(
                """
                SELECT seat_number, section, row_name
                FROM seats WHERE event_id = $1 AND is_booked = FALSE
                ORDER BY section, row_name, seat_number
                """,
                event_id
            )
        else:
            seats = await conn.fetch(
                """
                SELECT seat_number, section, row_name, is_booked, booked_by
                FROM seats WHERE event_id = $1
                ORDER BY section, row_name, seat_number
                """,
                event_id
            )

        return {"seats": [dict(s) for s in seats]}


@app.post("/book")
async def book_seat(request: BookingRequest):
    """Book a seat using the configured locking strategy."""
    start_time = time.time()

    with tracer.start_as_current_span("book_seat") as span:
        span.set_attribute("event_id", request.event_id)
        span.set_attribute("seat_number", request.seat_number)
        span.set_attribute("customer_id", request.customer_id)
        span.set_attribute("strategy", current_strategy)

        try:
            if current_strategy == "none":
                result = await book_no_locking(request)
            elif current_strategy == "pessimistic":
                result = await book_pessimistic(request)
            elif current_strategy == "optimistic":
                result = await book_optimistic(request)
            elif current_strategy == "distributed":
                result = await book_distributed(request)
            else:
                raise HTTPException(status_code=400, detail=f"Unknown strategy: {current_strategy}")

            duration = time.time() - start_time
            BOOKING_LATENCY.labels(strategy=current_strategy).observe(duration)
            BOOKINGS_TOTAL.labels(strategy=current_strategy, status=result["status"]).inc()

            return result

        except HTTPException:
            BOOKINGS_TOTAL.labels(strategy=current_strategy, status="error").inc()
            raise
        except Exception as e:
            BOOKINGS_TOTAL.labels(strategy=current_strategy, status="error").inc()
            logger.error(f"Booking error: {e}")
            raise HTTPException(status_code=500, detail=str(e))


async def book_no_locking(request: BookingRequest) -> dict:
    """
    Book a seat WITHOUT any locking.
    This deliberately creates a race condition window to demonstrate double-booking.
    """
    async with db_pool.acquire() as conn:
        # Step 1: Check if seat is available (READ)
        seat = await conn.fetchrow(
            """
            SELECT id, is_booked, booked_by FROM seats
            WHERE event_id = $1 AND seat_number = $2
            """,
            request.event_id, request.seat_number
        )

        if not seat:
            raise HTTPException(status_code=404, detail="Seat not found")

        if seat["is_booked"]:
            # Record attempted booking of already-booked seat
            await conn.execute(
                """
                INSERT INTO bookings (event_id, seat_id, customer_id, status, locking_strategy)
                VALUES ($1, $2, $3, 'failed', 'none')
                """,
                request.event_id, seat["id"], request.customer_id
            )
            return {
                "status": "failed",
                "message": f"Seat {request.seat_number} already booked by {seat['booked_by']}",
                "strategy": "none"
            }

        # RACE CONDITION WINDOW: Artificial delay to increase likelihood of collision
        await asyncio.sleep(0.05)  # 50ms delay

        # Step 2: Book the seat (WRITE) - without checking again!
        await conn.execute(
            """
            UPDATE seats SET is_booked = TRUE, booked_by = $1, booked_at = NOW()
            WHERE id = $2
            """,
            request.customer_id, seat["id"]
        )

        # Record the booking
        await conn.execute(
            """
            INSERT INTO bookings (event_id, seat_id, customer_id, status, locking_strategy)
            VALUES ($1, $2, $3, 'confirmed', 'none')
            """,
            request.event_id, seat["id"], request.customer_id
        )

        # Check for double booking
        booking_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM bookings
            WHERE seat_id = $1 AND status = 'confirmed'
            """,
            seat["id"]
        )

        if booking_count > 1:
            DOUBLE_BOOKINGS.labels(strategy="none").inc()
            logger.warning(f"DOUBLE BOOKING DETECTED: Seat {request.seat_number} booked {booking_count} times!")
            return {
                "status": "double_booked",
                "message": f"WARNING: Double booking detected for seat {request.seat_number}!",
                "booking_count": booking_count,
                "strategy": "none"
            }

        return {
            "status": "confirmed",
            "message": f"Seat {request.seat_number} booked successfully",
            "customer_id": request.customer_id,
            "strategy": "none"
        }


async def book_pessimistic(request: BookingRequest) -> dict:
    """
    Book a seat using PESSIMISTIC locking (SELECT FOR UPDATE).
    The database row is locked during the transaction.
    """
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # Acquire exclusive lock on the row
            seat = await conn.fetchrow(
                """
                SELECT id, is_booked, booked_by FROM seats
                WHERE event_id = $1 AND seat_number = $2
                FOR UPDATE
                """,
                request.event_id, request.seat_number
            )

            if not seat:
                raise HTTPException(status_code=404, detail="Seat not found")

            if seat["is_booked"]:
                LOCK_CONTENTIONS.labels(strategy="pessimistic").inc()
                await conn.execute(
                    """
                    INSERT INTO bookings (event_id, seat_id, customer_id, status, locking_strategy)
                    VALUES ($1, $2, $3, 'failed', 'pessimistic')
                    """,
                    request.event_id, seat["id"], request.customer_id
                )
                return {
                    "status": "failed",
                    "message": f"Seat {request.seat_number} already booked by {seat['booked_by']}",
                    "strategy": "pessimistic"
                }

            # Same artificial delay, but now protected by lock
            await asyncio.sleep(0.05)

            # Book the seat (other transactions wait for our lock to release)
            await conn.execute(
                """
                UPDATE seats SET is_booked = TRUE, booked_by = $1, booked_at = NOW()
                WHERE id = $2
                """,
                request.customer_id, seat["id"]
            )

            await conn.execute(
                """
                INSERT INTO bookings (event_id, seat_id, customer_id, status, locking_strategy)
                VALUES ($1, $2, $3, 'confirmed', 'pessimistic')
                """,
                request.event_id, seat["id"], request.customer_id
            )

            return {
                "status": "confirmed",
                "message": f"Seat {request.seat_number} booked successfully",
                "customer_id": request.customer_id,
                "strategy": "pessimistic"
            }


async def book_optimistic(request: BookingRequest, max_retries: int = 3) -> dict:
    """
    Book a seat using OPTIMISTIC locking (version column).
    If the version changed since we read it, we retry.
    """
    for attempt in range(max_retries):
        async with db_pool.acquire() as conn:
            # Read current state with version
            seat = await conn.fetchrow(
                """
                SELECT id, is_booked, booked_by, version FROM seats
                WHERE event_id = $1 AND seat_number = $2
                """,
                request.event_id, request.seat_number
            )

            if not seat:
                raise HTTPException(status_code=404, detail="Seat not found")

            if seat["is_booked"]:
                await conn.execute(
                    """
                    INSERT INTO bookings (event_id, seat_id, customer_id, status, locking_strategy)
                    VALUES ($1, $2, $3, 'failed', 'optimistic')
                    """,
                    request.event_id, seat["id"], request.customer_id
                )
                return {
                    "status": "failed",
                    "message": f"Seat {request.seat_number} already booked by {seat['booked_by']}",
                    "strategy": "optimistic"
                }

            current_version = seat["version"]

            # Simulate work (creates race condition opportunity)
            await asyncio.sleep(0.05)

            # Attempt to update with version check
            result = await conn.execute(
                """
                UPDATE seats
                SET is_booked = TRUE, booked_by = $1, booked_at = NOW(), version = version + 1
                WHERE id = $2 AND version = $3
                """,
                request.customer_id, seat["id"], current_version
            )

            # Check if update succeeded (version matched)
            if result == "UPDATE 1":
                await conn.execute(
                    """
                    INSERT INTO bookings (event_id, seat_id, customer_id, status, locking_strategy)
                    VALUES ($1, $2, $3, 'confirmed', 'optimistic')
                    """,
                    request.event_id, seat["id"], request.customer_id
                )
                return {
                    "status": "confirmed",
                    "message": f"Seat {request.seat_number} booked successfully",
                    "customer_id": request.customer_id,
                    "strategy": "optimistic",
                    "attempts": attempt + 1
                }

            # Version mismatch - someone else modified the row
            OPTIMISTIC_RETRIES.labels(strategy="optimistic").inc()
            logger.info(f"Optimistic lock conflict, attempt {attempt + 1}/{max_retries}")

            # Exponential backoff before retry
            await asyncio.sleep(0.01 * (2 ** attempt))

    LOCK_CONTENTIONS.labels(strategy="optimistic").inc()
    return {
        "status": "failed",
        "message": f"Could not book seat {request.seat_number} after {max_retries} attempts (contention)",
        "strategy": "optimistic"
    }


async def book_distributed(request: BookingRequest) -> dict:
    """
    Book a seat using DISTRIBUTED locking (Redis).
    Only one instance across all servers can hold the lock.
    """
    lock_key = f"seat_lock:{request.event_id}:{request.seat_number}"
    lock_id = str(uuid.uuid4())
    lock_ttl = 5  # 5 second lock timeout

    # Try to acquire distributed lock
    lock_acquired = await redis_client.set(lock_key, lock_id, nx=True, ex=lock_ttl)

    if not lock_acquired:
        LOCK_CONTENTIONS.labels(strategy="distributed").inc()
        return {
            "status": "failed",
            "message": f"Could not acquire lock for seat {request.seat_number}",
            "strategy": "distributed"
        }

    try:
        async with db_pool.acquire() as conn:
            seat = await conn.fetchrow(
                """
                SELECT id, is_booked, booked_by FROM seats
                WHERE event_id = $1 AND seat_number = $2
                """,
                request.event_id, request.seat_number
            )

            if not seat:
                raise HTTPException(status_code=404, detail="Seat not found")

            if seat["is_booked"]:
                await conn.execute(
                    """
                    INSERT INTO bookings (event_id, seat_id, customer_id, status, locking_strategy)
                    VALUES ($1, $2, $3, 'failed', 'distributed')
                    """,
                    request.event_id, seat["id"], request.customer_id
                )
                return {
                    "status": "failed",
                    "message": f"Seat {request.seat_number} already booked by {seat['booked_by']}",
                    "strategy": "distributed"
                }

            # Simulate work (protected by distributed lock)
            await asyncio.sleep(0.05)

            await conn.execute(
                """
                UPDATE seats SET is_booked = TRUE, booked_by = $1, booked_at = NOW()
                WHERE id = $2
                """,
                request.customer_id, seat["id"]
            )

            await conn.execute(
                """
                INSERT INTO bookings (event_id, seat_id, customer_id, status, locking_strategy)
                VALUES ($1, $2, $3, 'confirmed', 'distributed')
                """,
                request.event_id, seat["id"], request.customer_id
            )

            return {
                "status": "confirmed",
                "message": f"Seat {request.seat_number} booked successfully",
                "customer_id": request.customer_id,
                "strategy": "distributed"
            }

    finally:
        # Release lock only if we still own it (compare-and-delete)
        current_lock = await redis_client.get(lock_key)
        if current_lock and current_lock.decode() == lock_id:
            await redis_client.delete(lock_key)


@app.post("/admin/strategy")
async def set_strategy(request: StrategyRequest):
    """Change the locking strategy at runtime."""
    global current_strategy

    if request.strategy not in ["none", "pessimistic", "optimistic", "distributed"]:
        raise HTTPException(status_code=400, detail="Invalid strategy")

    old_strategy = current_strategy
    current_strategy = request.strategy
    CURRENT_STRATEGY.set(strategy_to_number(current_strategy))

    logger.info(f"Strategy changed from {old_strategy} to {current_strategy}")

    return {
        "old_strategy": old_strategy,
        "new_strategy": current_strategy
    }


@app.get("/admin/strategy")
async def get_strategy():
    """Get current locking strategy."""
    return {"strategy": current_strategy}


@app.post("/admin/reset")
async def reset_event(event_id: int = 1):
    """Reset all seats for an event (for testing)."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE seats SET is_booked = FALSE, booked_by = NULL, booked_at = NULL, version = 0
            WHERE event_id = $1
            """,
            event_id
        )
        await conn.execute(
            """
            DELETE FROM bookings WHERE event_id = $1
            """,
            event_id
        )

    return {"message": f"Event {event_id} reset successfully"}


@app.get("/admin/double-bookings")
async def get_double_bookings():
    """Check for any double bookings in the system."""
    async with db_pool.acquire() as conn:
        double_bookings = await conn.fetch(
            """
            SELECT
                s.seat_number,
                s.event_id,
                COUNT(*) as booking_count,
                array_agg(b.customer_id) as customers
            FROM seats s
            JOIN bookings b ON s.id = b.seat_id
            WHERE b.status = 'confirmed'
            GROUP BY s.id, s.seat_number, s.event_id
            HAVING COUNT(*) > 1
            """
        )

        return {
            "double_bookings": [dict(d) for d in double_bookings],
            "count": len(double_bookings)
        }


@app.get("/admin/stats")
async def get_stats():
    """Get booking statistics."""
    async with db_pool.acquire() as conn:
        stats = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE status = 'confirmed') as confirmed,
                COUNT(*) FILTER (WHERE status = 'failed') as failed,
                COUNT(*) FILTER (WHERE status = 'double_booked') as double_booked,
                COUNT(*) as total
            FROM bookings
            """
        )

        by_strategy = await conn.fetch(
            """
            SELECT
                locking_strategy,
                COUNT(*) FILTER (WHERE status = 'confirmed') as confirmed,
                COUNT(*) FILTER (WHERE status = 'failed') as failed,
                COUNT(*) as total
            FROM bookings
            GROUP BY locking_strategy
            """
        )

        return {
            "totals": dict(stats),
            "by_strategy": [dict(s) for s in by_strategy],
            "current_strategy": current_strategy
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
