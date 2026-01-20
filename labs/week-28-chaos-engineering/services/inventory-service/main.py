"""
Inventory Service - Manages product inventory.

Handles inventory checks, reservations, and releases.
Integrates with chaos controller for chaos injection.
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

import httpx
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "inventory-service")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab28-otel-collector:4317")
CHAOS_CONTROLLER_URL = os.getenv("CHAOS_CONTROLLER_URL", "http://lab28-chaos-controller:8080")

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
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["service", "method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)
INVENTORY_OPERATIONS = Counter(
    "inventory_operations_total",
    "Total inventory operations",
    ["service", "operation", "status"]
)
INVENTORY_LEVEL = Gauge(
    "inventory_level",
    "Current inventory level",
    ["service", "product_id"]
)
CHAOS_INJECTED = Counter(
    "chaos_injected_total",
    "Total chaos injections applied",
    ["service", "chaos_type"]
)

# Simulated inventory database
inventory: Dict[str, int] = {
    "product-001": 100,
    "product-002": 50,
    "product-003": 200,
    "product-004": 75,
    "product-005": 30
}

# Reservations by order_id
reservations: Dict[str, List[Dict]] = {}

# Chaos state cache
chaos_state = {
    "chaos_enabled": False,
    "latency_ms": 0,
    "error_rate": 0.0,
    "last_fetched": 0
}


class InventoryCheckRequest(BaseModel):
    product_id: str
    quantity: int


class ReserveRequest(BaseModel):
    order_id: str
    items: List[Dict]


class ReleaseRequest(BaseModel):
    order_id: str


async def fetch_chaos_state():
    """Fetch current chaos state from controller."""
    global chaos_state
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{CHAOS_CONTROLLER_URL}/chaos/state/{SERVICE_NAME}")
            if response.status_code == 200:
                chaos_state = response.json()
                chaos_state["last_fetched"] = time.time()
    except Exception as e:
        logger.debug(f"Could not fetch chaos state: {e}")


async def apply_chaos():
    """Apply chaos based on current state."""
    if time.time() - chaos_state.get("last_fetched", 0) > 5:
        await fetch_chaos_state()

    if not chaos_state.get("chaos_enabled", False):
        return

    # Apply latency
    latency_ms = chaos_state.get("latency_ms", 0)
    jitter_ms = chaos_state.get("jitter_ms", 0)
    if latency_ms > 0:
        actual_latency = latency_ms + random.randint(-jitter_ms, jitter_ms)
        actual_latency = max(0, actual_latency)
        await asyncio.sleep(actual_latency / 1000.0)
        CHAOS_INJECTED.labels(service=SERVICE_NAME, chaos_type="latency").inc()

    # Apply error injection
    error_rate = chaos_state.get("error_rate", 0.0)
    if error_rate > 0 and random.random() < error_rate:
        CHAOS_INJECTED.labels(service=SERVICE_NAME, chaos_type="error").inc()
        error_code = chaos_state.get("error_code", 500)
        error_message = chaos_state.get("error_message", "Chaos-induced error")
        raise HTTPException(status_code=error_code, detail=error_message)

    # Apply network partition
    drop_rate = chaos_state.get("drop_rate", 0.0)
    if drop_rate > 0 and random.random() < drop_rate:
        CHAOS_INJECTED.labels(service=SERVICE_NAME, chaos_type="network_partition").inc()
        raise HTTPException(status_code=503, detail="Service unavailable (simulated network partition)")

    # Apply CPU load simulation (busy-wait)
    cpu_load = chaos_state.get("cpu_load", 0.0)
    if cpu_load > 0:
        busy_time = cpu_load * 0.1  # Scale to reasonable amount
        end_time = time.time() + busy_time
        while time.time() < end_time:
            _ = sum(i * i for i in range(1000))
        CHAOS_INJECTED.labels(service=SERVICE_NAME, chaos_type="cpu_load").inc()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    # Initialize inventory gauges
    for product_id, quantity in inventory.items():
        INVENTORY_LEVEL.labels(service=SERVICE_NAME, product_id=product_id).set(quantity)
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


@app.get("/chaos/state")
async def get_chaos_state():
    """Get current chaos state."""
    await fetch_chaos_state()
    return chaos_state


@app.get("/inventory")
async def list_inventory():
    """List all inventory."""
    await apply_chaos()
    return {
        "service": SERVICE_NAME,
        "inventory": inventory,
        "reservations_count": len(reservations)
    }


@app.get("/inventory/{product_id}")
async def get_inventory(product_id: str):
    """Get inventory for a specific product."""
    start_time = time.time()

    await apply_chaos()

    # Simulate database lookup
    await asyncio.sleep(random.randint(5, 20) / 1000.0)

    if product_id not in inventory:
        raise HTTPException(status_code=404, detail="Product not found")

    duration = time.time() - start_time

    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/inventory/{id}",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/inventory/{id}"
    ).observe(duration)

    return {
        "service": SERVICE_NAME,
        "product_id": product_id,
        "quantity": inventory[product_id],
        "duration_ms": round(duration * 1000, 2)
    }


@app.post("/inventory/check")
async def check_inventory(request: InventoryCheckRequest):
    """Check if requested quantity is available."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    await apply_chaos()

    # Simulate database check
    await asyncio.sleep(random.randint(10, 30) / 1000.0)

    if request.product_id not in inventory:
        INVENTORY_OPERATIONS.labels(
            service=SERVICE_NAME,
            operation="check",
            status="not_found"
        ).inc()
        raise HTTPException(status_code=404, detail="Product not found")

    available = inventory[request.product_id]
    sufficient = available >= request.quantity

    duration = time.time() - start_time

    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="POST",
        endpoint="/inventory/check",
        status="200" if sufficient else "400"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="POST",
        endpoint="/inventory/check"
    ).observe(duration)
    INVENTORY_OPERATIONS.labels(
        service=SERVICE_NAME,
        operation="check",
        status="sufficient" if sufficient else "insufficient"
    ).inc()

    if not sufficient:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient inventory: requested {request.quantity}, available {available}"
        )

    return {
        "service": SERVICE_NAME,
        "product_id": request.product_id,
        "requested": request.quantity,
        "available": available,
        "sufficient": True,
        "trace_id": trace_id,
        "duration_ms": round(duration * 1000, 2)
    }


@app.post("/inventory/reserve")
async def reserve_inventory(request: ReserveRequest):
    """Reserve inventory for an order."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    await apply_chaos()

    # Simulate transaction
    await asyncio.sleep(random.randint(20, 50) / 1000.0)

    # Check all items first
    for item in request.items:
        product_id = item["product_id"]
        quantity = item["quantity"]

        if product_id not in inventory:
            INVENTORY_OPERATIONS.labels(
                service=SERVICE_NAME,
                operation="reserve",
                status="not_found"
            ).inc()
            raise HTTPException(status_code=404, detail=f"Product {product_id} not found")

        if inventory[product_id] < quantity:
            INVENTORY_OPERATIONS.labels(
                service=SERVICE_NAME,
                operation="reserve",
                status="insufficient"
            ).inc()
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient inventory for {product_id}"
            )

    # Reserve all items
    reservations[request.order_id] = []
    for item in request.items:
        product_id = item["product_id"]
        quantity = item["quantity"]

        inventory[product_id] -= quantity
        INVENTORY_LEVEL.labels(service=SERVICE_NAME, product_id=product_id).set(inventory[product_id])

        reservations[request.order_id].append({
            "product_id": product_id,
            "quantity": quantity,
            "reserved_at": time.time()
        })

    duration = time.time() - start_time

    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="POST",
        endpoint="/inventory/reserve",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="POST",
        endpoint="/inventory/reserve"
    ).observe(duration)
    INVENTORY_OPERATIONS.labels(
        service=SERVICE_NAME,
        operation="reserve",
        status="success"
    ).inc()

    logger.info(f"Reserved inventory for order {request.order_id}")

    return {
        "service": SERVICE_NAME,
        "order_id": request.order_id,
        "reserved_items": reservations[request.order_id],
        "trace_id": trace_id,
        "duration_ms": round(duration * 1000, 2)
    }


@app.post("/inventory/release")
async def release_inventory(request: ReleaseRequest):
    """Release reserved inventory (rollback)."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    await apply_chaos()

    if request.order_id not in reservations:
        return {
            "service": SERVICE_NAME,
            "order_id": request.order_id,
            "status": "no_reservation_found"
        }

    # Release all reserved items
    for item in reservations[request.order_id]:
        product_id = item["product_id"]
        quantity = item["quantity"]

        inventory[product_id] += quantity
        INVENTORY_LEVEL.labels(service=SERVICE_NAME, product_id=product_id).set(inventory[product_id])

    released_items = reservations.pop(request.order_id)

    duration = time.time() - start_time

    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="POST",
        endpoint="/inventory/release",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="POST",
        endpoint="/inventory/release"
    ).observe(duration)
    INVENTORY_OPERATIONS.labels(
        service=SERVICE_NAME,
        operation="release",
        status="success"
    ).inc()

    logger.info(f"Released inventory for order {request.order_id}")

    return {
        "service": SERVICE_NAME,
        "order_id": request.order_id,
        "released_items": released_items,
        "trace_id": trace_id,
        "duration_ms": round(duration * 1000, 2)
    }


@app.post("/inventory/restock/{product_id}")
async def restock_inventory(product_id: str, quantity: int = 50):
    """Restock inventory for a product."""
    await apply_chaos()

    if product_id not in inventory:
        inventory[product_id] = 0

    inventory[product_id] += quantity
    INVENTORY_LEVEL.labels(service=SERVICE_NAME, product_id=product_id).set(inventory[product_id])

    INVENTORY_OPERATIONS.labels(
        service=SERVICE_NAME,
        operation="restock",
        status="success"
    ).inc()

    return {
        "service": SERVICE_NAME,
        "product_id": product_id,
        "new_quantity": inventory[product_id]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
