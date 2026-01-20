"""
Flaky Webhook Receiver - Sometimes fails, sometimes succeeds.

This endpoint simulates an unreliable webhook consumer that:
- Has configurable failure rate
- Sometimes times out
- Sometimes returns errors
- Can be configured at runtime
"""
import asyncio
import hashlib
import hmac
import logging
import os
import random
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
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "receiver-flaky")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab33-otel-collector:4317")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "super-secret-key-for-hmac")

# Flakiness configuration (mutable at runtime)
flaky_config = {
    "failure_rate": float(os.getenv("FAILURE_RATE", "0.3")),  # 30% failure
    "timeout_rate": float(os.getenv("TIMEOUT_RATE", "0.1")),  # 10% timeout
    "error_codes": [500, 502, 503],
    "timeout_delay_sec": 15.0,  # 15 second delay to trigger sender timeout
    "process_delay_ms": int(os.getenv("PROCESS_DELAY_MS", "100"))
}

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
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0, 10.0, 15.0]
)
FAILURES_INJECTED = Counter(
    "failures_injected_total",
    "Total failures injected",
    ["type"]
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


class FlakyConfig(BaseModel):
    failure_rate: float = 0.3
    timeout_rate: float = 0.1
    process_delay_ms: int = 100


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Failure rate: {flaky_config['failure_rate']*100}%")
    logger.info(f"Timeout rate: {flaky_config['timeout_rate']*100}%")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title="Flaky Webhook Receiver", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/config")
async def get_config():
    """Get current flakiness configuration."""
    return flaky_config


@app.post("/config")
async def set_config(config: FlakyConfig):
    """Update flakiness configuration at runtime."""
    flaky_config["failure_rate"] = max(0.0, min(1.0, config.failure_rate))
    flaky_config["timeout_rate"] = max(0.0, min(1.0, config.timeout_rate))
    flaky_config["process_delay_ms"] = max(0, config.process_delay_ms)
    logger.info(f"Config updated: failure_rate={flaky_config['failure_rate']}, timeout_rate={flaky_config['timeout_rate']}")
    return flaky_config


@app.post("/webhook")
async def receive_webhook(
    request: Request,
    x_webhook_id: str = Header(None),
    x_webhook_signature: str = Header(None),
    x_webhook_event_type: str = Header(None),
    x_webhook_timestamp: str = Header(None)
):
    """Receive and process a webhook (with random failures)."""
    import json

    start_time = time.time()

    current_span = trace.get_current_span()
    current_span.set_attribute("webhook.id", x_webhook_id or "unknown")
    current_span.set_attribute("webhook.event_type", x_webhook_event_type or "unknown")

    # Get body
    body = await request.body()
    payload = body.decode('utf-8')

    # Decide failure mode BEFORE processing
    roll = random.random()

    # Check for timeout
    if roll < flaky_config["timeout_rate"]:
        FAILURES_INJECTED.labels(type="timeout").inc()
        current_span.set_attribute("failure.type", "timeout")
        logger.warning(f"Injecting timeout for webhook {x_webhook_id}")
        # Sleep longer than sender timeout
        await asyncio.sleep(flaky_config["timeout_delay_sec"])
        # Return after timeout (sender will have already given up)
        WEBHOOKS_RECEIVED.labels(event_type=x_webhook_event_type or "unknown", status="timeout").inc()
        return {"status": "accepted"}  # This won't be seen by sender

    # Check for error
    if roll < flaky_config["timeout_rate"] + flaky_config["failure_rate"]:
        error_code = random.choice(flaky_config["error_codes"])
        FAILURES_INJECTED.labels(type=f"http_{error_code}").inc()
        current_span.set_attribute("failure.type", f"http_{error_code}")
        logger.warning(f"Injecting HTTP {error_code} for webhook {x_webhook_id}")
        WEBHOOKS_RECEIVED.labels(event_type=x_webhook_event_type or "unknown", status="error").inc()
        raise HTTPException(status_code=error_code, detail=f"Simulated {error_code} error")

    # Normal processing - verify signature
    signature_valid = True
    if x_webhook_signature:
        try:
            payload_dict = json.loads(payload)
            normalized_payload = json.dumps(payload_dict, sort_keys=True)
            signature_valid = verify_signature(normalized_payload, x_webhook_signature)
            current_span.set_attribute("webhook.signature_valid", signature_valid)
        except json.JSONDecodeError:
            signature_valid = False

    if not signature_valid:
        logger.warning(f"Invalid signature for webhook {x_webhook_id}")
        WEBHOOKS_RECEIVED.labels(event_type=x_webhook_event_type or "unknown", status="invalid_signature").inc()
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Simulate processing delay
    await asyncio.sleep(flaky_config["process_delay_ms"] / 1000.0)

    # Store webhook
    webhook_record = {
        "id": x_webhook_id,
        "event_type": x_webhook_event_type,
        "payload": json.loads(payload),
        "received_at": datetime.utcnow().isoformat()
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
    uvicorn.run(app, host="0.0.0.0", port=8002)
