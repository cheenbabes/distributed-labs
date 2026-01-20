"""
Saga Orchestrator - Coordinates distributed transactions using the Saga pattern.

The orchestrator manages the order processing saga with three steps:
1. Reserve Inventory
2. Process Payment
3. Ship Order

If any step fails, compensating transactions are executed in reverse order.
"""
import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "saga-orchestrator")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
INVENTORY_URL = os.getenv("INVENTORY_URL", "http://inventory-service:8002")
PAYMENT_URL = os.getenv("PAYMENT_URL", "http://payment-service:8003")
SHIPPING_URL = os.getenv("SHIPPING_URL", "http://shipping-service:8004")

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
SAGAS_STARTED = Counter(
    "sagas_started_total",
    "Total sagas started",
    ["service"]
)
SAGAS_COMPLETED = Counter(
    "sagas_completed_total",
    "Total sagas completed successfully",
    ["service"]
)
SAGAS_COMPENSATED = Counter(
    "sagas_compensated_total",
    "Total sagas that required compensation (rollback)",
    ["service", "failed_step"]
)
SAGA_STEP_DURATION = Histogram(
    "saga_step_duration_seconds",
    "Duration of each saga step",
    ["service", "step", "action"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
ACTIVE_SAGAS = Gauge(
    "active_sagas",
    "Number of currently active sagas",
    ["service"]
)
SAGA_STEP_FAILURES = Counter(
    "saga_step_failures_total",
    "Total saga step failures",
    ["service", "step"]
)
COMPENSATION_ACTIONS = Counter(
    "compensation_actions_total",
    "Total compensation actions executed",
    ["service", "step"]
)


class SagaState(str, Enum):
    PENDING = "pending"
    INVENTORY_RESERVED = "inventory_reserved"
    PAYMENT_PROCESSED = "payment_processed"
    SHIPPED = "shipped"
    COMPLETED = "completed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    FAILED = "failed"


class SagaStep(str, Enum):
    RESERVE_INVENTORY = "reserve_inventory"
    PROCESS_PAYMENT = "process_payment"
    SHIP_ORDER = "ship_order"


@dataclass
class SagaExecution:
    saga_id: str
    order_id: str
    state: SagaState
    created_at: datetime
    updated_at: datetime
    completed_steps: List[SagaStep] = field(default_factory=list)
    failed_step: Optional[SagaStep] = None
    error_message: Optional[str] = None
    compensation_completed: List[SagaStep] = field(default_factory=list)
    inventory_reservation_id: Optional[str] = None
    payment_transaction_id: Optional[str] = None
    shipment_id: Optional[str] = None


# In-memory saga store (in production, use a durable store)
saga_store: Dict[str, SagaExecution] = {}


class OrderRequest(BaseModel):
    order_id: str
    customer_id: str
    items: List[dict]
    total_amount: float


class SagaResponse(BaseModel):
    saga_id: str
    order_id: str
    state: str
    completed_steps: List[str]
    failed_step: Optional[str] = None
    error_message: Optional[str] = None
    compensation_completed: List[str] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Inventory URL: {INVENTORY_URL}")
    logger.info(f"Payment URL: {PAYMENT_URL}")
    logger.info(f"Shipping URL: {SHIPPING_URL}")
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


async def execute_step(
    step: SagaStep,
    url: str,
    payload: dict,
    saga: SagaExecution
) -> dict:
    """Execute a single saga step with timing and error handling."""
    start_time = time.time()

    with tracer.start_as_current_span(f"saga_step_{step.value}") as span:
        span.set_attribute("saga.id", saga.saga_id)
        span.set_attribute("saga.step", step.value)
        span.set_attribute("saga.order_id", saga.order_id)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload)

                if response.status_code != 200:
                    error_detail = response.json().get("detail", "Unknown error")
                    raise Exception(f"Step failed: {error_detail}")

                result = response.json()

                duration = time.time() - start_time
                SAGA_STEP_DURATION.labels(
                    service=SERVICE_NAME,
                    step=step.value,
                    action="execute"
                ).observe(duration)

                span.set_attribute("saga.step.success", True)
                span.set_attribute("saga.step.duration_ms", duration * 1000)

                logger.info(f"Saga {saga.saga_id}: Step {step.value} completed in {duration*1000:.0f}ms")

                return result

        except Exception as e:
            duration = time.time() - start_time
            SAGA_STEP_FAILURES.labels(
                service=SERVICE_NAME,
                step=step.value
            ).inc()

            span.set_attribute("saga.step.success", False)
            span.set_attribute("saga.step.error", str(e))

            logger.error(f"Saga {saga.saga_id}: Step {step.value} failed: {e}")
            raise


async def compensate_step(
    step: SagaStep,
    url: str,
    payload: dict,
    saga: SagaExecution
) -> dict:
    """Execute a compensation action for a saga step."""
    start_time = time.time()

    with tracer.start_as_current_span(f"saga_compensate_{step.value}") as span:
        span.set_attribute("saga.id", saga.saga_id)
        span.set_attribute("saga.step", step.value)
        span.set_attribute("saga.action", "compensate")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload)
                result = response.json()

                duration = time.time() - start_time
                SAGA_STEP_DURATION.labels(
                    service=SERVICE_NAME,
                    step=step.value,
                    action="compensate"
                ).observe(duration)

                COMPENSATION_ACTIONS.labels(
                    service=SERVICE_NAME,
                    step=step.value
                ).inc()

                span.set_attribute("saga.compensation.success", True)

                logger.info(f"Saga {saga.saga_id}: Compensated {step.value} in {duration*1000:.0f}ms")

                return result

        except Exception as e:
            span.set_attribute("saga.compensation.success", False)
            span.set_attribute("saga.compensation.error", str(e))
            logger.error(f"Saga {saga.saga_id}: Compensation for {step.value} failed: {e}")
            raise


async def run_compensation(saga: SagaExecution):
    """Run compensation for all completed steps in reverse order."""
    saga.state = SagaState.COMPENSATING
    saga.updated_at = datetime.utcnow()

    with tracer.start_as_current_span("saga_compensation") as span:
        span.set_attribute("saga.id", saga.saga_id)
        span.set_attribute("saga.steps_to_compensate", [s.value for s in saga.completed_steps])

        # Compensate in reverse order
        for step in reversed(saga.completed_steps):
            try:
                if step == SagaStep.SHIP_ORDER and saga.shipment_id:
                    await compensate_step(
                        step,
                        f"{SHIPPING_URL}/cancel",
                        {"shipment_id": saga.shipment_id, "saga_id": saga.saga_id},
                        saga
                    )
                    saga.compensation_completed.append(step)

                elif step == SagaStep.PROCESS_PAYMENT and saga.payment_transaction_id:
                    await compensate_step(
                        step,
                        f"{PAYMENT_URL}/refund",
                        {"transaction_id": saga.payment_transaction_id, "saga_id": saga.saga_id},
                        saga
                    )
                    saga.compensation_completed.append(step)

                elif step == SagaStep.RESERVE_INVENTORY and saga.inventory_reservation_id:
                    await compensate_step(
                        step,
                        f"{INVENTORY_URL}/release",
                        {"reservation_id": saga.inventory_reservation_id, "saga_id": saga.saga_id},
                        saga
                    )
                    saga.compensation_completed.append(step)

            except Exception as e:
                logger.error(f"Saga {saga.saga_id}: Failed to compensate {step.value}: {e}")
                # Continue with other compensations even if one fails

        saga.state = SagaState.COMPENSATED
        saga.updated_at = datetime.utcnow()


@app.post("/saga/execute", response_model=SagaResponse)
async def execute_saga(order: OrderRequest):
    """Execute the order processing saga."""
    saga_id = str(uuid.uuid4())

    with tracer.start_as_current_span("saga_execution") as span:
        span.set_attribute("saga.id", saga_id)
        span.set_attribute("saga.order_id", order.order_id)

        saga = SagaExecution(
            saga_id=saga_id,
            order_id=order.order_id,
            state=SagaState.PENDING,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        saga_store[saga_id] = saga

        SAGAS_STARTED.labels(service=SERVICE_NAME).inc()
        ACTIVE_SAGAS.labels(service=SERVICE_NAME).inc()

        try:
            # Step 1: Reserve Inventory
            logger.info(f"Saga {saga_id}: Starting inventory reservation")
            inventory_result = await execute_step(
                SagaStep.RESERVE_INVENTORY,
                f"{INVENTORY_URL}/reserve",
                {
                    "saga_id": saga_id,
                    "order_id": order.order_id,
                    "items": order.items
                },
                saga
            )
            saga.inventory_reservation_id = inventory_result.get("reservation_id")
            saga.completed_steps.append(SagaStep.RESERVE_INVENTORY)
            saga.state = SagaState.INVENTORY_RESERVED
            saga.updated_at = datetime.utcnow()

            # Step 2: Process Payment
            logger.info(f"Saga {saga_id}: Starting payment processing")
            payment_result = await execute_step(
                SagaStep.PROCESS_PAYMENT,
                f"{PAYMENT_URL}/process",
                {
                    "saga_id": saga_id,
                    "order_id": order.order_id,
                    "customer_id": order.customer_id,
                    "amount": order.total_amount
                },
                saga
            )
            saga.payment_transaction_id = payment_result.get("transaction_id")
            saga.completed_steps.append(SagaStep.PROCESS_PAYMENT)
            saga.state = SagaState.PAYMENT_PROCESSED
            saga.updated_at = datetime.utcnow()

            # Step 3: Ship Order
            logger.info(f"Saga {saga_id}: Starting shipping")
            shipping_result = await execute_step(
                SagaStep.SHIP_ORDER,
                f"{SHIPPING_URL}/ship",
                {
                    "saga_id": saga_id,
                    "order_id": order.order_id,
                    "customer_id": order.customer_id,
                    "items": order.items
                },
                saga
            )
            saga.shipment_id = shipping_result.get("shipment_id")
            saga.completed_steps.append(SagaStep.SHIP_ORDER)
            saga.state = SagaState.COMPLETED
            saga.updated_at = datetime.utcnow()

            SAGAS_COMPLETED.labels(service=SERVICE_NAME).inc()
            logger.info(f"Saga {saga_id}: Completed successfully")

            span.set_attribute("saga.outcome", "completed")

        except Exception as e:
            # Determine which step failed
            if SagaStep.RESERVE_INVENTORY not in saga.completed_steps:
                saga.failed_step = SagaStep.RESERVE_INVENTORY
            elif SagaStep.PROCESS_PAYMENT not in saga.completed_steps:
                saga.failed_step = SagaStep.PROCESS_PAYMENT
            else:
                saga.failed_step = SagaStep.SHIP_ORDER

            saga.error_message = str(e)
            saga.state = SagaState.FAILED
            saga.updated_at = datetime.utcnow()

            SAGAS_COMPENSATED.labels(
                service=SERVICE_NAME,
                failed_step=saga.failed_step.value
            ).inc()

            span.set_attribute("saga.outcome", "compensated")
            span.set_attribute("saga.failed_step", saga.failed_step.value)

            logger.warning(f"Saga {saga_id}: Failed at {saga.failed_step.value}, starting compensation")

            # Run compensation for completed steps
            if saga.completed_steps:
                await run_compensation(saga)

        finally:
            ACTIVE_SAGAS.labels(service=SERVICE_NAME).dec()

        return SagaResponse(
            saga_id=saga.saga_id,
            order_id=saga.order_id,
            state=saga.state.value,
            completed_steps=[s.value for s in saga.completed_steps],
            failed_step=saga.failed_step.value if saga.failed_step else None,
            error_message=saga.error_message,
            compensation_completed=[s.value for s in saga.compensation_completed]
        )


@app.get("/saga/{saga_id}", response_model=SagaResponse)
async def get_saga(saga_id: str):
    """Get the current state of a saga."""
    if saga_id not in saga_store:
        raise HTTPException(status_code=404, detail="Saga not found")

    saga = saga_store[saga_id]
    return SagaResponse(
        saga_id=saga.saga_id,
        order_id=saga.order_id,
        state=saga.state.value,
        completed_steps=[s.value for s in saga.completed_steps],
        failed_step=saga.failed_step.value if saga.failed_step else None,
        error_message=saga.error_message,
        compensation_completed=[s.value for s in saga.compensation_completed]
    )


@app.get("/sagas")
async def list_sagas():
    """List all sagas and their states."""
    return {
        "sagas": [
            {
                "saga_id": saga.saga_id,
                "order_id": saga.order_id,
                "state": saga.state.value,
                "created_at": saga.created_at.isoformat(),
                "completed_steps": [s.value for s in saga.completed_steps],
                "failed_step": saga.failed_step.value if saga.failed_step else None
            }
            for saga in saga_store.values()
        ],
        "total": len(saga_store)
    }


@app.delete("/sagas")
async def clear_sagas():
    """Clear all saga state (for testing)."""
    saga_store.clear()
    return {"message": "All sagas cleared"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
