"""
Strongly Consistent Counter Service - For comparison with CRDT counters.

This service demonstrates the performance cost of strong consistency:
- Every increment requires coordination with the coordinator
- Reads always reflect the latest write
- Higher latency but no eventual consistency window

This is NOT how Netflix typically counts views (they use eventual consistency),
but it's useful for understanding the trade-offs.
"""
import asyncio
import logging
import os
import time
import threading
from contextlib import asynccontextmanager
from typing import Dict

import httpx
from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from pydantic import BaseModel

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "counter-consistent")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
SERVICE_PORT = int(os.getenv("SERVICE_PORT", "8004"))
COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://coordinator:8000")

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

# Instrument httpx
HTTPXClientInstrumentor().instrument()

# Prometheus metrics
CONSISTENT_COUNT = Gauge(
    "consistent_counter_value",
    "Current counter value",
    ["counter_name"]
)
INCREMENT_LATENCY = Histogram(
    "consistent_counter_increment_latency_seconds",
    "Increment operation latency",
    ["counter_name"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
INCREMENT_COUNT = Counter(
    "consistent_counter_increments_total",
    "Total increments",
    ["counter_name"]
)
LOCK_WAIT_TIME = Histogram(
    "consistent_counter_lock_wait_seconds",
    "Time spent waiting for lock",
    ["counter_name"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25]
)


class IncrementRequest(BaseModel):
    """Request to increment a counter."""
    counter_name: str = "views"
    amount: int = 1


class StronglyConsistentCounter:
    """
    A strongly consistent counter using mutual exclusion.

    This simulates coordination overhead:
    - Uses a lock to ensure serialized increments
    - Adds artificial "coordination" delay to simulate network round-trips
    - Always returns the true current value
    """

    def __init__(self):
        self.counters: Dict[str, int] = {}
        self.locks: Dict[str, asyncio.Lock] = {}
        self.global_lock = asyncio.Lock()
        # Simulated coordination delay (would be network RTT in real system)
        self.coordination_delay_ms = 50

    async def get_lock(self, counter_name: str) -> asyncio.Lock:
        """Get or create a lock for a counter."""
        async with self.global_lock:
            if counter_name not in self.locks:
                self.locks[counter_name] = asyncio.Lock()
            return self.locks[counter_name]

    async def increment(self, counter_name: str, amount: int = 1) -> Dict:
        """
        Increment with strong consistency.

        Steps:
        1. Acquire lock (simulates distributed lock)
        2. Read current value
        3. Increment
        4. Write new value
        5. Release lock

        This ensures linearizability but adds latency.
        """
        lock = await self.get_lock(counter_name)

        lock_wait_start = time.time()
        async with lock:
            lock_wait_time = time.time() - lock_wait_start
            LOCK_WAIT_TIME.labels(counter_name=counter_name).observe(lock_wait_time)

            # Simulate coordination delay (consensus/lock acquisition)
            await asyncio.sleep(self.coordination_delay_ms / 1000.0)

            # Perform the increment
            if counter_name not in self.counters:
                self.counters[counter_name] = 0
            self.counters[counter_name] += amount

            new_value = self.counters[counter_name]

            # Simulate write propagation delay
            await asyncio.sleep(self.coordination_delay_ms / 1000.0)

        return {
            "counter": counter_name,
            "value": new_value,
            "consistency": "strong",
            "lock_wait_ms": round(lock_wait_time * 1000, 2),
            "coordination_delay_ms": self.coordination_delay_ms * 2
        }

    def get(self, counter_name: str) -> int:
        """Get current value (always consistent)."""
        return self.counters.get(counter_name, 0)

    def get_all(self) -> Dict[str, int]:
        """Get all counters."""
        return dict(self.counters)


# Global counter store
counter_store: StronglyConsistentCounter = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global counter_store

    logger.info(f"{SERVICE_NAME} starting up")
    counter_store = StronglyConsistentCounter()

    yield

    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title="Strongly Consistent Counter", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/increment")
async def increment(request: IncrementRequest):
    """
    Increment with strong consistency.

    This is SLOWER than the eventual consistency counter but guarantees
    that reads immediately reflect the increment.
    """
    start_time = time.time()

    current_span = trace.get_current_span()
    current_span.set_attribute("counter.name", request.counter_name)
    current_span.set_attribute("counter.amount", request.amount)
    current_span.set_attribute("counter.consistency", "strong")

    result = await counter_store.increment(request.counter_name, request.amount)

    duration = time.time() - start_time

    # Update metrics
    CONSISTENT_COUNT.labels(counter_name=request.counter_name).set(result["value"])
    INCREMENT_LATENCY.labels(counter_name=request.counter_name).observe(duration)
    INCREMENT_COUNT.labels(counter_name=request.counter_name).inc()

    current_span.set_attribute("counter.value", result["value"])
    current_span.set_attribute("counter.latency_ms", round(duration * 1000, 2))

    return {
        **result,
        "total_latency_ms": round(duration * 1000, 2)
    }


@app.get("/counter/{counter_name}")
async def get_counter(counter_name: str):
    """Get the current value of a counter."""
    value = counter_store.get(counter_name)
    CONSISTENT_COUNT.labels(counter_name=counter_name).set(value)

    return {
        "counter": counter_name,
        "value": value,
        "consistency": "strong",
        "note": "This value is guaranteed to be the latest"
    }


@app.get("/counters")
async def get_all_counters():
    """Get all counters."""
    return {
        "counters": counter_store.get_all(),
        "consistency": "strong"
    }


@app.post("/admin/coordination-delay")
async def set_coordination_delay(delay_ms: int = 50):
    """Set the simulated coordination delay."""
    counter_store.coordination_delay_ms = delay_ms
    return {
        "coordination_delay_ms": delay_ms,
        "message": f"Set coordination delay to {delay_ms}ms"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
