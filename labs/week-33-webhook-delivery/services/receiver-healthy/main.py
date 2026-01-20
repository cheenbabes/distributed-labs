"""
Healthy Webhook Receiver - Always accepts webhooks successfully.

This endpoint simulates a healthy webhook consumer that:
- Always responds with 200 OK
- Validates HMAC signatures
- Logs received webhooks
"""
import hashlib
import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List

from fastapi import FastAPI, Request, Header, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "receiver-healthy")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab33-otel-collector:4317")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "super-secret-key-for-hmac")
PROCESS_DELAY_MS = int(os.getenv("PROCESS_DELAY_MS", "50"))

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

# Prometheus metrics
WEBHOOKS_RECEIVED = Counter(
    "webhooks_received_total",
    "Total webhooks received",
    ["event_type", "status"]
)
PROCESSING_LATENCY = Histogram(
    "webhook_processing_latency_seconds",
    "Time to process webhook",
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
SIGNATURE_VALIDATIONS = Counter(
    "webhook_signature_validations_total",
    "Signature validation results",
    ["result"]
)

# In-memory storage
received_webhooks: List[dict] = []


def verify_signature(payload: str, signature: str) -> bool:
    """Verify HMAC-SHA256 signature."""
    expected = hmac.new(
        WEBHOOK_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Process delay: {PROCESS_DELAY_MS}ms")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title="Healthy Webhook Receiver", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/webhook")
async def receive_webhook(
    request: Request,
    x_webhook_id: str = Header(None),
    x_webhook_signature: str = Header(None),
    x_webhook_event_type: str = Header(None),
    x_webhook_timestamp: str = Header(None)
):
    """Receive and process a webhook."""
    import asyncio
    import json

    start_time = time.time()

    current_span = trace.get_current_span()
    current_span.set_attribute("webhook.id", x_webhook_id or "unknown")
    current_span.set_attribute("webhook.event_type", x_webhook_event_type or "unknown")

    # Get body
    body = await request.body()
    payload = body.decode('utf-8')

    # Verify signature if provided
    signature_valid = True
    if x_webhook_signature:
        # Parse payload and re-serialize with sorted keys for consistent signature
        try:
            payload_dict = json.loads(payload)
            normalized_payload = json.dumps(payload_dict, sort_keys=True)
            signature_valid = verify_signature(normalized_payload, x_webhook_signature)
            SIGNATURE_VALIDATIONS.labels(result="valid" if signature_valid else "invalid").inc()
            current_span.set_attribute("webhook.signature_valid", signature_valid)
        except json.JSONDecodeError:
            signature_valid = False
            SIGNATURE_VALIDATIONS.labels(result="invalid").inc()

    if not signature_valid:
        logger.warning(f"Invalid signature for webhook {x_webhook_id}")
        WEBHOOKS_RECEIVED.labels(event_type=x_webhook_event_type or "unknown", status="invalid_signature").inc()
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Simulate processing delay
    await asyncio.sleep(PROCESS_DELAY_MS / 1000.0)

    # Store webhook
    webhook_record = {
        "id": x_webhook_id,
        "event_type": x_webhook_event_type,
        "payload": json.loads(payload),
        "received_at": datetime.utcnow().isoformat(),
        "signature_valid": signature_valid
    }
    received_webhooks.append(webhook_record)
    if len(received_webhooks) > 500:
        received_webhooks.pop(0)

    # Record metrics
    duration = time.time() - start_time
    WEBHOOKS_RECEIVED.labels(event_type=x_webhook_event_type or "unknown", status="success").inc()
    PROCESSING_LATENCY.observe(duration)

    logger.info(f"Received webhook {x_webhook_id} (type: {x_webhook_event_type})")

    return {
        "status": "accepted",
        "webhook_id": x_webhook_id,
        "processed_at": datetime.utcnow().isoformat()
    }


@app.get("/received")
async def list_received(limit: int = 50):
    """List recently received webhooks."""
    return {
        "total": len(received_webhooks),
        "webhooks": received_webhooks[-limit:]
    }


@app.delete("/received")
async def clear_received():
    """Clear received webhooks."""
    received_webhooks.clear()
    return {"status": "cleared"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
