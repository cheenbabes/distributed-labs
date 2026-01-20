"""
Client Service - Generates load against the API Gateway to test bulkhead patterns.

This service provides endpoints to:
- Generate sustained load against specific backends
- Run comparison tests (with/without bulkheads)
- Demonstrate cascade failures
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, Request, BackgroundTasks
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "client")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
API_GATEWAY_URL = os.getenv("API_GATEWAY_URL", "http://api-gateway:8000")

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
LOAD_TEST_REQUESTS = Counter(
    "client_loadtest_requests_total",
    "Total load test requests",
    ["endpoint", "status"]
)

LOAD_TEST_LATENCY = Histogram(
    "client_loadtest_duration_seconds",
    "Load test request latency",
    ["endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

ACTIVE_LOAD_TESTS = Gauge(
    "client_active_load_tests",
    "Number of active load tests"
)

# Global state for load tests
active_load_tests = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"API Gateway URL: {API_GATEWAY_URL}")
    yield
    # Cancel any running load tests
    for task_id, task in active_load_tests.items():
        task["running"] = False
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def make_request(endpoint: str, timeout: float = 10.0) -> dict:
    """Make a single request to the API gateway."""
    url = f"{API_GATEWAY_URL}{endpoint}"
    start_time = time.time()

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            duration = time.time() - start_time

            LOAD_TEST_REQUESTS.labels(endpoint=endpoint, status="success").inc()
            LOAD_TEST_LATENCY.labels(endpoint=endpoint).observe(duration)

            return {
                "status": "success",
                "status_code": response.status_code,
                "duration_ms": round(duration * 1000, 2),
                "data": response.json() if response.status_code == 200 else None
            }

    except httpx.TimeoutException:
        duration = time.time() - start_time
        LOAD_TEST_REQUESTS.labels(endpoint=endpoint, status="timeout").inc()
        LOAD_TEST_LATENCY.labels(endpoint=endpoint).observe(duration)
        return {
            "status": "timeout",
            "duration_ms": round(duration * 1000, 2),
            "error": "Request timed out"
        }

    except Exception as e:
        duration = time.time() - start_time
        LOAD_TEST_REQUESTS.labels(endpoint=endpoint, status="error").inc()
        LOAD_TEST_LATENCY.labels(endpoint=endpoint).observe(duration)
        return {
            "status": "error",
            "duration_ms": round(duration * 1000, 2),
            "error": str(e)
        }


@app.get("/test/single/{backend}")
async def test_single(backend: str):
    """Make a single request to a specific backend."""
    if backend not in ["fast", "slow", "flaky", "all"]:
        return {"error": f"Unknown backend: {backend}. Use: fast, slow, flaky, or all"}

    endpoint = f"/api/{backend}"
    result = await make_request(endpoint)

    return {
        "backend": backend,
        "endpoint": endpoint,
        "result": result
    }


@app.post("/test/burst")
async def test_burst(request: Request):
    """Send a burst of concurrent requests."""
    data = await request.json()

    backend = data.get("backend", "fast")
    count = data.get("count", 20)
    timeout = data.get("timeout", 10.0)

    if backend not in ["fast", "slow", "flaky", "all"]:
        return {"error": f"Unknown backend: {backend}"}

    endpoint = f"/api/{backend}"

    logger.info(f"Starting burst test: {count} requests to {endpoint}")
    start_time = time.time()

    # Send all requests concurrently
    tasks = [make_request(endpoint, timeout) for _ in range(count)]
    results = await asyncio.gather(*tasks)

    total_duration = time.time() - start_time

    # Analyze results
    success_count = sum(1 for r in results if r["status"] == "success")
    timeout_count = sum(1 for r in results if r["status"] == "timeout")
    error_count = sum(1 for r in results if r["status"] == "error")

    latencies = [r["duration_ms"] for r in results]

    return {
        "backend": backend,
        "total_requests": count,
        "total_duration_ms": round(total_duration * 1000, 2),
        "success_count": success_count,
        "timeout_count": timeout_count,
        "error_count": error_count,
        "success_rate": round(success_count / count * 100, 2),
        "latency": {
            "min_ms": round(min(latencies), 2),
            "max_ms": round(max(latencies), 2),
            "avg_ms": round(sum(latencies) / len(latencies), 2),
        }
    }


async def run_sustained_load(
    task_id: str,
    endpoint: str,
    rps: int,
    duration_seconds: int,
    timeout: float
):
    """Background task to run sustained load."""
    logger.info(f"Load test {task_id} starting: {rps} RPS to {endpoint} for {duration_seconds}s")

    active_load_tests[task_id]["status"] = "running"
    ACTIVE_LOAD_TESTS.inc()

    interval = 1.0 / rps
    end_time = time.time() + duration_seconds

    total_requests = 0
    success_count = 0
    error_count = 0

    try:
        while time.time() < end_time and active_load_tests[task_id]["running"]:
            # Make request without waiting
            asyncio.create_task(make_request(endpoint, timeout))
            total_requests += 1

            await asyncio.sleep(interval)

        active_load_tests[task_id]["status"] = "completed"
        logger.info(f"Load test {task_id} completed: {total_requests} requests sent")

    except Exception as e:
        active_load_tests[task_id]["status"] = "error"
        active_load_tests[task_id]["error"] = str(e)
        logger.error(f"Load test {task_id} failed: {e}")

    finally:
        ACTIVE_LOAD_TESTS.dec()
        active_load_tests[task_id]["total_requests"] = total_requests
        active_load_tests[task_id]["end_time"] = time.time()


@app.post("/test/sustained")
async def test_sustained(request: Request, background_tasks: BackgroundTasks):
    """Start a sustained load test in the background."""
    data = await request.json()

    backend = data.get("backend", "fast")
    rps = data.get("rps", 10)  # Requests per second
    duration = data.get("duration", 60)  # Duration in seconds
    timeout = data.get("timeout", 10.0)

    if backend not in ["fast", "slow", "flaky", "all"]:
        return {"error": f"Unknown backend: {backend}"}

    endpoint = f"/api/{backend}"
    task_id = f"load-{backend}-{int(time.time())}"

    active_load_tests[task_id] = {
        "running": True,
        "status": "starting",
        "backend": backend,
        "rps": rps,
        "duration": duration,
        "start_time": time.time(),
        "total_requests": 0,
    }

    background_tasks.add_task(
        run_sustained_load,
        task_id,
        endpoint,
        rps,
        duration,
        timeout
    )

    return {
        "task_id": task_id,
        "backend": backend,
        "rps": rps,
        "duration": duration,
        "status": "started"
    }


@app.get("/test/status")
async def test_status():
    """Get status of all load tests."""
    return {
        "active_count": sum(1 for t in active_load_tests.values() if t.get("running", False)),
        "tests": active_load_tests
    }


@app.post("/test/stop/{task_id}")
async def stop_test(task_id: str):
    """Stop a running load test."""
    if task_id not in active_load_tests:
        return {"error": f"Unknown task: {task_id}"}

    active_load_tests[task_id]["running"] = False
    return {"status": "stopping", "task_id": task_id}


@app.post("/test/compare")
async def test_compare(request: Request):
    """
    Compare behavior with and without bulkheads.
    Sends concurrent requests to all backends simultaneously.
    """
    data = await request.json()

    count = data.get("count", 30)
    timeout = data.get("timeout", 10.0)

    logger.info(f"Starting comparison test: {count} concurrent requests")

    # Test with all backends at once
    endpoints = ["/api/fast", "/api/slow", "/api/flaky"]

    start_time = time.time()

    # Create requests for all endpoints
    tasks = []
    for _ in range(count):
        for endpoint in endpoints:
            tasks.append(make_request(endpoint, timeout))

    results = await asyncio.gather(*tasks)

    total_duration = time.time() - start_time

    # Organize results by endpoint
    organized = {"/api/fast": [], "/api/slow": [], "/api/flaky": []}
    for i, result in enumerate(results):
        endpoint = endpoints[i % len(endpoints)]
        organized[endpoint].append(result)

    # Calculate stats per endpoint
    stats = {}
    for endpoint, endpoint_results in organized.items():
        success_count = sum(1 for r in endpoint_results if r["status"] == "success")
        latencies = [r["duration_ms"] for r in endpoint_results]

        stats[endpoint] = {
            "total": len(endpoint_results),
            "success": success_count,
            "success_rate": round(success_count / len(endpoint_results) * 100, 2),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0,
            "max_latency_ms": round(max(latencies), 2) if latencies else 0,
        }

    return {
        "total_requests": len(results),
        "total_duration_ms": round(total_duration * 1000, 2),
        "requests_per_second": round(len(results) / total_duration, 2),
        "by_endpoint": stats
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
