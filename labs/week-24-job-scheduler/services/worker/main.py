"""
Job Worker - Executes jobs from the scheduler queue.
"""
import asyncio
import logging
import os
import random
import signal
import sys
from datetime import datetime
from typing import Optional

import httpx
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, start_http_server

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "worker")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
WORKER_ID = os.getenv("WORKER_ID", f"worker-{random.randint(1000, 9999)}")
SCHEDULER_URL = os.getenv("SCHEDULER_URL", "http://scheduler:8000")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "1.0"))
METRICS_PORT = int(os.getenv("METRICS_PORT", "9100"))

# Logging
logging.basicConfig(
    level=logging.INFO,
    format=f'%(asctime)s [{WORKER_ID}] %(levelname)s - %(message)s'
)
logger = logging.getLogger(SERVICE_NAME)

# OpenTelemetry setup
resource = Resource.create({
    "service.name": SERVICE_NAME,
    "worker.id": WORKER_ID
})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# Instrument httpx for trace propagation
HTTPXClientInstrumentor().instrument()

# Prometheus metrics
JOBS_PROCESSED = Counter(
    "worker_jobs_processed_total",
    "Total jobs processed by this worker",
    ["worker_id", "status"]
)
JOB_EXECUTION_TIME = Histogram(
    "worker_job_execution_seconds",
    "Job execution time",
    ["worker_id"],
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]
)
WORKER_BUSY = Gauge(
    "worker_busy",
    "Whether worker is currently processing a job",
    ["worker_id"]
)
POLL_ERRORS = Counter(
    "worker_poll_errors_total",
    "Total poll errors",
    ["worker_id"]
)

# Global state
running = True
current_job_id: Optional[str] = None
http_client: Optional[httpx.AsyncClient] = None


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global running
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    running = False


async def send_heartbeat():
    """Send periodic heartbeat to scheduler."""
    global current_job_id

    while running:
        try:
            await http_client.post(
                f"{SCHEDULER_URL}/workers/heartbeat",
                json={
                    "worker_id": WORKER_ID,
                    "current_job_id": current_job_id
                }
            )
        except Exception as e:
            logger.warning(f"Failed to send heartbeat: {e}")

        await asyncio.sleep(10)


async def fetch_job() -> Optional[dict]:
    """Fetch a job from the scheduler queue."""
    try:
        response = await http_client.get(
            f"{SCHEDULER_URL}/queue/fetch",
            params={"worker_id": WORKER_ID}
        )
        data = response.json()

        if data.get("job"):
            job_id = data["job"]["id"]

            # Claim the job
            claim_response = await http_client.post(
                f"{SCHEDULER_URL}/jobs/{job_id}/claim",
                params={"worker_id": WORKER_ID}
            )

            if claim_response.status_code == 200:
                return claim_response.json()
            else:
                logger.warning(f"Failed to claim job {job_id}: {claim_response.text}")

        return None

    except httpx.HTTPStatusError as e:
        if e.response.status_code != 409:  # Ignore conflict errors (job already claimed)
            POLL_ERRORS.labels(worker_id=WORKER_ID).inc()
            logger.error(f"HTTP error fetching job: {e}")
        return None
    except Exception as e:
        POLL_ERRORS.labels(worker_id=WORKER_ID).inc()
        logger.error(f"Error fetching job: {e}")
        return None


async def execute_job(job: dict) -> tuple[bool, Optional[str]]:
    """
    Execute a job and return (success, error_message).

    This simulates job execution with configurable:
    - execution_time_ms: How long the job takes
    - fail_rate: Probability of failure (0.0 - 1.0)
    """
    with tracer.start_as_current_span("execute_job") as span:
        job_id = job["id"]
        job_name = job["name"]
        execution_time_ms = job.get("execution_time_ms", 1000)
        fail_rate = job.get("fail_rate", 0.0)
        attempt = job.get("attempt", 1)
        max_retries = job.get("max_retries", 3)
        payload = job.get("payload", {})

        span.set_attribute("job.id", job_id)
        span.set_attribute("job.name", job_name)
        span.set_attribute("job.attempt", attempt)
        span.set_attribute("job.execution_time_ms", execution_time_ms)
        span.set_attribute("job.fail_rate", fail_rate)

        logger.info(
            f"Executing job {job_id} (name={job_name}, attempt={attempt}/{max_retries}, "
            f"exec_time={execution_time_ms}ms, fail_rate={fail_rate})"
        )

        try:
            # Simulate job execution
            # Add some variability to execution time (+-20%)
            actual_time_ms = execution_time_ms * (0.8 + random.random() * 0.4)
            await asyncio.sleep(actual_time_ms / 1000.0)

            # Check for simulated failure
            if random.random() < fail_rate:
                error_msg = f"Simulated failure (fail_rate={fail_rate})"
                span.set_attribute("job.error", error_msg)
                logger.warning(f"Job {job_id} failed: {error_msg}")
                return False, error_msg

            # Simulate different job types based on payload
            job_action = payload.get("action", "default")

            if job_action == "compute":
                # Simulate CPU-bound work
                result = sum(i * i for i in range(10000))
                span.set_attribute("job.result", str(result)[:100])

            elif job_action == "fetch":
                # Simulate external API call
                url = payload.get("url", "https://httpbin.org/get")
                span.set_attribute("job.fetch_url", url)
                # Note: In a real scenario, we'd actually make the request
                # For this lab, we just simulate it

            elif job_action == "process_data":
                # Simulate data processing
                data_size = payload.get("data_size", 100)
                span.set_attribute("job.data_size", data_size)

            logger.info(f"Job {job_id} completed successfully")
            return True, None

        except asyncio.CancelledError:
            logger.warning(f"Job {job_id} was cancelled")
            return False, "Job cancelled"
        except Exception as e:
            error_msg = str(e)
            span.set_attribute("job.error", error_msg)
            logger.error(f"Job {job_id} failed with exception: {error_msg}")
            return False, error_msg


async def report_completion(job_id: str, success: bool, error_message: Optional[str] = None):
    """Report job completion to the scheduler."""
    try:
        await http_client.post(
            f"{SCHEDULER_URL}/jobs/{job_id}/complete",
            params={
                "worker_id": WORKER_ID,
                "success": str(success).lower(),
                "error_message": error_message
            }
        )
    except Exception as e:
        logger.error(f"Failed to report completion for job {job_id}: {e}")


async def worker_loop():
    """Main worker loop - fetch and execute jobs."""
    global current_job_id

    logger.info(f"Worker {WORKER_ID} starting...")
    logger.info(f"Scheduler URL: {SCHEDULER_URL}")
    logger.info(f"Poll interval: {POLL_INTERVAL}s")

    while running:
        try:
            # Fetch a job
            job = await fetch_job()

            if job:
                current_job_id = job["id"]
                WORKER_BUSY.labels(worker_id=WORKER_ID).set(1)

                start_time = datetime.utcnow()

                # Execute the job
                success, error_message = await execute_job(job)

                # Record metrics
                duration = (datetime.utcnow() - start_time).total_seconds()
                JOB_EXECUTION_TIME.labels(worker_id=WORKER_ID).observe(duration)
                JOBS_PROCESSED.labels(
                    worker_id=WORKER_ID,
                    status="success" if success else "failed"
                ).inc()

                # Report completion
                await report_completion(job["id"], success, error_message)

                current_job_id = None
                WORKER_BUSY.labels(worker_id=WORKER_ID).set(0)
            else:
                # No job available, wait before polling again
                await asyncio.sleep(POLL_INTERVAL)

        except Exception as e:
            logger.error(f"Error in worker loop: {e}")
            current_job_id = None
            WORKER_BUSY.labels(worker_id=WORKER_ID).set(0)
            await asyncio.sleep(POLL_INTERVAL)

    logger.info(f"Worker {WORKER_ID} shutting down...")


async def main():
    """Main entry point."""
    global http_client

    # Register signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Start metrics server
    start_http_server(METRICS_PORT)
    logger.info(f"Metrics server started on port {METRICS_PORT}")

    # Wait for scheduler to be available
    logger.info("Waiting for scheduler to be available...")
    http_client = httpx.AsyncClient(timeout=30.0)

    for i in range(30):
        try:
            response = await http_client.get(f"{SCHEDULER_URL}/health")
            if response.status_code == 200:
                logger.info("Scheduler is available")
                break
        except Exception:
            pass
        await asyncio.sleep(1)
    else:
        logger.error("Scheduler not available after 30 seconds, exiting")
        sys.exit(1)

    # Start background tasks
    heartbeat_task = asyncio.create_task(send_heartbeat())

    # Run worker loop
    try:
        await worker_loop()
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
