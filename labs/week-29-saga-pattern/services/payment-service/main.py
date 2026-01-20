"""
Payment Service - Processes payments for the saga pattern.

Provides:
- POST /process - Process a payment
- POST /refund - Refund (compensate) a payment
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
from typing import Dict, Optional

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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "payment-service")
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
PAYMENTS_TOTAL = Counter(
    "payments_total",
    "Total payment attempts",
    ["service", "status"]
)
REFUNDS_TOTAL = Counter(
    "refunds_total",
    "Total refunds (compensations)",
    ["service"]
)
PAYMENT_AMOUNT = Histogram(
    "payment_amount_dollars",
    "Payment amounts processed",
    ["service"],
    buckets=[10, 25, 50, 100, 250, 500, 1000, 2500, 5000]
)
OPERATION_DURATION = Histogram(
    "payment_operation_duration_seconds",
    "Duration of payment operations",
    ["service", "operation"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

# In-memory transaction store
transactions: Dict[str, dict] = {}

# Configurable failure rate
current_fail_rate = FAIL_RATE


class ProcessRequest(BaseModel):
    saga_id: str
    order_id: str
    customer_id: str
    amount: float


class RefundRequest(BaseModel):
    transaction_id: str
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


@app.post("/process")
async def process_payment(request: ProcessRequest):
    """Process a payment for an order."""
    global current_fail_rate
    start_time = time.time()

    with tracer.start_as_current_span("process_payment") as span:
        span.set_attribute("saga.id", request.saga_id)
        span.set_attribute("order.id", request.order_id)
        span.set_attribute("customer.id", request.customer_id)
        span.set_attribute("payment.amount", request.amount)

        # Simulate payment processing time (slightly longer than inventory)
        await asyncio.sleep(random.uniform(0.05, 0.15))

        # Check for injected failure
        if current_fail_rate > 0 and random.random() < current_fail_rate:
            PAYMENTS_TOTAL.labels(service=SERVICE_NAME, status="failed").inc()
            span.set_attribute("payment.status", "declined")
            span.set_attribute("payment.failure_reason", "injected_failure")
            logger.warning(f"Saga {request.saga_id}: Payment declined (injected)")
            raise HTTPException(
                status_code=500,
                detail="Payment declined: insufficient funds"
            )

        # Create transaction
        transaction_id = str(uuid.uuid4())
        transactions[transaction_id] = {
            "transaction_id": transaction_id,
            "saga_id": request.saga_id,
            "order_id": request.order_id,
            "customer_id": request.customer_id,
            "amount": request.amount,
            "status": "completed",
            "created_at": datetime.utcnow().isoformat()
        }

        duration = time.time() - start_time
        PAYMENTS_TOTAL.labels(service=SERVICE_NAME, status="success").inc()
        PAYMENT_AMOUNT.labels(service=SERVICE_NAME).observe(request.amount)
        OPERATION_DURATION.labels(service=SERVICE_NAME, operation="process").observe(duration)

        span.set_attribute("payment.status", "completed")
        span.set_attribute("payment.transaction_id", transaction_id)

        logger.info(f"Saga {request.saga_id}: Payment processed ${request.amount} (tx={transaction_id})")

        return {
            "transaction_id": transaction_id,
            "saga_id": request.saga_id,
            "order_id": request.order_id,
            "amount": request.amount,
            "status": "completed"
        }


@app.post("/refund")
async def refund_payment(request: RefundRequest):
    """Refund (compensate) a payment."""
    start_time = time.time()

    with tracer.start_as_current_span("refund_payment") as span:
        span.set_attribute("saga.id", request.saga_id)
        span.set_attribute("payment.transaction_id", request.transaction_id)
        span.set_attribute("payment.action", "compensate")

        # Simulate refund processing time
        await asyncio.sleep(random.uniform(0.03, 0.10))

        refund_amount = 0
        if request.transaction_id in transactions:
            transaction = transactions[request.transaction_id]
            refund_amount = transaction["amount"]
            transaction["status"] = "refunded"
            transaction["refunded_at"] = datetime.utcnow().isoformat()

            span.set_attribute("payment.status", "refunded")
            span.set_attribute("payment.refund_amount", refund_amount)
            logger.info(f"Saga {request.saga_id}: Payment refunded ${refund_amount} (tx={request.transaction_id})")
        else:
            span.set_attribute("payment.status", "not_found")
            logger.warning(f"Saga {request.saga_id}: Transaction not found (tx={request.transaction_id})")

        duration = time.time() - start_time
        REFUNDS_TOTAL.labels(service=SERVICE_NAME).inc()
        OPERATION_DURATION.labels(service=SERVICE_NAME, operation="refund").observe(duration)

        return {
            "transaction_id": request.transaction_id,
            "saga_id": request.saga_id,
            "refund_amount": refund_amount,
            "status": "refunded",
            "action": "compensation_completed"
        }


@app.get("/transactions")
async def list_transactions():
    """List all transactions."""
    return {
        "transactions": list(transactions.values()),
        "total": len(transactions)
    }


@app.get("/transactions/{transaction_id}")
async def get_transaction(transaction_id: str):
    """Get a specific transaction."""
    if transaction_id not in transactions:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transactions[transaction_id]


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


@app.delete("/transactions")
async def clear_transactions():
    """Clear all transactions (for testing)."""
    transactions.clear()
    return {"message": "All transactions cleared"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
