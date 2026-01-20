"""
Payment Service - Handles payment processing.

Simulates payment gateway integration with configurable failure modes.
Integrates with chaos controller for chaos injection.
"""
import asyncio
import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from typing import Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "payment-service")
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
PAYMENT_COUNT = Counter(
    "payments_total",
    "Total payments processed",
    ["service", "status"]
)
PAYMENT_AMOUNT = Counter(
    "payment_amount_total",
    "Total payment amount processed",
    ["service", "status"]
)
CHAOS_INJECTED = Counter(
    "chaos_injected_total",
    "Total chaos injections applied",
    ["service", "chaos_type"]
)

# Simulated payment records
payments: Dict[str, Dict] = {}

# Chaos state cache
chaos_state = {
    "chaos_enabled": False,
    "latency_ms": 0,
    "error_rate": 0.0,
    "last_fetched": 0
}


class PaymentRequest(BaseModel):
    order_id: str
    amount: float
    customer_id: str
    payment_method: Optional[str] = "card"


class RefundRequest(BaseModel):
    payment_id: str
    reason: Optional[str] = "customer_request"


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


@app.post("/payments/process")
async def process_payment(request: PaymentRequest):
    """
    Process a payment.

    Simulates external payment gateway call with configurable latency.
    """
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    await apply_chaos()

    payment_id = f"pay_{uuid.uuid4().hex[:12]}"

    # Simulate payment gateway call (external service latency)
    with tracer.start_as_current_span("payment_gateway_call") as span:
        span.set_attribute("payment_id", payment_id)
        span.set_attribute("amount", request.amount)
        span.set_attribute("payment_method", request.payment_method)

        # Simulate external call latency
        gateway_latency = random.randint(50, 150) / 1000.0
        await asyncio.sleep(gateway_latency)

        # Simulate rare payment gateway failures (5% base rate)
        if random.random() < 0.05:
            PAYMENT_COUNT.labels(service=SERVICE_NAME, status="gateway_error").inc()
            raise HTTPException(
                status_code=502,
                detail="Payment gateway temporarily unavailable"
            )

    # Simulate fraud check
    with tracer.start_as_current_span("fraud_check") as span:
        await asyncio.sleep(random.randint(20, 40) / 1000.0)

        # Very low rate of fraud detection
        if random.random() < 0.01:
            PAYMENT_COUNT.labels(service=SERVICE_NAME, status="fraud_detected").inc()
            raise HTTPException(
                status_code=400,
                detail="Payment declined: fraud detection triggered"
            )

    # Create payment record
    payment = {
        "payment_id": payment_id,
        "order_id": request.order_id,
        "customer_id": request.customer_id,
        "amount": request.amount,
        "payment_method": request.payment_method,
        "status": "completed",
        "created_at": time.time(),
        "trace_id": trace_id
    }
    payments[payment_id] = payment

    duration = time.time() - start_time

    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="POST",
        endpoint="/payments/process",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="POST",
        endpoint="/payments/process"
    ).observe(duration)
    PAYMENT_COUNT.labels(service=SERVICE_NAME, status="completed").inc()
    PAYMENT_AMOUNT.labels(service=SERVICE_NAME, status="completed").inc(request.amount)

    logger.info(f"Payment {payment_id} processed for order {request.order_id}, amount=${request.amount}")

    return {
        "service": SERVICE_NAME,
        "payment_id": payment_id,
        "order_id": request.order_id,
        "amount": request.amount,
        "status": "completed",
        "trace_id": trace_id,
        "duration_ms": round(duration * 1000, 2)
    }


@app.get("/payments/{payment_id}")
async def get_payment(payment_id: str):
    """Get payment details."""
    start_time = time.time()

    await apply_chaos()

    if payment_id not in payments:
        raise HTTPException(status_code=404, detail="Payment not found")

    duration = time.time() - start_time

    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/payments/{id}",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/payments/{id}"
    ).observe(duration)

    return {
        "service": SERVICE_NAME,
        "payment": payments[payment_id],
        "duration_ms": round(duration * 1000, 2)
    }


@app.post("/payments/refund")
async def refund_payment(request: RefundRequest):
    """Process a refund."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    await apply_chaos()

    if request.payment_id not in payments:
        raise HTTPException(status_code=404, detail="Payment not found")

    payment = payments[request.payment_id]

    if payment["status"] == "refunded":
        raise HTTPException(status_code=400, detail="Payment already refunded")

    # Simulate refund processing
    await asyncio.sleep(random.randint(30, 80) / 1000.0)

    payment["status"] = "refunded"
    payment["refund_reason"] = request.reason
    payment["refunded_at"] = time.time()

    duration = time.time() - start_time

    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="POST",
        endpoint="/payments/refund",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="POST",
        endpoint="/payments/refund"
    ).observe(duration)
    PAYMENT_COUNT.labels(service=SERVICE_NAME, status="refunded").inc()

    logger.info(f"Payment {request.payment_id} refunded, reason: {request.reason}")

    return {
        "service": SERVICE_NAME,
        "payment_id": request.payment_id,
        "status": "refunded",
        "reason": request.reason,
        "trace_id": trace_id,
        "duration_ms": round(duration * 1000, 2)
    }


@app.get("/payments")
async def list_payments():
    """List all payments."""
    await apply_chaos()
    return {
        "service": SERVICE_NAME,
        "payments": list(payments.values()),
        "count": len(payments)
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
