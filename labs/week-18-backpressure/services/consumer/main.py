"""
Consumer Service - Processes work items with configurable latency.

Simulates a slow consumer that can't keep up with a fast producer.
Processing time can be configured to create backpressure scenarios.
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "consumer")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab18-otel-collector:4317")
PROCESSING_TIME_MS = int(os.getenv("PROCESSING_TIME_MS", "100"))
VARIABLE_PROCESSING = os.getenv("VARIABLE_PROCESSING", "false").lower() == "true"
PROCESSING_MIN_MS = int(os.getenv("PROCESSING_MIN_MS", "50"))
PROCESSING_MAX_MS = int(os.getenv("PROCESSING_MAX_MS", "200"))

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
ITEMS_PROCESSED = Counter(
    "backpressure_items_processed_total",
    "Total items processed by consumer",
    ["service"]
)
ITEMS_FAILED = Counter(
    "backpressure_items_failed_total",
    "Total items that failed processing",
    ["service"]
)
PROCESSING_LATENCY = Histogram(
    "backpressure_processing_duration_seconds",
    "Time to process each item",
    ["service"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)
QUEUE_LATENCY = Histogram(
    "backpressure_queue_wait_seconds",
    "Time item spent waiting in queue before processing",
    ["service"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0]
)
ITEMS_IN_FLIGHT = Gauge(
    "backpressure_items_in_flight",
    "Number of items currently being processed",
    ["service"]
)
CONFIGURED_PROCESSING_TIME = Gauge(
    "backpressure_configured_processing_time_ms",
    "Configured processing time in milliseconds",
    ["service"]
)
THROUGHPUT = Gauge(
    "backpressure_consumer_throughput",
    "Current throughput (items per second)",
    ["service"]
)

# Runtime state
items_in_flight = 0
last_throughput_time = time.time()
items_since_last_check = 0


def get_processing_time() -> int:
    """Get processing time based on configuration."""
    if VARIABLE_PROCESSING:
        return random.randint(PROCESSING_MIN_MS, PROCESSING_MAX_MS)
    return PROCESSING_TIME_MS


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Processing time: {PROCESSING_TIME_MS}ms")
    logger.info(f"Variable processing: {VARIABLE_PROCESSING}")

    # Set initial configured processing time metric
    CONFIGURED_PROCESSING_TIME.labels(service=SERVICE_NAME).set(PROCESSING_TIME_MS)

    yield

    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "processing_time_ms": PROCESSING_TIME_MS,
        "variable_processing": VARIABLE_PROCESSING
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
async def status():
    """Get detailed status of the consumer."""
    return {
        "service": SERVICE_NAME,
        "config": {
            "processing_time_ms": PROCESSING_TIME_MS,
            "variable_processing": VARIABLE_PROCESSING,
            "processing_range_ms": f"{PROCESSING_MIN_MS}-{PROCESSING_MAX_MS}" if VARIABLE_PROCESSING else None
        },
        "state": {
            "items_in_flight": items_in_flight,
            "max_throughput": f"{1000 / PROCESSING_TIME_MS:.2f} items/sec"
        }
    }


@app.post("/process")
async def process_item(request: Request):
    """Process a work item from the producer."""
    global items_in_flight, last_throughput_time, items_since_last_check

    data = await request.json()
    item_id = data.get("id", "unknown")
    created_at = data.get("created_at", time.time())

    # Calculate queue latency (time spent waiting)
    queue_latency = time.time() - created_at
    QUEUE_LATENCY.labels(service=SERVICE_NAME).observe(queue_latency)

    # Track in-flight items
    items_in_flight += 1
    ITEMS_IN_FLIGHT.labels(service=SERVICE_NAME).set(items_in_flight)

    try:
        with tracer.start_as_current_span("process_item") as span:
            span.set_attribute("item.id", item_id)
            span.set_attribute("queue.latency_seconds", queue_latency)

            # Get processing time
            processing_time_ms = get_processing_time()
            span.set_attribute("processing.time_ms", processing_time_ms)

            # Simulate processing work
            start_time = time.time()
            await asyncio.sleep(processing_time_ms / 1000.0)
            actual_processing_time = time.time() - start_time

            # Record metrics
            PROCESSING_LATENCY.labels(service=SERVICE_NAME).observe(actual_processing_time)
            ITEMS_PROCESSED.labels(service=SERVICE_NAME).inc()

            # Update throughput calculation
            items_since_last_check += 1
            now = time.time()
            elapsed = now - last_throughput_time
            if elapsed >= 1.0:
                throughput = items_since_last_check / elapsed
                THROUGHPUT.labels(service=SERVICE_NAME).set(throughput)
                items_since_last_check = 0
                last_throughput_time = now

            logger.debug(
                f"Processed item {item_id}: "
                f"queue_latency={queue_latency:.3f}s, "
                f"processing_time={actual_processing_time:.3f}s"
            )

            return {
                "status": "processed",
                "item_id": item_id,
                "queue_latency_seconds": round(queue_latency, 3),
                "processing_time_seconds": round(actual_processing_time, 3)
            }

    except Exception as e:
        ITEMS_FAILED.labels(service=SERVICE_NAME).inc()
        logger.error(f"Failed to process item {item_id}: {e}")
        raise

    finally:
        items_in_flight -= 1
        ITEMS_IN_FLIGHT.labels(service=SERVICE_NAME).set(items_in_flight)


@app.post("/admin/config")
async def update_config(request: Request):
    """Update consumer configuration dynamically."""
    global PROCESSING_TIME_MS, VARIABLE_PROCESSING, PROCESSING_MIN_MS, PROCESSING_MAX_MS

    data = await request.json()

    if "processing_time_ms" in data:
        PROCESSING_TIME_MS = int(data["processing_time_ms"])
        CONFIGURED_PROCESSING_TIME.labels(service=SERVICE_NAME).set(PROCESSING_TIME_MS)
        logger.info(f"Processing time changed to: {PROCESSING_TIME_MS}ms")

    if "variable_processing" in data:
        VARIABLE_PROCESSING = bool(data["variable_processing"])
        logger.info(f"Variable processing changed to: {VARIABLE_PROCESSING}")

    if "processing_min_ms" in data:
        PROCESSING_MIN_MS = int(data["processing_min_ms"])
        logger.info(f"Processing min changed to: {PROCESSING_MIN_MS}ms")

    if "processing_max_ms" in data:
        PROCESSING_MAX_MS = int(data["processing_max_ms"])
        logger.info(f"Processing max changed to: {PROCESSING_MAX_MS}ms")

    return {
        "status": "updated",
        "config": {
            "processing_time_ms": PROCESSING_TIME_MS,
            "variable_processing": VARIABLE_PROCESSING,
            "processing_min_ms": PROCESSING_MIN_MS,
            "processing_max_ms": PROCESSING_MAX_MS,
            "max_throughput": f"{1000 / PROCESSING_TIME_MS:.2f} items/sec"
        }
    }


@app.post("/admin/slow-down")
async def slow_down(request: Request):
    """Convenience endpoint to slow down the consumer."""
    global PROCESSING_TIME_MS

    data = await request.json()
    factor = data.get("factor", 2)

    old_time = PROCESSING_TIME_MS
    PROCESSING_TIME_MS = int(PROCESSING_TIME_MS * factor)
    CONFIGURED_PROCESSING_TIME.labels(service=SERVICE_NAME).set(PROCESSING_TIME_MS)

    logger.info(f"Consumer slowed down: {old_time}ms -> {PROCESSING_TIME_MS}ms (factor={factor})")

    return {
        "status": "slowed_down",
        "old_processing_time_ms": old_time,
        "new_processing_time_ms": PROCESSING_TIME_MS,
        "factor": factor,
        "new_max_throughput": f"{1000 / PROCESSING_TIME_MS:.2f} items/sec"
    }


@app.post("/admin/speed-up")
async def speed_up(request: Request):
    """Convenience endpoint to speed up the consumer."""
    global PROCESSING_TIME_MS

    data = await request.json()
    factor = data.get("factor", 2)

    old_time = PROCESSING_TIME_MS
    PROCESSING_TIME_MS = max(10, int(PROCESSING_TIME_MS / factor))  # Minimum 10ms
    CONFIGURED_PROCESSING_TIME.labels(service=SERVICE_NAME).set(PROCESSING_TIME_MS)

    logger.info(f"Consumer sped up: {old_time}ms -> {PROCESSING_TIME_MS}ms (factor={factor})")

    return {
        "status": "sped_up",
        "old_processing_time_ms": old_time,
        "new_processing_time_ms": PROCESSING_TIME_MS,
        "factor": factor,
        "new_max_throughput": f"{1000 / PROCESSING_TIME_MS:.2f} items/sec"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
