"""
Order Service - Handles order creation and management.

Coordinates with inventory and payment services.
Integrates with chaos controller for chaos injection.
"""
import asyncio
import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
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
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "order-service")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab28-otel-collector:4317")
INVENTORY_SERVICE_URL = os.getenv("INVENTORY_SERVICE_URL", "http://lab28-inventory-service:8002")
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://lab28-payment-service:8003")
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

# Instrument httpx
HTTPXClientInstrumentor().instrument()

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
ORDER_COUNT = Counter(
    "orders_total",
    "Total orders processed",
    ["service", "status"]
)
CHAOS_INJECTED = Counter(
    "chaos_injected_total",
    "Total chaos injections applied",
    ["service", "chaos_type"]
)
DOWNSTREAM_ERRORS = Counter(
    "downstream_errors_total",
    "Errors from downstream services",
    ["service", "downstream_service", "error_type"]
)

# In-memory order storage
orders: Dict[str, Dict] = {}

# Chaos state cache
chaos_state = {
    "chaos_enabled": False,
    "latency_ms": 0,
    "error_rate": 0.0,
    "last_fetched": 0
}


class OrderItem(BaseModel):
    product_id: str
    quantity: int


class CreateOrderRequest(BaseModel):
    items: List[OrderItem]
    customer_id: Optional[str] = "anonymous"


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Inventory service URL: {INVENTORY_SERVICE_URL}")
    logger.info(f"Payment service URL: {PAYMENT_SERVICE_URL}")
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


@app.post("/orders")
async def create_order(order_request: CreateOrderRequest):
    """
    Create a new order.

    Flow:
    1. Validate order
    2. Check inventory
    3. Reserve inventory
    4. Process payment
    5. Confirm order
    """
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Apply chaos
    await apply_chaos()

    order_id = str(uuid.uuid4())[:8]

    # Simulate order validation
    await asyncio.sleep(random.randint(10, 30) / 1000.0)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1: Check inventory for all items
            inventory_results = []
            for item in order_request.items:
                with tracer.start_as_current_span("check_inventory") as span:
                    span.set_attribute("product_id", item.product_id)
                    span.set_attribute("quantity", item.quantity)

                    response = await client.post(
                        f"{INVENTORY_SERVICE_URL}/inventory/check",
                        json={"product_id": item.product_id, "quantity": item.quantity}
                    )

                    if response.status_code != 200:
                        ORDER_COUNT.labels(service=SERVICE_NAME, status="inventory_failed").inc()
                        raise HTTPException(
                            status_code=400,
                            detail=f"Insufficient inventory for {item.product_id}"
                        )

                    inventory_results.append(response.json())

            # Step 2: Reserve inventory
            with tracer.start_as_current_span("reserve_inventory") as span:
                reserve_response = await client.post(
                    f"{INVENTORY_SERVICE_URL}/inventory/reserve",
                    json={
                        "order_id": order_id,
                        "items": [item.dict() for item in order_request.items]
                    }
                )

                if reserve_response.status_code != 200:
                    ORDER_COUNT.labels(service=SERVICE_NAME, status="reserve_failed").inc()
                    raise HTTPException(status_code=400, detail="Failed to reserve inventory")

            # Step 3: Process payment
            total_amount = sum(
                item.quantity * 10.0  # Simplified pricing
                for item in order_request.items
            )

            with tracer.start_as_current_span("process_payment") as span:
                span.set_attribute("amount", total_amount)

                payment_response = await client.post(
                    f"{PAYMENT_SERVICE_URL}/payments/process",
                    json={
                        "order_id": order_id,
                        "amount": total_amount,
                        "customer_id": order_request.customer_id
                    }
                )

                if payment_response.status_code != 200:
                    # Rollback inventory reservation
                    await client.post(
                        f"{INVENTORY_SERVICE_URL}/inventory/release",
                        json={"order_id": order_id}
                    )
                    ORDER_COUNT.labels(service=SERVICE_NAME, status="payment_failed").inc()
                    raise HTTPException(status_code=400, detail="Payment failed")

                payment_result = payment_response.json()

        # Create order record
        order = {
            "order_id": order_id,
            "customer_id": order_request.customer_id,
            "items": [item.dict() for item in order_request.items],
            "total_amount": total_amount,
            "status": "confirmed",
            "payment_id": payment_result.get("payment_id"),
            "created_at": time.time(),
            "trace_id": trace_id
        }
        orders[order_id] = order

        duration = time.time() - start_time

        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="POST",
            endpoint="/orders",
            status="200"
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME,
            method="POST",
            endpoint="/orders"
        ).observe(duration)
        ORDER_COUNT.labels(service=SERVICE_NAME, status="confirmed").inc()

        logger.info(f"Order {order_id} created successfully, trace_id={trace_id}")

        return {
            "service": SERVICE_NAME,
            "order": order,
            "duration_ms": round(duration * 1000, 2)
        }

    except httpx.RequestError as e:
        DOWNSTREAM_ERRORS.labels(
            service=SERVICE_NAME,
            downstream_service="unknown",
            error_type="connection_error"
        ).inc()
        raise HTTPException(status_code=503, detail=f"Downstream service error: {str(e)}")


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    """Get order details."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    # Apply chaos
    await apply_chaos()

    if order_id not in orders:
        raise HTTPException(status_code=404, detail="Order not found")

    duration = time.time() - start_time

    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/orders/{id}",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/orders/{id}"
    ).observe(duration)

    return {
        "service": SERVICE_NAME,
        "order": orders[order_id],
        "trace_id": trace_id,
        "duration_ms": round(duration * 1000, 2)
    }


@app.get("/orders")
async def list_orders():
    """List all orders."""
    await apply_chaos()
    return {
        "service": SERVICE_NAME,
        "orders": list(orders.values()),
        "count": len(orders)
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
