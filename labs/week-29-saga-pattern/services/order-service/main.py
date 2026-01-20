"""
Order Service - API gateway for order processing using the Saga pattern.

This is the entry point for clients to place orders. It delegates to the
Saga Orchestrator to coordinate the distributed transaction.
"""
import asyncio
import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "order-service")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8001")

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

# Instrument httpx for trace propagation
HTTPXClientInstrumentor().instrument()

# Prometheus metrics
ORDERS_TOTAL = Counter(
    "orders_total",
    "Total orders submitted",
    ["service", "status"]
)
ORDER_LATENCY = Histogram(
    "order_processing_duration_seconds",
    "Order processing duration",
    ["service"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)


class OrderItem(BaseModel):
    product_id: str
    name: str
    quantity: int
    price: float


class CreateOrderRequest(BaseModel):
    customer_id: str
    items: List[OrderItem]


class OrderResponse(BaseModel):
    order_id: str
    customer_id: str
    total_amount: float
    saga_id: str
    saga_state: str
    completed_steps: List[str]
    failed_step: Optional[str] = None
    compensation_completed: List[str] = []
    trace_id: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Orchestrator URL: {ORCHESTRATOR_URL}")
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


@app.post("/orders", response_model=OrderResponse)
async def create_order(order: CreateOrderRequest):
    """Create a new order using the Saga pattern for distributed transaction."""
    start_time = time.time()

    with tracer.start_as_current_span("create_order") as span:
        order_id = str(uuid.uuid4())
        total_amount = sum(item.price * item.quantity for item in order.items)

        current_span = trace.get_current_span()
        trace_id = format(current_span.get_span_context().trace_id, "032x")

        span.set_attribute("order.id", order_id)
        span.set_attribute("order.customer_id", order.customer_id)
        span.set_attribute("order.total_amount", total_amount)
        span.set_attribute("order.items_count", len(order.items))

        logger.info(f"Order {order_id}: Creating order for customer {order.customer_id}, total ${total_amount:.2f}")

        try:
            # Call the saga orchestrator
            async with httpx.AsyncClient(timeout=60.0) as client:
                saga_request = {
                    "order_id": order_id,
                    "customer_id": order.customer_id,
                    "items": [item.model_dump() for item in order.items],
                    "total_amount": total_amount
                }

                response = await client.post(
                    f"{ORCHESTRATOR_URL}/saga/execute",
                    json=saga_request
                )

                if response.status_code != 200:
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"Saga execution failed: {response.text}"
                    )

                saga_result = response.json()

            duration = time.time() - start_time

            # Determine if order was successful
            is_success = saga_result["state"] == "completed"
            status = "success" if is_success else "failed"

            ORDERS_TOTAL.labels(service=SERVICE_NAME, status=status).inc()
            ORDER_LATENCY.labels(service=SERVICE_NAME).observe(duration)

            span.set_attribute("order.status", status)
            span.set_attribute("order.saga_state", saga_result["state"])
            span.set_attribute("order.duration_ms", duration * 1000)

            if is_success:
                logger.info(f"Order {order_id}: Completed successfully in {duration*1000:.0f}ms")
            else:
                logger.warning(f"Order {order_id}: Failed at {saga_result.get('failed_step')}, compensated in {duration*1000:.0f}ms")

            return OrderResponse(
                order_id=order_id,
                customer_id=order.customer_id,
                total_amount=total_amount,
                saga_id=saga_result["saga_id"],
                saga_state=saga_result["state"],
                completed_steps=saga_result["completed_steps"],
                failed_step=saga_result.get("failed_step"),
                compensation_completed=saga_result.get("compensation_completed", []),
                trace_id=trace_id
            )

        except httpx.RequestError as e:
            ORDERS_TOTAL.labels(service=SERVICE_NAME, status="error").inc()
            span.set_attribute("order.status", "error")
            span.set_attribute("order.error", str(e))
            logger.error(f"Order {order_id}: Failed to reach orchestrator: {e}")
            raise HTTPException(status_code=503, detail=f"Service unavailable: {e}")


@app.get("/orders/{order_id}/saga")
async def get_order_saga(order_id: str):
    """Get the saga state for an order (queries all sagas and finds by order_id)."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{ORCHESTRATOR_URL}/sagas")
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch sagas")

        sagas = response.json()["sagas"]
        for saga in sagas:
            if saga["order_id"] == order_id:
                return saga

        raise HTTPException(status_code=404, detail="Order saga not found")


@app.get("/demo/order")
async def demo_order():
    """Generate a demo order for testing."""
    demo_items = [
        OrderItem(product_id="PROD-001", name="Widget Pro", quantity=2, price=29.99),
        OrderItem(product_id="PROD-002", name="Gadget Plus", quantity=1, price=49.99),
    ]

    order_request = CreateOrderRequest(
        customer_id=f"CUST-{random.randint(1000, 9999)}",
        items=demo_items
    )

    return await create_order(order_request)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
