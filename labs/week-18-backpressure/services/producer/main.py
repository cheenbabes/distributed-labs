"""
Producer Service - Generates work items and manages queue backpressure.

Demonstrates three queue modes:
1. unbounded - Queue grows indefinitely (leads to OOM)
2. bounded - Queue has max size, rejects when full
3. rate_limited - Producer slows down to match consumer capacity
"""
import asyncio
import logging
import os
import sys
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "producer")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab18-otel-collector:4317")
CONSUMER_URL = os.getenv("CONSUMER_URL", "http://lab18-consumer:8001")
PRODUCTION_RATE = int(os.getenv("PRODUCTION_RATE", "10"))  # items per second
QUEUE_MODE = os.getenv("QUEUE_MODE", "unbounded")  # unbounded, bounded, rate_limited
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "100"))
RATE_LIMIT = int(os.getenv("RATE_LIMIT", "5"))  # items per second
MEMORY_LIMIT_MB = int(os.getenv("MEMORY_LIMIT_MB", "256"))

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
ITEMS_PRODUCED = Counter(
    "backpressure_items_produced_total",
    "Total items produced",
    ["service"]
)
ITEMS_SENT = Counter(
    "backpressure_items_sent_total",
    "Total items successfully sent to consumer",
    ["service"]
)
ITEMS_REJECTED = Counter(
    "backpressure_items_rejected_total",
    "Total items rejected due to queue full",
    ["service"]
)
ITEMS_DROPPED = Counter(
    "backpressure_items_dropped_total",
    "Total items dropped due to consumer failure",
    ["service"]
)
QUEUE_DEPTH = Gauge(
    "backpressure_queue_depth",
    "Current queue depth",
    ["service"]
)
QUEUE_MEMORY_BYTES = Gauge(
    "backpressure_queue_memory_bytes",
    "Estimated memory used by queue",
    ["service"]
)
PROCESS_MEMORY_BYTES = Gauge(
    "backpressure_process_memory_bytes",
    "Process memory usage",
    ["service"]
)
SEND_LATENCY = Histogram(
    "backpressure_send_duration_seconds",
    "Time to send item to consumer",
    ["service"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)
RATE_LIMIT_DELAYS = Counter(
    "backpressure_rate_limit_delays_total",
    "Number of times producer was delayed due to rate limiting",
    ["service"]
)


@dataclass
class WorkItem:
    """A work item to be processed."""
    id: str
    created_at: float
    payload: dict = field(default_factory=dict)
    size_bytes: int = 0

    def __post_init__(self):
        # Simulate realistic payload size (1-10 KB per item)
        if not self.payload:
            # Create a payload that takes up memory
            self.payload = {
                "data": "x" * 1024,  # ~1KB base
                "metadata": {
                    "timestamp": datetime.now().isoformat(),
                    "source": "producer",
                    "batch_id": str(uuid.uuid4()),
                }
            }
        self.size_bytes = sys.getsizeof(str(self.payload))


class BackpressureQueue:
    """Queue with different backpressure strategies."""

    def __init__(self, mode: str = "unbounded", max_size: int = 100, rate_limit: int = 5):
        self.mode = mode
        self.max_size = max_size
        self.rate_limit = rate_limit
        self.queue: deque = deque()
        self.total_bytes = 0
        self.last_send_time = 0.0
        self.send_interval = 1.0 / rate_limit if rate_limit > 0 else 0
        self._lock = asyncio.Lock()

    async def enqueue(self, item: WorkItem) -> tuple[bool, str]:
        """
        Add item to queue.
        Returns (success, reason).
        """
        async with self._lock:
            if self.mode == "bounded" and len(self.queue) >= self.max_size:
                return False, "queue_full"

            self.queue.append(item)
            self.total_bytes += item.size_bytes

            # Update metrics
            QUEUE_DEPTH.labels(service=SERVICE_NAME).set(len(self.queue))
            QUEUE_MEMORY_BYTES.labels(service=SERVICE_NAME).set(self.total_bytes)

            return True, "enqueued"

    async def dequeue(self) -> Optional[WorkItem]:
        """Remove and return item from queue."""
        async with self._lock:
            if not self.queue:
                return None

            # Rate limiting: wait if needed
            if self.mode == "rate_limited":
                now = time.time()
                elapsed = now - self.last_send_time
                if elapsed < self.send_interval:
                    wait_time = self.send_interval - elapsed
                    RATE_LIMIT_DELAYS.labels(service=SERVICE_NAME).inc()
                    await asyncio.sleep(wait_time)
                self.last_send_time = time.time()

            item = self.queue.popleft()
            self.total_bytes -= item.size_bytes

            # Update metrics
            QUEUE_DEPTH.labels(service=SERVICE_NAME).set(len(self.queue))
            QUEUE_MEMORY_BYTES.labels(service=SERVICE_NAME).set(self.total_bytes)

            return item

    def size(self) -> int:
        return len(self.queue)

    def memory_bytes(self) -> int:
        return self.total_bytes


# Global state
queue: Optional[BackpressureQueue] = None
producer_task: Optional[asyncio.Task] = None
consumer_task: Optional[asyncio.Task] = None
running = False


def get_memory_usage() -> int:
    """Get current process memory usage in bytes."""
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024  # Convert to bytes on macOS/Linux
    except Exception:
        return 0


async def produce_items():
    """Background task that produces work items."""
    global running
    interval = 1.0 / PRODUCTION_RATE if PRODUCTION_RATE > 0 else 1.0

    logger.info(f"Producer started: rate={PRODUCTION_RATE}/s, mode={QUEUE_MODE}")

    while running:
        try:
            item = WorkItem(
                id=str(uuid.uuid4()),
                created_at=time.time()
            )

            success, reason = await queue.enqueue(item)

            if success:
                ITEMS_PRODUCED.labels(service=SERVICE_NAME).inc()
                logger.debug(f"Produced item {item.id}, queue_size={queue.size()}")
            else:
                ITEMS_REJECTED.labels(service=SERVICE_NAME).inc()
                logger.warning(f"Rejected item {item.id}: {reason}")

            # Update memory metrics
            PROCESS_MEMORY_BYTES.labels(service=SERVICE_NAME).set(get_memory_usage())

            await asyncio.sleep(interval)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Producer error: {e}")
            await asyncio.sleep(1)


async def consume_and_send():
    """Background task that sends items to consumer."""
    global running

    logger.info(f"Sender started: consumer_url={CONSUMER_URL}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        while running:
            try:
                item = await queue.dequeue()

                if item is None:
                    await asyncio.sleep(0.01)
                    continue

                start_time = time.time()

                with tracer.start_as_current_span("send_to_consumer") as span:
                    span.set_attribute("item.id", item.id)
                    span.set_attribute("queue.size", queue.size())
                    span.set_attribute("queue.mode", QUEUE_MODE)

                    try:
                        response = await client.post(
                            f"{CONSUMER_URL}/process",
                            json={
                                "id": item.id,
                                "created_at": item.created_at,
                                "payload": item.payload
                            }
                        )

                        duration = time.time() - start_time
                        SEND_LATENCY.labels(service=SERVICE_NAME).observe(duration)

                        if response.status_code == 200:
                            ITEMS_SENT.labels(service=SERVICE_NAME).inc()
                            queue_latency = time.time() - item.created_at
                            span.set_attribute("queue.latency_seconds", queue_latency)
                            logger.debug(f"Sent item {item.id}, queue_latency={queue_latency:.3f}s")
                        else:
                            ITEMS_DROPPED.labels(service=SERVICE_NAME).inc()
                            span.set_attribute("error", True)
                            logger.warning(f"Consumer returned {response.status_code} for item {item.id}")

                    except httpx.RequestError as e:
                        ITEMS_DROPPED.labels(service=SERVICE_NAME).inc()
                        span.set_attribute("error", True)
                        span.set_attribute("error.message", str(e))
                        logger.error(f"Failed to send item {item.id}: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Sender error: {e}")
                await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global queue, producer_task, consumer_task, running

    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Queue mode: {QUEUE_MODE}")
    logger.info(f"Production rate: {PRODUCTION_RATE}/s")
    logger.info(f"Max queue size: {MAX_QUEUE_SIZE}")
    logger.info(f"Rate limit: {RATE_LIMIT}/s")

    # Initialize queue
    queue = BackpressureQueue(
        mode=QUEUE_MODE,
        max_size=MAX_QUEUE_SIZE,
        rate_limit=RATE_LIMIT
    )

    # Start background tasks
    running = True
    producer_task = asyncio.create_task(produce_items())
    consumer_task = asyncio.create_task(consume_and_send())

    yield

    # Shutdown
    logger.info(f"{SERVICE_NAME} shutting down")
    running = False

    if producer_task:
        producer_task.cancel()
        try:
            await producer_task
        except asyncio.CancelledError:
            pass

    if consumer_task:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "queue_mode": QUEUE_MODE,
        "queue_size": queue.size() if queue else 0
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
async def status():
    """Get detailed status of the producer."""
    memory_usage = get_memory_usage()

    return {
        "service": SERVICE_NAME,
        "queue": {
            "mode": QUEUE_MODE,
            "size": queue.size() if queue else 0,
            "max_size": MAX_QUEUE_SIZE if QUEUE_MODE == "bounded" else "unlimited",
            "memory_bytes": queue.memory_bytes() if queue else 0,
            "memory_mb": round((queue.memory_bytes() if queue else 0) / 1024 / 1024, 2)
        },
        "config": {
            "production_rate": PRODUCTION_RATE,
            "rate_limit": RATE_LIMIT if QUEUE_MODE == "rate_limited" else None,
            "consumer_url": CONSUMER_URL
        },
        "process": {
            "memory_bytes": memory_usage,
            "memory_mb": round(memory_usage / 1024 / 1024, 2),
            "memory_limit_mb": MEMORY_LIMIT_MB
        }
    }


@app.post("/admin/config")
async def update_config(request: Request):
    """Update producer configuration dynamically."""
    global QUEUE_MODE, MAX_QUEUE_SIZE, RATE_LIMIT, PRODUCTION_RATE, queue

    data = await request.json()

    if "queue_mode" in data:
        new_mode = data["queue_mode"]
        if new_mode not in ["unbounded", "bounded", "rate_limited"]:
            raise HTTPException(status_code=400, detail="Invalid queue mode")
        QUEUE_MODE = new_mode
        logger.info(f"Queue mode changed to: {QUEUE_MODE}")

    if "max_queue_size" in data:
        MAX_QUEUE_SIZE = int(data["max_queue_size"])
        logger.info(f"Max queue size changed to: {MAX_QUEUE_SIZE}")

    if "rate_limit" in data:
        RATE_LIMIT = int(data["rate_limit"])
        logger.info(f"Rate limit changed to: {RATE_LIMIT}")

    if "production_rate" in data:
        PRODUCTION_RATE = int(data["production_rate"])
        logger.info(f"Production rate changed to: {PRODUCTION_RATE}")

    # Recreate queue with new settings
    old_queue = queue
    queue = BackpressureQueue(
        mode=QUEUE_MODE,
        max_size=MAX_QUEUE_SIZE,
        rate_limit=RATE_LIMIT
    )

    # Transfer items from old queue (up to new max)
    if old_queue:
        transferred = 0
        while old_queue.size() > 0:
            item = await old_queue.dequeue()
            if item:
                success, _ = await queue.enqueue(item)
                if success:
                    transferred += 1
                else:
                    break
        logger.info(f"Transferred {transferred} items to new queue")

    return {
        "status": "updated",
        "config": {
            "queue_mode": QUEUE_MODE,
            "max_queue_size": MAX_QUEUE_SIZE,
            "rate_limit": RATE_LIMIT,
            "production_rate": PRODUCTION_RATE
        }
    }


@app.post("/admin/clear-queue")
async def clear_queue():
    """Clear all items from the queue."""
    global queue

    old_size = queue.size() if queue else 0
    queue = BackpressureQueue(
        mode=QUEUE_MODE,
        max_size=MAX_QUEUE_SIZE,
        rate_limit=RATE_LIMIT
    )

    QUEUE_DEPTH.labels(service=SERVICE_NAME).set(0)
    QUEUE_MEMORY_BYTES.labels(service=SERVICE_NAME).set(0)

    logger.info(f"Queue cleared: removed {old_size} items")

    return {
        "status": "cleared",
        "items_removed": old_size
    }


@app.post("/produce")
async def manual_produce(request: Request):
    """Manually produce items (for testing)."""
    data = await request.json()
    count = data.get("count", 1)

    produced = 0
    rejected = 0

    for _ in range(count):
        item = WorkItem(
            id=str(uuid.uuid4()),
            created_at=time.time()
        )
        success, _ = await queue.enqueue(item)
        if success:
            produced += 1
            ITEMS_PRODUCED.labels(service=SERVICE_NAME).inc()
        else:
            rejected += 1
            ITEMS_REJECTED.labels(service=SERVICE_NAME).inc()

    return {
        "produced": produced,
        "rejected": rejected,
        "queue_size": queue.size()
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
