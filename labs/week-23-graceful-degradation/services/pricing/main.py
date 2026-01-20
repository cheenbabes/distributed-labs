"""
Pricing Service - Provides real-time product pricing.

This service can be configured to simulate failures for testing graceful degradation.
When unavailable, product-catalog will fall back to cached prices.
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "pricing")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab23-otel-collector:4317")
LATENCY_MIN_MS = int(os.getenv("LATENCY_MIN_MS", "10"))
LATENCY_MAX_MS = int(os.getenv("LATENCY_MAX_MS", "50"))

# Failure injection state
failure_config = {
    "enabled": os.getenv("INJECT_FAILURE", "false").lower() == "true",
    "failure_rate": float(os.getenv("FAILURE_RATE", "1.0")),
    "failure_type": os.getenv("FAILURE_TYPE", "error"),
    "slow_ms": int(os.getenv("SLOW_MS", "5000"))
}

# Simulated pricing data (live prices - slightly different from cached)
PRICING_DB = {
    "SKU-001": {"price": 31.99, "currency": "USD", "discount": 0.05},
    "SKU-002": {"price": 52.99, "currency": "USD", "discount": 0.10},
    "SKU-003": {"price": 18.99, "currency": "USD", "discount": 0.00},
    "SKU-005": {"price": 94.99, "currency": "USD", "discount": 0.15},
    "SKU-010": {"price": 159.99, "currency": "USD", "discount": 0.05},
    "SKU-007": {"price": 79.99, "currency": "USD", "discount": 0.00},
    "SKU-008": {"price": 42.99, "currency": "USD", "discount": 0.20},
    "SKU-012": {"price": 129.99, "currency": "USD", "discount": 0.10},
}

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
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
FAILURES_INJECTED = Counter(
    "failures_injected_total",
    "Total failures injected",
    ["service", "failure_type"]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Latency range: {LATENCY_MIN_MS}-{LATENCY_MAX_MS}ms")
    logger.info(f"Failure injection: {'enabled' if failure_config['enabled'] else 'disabled'}")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


class FailureConfig(BaseModel):
    enabled: bool
    failure_rate: float = 1.0
    failure_type: str = "error"
    slow_ms: int = 5000


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/admin/failure")
async def get_failure_config():
    """Get current failure injection configuration."""
    return failure_config


@app.post("/admin/failure")
async def set_failure_config(config: FailureConfig):
    """Set failure injection configuration at runtime."""
    failure_config["enabled"] = config.enabled
    failure_config["failure_rate"] = config.failure_rate
    failure_config["failure_type"] = config.failure_type
    failure_config["slow_ms"] = config.slow_ms
    logger.info(f"Failure injection updated: {failure_config}")
    return failure_config


async def maybe_inject_failure():
    """Inject failure based on configuration."""
    if not failure_config["enabled"]:
        return

    if random.random() > failure_config["failure_rate"]:
        return

    failure_type = failure_config["failure_type"]
    FAILURES_INJECTED.labels(service=SERVICE_NAME, failure_type=failure_type).inc()

    if failure_type == "error":
        raise HTTPException(status_code=503, detail="Pricing service unavailable (injected)")
    elif failure_type == "timeout":
        await asyncio.sleep(30)
    elif failure_type == "slow":
        await asyncio.sleep(failure_config["slow_ms"] / 1000.0)


@app.get("/price/{product_id}")
async def get_price(request: Request, product_id: str):
    """Get current price for a product."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    current_span.set_attribute("product_id", product_id)

    # Check for failure injection
    await maybe_inject_failure()

    # Simulate processing latency
    latency_ms = random.randint(LATENCY_MIN_MS, LATENCY_MAX_MS)
    await asyncio.sleep(latency_ms / 1000.0)

    # Get price
    pricing = PRICING_DB.get(product_id)
    if not pricing:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")

    # Calculate final price with discount
    base_price = pricing["price"]
    discount = pricing["discount"]
    final_price = round(base_price * (1 - discount), 2)

    duration = time.time() - start_time

    # Record metrics
    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/price",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/price"
    ).observe(duration)

    current_span.set_attribute("price", final_price)
    current_span.set_attribute("discount", discount)
    current_span.set_attribute("latency_ms", latency_ms)

    logger.info(f"Price request trace_id={trace_id} product={product_id} price={final_price}")

    return {
        "service": SERVICE_NAME,
        "product_id": product_id,
        "price": final_price,
        "base_price": base_price,
        "discount_percent": int(discount * 100),
        "currency": pricing["currency"],
        "price_type": "live",
        "latency_ms": latency_ms,
        "trace_id": trace_id
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
