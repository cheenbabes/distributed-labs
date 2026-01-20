"""
Inventory Service - Manages inventory reservations for the saga pattern.

Provides:
- POST /reserve - Reserve inventory for an order
- POST /release - Release (compensate) a reservation
- GET /admin/fail-rate - Get current failure rate
- POST /admin/fail-rate - Set failure rate for testing
"""
import asyncio
import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "inventory-service")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
FAIL_RATE = float(os.getenv("FAIL_RATE", "0.0"))

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
RESERVATIONS_TOTAL = Counter(
    "inventory_reservations_total",
    "Total inventory reservations attempted",
    ["service", "status"]
)
RELEASES_TOTAL = Counter(
    "inventory_releases_total",
    "Total inventory releases (compensations)",
    ["service"]
)
OPERATION_DURATION = Histogram(
    "inventory_operation_duration_seconds",
    "Duration of inventory operations",
    ["service", "operation"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

# In-memory reservation store
reservations: Dict[str, dict] = {}

# Configurable failure rate
current_fail_rate = FAIL_RATE


class ReserveRequest(BaseModel):
    saga_id: str
    order_id: str
    items: List[dict]


class ReleaseRequest(BaseModel):
    reservation_id: str
    saga_id: str


class FailRateRequest(BaseModel):
    rate: float


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Initial fail rate: {current_fail_rate}")
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


@app.post("/reserve")
async def reserve_inventory(request: ReserveRequest):
    """Reserve inventory for an order."""
    global current_fail_rate
    start_time = time.time()

    with tracer.start_as_current_span("reserve_inventory") as span:
        span.set_attribute("saga.id", request.saga_id)
        span.set_attribute("order.id", request.order_id)
        span.set_attribute("items.count", len(request.items))

        # Simulate processing time
        await asyncio.sleep(random.uniform(0.02, 0.08))

        # Check for injected failure
        if current_fail_rate > 0 and random.random() < current_fail_rate:
            RESERVATIONS_TOTAL.labels(service=SERVICE_NAME, status="failed").inc()
            span.set_attribute("inventory.status", "failed")
            span.set_attribute("inventory.failure_reason", "injected_failure")
            logger.warning(f"Saga {request.saga_id}: Inventory reservation failed (injected)")
            raise HTTPException(
                status_code=500,
                detail="Inventory reservation failed: insufficient stock"
            )

        # Create reservation
        reservation_id = str(uuid.uuid4())
        reservations[reservation_id] = {
            "reservation_id": reservation_id,
            "saga_id": request.saga_id,
            "order_id": request.order_id,
            "items": request.items,
            "status": "reserved",
            "created_at": datetime.utcnow().isoformat()
        }

        duration = time.time() - start_time
        RESERVATIONS_TOTAL.labels(service=SERVICE_NAME, status="success").inc()
        OPERATION_DURATION.labels(service=SERVICE_NAME, operation="reserve").observe(duration)

        span.set_attribute("inventory.status", "reserved")
        span.set_attribute("inventory.reservation_id", reservation_id)

        logger.info(f"Saga {request.saga_id}: Inventory reserved (id={reservation_id})")

        return {
            "reservation_id": reservation_id,
            "saga_id": request.saga_id,
            "order_id": request.order_id,
            "status": "reserved",
            "items_reserved": len(request.items)
        }


@app.post("/release")
async def release_inventory(request: ReleaseRequest):
    """Release (compensate) an inventory reservation."""
    start_time = time.time()

    with tracer.start_as_current_span("release_inventory") as span:
        span.set_attribute("saga.id", request.saga_id)
        span.set_attribute("inventory.reservation_id", request.reservation_id)
        span.set_attribute("inventory.action", "compensate")

        # Simulate processing time
        await asyncio.sleep(random.uniform(0.01, 0.05))

        if request.reservation_id in reservations:
            reservation = reservations[request.reservation_id]
            reservation["status"] = "released"
            reservation["released_at"] = datetime.utcnow().isoformat()

            span.set_attribute("inventory.status", "released")
            logger.info(f"Saga {request.saga_id}: Inventory released (id={request.reservation_id})")
        else:
            span.set_attribute("inventory.status", "not_found")
            logger.warning(f"Saga {request.saga_id}: Reservation not found (id={request.reservation_id})")

        duration = time.time() - start_time
        RELEASES_TOTAL.labels(service=SERVICE_NAME).inc()
        OPERATION_DURATION.labels(service=SERVICE_NAME, operation="release").observe(duration)

        return {
            "reservation_id": request.reservation_id,
            "saga_id": request.saga_id,
            "status": "released",
            "action": "compensation_completed"
        }


@app.get("/reservations")
async def list_reservations():
    """List all reservations."""
    return {
        "reservations": list(reservations.values()),
        "total": len(reservations)
    }


@app.get("/reservations/{reservation_id}")
async def get_reservation(reservation_id: str):
    """Get a specific reservation."""
    if reservation_id not in reservations:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return reservations[reservation_id]


@app.get("/admin/fail-rate")
async def get_fail_rate():
    """Get current failure rate."""
    return {"fail_rate": current_fail_rate, "service": SERVICE_NAME}


@app.post("/admin/fail-rate")
async def set_fail_rate(request: FailRateRequest):
    """Set failure rate for testing."""
    global current_fail_rate
    if request.rate < 0 or request.rate > 1:
        raise HTTPException(status_code=400, detail="Rate must be between 0 and 1")

    old_rate = current_fail_rate
    current_fail_rate = request.rate
    logger.info(f"Fail rate changed from {old_rate} to {current_fail_rate}")

    return {
        "fail_rate": current_fail_rate,
        "previous_rate": old_rate,
        "service": SERVICE_NAME
    }


@app.delete("/reservations")
async def clear_reservations():
    """Clear all reservations (for testing)."""
    reservations.clear()
    return {"message": "All reservations cleared"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
