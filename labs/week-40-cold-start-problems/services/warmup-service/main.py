"""
Warmup Service - Demonstrates cache warming strategies and gradual traffic shifting.
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import httpx
import redis.asyncio as redis
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "warmup-service")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
API_WARM_URL = os.getenv("API_WARM_URL", "http://api-warm:8000")
API_COLD_URL = os.getenv("API_COLD_URL", "http://api-cold:8000")

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
WARMUP_REQUESTS = Counter(
    "warmup_requests_total",
    "Total warmup requests sent",
    ["target", "endpoint"]
)
WARMUP_DURATION = Histogram(
    "warmup_duration_seconds",
    "Duration of warmup operations",
    ["strategy"],
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]
)
WARMUP_STATUS = Gauge(
    "warmup_in_progress",
    "Whether a warmup is currently in progress",
    ["target"]
)
TRAFFIC_WEIGHT = Gauge(
    "traffic_weight_percent",
    "Current traffic weight percentage",
    ["instance"]
)

# Global state
redis_client: Optional[redis.Redis] = None
http_client: Optional[httpx.AsyncClient] = None
warmup_in_progress = {"warm": False, "cold": False}
traffic_weights = {"warm": 100, "cold": 0}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client, http_client

    logger.info(f"{SERVICE_NAME} starting up")

    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        decode_responses=True
    )

    http_client = httpx.AsyncClient(timeout=60.0)

    # Initialize traffic weights in Redis
    await redis_client.hset("traffic_weights", mapping={"warm": "100", "cold": "0"})

    yield

    await redis_client.close()
    await http_client.aclose()
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    # Update traffic weight gauges
    for instance, weight in traffic_weights.items():
        TRAFFIC_WEIGHT.labels(instance=instance).set(weight)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def run_cache_warmup(target_url: str, target_name: str):
    """Execute cache warming by making requests to common endpoints."""
    global warmup_in_progress

    warmup_in_progress[target_name] = True
    WARMUP_STATUS.labels(target=target_name).set(1)

    with tracer.start_as_current_span(f"cache_warmup_{target_name}") as span:
        start = time.time()
        requests_made = 0
        errors = 0

        # Warm product cache
        for product_id in range(1, 51):
            try:
                response = await http_client.get(f"{target_url}/api/product/{product_id}")
                if response.status_code == 200:
                    WARMUP_REQUESTS.labels(target=target_name, endpoint="product").inc()
                    requests_made += 1
                else:
                    errors += 1
            except Exception as e:
                logger.warning(f"Warmup request failed: {e}")
                errors += 1

            # Small delay to avoid overwhelming the service
            await asyncio.sleep(0.05)

        # Warm recommendations cache
        for user_id in range(1, 21):
            try:
                response = await http_client.get(f"{target_url}/api/user/{user_id}/recommendations")
                if response.status_code == 200:
                    WARMUP_REQUESTS.labels(target=target_name, endpoint="recommendations").inc()
                    requests_made += 1
                else:
                    errors += 1
            except Exception as e:
                logger.warning(f"Warmup request failed: {e}")
                errors += 1

            await asyncio.sleep(0.05)

        # Execute some DB queries to warm connection pool
        for _ in range(10):
            try:
                response = await http_client.get(f"{target_url}/api/db-query")
                if response.status_code == 200:
                    WARMUP_REQUESTS.labels(target=target_name, endpoint="db-query").inc()
                    requests_made += 1
            except Exception as e:
                logger.warning(f"Warmup request failed: {e}")
                errors += 1

        duration = time.time() - start
        span.set_attribute("requests_made", requests_made)
        span.set_attribute("errors", errors)
        span.set_attribute("duration_seconds", duration)

        WARMUP_DURATION.labels(strategy="cache_warmup").observe(duration)

        logger.info(f"Cache warmup for {target_name} completed: {requests_made} requests, {errors} errors, {duration:.2f}s")

    warmup_in_progress[target_name] = False
    WARMUP_STATUS.labels(target=target_name).set(0)

    return {"requests_made": requests_made, "errors": errors, "duration_seconds": duration}


@app.post("/warmup/cache/{target}")
async def warmup_cache(target: str, background_tasks: BackgroundTasks):
    """Trigger cache warmup for a target instance."""
    if target not in ["warm", "cold"]:
        return {"error": "Target must be 'warm' or 'cold'"}

    if warmup_in_progress.get(target, False):
        return {"status": "already_running", "target": target}

    target_url = API_WARM_URL if target == "warm" else API_COLD_URL
    background_tasks.add_task(run_cache_warmup, target_url, target)

    return {"status": "started", "target": target, "url": target_url}


@app.post("/warmup/cache/{target}/sync")
async def warmup_cache_sync(target: str):
    """Trigger cache warmup synchronously (waits for completion)."""
    if target not in ["warm", "cold"]:
        return {"error": "Target must be 'warm' or 'cold'"}

    target_url = API_WARM_URL if target == "warm" else API_COLD_URL
    result = await run_cache_warmup(target_url, target)

    return {"status": "completed", "target": target, **result}


async def gradual_traffic_shift(from_instance: str, to_instance: str, duration_seconds: int, step_percent: int = 10):
    """Gradually shift traffic from one instance to another."""
    global traffic_weights

    with tracer.start_as_current_span("gradual_traffic_shift") as span:
        span.set_attribute("from_instance", from_instance)
        span.set_attribute("to_instance", to_instance)
        span.set_attribute("duration_seconds", duration_seconds)

        steps = 100 // step_percent
        delay_between_steps = duration_seconds / steps

        logger.info(f"Starting traffic shift: {from_instance} -> {to_instance} over {duration_seconds}s")

        for step in range(steps + 1):
            to_weight = step * step_percent
            from_weight = 100 - to_weight

            traffic_weights[from_instance] = from_weight
            traffic_weights[to_instance] = to_weight

            # Update Redis for load balancer to read
            await redis_client.hset("traffic_weights", mapping={
                from_instance: str(from_weight),
                to_instance: str(to_weight)
            })

            TRAFFIC_WEIGHT.labels(instance=from_instance).set(from_weight)
            TRAFFIC_WEIGHT.labels(instance=to_instance).set(to_weight)

            logger.info(f"Traffic shift step {step}/{steps}: {from_instance}={from_weight}%, {to_instance}={to_weight}%")

            if step < steps:
                await asyncio.sleep(delay_between_steps)

        WARMUP_DURATION.labels(strategy="traffic_shift").observe(duration_seconds)

        return {
            "status": "completed",
            "final_weights": traffic_weights.copy()
        }


@app.post("/traffic/shift")
async def shift_traffic(
    from_instance: str,
    to_instance: str,
    duration_seconds: int = 60,
    step_percent: int = 10,
    background_tasks: BackgroundTasks = None
):
    """
    Gradually shift traffic between instances.

    This demonstrates the gradual traffic shifting strategy to avoid
    overwhelming a cold instance with traffic.
    """
    if from_instance not in ["warm", "cold"] or to_instance not in ["warm", "cold"]:
        return {"error": "Instances must be 'warm' or 'cold'"}

    if from_instance == to_instance:
        return {"error": "from_instance and to_instance must be different"}

    background_tasks.add_task(
        gradual_traffic_shift,
        from_instance,
        to_instance,
        duration_seconds,
        step_percent
    )

    return {
        "status": "started",
        "from_instance": from_instance,
        "to_instance": to_instance,
        "duration_seconds": duration_seconds,
        "step_percent": step_percent
    }


@app.post("/traffic/shift/sync")
async def shift_traffic_sync(
    from_instance: str,
    to_instance: str,
    duration_seconds: int = 60,
    step_percent: int = 10
):
    """Synchronous traffic shift (waits for completion)."""
    if from_instance not in ["warm", "cold"] or to_instance not in ["warm", "cold"]:
        return {"error": "Instances must be 'warm' or 'cold'"}

    result = await gradual_traffic_shift(from_instance, to_instance, duration_seconds, step_percent)
    return result


@app.get("/traffic/weights")
async def get_traffic_weights():
    """Get current traffic weights."""
    stored_weights = await redis_client.hgetall("traffic_weights")
    return {
        "weights": traffic_weights,
        "stored_weights": stored_weights
    }


@app.post("/traffic/set")
async def set_traffic_weight(warm: int = 100, cold: int = 0):
    """Immediately set traffic weights (for testing)."""
    global traffic_weights

    if warm + cold != 100:
        return {"error": "Weights must sum to 100"}

    traffic_weights["warm"] = warm
    traffic_weights["cold"] = cold

    await redis_client.hset("traffic_weights", mapping={
        "warm": str(warm),
        "cold": str(cold)
    })

    TRAFFIC_WEIGHT.labels(instance="warm").set(warm)
    TRAFFIC_WEIGHT.labels(instance="cold").set(cold)

    return {"status": "ok", "weights": traffic_weights}


@app.get("/status")
async def get_status():
    """Get overall warmup service status."""
    stored_weights = await redis_client.hgetall("traffic_weights")

    # Check instance health
    instance_status = {}
    for name, url in [("warm", API_WARM_URL), ("cold", API_COLD_URL)]:
        try:
            response = await http_client.get(f"{url}/admin/status", timeout=5.0)
            if response.status_code == 200:
                instance_status[name] = response.json()
            else:
                instance_status[name] = {"error": f"HTTP {response.status_code}"}
        except Exception as e:
            instance_status[name] = {"error": str(e)}

    return {
        "service": SERVICE_NAME,
        "warmup_in_progress": warmup_in_progress,
        "traffic_weights": traffic_weights,
        "stored_weights": stored_weights,
        "instances": instance_status
    }


@app.post("/simulate/deploy")
async def simulate_deploy(background_tasks: BackgroundTasks, warmup_first: bool = True, shift_duration: int = 60):
    """
    Simulate a deployment scenario.

    This demonstrates the recommended deployment pattern:
    1. Start new instance (cold)
    2. Warm caches and connections
    3. Gradually shift traffic to new instance
    """
    steps = []

    if warmup_first:
        # Step 1: Clear cold instance cache to simulate fresh deploy
        try:
            await http_client.post(f"{API_COLD_URL}/admin/clear-cache")
            steps.append({"step": "clear_cache", "status": "completed"})
        except Exception as e:
            steps.append({"step": "clear_cache", "status": "failed", "error": str(e)})

        # Step 2: Warm the cold instance
        warmup_result = await run_cache_warmup(API_COLD_URL, "cold")
        steps.append({"step": "warmup", "status": "completed", "details": warmup_result})

    # Step 3: Gradually shift traffic
    background_tasks.add_task(gradual_traffic_shift, "warm", "cold", shift_duration, 10)
    steps.append({"step": "traffic_shift", "status": "started", "duration": shift_duration})

    return {
        "status": "deployment_simulation_started",
        "warmup_first": warmup_first,
        "steps": steps
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
