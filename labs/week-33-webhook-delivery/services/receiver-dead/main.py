"""
Dead Webhook Receiver - Always fails to simulate a dead endpoint.

This endpoint simulates an endpoint that has gone completely down:
- Always returns 503 Service Unavailable
- Can be toggled to "alive" mode for recovery testing
- Triggers circuit breaker in sender
"""
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime

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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "receiver-dead")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab33-otel-collector:4317")

# Dead mode configuration (mutable at runtime)
dead_config = {
    "is_dead": os.getenv("IS_DEAD", "true").lower() == "true",
    "error_code": int(os.getenv("ERROR_CODE", "503")),
    "error_message": os.getenv("ERROR_MESSAGE", "Service Unavailable - Endpoint is down")
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
WEBHOOKS_REJECTED = Counter(
    "webhooks_rejected_total",
    "Total webhooks rejected",
    ["reason"]
)
WEBHOOKS_RECEIVED = Counter(
    "webhooks_received_total",
    "Total webhooks received when alive",
    ["status"]
)

# Track attempts for demo
attempt_log = []


class DeadConfig(BaseModel):
    is_dead: bool = True
    error_code: int = 503
    error_message: str = "Service Unavailable"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Dead mode: {dead_config['is_dead']}")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title="Dead Webhook Receiver", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    """Health check - always healthy even if 'dead' to simulate a running but broken service."""
    return {
        "status": "ok" if not dead_config["is_dead"] else "degraded",
        "service": SERVICE_NAME,
        "is_dead": dead_config["is_dead"]
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/config")
async def get_config():
    """Get current dead mode configuration."""
    return dead_config


@app.post("/config")
async def set_config(config: DeadConfig):
    """Update dead mode configuration at runtime."""
    dead_config["is_dead"] = config.is_dead
    dead_config["error_code"] = config.error_code
    dead_config["error_message"] = config.error_message
    logger.info(f"Config updated: is_dead={dead_config['is_dead']}")
    return dead_config


@app.post("/revive")
async def revive():
    """Bring the endpoint back to life."""
    dead_config["is_dead"] = False
    logger.info("Endpoint revived - now accepting webhooks")
    return {"status": "revived", "is_dead": False}


@app.post("/kill")
async def kill():
    """Kill the endpoint again."""
    dead_config["is_dead"] = True
    logger.info("Endpoint killed - now rejecting all webhooks")
    return {"status": "killed", "is_dead": True}


@app.post("/webhook")
async def receive_webhook(
    request: Request,
    x_webhook_id: str = Header(None),
    x_webhook_signature: str = Header(None),
    x_webhook_event_type: str = Header(None),
    x_webhook_timestamp: str = Header(None)
):
    """Receive a webhook - always fails if dead."""
    import json

    current_span = trace.get_current_span()
    current_span.set_attribute("webhook.id", x_webhook_id or "unknown")
    current_span.set_attribute("endpoint.is_dead", dead_config["is_dead"])

    # Log the attempt
    attempt_log.append({
        "webhook_id": x_webhook_id,
        "timestamp": datetime.utcnow().isoformat(),
        "was_dead": dead_config["is_dead"]
    })
    if len(attempt_log) > 500:
        attempt_log.pop(0)

    if dead_config["is_dead"]:
        WEBHOOKS_REJECTED.labels(reason="dead").inc()
        logger.warning(f"Rejecting webhook {x_webhook_id} - endpoint is dead")
        raise HTTPException(
            status_code=dead_config["error_code"],
            detail=dead_config["error_message"]
        )

    # If alive, accept the webhook
    body = await request.body()
    payload = body.decode('utf-8')

    WEBHOOKS_RECEIVED.labels(status="success").inc()
    logger.info(f"Received webhook {x_webhook_id} (endpoint is alive)")

    return {
        "status": "accepted",
        "webhook_id": x_webhook_id,
        "processed_at": datetime.utcnow().isoformat(),
        "note": "Endpoint is alive and accepting webhooks"
    }


@app.get("/attempts")
async def get_attempts(limit: int = 50):
    """Get recent delivery attempts (for debugging)."""
    return {
        "total": len(attempt_log),
        "attempts": attempt_log[-limit:]
    }


@app.delete("/attempts")
async def clear_attempts():
    """Clear attempt log."""
    attempt_log.clear()
    return {"status": "cleared"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
