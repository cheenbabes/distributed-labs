"""
Canary Controller - Manages canary deployment traffic weights and automatic rollback.
Monitors error rates and triggers rollback when thresholds are exceeded.

The controller writes nginx upstream configuration to a shared volume.
Nginx watches for file changes and automatically reloads its config.
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "canary-controller")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
NGINX_CONFIG_PATH = os.getenv("NGINX_CONFIG_PATH", "/etc/nginx/conf.d/upstream.conf")
ERROR_RATE_THRESHOLD = float(os.getenv("ERROR_RATE_THRESHOLD", "0.05"))  # 5% error rate triggers rollback
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "10"))

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

HTTPXClientInstrumentor().instrument()

# Prometheus metrics
CANARY_WEIGHT = Gauge(
    "canary_weight_percent",
    "Current canary traffic weight percentage"
)
STABLE_WEIGHT = Gauge(
    "stable_weight_percent",
    "Current stable traffic weight percentage"
)
ROLLBACK_COUNT = Counter(
    "canary_rollback_total",
    "Total number of automatic rollbacks"
)
PROMOTION_COUNT = Counter(
    "canary_promotion_total",
    "Total number of canary promotions"
)
CANARY_ERROR_RATE = Gauge(
    "canary_observed_error_rate",
    "Observed error rate of canary version"
)
STABLE_ERROR_RATE = Gauge(
    "stable_observed_error_rate",
    "Observed error rate of stable version"
)
AUTO_ROLLBACK_ENABLED = Gauge(
    "auto_rollback_enabled",
    "Whether automatic rollback is enabled"
)

# State
state = {
    "canary_weight": 0,  # percentage (0-100)
    "auto_rollback_enabled": True,
    "error_rate_threshold": ERROR_RATE_THRESHOLD,
    "last_rollback_time": None,
    "last_promotion_time": None,
    "monitoring_active": True
}

# Weight presets for gradual rollout
WEIGHT_STAGES = [0, 1, 5, 10, 25, 50, 75, 100]


class WeightConfig(BaseModel):
    canary_weight: int  # 0-100


class RollbackConfig(BaseModel):
    enabled: bool
    error_rate_threshold: Optional[float] = None


def generate_nginx_config(canary_weight: int) -> str:
    """Generate nginx upstream configuration with weighted load balancing."""
    stable_weight = 100 - canary_weight

    # If canary is 0%, all traffic to stable
    if canary_weight == 0:
        return """upstream backend {
    server lab19-app-v1:8080 weight=100;
    # Canary disabled
}
"""
    # If canary is 100%, all traffic to canary
    elif canary_weight == 100:
        return """upstream backend {
    # Stable disabled
    server lab19-app-v2:8080 weight=100;
}
"""
    else:
        return f"""upstream backend {{
    server lab19-app-v1:8080 weight={stable_weight};
    server lab19-app-v2:8080 weight={canary_weight};
}}
"""


async def update_nginx_config(canary_weight: int) -> bool:
    """
    Update nginx configuration file.
    Nginx has a watcher that will detect the change and reload automatically.
    """
    try:
        config = generate_nginx_config(canary_weight)

        # Write config file atomically using write-then-rename
        temp_path = f"{NGINX_CONFIG_PATH}.tmp"
        with open(temp_path, 'w') as f:
            f.write(config)

        # Rename to actual path (atomic on POSIX)
        os.rename(temp_path, NGINX_CONFIG_PATH)

        logger.info(f"Nginx config updated: canary={canary_weight}%, stable={100-canary_weight}%")
        logger.info(f"Nginx will auto-reload via inotify watcher")
        return True
    except Exception as e:
        logger.error(f"Error updating nginx config: {e}")
        return False


async def get_error_rate(version: str) -> float:
    """Query Prometheus for the error rate of a version."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Query for error rate over last 1 minute
            query = f'''
                sum(rate(http_requests_total{{version="{version}", status="500"}}[1m])) /
                sum(rate(http_requests_total{{version="{version}"}}[1m]))
            '''
            response = await client.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={"query": query}
            )
            data = response.json()

            if data["status"] == "success" and data["data"]["result"]:
                value = float(data["data"]["result"][0]["value"][1])
                # Handle NaN (no requests)
                if value != value:  # NaN check
                    return 0.0
                return value
            return 0.0
    except Exception as e:
        logger.error(f"Error querying Prometheus: {e}")
        return 0.0


async def monitoring_loop():
    """Background task that monitors error rates and triggers rollback if needed."""
    logger.info("Starting canary monitoring loop")

    while state["monitoring_active"]:
        try:
            if state["canary_weight"] > 0:
                # Get error rates
                canary_error_rate = await get_error_rate("v2")
                stable_error_rate = await get_error_rate("v1")

                CANARY_ERROR_RATE.set(canary_error_rate)
                STABLE_ERROR_RATE.set(stable_error_rate)

                logger.info(f"Error rates - Canary: {canary_error_rate:.2%}, Stable: {stable_error_rate:.2%}")

                # Check if we need to rollback
                if (state["auto_rollback_enabled"] and
                    canary_error_rate > state["error_rate_threshold"] and
                    state["canary_weight"] > 0):

                    logger.warning(f"Canary error rate {canary_error_rate:.2%} exceeds threshold {state['error_rate_threshold']:.2%}")
                    logger.warning("Triggering automatic rollback!")

                    # Rollback
                    state["canary_weight"] = 0
                    await update_nginx_config(0)
                    CANARY_WEIGHT.set(0)
                    STABLE_WEIGHT.set(100)
                    ROLLBACK_COUNT.inc()
                    state["last_rollback_time"] = time.time()

        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Error rate threshold: {state['error_rate_threshold']:.2%}")
    logger.info(f"Config path: {NGINX_CONFIG_PATH}")

    # Initialize metrics
    CANARY_WEIGHT.set(state["canary_weight"])
    STABLE_WEIGHT.set(100 - state["canary_weight"])
    AUTO_ROLLBACK_ENABLED.set(1 if state["auto_rollback_enabled"] else 0)

    # Initialize nginx config (start with all traffic to stable)
    await update_nginx_config(state["canary_weight"])

    # Start monitoring loop
    monitoring_task = asyncio.create_task(monitoring_loop())

    yield

    # Stop monitoring
    state["monitoring_active"] = False
    monitoring_task.cancel()
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
async def get_status():
    """Get current canary deployment status."""
    canary_error_rate = await get_error_rate("v2")
    stable_error_rate = await get_error_rate("v1")

    return {
        "canary_weight": state["canary_weight"],
        "stable_weight": 100 - state["canary_weight"],
        "auto_rollback_enabled": state["auto_rollback_enabled"],
        "error_rate_threshold": state["error_rate_threshold"],
        "canary_error_rate": round(canary_error_rate, 4),
        "stable_error_rate": round(stable_error_rate, 4),
        "last_rollback_time": state["last_rollback_time"],
        "last_promotion_time": state["last_promotion_time"]
    }


@app.post("/weight")
async def set_weight(config: WeightConfig):
    """Set the canary traffic weight."""
    weight = max(0, min(100, config.canary_weight))
    state["canary_weight"] = weight

    success = await update_nginx_config(weight)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update nginx config")

    CANARY_WEIGHT.set(weight)
    STABLE_WEIGHT.set(100 - weight)

    if weight > 0:
        state["last_promotion_time"] = time.time()

    logger.info(f"Traffic weight updated: canary={weight}%, stable={100-weight}%")

    return {
        "status": "updated",
        "canary_weight": weight,
        "stable_weight": 100 - weight
    }


@app.post("/promote")
async def promote():
    """Promote canary to next weight stage."""
    current = state["canary_weight"]

    # Find next stage
    next_weight = 100
    for stage in WEIGHT_STAGES:
        if stage > current:
            next_weight = stage
            break

    state["canary_weight"] = next_weight
    await update_nginx_config(next_weight)
    CANARY_WEIGHT.set(next_weight)
    STABLE_WEIGHT.set(100 - next_weight)
    PROMOTION_COUNT.inc()
    state["last_promotion_time"] = time.time()

    logger.info(f"Canary promoted: {current}% -> {next_weight}%")

    return {
        "status": "promoted",
        "previous_weight": current,
        "canary_weight": next_weight,
        "stable_weight": 100 - next_weight,
        "stages": WEIGHT_STAGES,
        "fully_promoted": next_weight == 100
    }


@app.post("/rollback")
async def rollback():
    """Immediately rollback canary to 0%."""
    previous = state["canary_weight"]
    state["canary_weight"] = 0

    await update_nginx_config(0)
    CANARY_WEIGHT.set(0)
    STABLE_WEIGHT.set(100)
    ROLLBACK_COUNT.inc()
    state["last_rollback_time"] = time.time()

    logger.warning(f"Manual rollback triggered: {previous}% -> 0%")

    return {
        "status": "rolled_back",
        "previous_weight": previous,
        "canary_weight": 0,
        "stable_weight": 100
    }


@app.post("/auto-rollback")
async def configure_auto_rollback(config: RollbackConfig):
    """Configure automatic rollback settings."""
    state["auto_rollback_enabled"] = config.enabled
    if config.error_rate_threshold is not None:
        state["error_rate_threshold"] = max(0.0, min(1.0, config.error_rate_threshold))

    AUTO_ROLLBACK_ENABLED.set(1 if state["auto_rollback_enabled"] else 0)

    logger.info(f"Auto-rollback config: enabled={state['auto_rollback_enabled']}, threshold={state['error_rate_threshold']:.2%}")

    return {
        "status": "updated",
        "auto_rollback_enabled": state["auto_rollback_enabled"],
        "error_rate_threshold": state["error_rate_threshold"]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090)
