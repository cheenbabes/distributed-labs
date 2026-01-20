"""
Worker Service - Competes for distributed locks to perform work.

This worker demonstrates lock contention by repeatedly:
1. Trying to acquire a lock on a shared resource
2. Performing simulated work while holding the lock
3. Releasing the lock

Multiple workers compete for the same resource to demonstrate Redlock behavior.
"""
import asyncio
import logging
import os
import random
import time
from datetime import datetime

import httpx
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "worker")
WORKER_ID = os.getenv("WORKER_ID", "worker-1")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
LOCK_SERVICE_URL = os.getenv("LOCK_SERVICE_URL", "http://lock-service:8000")
WORK_DURATION_MS = int(os.getenv("WORK_DURATION_MS", "2000"))
WORK_INTERVAL_MS = int(os.getenv("WORK_INTERVAL_MS", "1000"))
RESOURCE_NAME = os.getenv("RESOURCE_NAME", "shared-resource")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format=f'%(asctime)s [{WORKER_ID}] %(levelname)s: %(message)s'
)
logger = logging.getLogger(WORKER_ID)

# OpenTelemetry setup
resource = Resource.create({"service.name": SERVICE_NAME, "worker.id": WORKER_ID})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# Instrument httpx for trace propagation
HTTPXClientInstrumentor().instrument()

# Statistics
stats = {
    "lock_attempts": 0,
    "lock_acquired": 0,
    "lock_failed": 0,
    "work_completed": 0,
    "errors": 0
}


async def acquire_lock(client: httpx.AsyncClient, resource: str) -> dict:
    """Try to acquire a lock on the resource."""
    try:
        response = await client.post(
            f"{LOCK_SERVICE_URL}/lock/acquire",
            json={
                "resource": resource,
                "client_id": f"{WORKER_ID}-{int(time.time() * 1000)}"
            },
            timeout=10.0
        )
        return response.json()
    except Exception as e:
        logger.error(f"Error acquiring lock: {e}")
        stats["errors"] += 1
        return {"acquired": False, "message": str(e)}


async def release_lock(client: httpx.AsyncClient, resource: str, lock_id: str) -> dict:
    """Release a held lock."""
    try:
        response = await client.post(
            f"{LOCK_SERVICE_URL}/lock/release",
            json={
                "resource": resource,
                "lock_id": lock_id
            },
            timeout=10.0
        )
        return response.json()
    except Exception as e:
        logger.error(f"Error releasing lock: {e}")
        stats["errors"] += 1
        return {"released": False, "message": str(e)}


async def do_work(lock_id: str, duration_ms: int):
    """Simulate doing some work while holding the lock."""
    with tracer.start_as_current_span("do_work") as span:
        span.set_attribute("worker.id", WORKER_ID)
        span.set_attribute("lock.id", lock_id)
        span.set_attribute("work.duration_ms", duration_ms)

        # Add some randomness to work duration
        actual_duration = duration_ms + random.randint(-200, 200)
        actual_duration = max(100, actual_duration)  # At least 100ms

        logger.info(f"Working for {actual_duration}ms with lock {lock_id}")

        # Simulate work in chunks to show progress
        chunk_size = 500
        elapsed = 0
        while elapsed < actual_duration:
            await asyncio.sleep(min(chunk_size, actual_duration - elapsed) / 1000.0)
            elapsed += chunk_size

        logger.info(f"Work completed after {actual_duration}ms")
        return actual_duration


async def work_loop():
    """Main work loop - continuously try to acquire locks and do work."""
    logger.info(f"Worker {WORKER_ID} starting work loop")
    logger.info(f"Lock service: {LOCK_SERVICE_URL}")
    logger.info(f"Resource: {RESOURCE_NAME}")
    logger.info(f"Work duration: {WORK_DURATION_MS}ms")
    logger.info(f"Work interval: {WORK_INTERVAL_MS}ms")

    async with httpx.AsyncClient() as client:
        while True:
            with tracer.start_as_current_span("work_cycle") as span:
                span.set_attribute("worker.id", WORKER_ID)
                span.set_attribute("resource", RESOURCE_NAME)

                stats["lock_attempts"] += 1

                # Try to acquire lock
                logger.info(f"Attempting to acquire lock on '{RESOURCE_NAME}'...")
                lock_result = await acquire_lock(client, RESOURCE_NAME)

                if lock_result.get("acquired"):
                    stats["lock_acquired"] += 1
                    lock_id = lock_result["lock_id"]
                    span.set_attribute("lock.acquired", True)
                    span.set_attribute("lock.id", lock_id)

                    logger.info(
                        f"Lock acquired! ID: {lock_id}, "
                        f"Nodes: {lock_result.get('redis_nodes_locked', 'unknown')}"
                    )

                    try:
                        # Do the work
                        work_time = await do_work(lock_id, WORK_DURATION_MS)
                        stats["work_completed"] += 1
                        span.set_attribute("work.completed", True)
                        span.set_attribute("work.duration_ms", work_time)

                    finally:
                        # Always release the lock
                        release_result = await release_lock(client, RESOURCE_NAME, lock_id)
                        logger.info(
                            f"Lock released: {release_result.get('released')}, "
                            f"Nodes: {release_result.get('nodes_released', 'unknown')}"
                        )

                else:
                    stats["lock_failed"] += 1
                    span.set_attribute("lock.acquired", False)
                    logger.info(f"Lock not acquired: {lock_result.get('message')}")

                # Log stats periodically
                if stats["lock_attempts"] % 10 == 0:
                    success_rate = (
                        stats["lock_acquired"] / stats["lock_attempts"] * 100
                        if stats["lock_attempts"] > 0 else 0
                    )
                    logger.info(
                        f"Stats: attempts={stats['lock_attempts']}, "
                        f"acquired={stats['lock_acquired']}, "
                        f"failed={stats['lock_failed']}, "
                        f"completed={stats['work_completed']}, "
                        f"success_rate={success_rate:.1f}%"
                    )

            # Wait before next attempt
            jitter = random.randint(-100, 100)
            wait_time = max(100, WORK_INTERVAL_MS + jitter)
            await asyncio.sleep(wait_time / 1000.0)


async def health_check_loop():
    """Periodically log health status."""
    while True:
        await asyncio.sleep(30)
        logger.info(
            f"Health check - running, "
            f"lock_attempts={stats['lock_attempts']}, "
            f"errors={stats['errors']}"
        )


async def main():
    """Main entry point."""
    logger.info(f"Starting worker {WORKER_ID}")

    # Wait a bit for lock service to be ready
    logger.info("Waiting for lock service to be ready...")
    await asyncio.sleep(5)

    # Run work loop and health check concurrently
    await asyncio.gather(
        work_loop(),
        health_check_loop()
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info(f"Worker {WORKER_ID} shutting down")
        logger.info(f"Final stats: {stats}")
