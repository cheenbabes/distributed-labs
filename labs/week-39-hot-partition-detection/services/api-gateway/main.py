"""
API Gateway - Routes requests to appropriate shards based on key hashing.
Supports optional local caching for hot key mitigation.
"""
import asyncio
import hashlib
import logging
import os
import random
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from threading import Lock

import httpx
from fastapi import FastAPI, Request, HTTPException
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "api-gateway")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab39-otel-collector:4317")
SHARD_URLS = os.getenv("SHARD_URLS", "http://lab39-shard-0:9000,http://lab39-shard-1:9001,http://lab39-shard-2:9002,http://lab39-shard-3:9003").split(",")
NUM_SHARDS = int(os.getenv("NUM_SHARDS", "4"))
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "false").lower() == "true"
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "60"))

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
REQUEST_COUNT = Counter(
    "gateway_requests_total",
    "Total requests to the gateway",
    ["operation", "shard_id", "cache_hit"]
)
REQUEST_LATENCY = Histogram(
    "gateway_request_duration_seconds",
    "Gateway request latency",
    ["operation", "shard_id"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)
SHARD_DISTRIBUTION = Counter(
    "gateway_shard_distribution_total",
    "Distribution of requests across shards",
    ["shard_id"]
)
KEY_ACCESS_COUNT = Counter(
    "gateway_key_access_total",
    "Access count per key at gateway level",
    ["key"]
)
CACHE_SIZE = Gauge(
    "gateway_cache_size",
    "Number of items in the cache"
)
CACHE_HITS = Counter(
    "gateway_cache_hits_total",
    "Cache hit count"
)
CACHE_MISSES = Counter(
    "gateway_cache_misses_total",
    "Cache miss count"
)

# Local cache for hot key mitigation
cache: dict[str, tuple[float, dict]] = {}
cache_lock = Lock()

# Request tracking for hot key detection
key_access_counts: dict[str, int] = defaultdict(int)
access_lock = Lock()


class KeyValue(BaseModel):
    key: str
    value: dict


class CacheConfig(BaseModel):
    enabled: bool
    ttl_seconds: int = 60


def get_shard_for_key(key: str) -> int:
    """Consistent hashing to determine which shard owns a key."""
    hash_value = int(hashlib.md5(key.encode()).hexdigest(), 16)
    return hash_value % NUM_SHARDS


def get_from_cache(key: str) -> dict | None:
    """Get value from local cache if present and not expired."""
    if not CACHE_ENABLED:
        return None

    with cache_lock:
        if key in cache:
            timestamp, value = cache[key]
            if time.time() - timestamp < CACHE_TTL_SECONDS:
                return value
            else:
                del cache[key]
    return None


def set_in_cache(key: str, value: dict):
    """Store value in local cache."""
    if not CACHE_ENABLED:
        return

    with cache_lock:
        cache[key] = (time.time(), value)
        CACHE_SIZE.set(len(cache))


def invalidate_cache(key: str):
    """Remove key from cache."""
    with cache_lock:
        if key in cache:
            del cache[key]
            CACHE_SIZE.set(len(cache))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Shard URLs: {SHARD_URLS}")
    logger.info(f"Cache enabled: {CACHE_ENABLED}")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/stats")
async def stats():
    """Return gateway statistics."""
    with access_lock:
        top_keys = sorted(key_access_counts.items(), key=lambda x: x[1], reverse=True)[:20]

    with cache_lock:
        cache_size = len(cache)

    return {
        "num_shards": NUM_SHARDS,
        "cache_enabled": CACHE_ENABLED,
        "cache_size": cache_size,
        "top_keys": [{"key": k, "count": c} for k, c in top_keys]
    }


@app.get("/data/{key}")
async def get_key(key: str):
    """Get a value by key, routing to appropriate shard."""
    start_time = time.time()

    current_span = trace.get_current_span()
    shard_id = get_shard_for_key(key)
    current_span.set_attribute("key", key)
    current_span.set_attribute("shard.id", shard_id)

    # Track key access
    with access_lock:
        key_access_counts[key] += 1

    KEY_ACCESS_COUNT.labels(key=key).inc()
    SHARD_DISTRIBUTION.labels(shard_id=str(shard_id)).inc()

    # Check cache first
    cached_value = get_from_cache(key)
    if cached_value is not None:
        CACHE_HITS.inc()
        REQUEST_COUNT.labels(operation="get", shard_id=str(shard_id), cache_hit="true").inc()
        current_span.set_attribute("cache_hit", True)

        duration = time.time() - start_time
        REQUEST_LATENCY.labels(operation="get", shard_id=str(shard_id)).observe(duration)

        return {
            "key": key,
            "value": cached_value,
            "shard_id": shard_id,
            "cache_hit": True,
            "latency_ms": round(duration * 1000, 2)
        }

    CACHE_MISSES.inc()
    current_span.set_attribute("cache_hit", False)

    # Route to appropriate shard
    shard_url = SHARD_URLS[shard_id]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{shard_url}/data/{key}")
            response.raise_for_status()
            result = response.json()

        # Cache the result
        if result.get("found", False) or result.get("auto_created", False):
            set_in_cache(key, result.get("value", {}))

        duration = time.time() - start_time
        REQUEST_COUNT.labels(operation="get", shard_id=str(shard_id), cache_hit="false").inc()
        REQUEST_LATENCY.labels(operation="get", shard_id=str(shard_id)).observe(duration)

        return {
            "key": key,
            "value": result.get("value"),
            "shard_id": shard_id,
            "cache_hit": False,
            "latency_ms": round(duration * 1000, 2)
        }

    except httpx.HTTPError as e:
        logger.error(f"Error calling shard {shard_id}: {e}")
        raise HTTPException(status_code=503, detail=f"Shard {shard_id} unavailable")


@app.post("/data")
async def set_key(kv: KeyValue):
    """Set a key-value pair, routing to appropriate shard."""
    start_time = time.time()

    current_span = trace.get_current_span()
    shard_id = get_shard_for_key(kv.key)
    current_span.set_attribute("key", kv.key)
    current_span.set_attribute("shard.id", shard_id)

    with access_lock:
        key_access_counts[kv.key] += 1

    KEY_ACCESS_COUNT.labels(key=kv.key).inc()
    SHARD_DISTRIBUTION.labels(shard_id=str(shard_id)).inc()

    # Invalidate cache
    invalidate_cache(kv.key)

    # Route to appropriate shard
    shard_url = SHARD_URLS[shard_id]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{shard_url}/data",
                json={"key": kv.key, "value": kv.value}
            )
            response.raise_for_status()
            result = response.json()

        duration = time.time() - start_time
        REQUEST_COUNT.labels(operation="set", shard_id=str(shard_id), cache_hit="false").inc()
        REQUEST_LATENCY.labels(operation="set", shard_id=str(shard_id)).observe(duration)

        return {
            "key": kv.key,
            "shard_id": shard_id,
            "status": "stored",
            "latency_ms": round(duration * 1000, 2)
        }

    except httpx.HTTPError as e:
        logger.error(f"Error calling shard {shard_id}: {e}")
        raise HTTPException(status_code=503, detail=f"Shard {shard_id} unavailable")


@app.post("/admin/cache")
async def configure_cache(config: CacheConfig):
    """Configure caching (for hot key mitigation demo)."""
    global CACHE_ENABLED, CACHE_TTL_SECONDS

    CACHE_ENABLED = config.enabled
    CACHE_TTL_SECONDS = config.ttl_seconds

    if not config.enabled:
        with cache_lock:
            cache.clear()
            CACHE_SIZE.set(0)

    logger.info(f"Cache configured: enabled={CACHE_ENABLED}, ttl={CACHE_TTL_SECONDS}")

    return {
        "cache_enabled": CACHE_ENABLED,
        "cache_ttl_seconds": CACHE_TTL_SECONDS
    }


@app.post("/admin/reset")
async def reset_stats():
    """Reset gateway statistics and cache."""
    global key_access_counts

    with access_lock:
        key_access_counts.clear()

    with cache_lock:
        cache.clear()
        CACHE_SIZE.set(0)

    return {"status": "reset"}


@app.get("/admin/shard-mapping/{key}")
async def get_shard_mapping(key: str):
    """Show which shard a key maps to (for debugging)."""
    shard_id = get_shard_for_key(key)
    return {
        "key": key,
        "shard_id": shard_id,
        "shard_url": SHARD_URLS[shard_id]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
