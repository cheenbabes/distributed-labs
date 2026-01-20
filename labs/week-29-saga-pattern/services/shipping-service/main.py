"""
Shipping Service - Handles order shipping for the saga pattern.

Provides:
- POST /ship - Ship an order
- POST /cancel - Cancel (compensate) a shipment
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "shipping-service")
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
SHIPMENTS_TOTAL = Counter(
    "shipments_total",
    "Total shipment attempts",
    ["service", "status"]
)
CANCELLATIONS_TOTAL = Counter(
    "shipment_cancellations_total",
    "Total shipment cancellations (compensations)",
    ["service"]
)
OPERATION_DURATION = Histogram(
    "shipping_operation_duration_seconds",
    "Duration of shipping operations",
    ["service", "operation"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

# In-memory shipment store
shipments: Dict[str, dict] = {}

# Configurable failure rate
current_fail_rate = FAIL_RATE


class ShipRequest(BaseModel):
    saga_id: str
    order_id: str
    customer_id: str
    items: List[dict]


class CancelRequest(BaseModel):
    shipment_id: str
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


@app.post("/ship")
async def ship_order(request: ShipRequest):
    """Ship an order."""
    global current_fail_rate
    start_time = time.time()

    with tracer.start_as_current_span("ship_order") as span:
        span.set_attribute("saga.id", request.saga_id)
        span.set_attribute("order.id", request.order_id)
        span.set_attribute("customer.id", request.customer_id)
        span.set_attribute("items.count", len(request.items))

        # Simulate shipping processing time (longest step)
        await asyncio.sleep(random.uniform(0.08, 0.20))

        # Check for injected failure
        if current_fail_rate > 0 and random.random() < current_fail_rate:
            SHIPMENTS_TOTAL.labels(service=SERVICE_NAME, status="failed").inc()
            span.set_attribute("shipping.status", "failed")
            span.set_attribute("shipping.failure_reason", "injected_failure")
            logger.warning(f"Saga {request.saga_id}: Shipping failed (injected)")
            raise HTTPException(
                status_code=500,
                detail="Shipping failed: carrier unavailable"
            )

        # Create shipment
        shipment_id = str(uuid.uuid4())
        tracking_number = f"TRK{random.randint(100000000, 999999999)}"

        shipments[shipment_id] = {
            "shipment_id": shipment_id,
            "saga_id": request.saga_id,
            "order_id": request.order_id,
            "customer_id": request.customer_id,
            "items": request.items,
            "tracking_number": tracking_number,
            "status": "shipped",
            "carrier": "FastShip Express",
            "created_at": datetime.utcnow().isoformat(),
            "estimated_delivery": "3-5 business days"
        }

        duration = time.time() - start_time
        SHIPMENTS_TOTAL.labels(service=SERVICE_NAME, status="success").inc()
        OPERATION_DURATION.labels(service=SERVICE_NAME, operation="ship").observe(duration)

        span.set_attribute("shipping.status", "shipped")
        span.set_attribute("shipping.shipment_id", shipment_id)
        span.set_attribute("shipping.tracking_number", tracking_number)

        logger.info(f"Saga {request.saga_id}: Order shipped (id={shipment_id}, tracking={tracking_number})")

        return {
            "shipment_id": shipment_id,
            "saga_id": request.saga_id,
            "order_id": request.order_id,
            "tracking_number": tracking_number,
            "status": "shipped",
            "carrier": "FastShip Express"
        }


@app.post("/cancel")
async def cancel_shipment(request: CancelRequest):
    """Cancel (compensate) a shipment."""
    start_time = time.time()

    with tracer.start_as_current_span("cancel_shipment") as span:
        span.set_attribute("saga.id", request.saga_id)
        span.set_attribute("shipping.shipment_id", request.shipment_id)
        span.set_attribute("shipping.action", "compensate")

        # Simulate cancellation processing time
        await asyncio.sleep(random.uniform(0.02, 0.08))

        if request.shipment_id in shipments:
            shipment = shipments[request.shipment_id]
            shipment["status"] = "cancelled"
            shipment["cancelled_at"] = datetime.utcnow().isoformat()

            span.set_attribute("shipping.status", "cancelled")
            logger.info(f"Saga {request.saga_id}: Shipment cancelled (id={request.shipment_id})")
        else:
            span.set_attribute("shipping.status", "not_found")
            logger.warning(f"Saga {request.saga_id}: Shipment not found (id={request.shipment_id})")

        duration = time.time() - start_time
        CANCELLATIONS_TOTAL.labels(service=SERVICE_NAME).inc()
        OPERATION_DURATION.labels(service=SERVICE_NAME, operation="cancel").observe(duration)

        return {
            "shipment_id": request.shipment_id,
            "saga_id": request.saga_id,
            "status": "cancelled",
            "action": "compensation_completed"
        }


@app.get("/shipments")
async def list_shipments():
    """List all shipments."""
    return {
        "shipments": list(shipments.values()),
        "total": len(shipments)
    }


@app.get("/shipments/{shipment_id}")
async def get_shipment(shipment_id: str):
    """Get a specific shipment."""
    if shipment_id not in shipments:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return shipments[shipment_id]


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


@app.delete("/shipments")
async def clear_shipments():
    """Clear all shipments (for testing)."""
    shipments.clear()
    return {"message": "All shipments cleared"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
