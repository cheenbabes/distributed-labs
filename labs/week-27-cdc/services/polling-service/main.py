"""
Polling Service - Demonstrates the traditional polling approach for syncing data.
Polls the database at regular intervals to detect changes.
Used to compare with CDC approach for latency and resource usage.
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, List, Optional

import asyncpg
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "polling-service")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://cdc_user:cdc_password@postgres:5432/products_db")
POLL_INTERVAL_MS = int(os.getenv("POLL_INTERVAL_MS", "1000"))

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
POLL_COUNT = Counter(
    "polling_cycles_total",
    "Total polling cycles executed",
    ["service"]
)
POLL_LATENCY = Histogram(
    "polling_cycle_duration_seconds",
    "Time to complete a polling cycle",
    ["service"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
CHANGES_DETECTED = Counter(
    "polling_changes_detected_total",
    "Total changes detected via polling",
    ["service", "table", "operation"]
)
POLLING_LAG = Gauge(
    "polling_detection_lag_ms",
    "Time between DB change and polling detection",
    ["service"]
)
POLL_STATUS = Gauge(
    "polling_service_status",
    "Polling service status (1=running, 0=stopped)",
    ["service"]
)
DB_QUERIES = Counter(
    "polling_db_queries_total",
    "Total database queries executed for polling",
    ["service"]
)

# State
db_pool: Optional[asyncpg.Pool] = None
polling_task: Optional[asyncio.Task] = None
last_poll_time: Optional[datetime] = None
detected_changes: List[Dict] = []
MAX_CHANGES = 100

# Track known state for change detection
known_products: Dict[int, datetime] = {}  # id -> updated_at
known_orders: Dict[int, datetime] = {}


async def poll_for_changes():
    """Main polling loop - detects changes by comparing timestamps."""
    global last_poll_time, known_products, known_orders, detected_changes

    logger.info(f"Starting polling loop with interval {POLL_INTERVAL_MS}ms")
    POLL_STATUS.labels(service=SERVICE_NAME).set(1)

    # Initialize with current state
    await initialize_known_state()

    while True:
        try:
            poll_start = time.time()

            with tracer.start_as_current_span("poll_cycle") as span:
                changes = []

                async with db_pool.acquire() as conn:
                    # Poll products table
                    DB_QUERIES.labels(service=SERVICE_NAME).inc()
                    products = await conn.fetch(
                        "SELECT id, updated_at FROM products"
                    )

                    for row in products:
                        prod_id = row["id"]
                        updated_at = row["updated_at"]

                        if prod_id not in known_products:
                            # New product
                            changes.append({
                                "table": "products",
                                "operation": "create",
                                "id": prod_id,
                                "updated_at": updated_at.isoformat(),
                                "detected_at": datetime.now().isoformat()
                            })
                            known_products[prod_id] = updated_at
                            CHANGES_DETECTED.labels(
                                service=SERVICE_NAME,
                                table="products",
                                operation="create"
                            ).inc()
                        elif known_products[prod_id] < updated_at:
                            # Updated product
                            lag_ms = (datetime.now() - updated_at).total_seconds() * 1000
                            changes.append({
                                "table": "products",
                                "operation": "update",
                                "id": prod_id,
                                "updated_at": updated_at.isoformat(),
                                "detected_at": datetime.now().isoformat(),
                                "lag_ms": lag_ms
                            })
                            known_products[prod_id] = updated_at
                            POLLING_LAG.labels(service=SERVICE_NAME).set(lag_ms)
                            CHANGES_DETECTED.labels(
                                service=SERVICE_NAME,
                                table="products",
                                operation="update"
                            ).inc()

                    # Check for deletes (products in known but not in current)
                    current_product_ids = {row["id"] for row in products}
                    deleted_ids = set(known_products.keys()) - current_product_ids
                    for prod_id in deleted_ids:
                        changes.append({
                            "table": "products",
                            "operation": "delete",
                            "id": prod_id,
                            "detected_at": datetime.now().isoformat()
                        })
                        del known_products[prod_id]
                        CHANGES_DETECTED.labels(
                            service=SERVICE_NAME,
                            table="products",
                            operation="delete"
                        ).inc()

                    # Poll orders table
                    DB_QUERIES.labels(service=SERVICE_NAME).inc()
                    orders = await conn.fetch(
                        "SELECT id, updated_at FROM orders"
                    )

                    for row in orders:
                        order_id = row["id"]
                        updated_at = row["updated_at"]

                        if order_id not in known_orders:
                            changes.append({
                                "table": "orders",
                                "operation": "create",
                                "id": order_id,
                                "updated_at": updated_at.isoformat(),
                                "detected_at": datetime.now().isoformat()
                            })
                            known_orders[order_id] = updated_at
                            CHANGES_DETECTED.labels(
                                service=SERVICE_NAME,
                                table="orders",
                                operation="create"
                            ).inc()
                        elif known_orders[order_id] < updated_at:
                            lag_ms = (datetime.now() - updated_at).total_seconds() * 1000
                            changes.append({
                                "table": "orders",
                                "operation": "update",
                                "id": order_id,
                                "updated_at": updated_at.isoformat(),
                                "detected_at": datetime.now().isoformat(),
                                "lag_ms": lag_ms
                            })
                            known_orders[order_id] = updated_at
                            POLLING_LAG.labels(service=SERVICE_NAME).set(lag_ms)
                            CHANGES_DETECTED.labels(
                                service=SERVICE_NAME,
                                table="orders",
                                operation="update"
                            ).inc()

                # Store detected changes
                if changes:
                    detected_changes.extend(changes)
                    if len(detected_changes) > MAX_CHANGES:
                        detected_changes = detected_changes[-MAX_CHANGES:]
                    logger.info(f"Detected {len(changes)} changes in this poll cycle")

                poll_duration = time.time() - poll_start
                POLL_COUNT.labels(service=SERVICE_NAME).inc()
                POLL_LATENCY.labels(service=SERVICE_NAME).observe(poll_duration)

                span.set_attribute("poll.changes_detected", len(changes))
                span.set_attribute("poll.duration_ms", poll_duration * 1000)

                last_poll_time = datetime.now()

            # Wait for next poll interval
            await asyncio.sleep(POLL_INTERVAL_MS / 1000.0)

        except asyncio.CancelledError:
            logger.info("Polling task cancelled")
            break
        except Exception as e:
            logger.error(f"Polling error: {e}")
            POLL_STATUS.labels(service=SERVICE_NAME).set(0)
            await asyncio.sleep(5)  # Back off on error

    POLL_STATUS.labels(service=SERVICE_NAME).set(0)


async def initialize_known_state():
    """Load current state from database to establish baseline."""
    global known_products, known_orders

    logger.info("Initializing known state from database...")

    async with db_pool.acquire() as conn:
        DB_QUERIES.labels(service=SERVICE_NAME).inc()
        products = await conn.fetch("SELECT id, updated_at FROM products")
        known_products = {row["id"]: row["updated_at"] for row in products}

        DB_QUERIES.labels(service=SERVICE_NAME).inc()
        orders = await conn.fetch("SELECT id, updated_at FROM orders")
        known_orders = {row["id"]: row["updated_at"] for row in orders}

    logger.info(
        f"Initialized with {len(known_products)} products and {len(known_orders)} orders"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, polling_task

    logger.info(f"{SERVICE_NAME} starting up")

    # Create database connection pool
    retry_count = 0
    max_retries = 30
    while retry_count < max_retries:
        try:
            db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
            logger.info("Database connection pool created")
            break
        except Exception as e:
            retry_count += 1
            logger.warning(f"Failed to connect to database (attempt {retry_count}/{max_retries}): {e}")
            await asyncio.sleep(2)

    if db_pool is None:
        raise Exception("Failed to connect to database after maximum retries")

    # Start polling in background
    polling_task = asyncio.create_task(poll_for_changes())

    yield

    # Cleanup
    if polling_task:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass

    if db_pool:
        await db_pool.close()

    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "polling_active": polling_task is not None and not polling_task.done(),
        "poll_interval_ms": POLL_INTERVAL_MS
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/changes")
async def get_detected_changes(limit: int = 20):
    """Get recently detected changes."""
    return {
        "changes": detected_changes[-limit:],
        "total_stored": len(detected_changes),
        "last_poll_time": last_poll_time.isoformat() if last_poll_time else None,
        "poll_interval_ms": POLL_INTERVAL_MS
    }


@app.get("/status")
async def get_polling_status():
    """Get detailed polling status."""
    return {
        "polling_active": polling_task is not None and not polling_task.done(),
        "poll_interval_ms": POLL_INTERVAL_MS,
        "last_poll_time": last_poll_time.isoformat() if last_poll_time else None,
        "known_products": len(known_products),
        "known_orders": len(known_orders),
        "total_changes_detected": len(detected_changes)
    }


@app.post("/config/interval")
async def update_poll_interval(interval_ms: int):
    """Update the polling interval (requires restart to take effect)."""
    global POLL_INTERVAL_MS
    if interval_ms < 100:
        return {"error": "Minimum interval is 100ms"}
    if interval_ms > 60000:
        return {"error": "Maximum interval is 60000ms (1 minute)"}

    old_interval = POLL_INTERVAL_MS
    POLL_INTERVAL_MS = interval_ms

    logger.info(f"Poll interval changed from {old_interval}ms to {interval_ms}ms")

    return {
        "status": "updated",
        "old_interval_ms": old_interval,
        "new_interval_ms": interval_ms,
        "note": "Restart polling task for immediate effect"
    }


@app.post("/polling/restart")
async def restart_polling():
    """Restart the polling task."""
    global polling_task, known_products, known_orders, detected_changes

    if polling_task:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass

    # Reset state
    known_products = {}
    known_orders = {}
    detected_changes = []

    # Restart
    polling_task = asyncio.create_task(poll_for_changes())

    return {"status": "restarted", "poll_interval_ms": POLL_INTERVAL_MS}


@app.get("/compare")
async def compare_polling_vs_cdc():
    """
    Get information to compare polling vs CDC approach.
    Shows the inherent delays in polling-based change detection.
    """
    avg_lag = 0
    if detected_changes:
        lags = [c.get("lag_ms", 0) for c in detected_changes if c.get("lag_ms")]
        avg_lag = sum(lags) / len(lags) if lags else 0

    return {
        "approach": "polling",
        "poll_interval_ms": POLL_INTERVAL_MS,
        "theoretical_max_lag_ms": POLL_INTERVAL_MS,
        "observed_avg_lag_ms": round(avg_lag, 2),
        "db_queries_per_cycle": 2,  # One for products, one for orders
        "known_limitations": [
            f"Maximum detection lag is {POLL_INTERVAL_MS}ms (poll interval)",
            "Requires full table scans or timestamp indexes",
            "Cannot detect changes during downtime",
            "Misses intermediate changes between polls",
            "Database load increases with poll frequency"
        ],
        "cdc_advantages": [
            "Near real-time change detection (typically <100ms)",
            "Captures every change, including intermediate states",
            "Uses database WAL - no additional query load",
            "Provides before/after states for updates",
            "Survives consumer downtime (events persisted in Kafka)"
        ]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
