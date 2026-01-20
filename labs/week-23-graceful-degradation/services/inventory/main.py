"""
Inventory Service - Provides real-time stock information.

This service can be configured to simulate failures for testing graceful degradation.
When unavailable, product-catalog will fall back to "check in store" message.
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "inventory")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab23-otel-collector:4317")
LATENCY_MIN_MS = int(os.getenv("LATENCY_MIN_MS", "15"))
LATENCY_MAX_MS = int(os.getenv("LATENCY_MAX_MS", "60"))

# Failure injection state
failure_config = {
    "enabled": os.getenv("INJECT_FAILURE", "false").lower() == "true",
    "failure_rate": float(os.getenv("FAILURE_RATE", "1.0")),
    "failure_type": os.getenv("FAILURE_TYPE", "error"),
    "slow_ms": int(os.getenv("SLOW_MS", "5000"))
}

# Simulated inventory data
INVENTORY_DB = {
    "SKU-001": {"quantity": 150, "warehouse": "EAST", "reorder_point": 50},
    "SKU-002": {"quantity": 75, "warehouse": "WEST", "reorder_point": 25},
    "SKU-003": {"quantity": 0, "warehouse": "EAST", "reorder_point": 30},  # Out of stock
    "SKU-005": {"quantity": 200, "warehouse": "CENTRAL", "reorder_point": 75},
    "SKU-010": {"quantity": 12, "warehouse": "EAST", "reorder_point": 20},  # Low stock
    "SKU-007": {"quantity": 89, "warehouse": "WEST", "reorder_point": 40},
    "SKU-008": {"quantity": 250, "warehouse": "CENTRAL", "reorder_point": 100},
    "SKU-012": {"quantity": 5, "warehouse": "EAST", "reorder_point": 15},  # Low stock
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
STOCK_LEVEL = Counter(
    "stock_queries_total",
    "Stock level queries by status",
    ["service", "status"]
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
        raise HTTPException(status_code=503, detail="Inventory service unavailable (injected)")
    elif failure_type == "timeout":
        await asyncio.sleep(30)
    elif failure_type == "slow":
        await asyncio.sleep(failure_config["slow_ms"] / 1000.0)


@app.get("/stock/{product_id}")
async def get_stock(request: Request, product_id: str):
    """Get current stock level for a product."""
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    current_span.set_attribute("product_id", product_id)

    # Check for failure injection
    await maybe_inject_failure()

    # Simulate processing latency (inventory checks can be slower)
    latency_ms = random.randint(LATENCY_MIN_MS, LATENCY_MAX_MS)
    await asyncio.sleep(latency_ms / 1000.0)

    # Get stock info
    stock = INVENTORY_DB.get(product_id)
    if not stock:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found in inventory")

    quantity = stock["quantity"]
    reorder_point = stock["reorder_point"]

    # Determine stock status
    if quantity == 0:
        stock_status = "out_of_stock"
        in_stock = False
    elif quantity < reorder_point:
        stock_status = "low_stock"
        in_stock = True
    else:
        stock_status = "in_stock"
        in_stock = True

    STOCK_LEVEL.labels(service=SERVICE_NAME, status=stock_status).inc()

    duration = time.time() - start_time

    # Record metrics
    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/stock",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/stock"
    ).observe(duration)

    current_span.set_attribute("quantity", quantity)
    current_span.set_attribute("in_stock", in_stock)
    current_span.set_attribute("stock_status", stock_status)
    current_span.set_attribute("latency_ms", latency_ms)

    logger.info(f"Stock request trace_id={trace_id} product={product_id} quantity={quantity} status={stock_status}")

    return {
        "service": SERVICE_NAME,
        "product_id": product_id,
        "in_stock": in_stock,
        "quantity": quantity,
        "stock_status": stock_status,
        "warehouse": stock["warehouse"],
        "reorder_point": reorder_point,
        "latency_ms": latency_ms,
        "trace_id": trace_id
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
