"""
Webhook Sender Service - Reliable webhook delivery with retries and circuit breaker.

Features:
- In-memory webhook queue with persistence of delivery state
- Exponential backoff retries (1s, 2s, 4s, 8s, 16s max)
- Circuit breaker per endpoint (5 failures = open, 30s reset)
- HMAC-SHA256 signature verification
- Delivery audit log with full history
"""
import asyncio
import hashlib
import hmac
import logging
import os
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "webhook-sender")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab33-otel-collector:4317")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "super-secret-key-for-hmac")

# Retry configuration
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))
BASE_RETRY_DELAY_SEC = float(os.getenv("BASE_RETRY_DELAY_SEC", "1.0"))
MAX_RETRY_DELAY_SEC = float(os.getenv("MAX_RETRY_DELAY_SEC", "16.0"))

# Circuit breaker configuration
CIRCUIT_FAILURE_THRESHOLD = int(os.getenv("CIRCUIT_FAILURE_THRESHOLD", "5"))
CIRCUIT_RESET_TIMEOUT_SEC = float(os.getenv("CIRCUIT_RESET_TIMEOUT_SEC", "30.0"))

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
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
WEBHOOKS_SENT = Counter(
    "webhooks_sent_total",
    "Total webhooks sent",
    ["endpoint", "status"]
)
WEBHOOKS_FAILED = Counter(
    "webhooks_failed_total",
    "Total webhooks permanently failed",
    ["endpoint", "reason"]
)
WEBHOOKS_QUEUED = Gauge(
    "webhooks_queued",
    "Number of webhooks in queue",
    ["endpoint"]
)
DELIVERY_LATENCY = Histogram(
    "webhook_delivery_latency_seconds",
    "Time to deliver webhook (including retries)",
    ["endpoint"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]
)
RETRY_COUNT = Histogram(
    "webhook_retry_count",
    "Number of retries before success or failure",
    ["endpoint"],
    buckets=[0, 1, 2, 3, 4, 5]
)
CIRCUIT_STATE = Gauge(
    "circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half-open)",
    ["endpoint"]
)


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    RETRYING = "retrying"
    FAILED = "failed"
    CIRCUIT_OPEN = "circuit_open"


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Per-endpoint circuit breaker."""
    endpoint: str
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    success_count_in_half_open: int = 0

    def record_success(self):
        if self.state == CircuitState.HALF_OPEN:
            self.success_count_in_half_open += 1
            if self.success_count_in_half_open >= 2:
                logger.info(f"Circuit CLOSED for {self.endpoint} after successful half-open tests")
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count_in_half_open = 0
        else:
            self.failure_count = 0

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= CIRCUIT_FAILURE_THRESHOLD:
            if self.state != CircuitState.OPEN:
                logger.warning(f"Circuit OPEN for {self.endpoint} after {self.failure_count} failures")
            self.state = CircuitState.OPEN
        if self.state == CircuitState.HALF_OPEN:
            logger.info(f"Circuit back to OPEN for {self.endpoint} after half-open failure")
            self.state = CircuitState.OPEN
            self.success_count_in_half_open = 0

    def can_attempt(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time >= CIRCUIT_RESET_TIMEOUT_SEC:
                logger.info(f"Circuit HALF-OPEN for {self.endpoint} (testing)")
                self.state = CircuitState.HALF_OPEN
                self.success_count_in_half_open = 0
                return True
            return False
        # Half-open: allow one attempt
        return True

    def get_state_value(self) -> int:
        """Return numeric state for Prometheus gauge."""
        if self.state == CircuitState.CLOSED:
            return 0
        elif self.state == CircuitState.OPEN:
            return 1
        return 2


@dataclass
class DeliveryAttempt:
    """Record of a single delivery attempt."""
    timestamp: str
    status_code: Optional[int]
    error: Optional[str]
    latency_ms: float
    retry_number: int


@dataclass
class WebhookEvent:
    """Webhook event with delivery tracking."""
    id: str
    endpoint: str
    event_type: str
    payload: dict
    signature: str
    created_at: str
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempts: List[DeliveryAttempt] = field(default_factory=list)
    retry_count: int = 0
    next_retry_at: Optional[float] = None
    delivered_at: Optional[str] = None
    failed_at: Optional[str] = None
    failure_reason: Optional[str] = None


# In-memory stores
webhook_queue: Dict[str, WebhookEvent] = {}
delivery_log: List[WebhookEvent] = []
circuit_breakers: Dict[str, CircuitBreaker] = {}
endpoint_queues: Dict[str, List[str]] = defaultdict(list)


def get_circuit_breaker(endpoint: str) -> CircuitBreaker:
    """Get or create circuit breaker for endpoint."""
    if endpoint not in circuit_breakers:
        circuit_breakers[endpoint] = CircuitBreaker(endpoint=endpoint)
    return circuit_breakers[endpoint]


def generate_signature(payload: str) -> str:
    """Generate HMAC-SHA256 signature for payload."""
    return hmac.new(
        WEBHOOK_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()


def verify_signature(payload: str, signature: str) -> bool:
    """Verify HMAC-SHA256 signature."""
    expected = generate_signature(payload)
    return hmac.compare_digest(expected, signature)


async def deliver_webhook(webhook: WebhookEvent) -> bool:
    """Attempt to deliver a single webhook. Returns True if successful."""
    circuit = get_circuit_breaker(webhook.endpoint)

    # Check circuit breaker
    if not circuit.can_attempt():
        logger.warning(f"Circuit open for {webhook.endpoint}, skipping delivery of {webhook.id}")
        webhook.status = DeliveryStatus.CIRCUIT_OPEN
        CIRCUIT_STATE.labels(endpoint=webhook.endpoint).set(circuit.get_state_value())
        return False

    start_time = time.time()
    attempt = DeliveryAttempt(
        timestamp=datetime.utcnow().isoformat(),
        status_code=None,
        error=None,
        latency_ms=0,
        retry_number=webhook.retry_count
    )

    with tracer.start_as_current_span("webhook_delivery") as span:
        span.set_attribute("webhook.id", webhook.id)
        span.set_attribute("webhook.endpoint", webhook.endpoint)
        span.set_attribute("webhook.event_type", webhook.event_type)
        span.set_attribute("webhook.retry_count", webhook.retry_count)

        try:
            import json
            payload_str = json.dumps(webhook.payload)

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    webhook.endpoint,
                    json=webhook.payload,
                    headers={
                        "X-Webhook-ID": webhook.id,
                        "X-Webhook-Signature": webhook.signature,
                        "X-Webhook-Event-Type": webhook.event_type,
                        "X-Webhook-Timestamp": webhook.created_at,
                        "Content-Type": "application/json"
                    }
                )

            latency = time.time() - start_time
            attempt.latency_ms = latency * 1000
            attempt.status_code = response.status_code
            span.set_attribute("http.status_code", response.status_code)

            if response.status_code >= 200 and response.status_code < 300:
                # Success
                webhook.status = DeliveryStatus.DELIVERED
                webhook.delivered_at = datetime.utcnow().isoformat()
                webhook.attempts.append(attempt)
                circuit.record_success()
                WEBHOOKS_SENT.labels(endpoint=webhook.endpoint, status="success").inc()
                DELIVERY_LATENCY.labels(endpoint=webhook.endpoint).observe(latency)
                RETRY_COUNT.labels(endpoint=webhook.endpoint).observe(webhook.retry_count)
                CIRCUIT_STATE.labels(endpoint=webhook.endpoint).set(circuit.get_state_value())
                logger.info(f"Webhook {webhook.id} delivered to {webhook.endpoint} after {webhook.retry_count} retries")
                return True
            else:
                # HTTP error
                attempt.error = f"HTTP {response.status_code}"
                webhook.attempts.append(attempt)
                circuit.record_failure()
                WEBHOOKS_SENT.labels(endpoint=webhook.endpoint, status="error").inc()
                CIRCUIT_STATE.labels(endpoint=webhook.endpoint).set(circuit.get_state_value())
                logger.warning(f"Webhook {webhook.id} failed with HTTP {response.status_code}")
                return False

        except httpx.TimeoutException as e:
            latency = time.time() - start_time
            attempt.latency_ms = latency * 1000
            attempt.error = "timeout"
            webhook.attempts.append(attempt)
            circuit.record_failure()
            WEBHOOKS_SENT.labels(endpoint=webhook.endpoint, status="timeout").inc()
            CIRCUIT_STATE.labels(endpoint=webhook.endpoint).set(circuit.get_state_value())
            logger.warning(f"Webhook {webhook.id} timed out")
            span.set_attribute("error", True)
            span.set_attribute("error.type", "timeout")
            return False

        except Exception as e:
            latency = time.time() - start_time
            attempt.latency_ms = latency * 1000
            attempt.error = str(e)
            webhook.attempts.append(attempt)
            circuit.record_failure()
            WEBHOOKS_SENT.labels(endpoint=webhook.endpoint, status="error").inc()
            CIRCUIT_STATE.labels(endpoint=webhook.endpoint).set(circuit.get_state_value())
            logger.error(f"Webhook {webhook.id} delivery error: {e}")
            span.set_attribute("error", True)
            span.set_attribute("error.message", str(e))
            return False


async def process_webhook_with_retries(webhook_id: str):
    """Process a webhook with exponential backoff retries."""
    webhook = webhook_queue.get(webhook_id)
    if not webhook:
        return

    webhook.status = DeliveryStatus.IN_PROGRESS

    while webhook.retry_count <= MAX_RETRIES:
        success = await deliver_webhook(webhook)

        if success:
            # Move to delivery log
            delivery_log.append(webhook)
            if len(delivery_log) > 1000:
                delivery_log.pop(0)
            del webhook_queue[webhook_id]
            if webhook_id in endpoint_queues.get(webhook.endpoint, []):
                endpoint_queues[webhook.endpoint].remove(webhook_id)
            WEBHOOKS_QUEUED.labels(endpoint=webhook.endpoint).set(
                len(endpoint_queues.get(webhook.endpoint, []))
            )
            return

        # Check if we should retry
        webhook.retry_count += 1
        if webhook.retry_count > MAX_RETRIES:
            break

        # Calculate backoff delay
        delay = min(
            BASE_RETRY_DELAY_SEC * (2 ** (webhook.retry_count - 1)),
            MAX_RETRY_DELAY_SEC
        )

        # Check if circuit is open - wait for reset timeout instead
        circuit = get_circuit_breaker(webhook.endpoint)
        if circuit.state == CircuitState.OPEN:
            delay = max(delay, CIRCUIT_RESET_TIMEOUT_SEC)

        webhook.status = DeliveryStatus.RETRYING
        webhook.next_retry_at = time.time() + delay
        logger.info(f"Webhook {webhook_id} scheduled for retry {webhook.retry_count} in {delay:.1f}s")

        await asyncio.sleep(delay)

    # Max retries exceeded or permanently failed
    webhook.status = DeliveryStatus.FAILED
    webhook.failed_at = datetime.utcnow().isoformat()
    webhook.failure_reason = f"Max retries ({MAX_RETRIES}) exceeded"
    WEBHOOKS_FAILED.labels(endpoint=webhook.endpoint, reason="max_retries").inc()
    logger.error(f"Webhook {webhook_id} permanently failed after {webhook.retry_count} attempts")

    # Move to delivery log
    delivery_log.append(webhook)
    if len(delivery_log) > 1000:
        delivery_log.pop(0)
    del webhook_queue[webhook_id]
    if webhook_id in endpoint_queues.get(webhook.endpoint, []):
        endpoint_queues[webhook.endpoint].remove(webhook_id)
    WEBHOOKS_QUEUED.labels(endpoint=webhook.endpoint).set(
        len(endpoint_queues.get(webhook.endpoint, []))
    )


# Pydantic models
class WebhookRequest(BaseModel):
    endpoint: str
    event_type: str
    payload: dict


class WebhookResponse(BaseModel):
    id: str
    endpoint: str
    event_type: str
    status: str
    signature: str
    created_at: str


class EndpointConfig(BaseModel):
    endpoint: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Max retries: {MAX_RETRIES}, Base delay: {BASE_RETRY_DELAY_SEC}s")
    logger.info(f"Circuit breaker threshold: {CIRCUIT_FAILURE_THRESHOLD} failures")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title="Webhook Sender", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/webhooks", response_model=WebhookResponse)
async def send_webhook(request: WebhookRequest, background_tasks: BackgroundTasks):
    """Queue a webhook for delivery."""
    import json

    webhook_id = str(uuid.uuid4())
    payload_str = json.dumps(request.payload, sort_keys=True)
    signature = generate_signature(payload_str)

    webhook = WebhookEvent(
        id=webhook_id,
        endpoint=request.endpoint,
        event_type=request.event_type,
        payload=request.payload,
        signature=signature,
        created_at=datetime.utcnow().isoformat(),
        status=DeliveryStatus.PENDING
    )

    webhook_queue[webhook_id] = webhook
    endpoint_queues[request.endpoint].append(webhook_id)
    WEBHOOKS_QUEUED.labels(endpoint=request.endpoint).set(
        len(endpoint_queues[request.endpoint])
    )

    # Start delivery in background
    background_tasks.add_task(process_webhook_with_retries, webhook_id)

    logger.info(f"Queued webhook {webhook_id} for {request.endpoint}")

    return WebhookResponse(
        id=webhook_id,
        endpoint=request.endpoint,
        event_type=request.event_type,
        status=webhook.status.value,
        signature=signature,
        created_at=webhook.created_at
    )


@app.get("/webhooks/{webhook_id}")
async def get_webhook(webhook_id: str):
    """Get webhook status and delivery history."""
    # Check active queue
    if webhook_id in webhook_queue:
        webhook = webhook_queue[webhook_id]
    else:
        # Check delivery log
        webhook = next((w for w in delivery_log if w.id == webhook_id), None)

    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")

    return {
        "id": webhook.id,
        "endpoint": webhook.endpoint,
        "event_type": webhook.event_type,
        "payload": webhook.payload,
        "signature": webhook.signature,
        "status": webhook.status.value,
        "created_at": webhook.created_at,
        "delivered_at": webhook.delivered_at,
        "failed_at": webhook.failed_at,
        "failure_reason": webhook.failure_reason,
        "retry_count": webhook.retry_count,
        "attempts": [
            {
                "timestamp": a.timestamp,
                "status_code": a.status_code,
                "error": a.error,
                "latency_ms": round(a.latency_ms, 2),
                "retry_number": a.retry_number
            }
            for a in webhook.attempts
        ]
    }


@app.get("/webhooks")
async def list_webhooks(status: Optional[str] = None, limit: int = 50):
    """List webhooks with optional status filter."""
    all_webhooks = list(webhook_queue.values()) + delivery_log

    if status:
        all_webhooks = [w for w in all_webhooks if w.status.value == status]

    # Sort by created_at descending
    all_webhooks.sort(key=lambda w: w.created_at, reverse=True)

    return {
        "total": len(all_webhooks),
        "webhooks": [
            {
                "id": w.id,
                "endpoint": w.endpoint,
                "event_type": w.event_type,
                "status": w.status.value,
                "created_at": w.created_at,
                "retry_count": w.retry_count
            }
            for w in all_webhooks[:limit]
        ]
    }


@app.get("/delivery-log")
async def get_delivery_log(limit: int = 100):
    """Get recent delivery log entries."""
    return {
        "total": len(delivery_log),
        "entries": [
            {
                "id": w.id,
                "endpoint": w.endpoint,
                "event_type": w.event_type,
                "status": w.status.value,
                "created_at": w.created_at,
                "delivered_at": w.delivered_at,
                "failed_at": w.failed_at,
                "failure_reason": w.failure_reason,
                "retry_count": w.retry_count,
                "attempt_count": len(w.attempts)
            }
            for w in delivery_log[-limit:]
        ]
    }


@app.get("/circuit-breakers")
async def get_circuit_breakers():
    """Get circuit breaker status for all endpoints."""
    return {
        "circuit_breakers": {
            endpoint: {
                "state": cb.state.value,
                "failure_count": cb.failure_count,
                "last_failure_time": cb.last_failure_time,
                "time_since_failure": time.time() - cb.last_failure_time if cb.last_failure_time > 0 else None,
                "reset_timeout_remaining": max(0, CIRCUIT_RESET_TIMEOUT_SEC - (time.time() - cb.last_failure_time)) if cb.state == CircuitState.OPEN else None
            }
            for endpoint, cb in circuit_breakers.items()
        }
    }


@app.post("/circuit-breakers/{endpoint}/reset")
async def reset_circuit_breaker(endpoint: str):
    """Manually reset a circuit breaker."""
    # URL decode the endpoint
    import urllib.parse
    decoded_endpoint = urllib.parse.unquote(endpoint)

    if decoded_endpoint not in circuit_breakers:
        raise HTTPException(status_code=404, detail="Circuit breaker not found")

    cb = circuit_breakers[decoded_endpoint]
    cb.state = CircuitState.CLOSED
    cb.failure_count = 0
    cb.success_count_in_half_open = 0
    CIRCUIT_STATE.labels(endpoint=decoded_endpoint).set(0)

    logger.info(f"Circuit breaker manually reset for {decoded_endpoint}")
    return {"status": "reset", "endpoint": decoded_endpoint}


@app.get("/stats")
async def get_stats():
    """Get overall statistics."""
    statuses = defaultdict(int)
    for w in list(webhook_queue.values()) + delivery_log:
        statuses[w.status.value] += 1

    return {
        "queue_size": len(webhook_queue),
        "delivery_log_size": len(delivery_log),
        "status_counts": dict(statuses),
        "endpoints": {
            endpoint: {
                "queued": len(queue),
                "circuit_state": circuit_breakers.get(endpoint, CircuitBreaker(endpoint=endpoint)).state.value
            }
            for endpoint, queue in endpoint_queues.items()
        }
    }


@app.post("/verify-signature")
async def verify_webhook_signature(payload: str, signature: str):
    """Utility endpoint to verify a webhook signature."""
    is_valid = verify_signature(payload, signature)
    return {
        "valid": is_valid,
        "expected_signature": generate_signature(payload) if not is_valid else signature
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
