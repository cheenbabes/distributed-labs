"""
Distributed Rate Limiter Service - Demonstrates distributed rate limiting with Redis.

This service implements:
1. Local in-memory rate limiting (per-instance, doesn't work with multiple instances)
2. Redis-based distributed rate limiting with sliding window
3. Race condition demonstration (non-atomic operations)
4. Atomic Lua script solution for race-free rate limiting
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Dict, Optional

import redis.asyncio as redis
from fastapi import FastAPI, Request, Response, Header
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import JSONResponse

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "api")
INSTANCE_ID = os.getenv("INSTANCE_ID", "api-1")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
RATE_LIMIT = int(os.getenv("RATE_LIMIT", "100"))  # requests per window
WINDOW_SIZE_SECONDS = int(os.getenv("WINDOW_SIZE_SECONDS", "60"))  # window size
USE_REDIS = os.getenv("USE_REDIS", "true").lower() == "true"
USE_LUA_SCRIPT = os.getenv("USE_LUA_SCRIPT", "true").lower() == "true"

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(SERVICE_NAME)

# OpenTelemetry setup
resource = Resource.create({"service.name": SERVICE_NAME, "service.instance.id": INSTANCE_ID})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# Prometheus metrics
RATE_LIMIT_ALLOWED = Counter(
    "rate_limit_allowed_total",
    "Total requests allowed by rate limiter",
    ["instance", "client", "mode"]
)
RATE_LIMIT_REJECTED = Counter(
    "rate_limit_rejected_total",
    "Total requests rejected by rate limiter",
    ["instance", "client", "mode"]
)
RATE_LIMIT_REMAINING = Gauge(
    "rate_limit_remaining",
    "Current remaining requests allowed",
    ["instance", "client"]
)
RATE_LIMIT_CHECK_DURATION = Histogram(
    "rate_limit_check_duration_seconds",
    "Time to check rate limit",
    ["instance", "mode"],
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1]
)
REDIS_OPERATIONS = Counter(
    "redis_operations_total",
    "Total Redis operations",
    ["instance", "operation", "status"]
)
RACE_CONDITIONS_DETECTED = Counter(
    "race_conditions_detected_total",
    "Race conditions detected (requests over limit)",
    ["instance"]
)

# Lua script for atomic sliding window rate limiting
# This script atomically:
# 1. Removes expired entries from the sorted set
# 2. Counts current entries
# 3. Adds new entry if under limit
# 4. Returns (allowed, current_count, oldest_timestamp)
SLIDING_WINDOW_LUA_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window_size = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local request_id = ARGV[4]

-- Remove expired entries (older than window)
local window_start = now - window_size
redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)

-- Count current entries
local count = redis.call('ZCARD', key)

-- Check if under limit
if count < limit then
    -- Add new request with timestamp as score
    redis.call('ZADD', key, now, request_id .. ':' .. now)
    -- Set expiry on key
    redis.call('EXPIRE', key, window_size + 1)
    return {1, count + 1, limit - count - 1}
else
    -- Get the oldest entry to calculate retry time
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local oldest_ts = 0
    if #oldest > 0 then
        oldest_ts = tonumber(oldest[2])
    end
    return {0, count, 0, oldest_ts}
end
"""

# Global state
redis_client: Optional[redis.Redis] = None
lua_script_sha: Optional[str] = None

# Local in-memory rate limiter (for single instance mode)
local_rate_limits: Dict[str, Dict] = {}


class RateLimitConfig(BaseModel):
    use_redis: bool
    use_lua_script: bool


class RateLimitStats(BaseModel):
    instance: str
    mode: str
    use_redis: bool
    use_lua_script: bool
    rate_limit: int
    window_size_seconds: int


async def init_redis():
    """Initialize Redis connection and Lua script."""
    global redis_client, lua_script_sha
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        await redis_client.ping()
        logger.info(f"Connected to Redis at {REDIS_URL}")

        # Register Lua script
        lua_script_sha = await redis_client.script_load(SLIDING_WINDOW_LUA_SCRIPT)
        logger.info(f"Lua script registered with SHA: {lua_script_sha}")

        return True
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
        return False


async def close_redis():
    """Close Redis connection."""
    global redis_client
    if redis_client:
        await redis_client.close()
        logger.info("Redis connection closed")


def get_local_rate_limit(client_id: str) -> tuple[bool, int]:
    """
    Local in-memory rate limiter using sliding window.
    Only works for a single instance - won't share state across instances.
    """
    now = time.time()
    window_start = now - WINDOW_SIZE_SECONDS

    if client_id not in local_rate_limits:
        local_rate_limits[client_id] = {"requests": []}

    # Remove expired entries
    local_rate_limits[client_id]["requests"] = [
        ts for ts in local_rate_limits[client_id]["requests"]
        if ts > window_start
    ]

    current_count = len(local_rate_limits[client_id]["requests"])

    if current_count < RATE_LIMIT:
        local_rate_limits[client_id]["requests"].append(now)
        return True, RATE_LIMIT - current_count - 1
    else:
        return False, 0


async def check_rate_limit_redis_naive(client_id: str) -> tuple[bool, int, Optional[float]]:
    """
    Redis-based rate limiter WITHOUT atomic operations.
    This demonstrates the race condition problem.

    Steps (non-atomic):
    1. Get current count
    2. Check if under limit
    3. Increment count
    4. Add timestamp

    Race condition: Between steps 1 and 3, another request could also pass
    the check, leading to more requests than the limit allows.
    """
    global redis_client

    now = time.time()
    window_start = now - WINDOW_SIZE_SECONDS
    key = f"ratelimit:naive:{client_id}"

    try:
        # Step 1: Remove expired entries
        await redis_client.zremrangebyscore(key, "-inf", window_start)
        REDIS_OPERATIONS.labels(instance=INSTANCE_ID, operation="zremrangebyscore", status="success").inc()

        # Introduce artificial delay to increase race condition probability
        await asyncio.sleep(0.001)  # 1ms delay

        # Step 2: Count current entries (non-atomic read)
        current_count = await redis_client.zcard(key)
        REDIS_OPERATIONS.labels(instance=INSTANCE_ID, operation="zcard", status="success").inc()

        # Another artificial delay
        await asyncio.sleep(0.001)  # 1ms delay

        # Step 3: Check and add (non-atomic)
        if current_count < RATE_LIMIT:
            # Add new request
            request_id = f"{INSTANCE_ID}:{now}"
            await redis_client.zadd(key, {request_id: now})
            await redis_client.expire(key, WINDOW_SIZE_SECONDS + 1)
            REDIS_OPERATIONS.labels(instance=INSTANCE_ID, operation="zadd", status="success").inc()

            # Verify we didn't exceed limit (race detection)
            final_count = await redis_client.zcard(key)
            if final_count > RATE_LIMIT:
                RACE_CONDITIONS_DETECTED.labels(instance=INSTANCE_ID).inc()
                logger.warning(f"Race condition detected! Count {final_count} > limit {RATE_LIMIT}")

            return True, RATE_LIMIT - current_count - 1, None
        else:
            # Get oldest entry for retry calculation
            oldest = await redis_client.zrange(key, 0, 0, withscores=True)
            retry_after = None
            if oldest:
                oldest_ts = oldest[0][1]
                retry_after = (oldest_ts + WINDOW_SIZE_SECONDS) - now

            return False, 0, retry_after

    except Exception as e:
        logger.error(f"Redis error: {e}")
        REDIS_OPERATIONS.labels(instance=INSTANCE_ID, operation="error", status="error").inc()
        # Fail open - allow request if Redis is unavailable
        return True, RATE_LIMIT, None


async def check_rate_limit_redis_atomic(client_id: str) -> tuple[bool, int, Optional[float]]:
    """
    Redis-based rate limiter with atomic Lua script.
    This prevents race conditions by executing all operations atomically.
    """
    global redis_client, lua_script_sha

    now = time.time()
    key = f"ratelimit:atomic:{client_id}"
    request_id = f"{INSTANCE_ID}:{now}"

    try:
        # Execute atomic Lua script
        result = await redis_client.evalsha(
            lua_script_sha,
            1,  # number of keys
            key,  # KEYS[1]
            str(now),  # ARGV[1] - current timestamp
            str(WINDOW_SIZE_SECONDS),  # ARGV[2] - window size
            str(RATE_LIMIT),  # ARGV[3] - limit
            request_id  # ARGV[4] - unique request ID
        )
        REDIS_OPERATIONS.labels(instance=INSTANCE_ID, operation="evalsha", status="success").inc()

        allowed = bool(result[0])
        current_count = int(result[1])
        remaining = int(result[2])

        if allowed:
            return True, remaining, None
        else:
            # Calculate retry time from oldest entry
            oldest_ts = float(result[3]) if len(result) > 3 else now
            retry_after = (oldest_ts + WINDOW_SIZE_SECONDS) - now
            return False, 0, max(0, retry_after)

    except redis.exceptions.NoScriptError:
        # Script was flushed, reload it
        lua_script_sha = await redis_client.script_load(SLIDING_WINDOW_LUA_SCRIPT)
        return await check_rate_limit_redis_atomic(client_id)
    except Exception as e:
        logger.error(f"Redis error: {e}")
        REDIS_OPERATIONS.labels(instance=INSTANCE_ID, operation="evalsha", status="error").inc()
        # Fail open
        return True, RATE_LIMIT, None


async def check_global_rate_limit(use_lua: bool = True) -> tuple[bool, int, Optional[float]]:
    """
    Global rate limit across all clients.
    Useful for protecting against total system overload.
    """
    global_client_id = "__global__"

    if use_lua:
        return await check_rate_limit_redis_atomic(global_client_id)
    else:
        return await check_rate_limit_redis_naive(global_client_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} ({INSTANCE_ID}) starting up")
    logger.info(f"Rate limit: {RATE_LIMIT} requests per {WINDOW_SIZE_SECONDS} seconds")
    logger.info(f"USE_REDIS: {USE_REDIS}, USE_LUA_SCRIPT: {USE_LUA_SCRIPT}")

    if USE_REDIS:
        await init_redis()

    yield

    if USE_REDIS:
        await close_redis()

    logger.info(f"{SERVICE_NAME} ({INSTANCE_ID}) shutting down")


app = FastAPI(title=f"{SERVICE_NAME} - {INSTANCE_ID}", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    redis_status = "not_configured"
    if USE_REDIS and redis_client:
        try:
            await redis_client.ping()
            redis_status = "connected"
        except Exception:
            redis_status = "disconnected"

    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "instance": INSTANCE_ID,
        "redis": redis_status
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/request")
async def api_request(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key")
):
    """
    Rate-limited API endpoint.

    The client is identified by:
    1. X-API-Key header (if present)
    2. X-Real-IP header (from nginx)
    3. Client IP address

    Returns 200 if allowed, 429 if rate limited.
    """
    global USE_REDIS, USE_LUA_SCRIPT

    # Determine client identifier
    client_id = x_api_key or request.headers.get("X-Real-IP") or request.client.host or "unknown"

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    current_span.set_attribute("rate_limit.client_id", client_id)
    current_span.set_attribute("rate_limit.instance", INSTANCE_ID)

    # Determine mode
    if USE_REDIS:
        mode = "redis_atomic" if USE_LUA_SCRIPT else "redis_naive"
    else:
        mode = "local"

    # Check rate limit
    start_time = time.time()

    if not USE_REDIS:
        allowed, remaining = get_local_rate_limit(client_id)
        retry_after = WINDOW_SIZE_SECONDS if not allowed else None
    elif USE_LUA_SCRIPT:
        allowed, remaining, retry_after = await check_rate_limit_redis_atomic(client_id)
    else:
        allowed, remaining, retry_after = await check_rate_limit_redis_naive(client_id)

    check_duration = time.time() - start_time
    RATE_LIMIT_CHECK_DURATION.labels(instance=INSTANCE_ID, mode=mode).observe(check_duration)

    # Record metrics
    if allowed:
        RATE_LIMIT_ALLOWED.labels(instance=INSTANCE_ID, client=client_id[:20], mode=mode).inc()
    else:
        RATE_LIMIT_REJECTED.labels(instance=INSTANCE_ID, client=client_id[:20], mode=mode).inc()

    RATE_LIMIT_REMAINING.labels(instance=INSTANCE_ID, client=client_id[:20]).set(remaining)

    current_span.set_attribute("rate_limit.allowed", allowed)
    current_span.set_attribute("rate_limit.remaining", remaining)
    current_span.set_attribute("rate_limit.mode", mode)

    # Build response headers
    headers = {
        "X-RateLimit-Limit": str(RATE_LIMIT),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Window": str(WINDOW_SIZE_SECONDS),
        "X-RateLimit-Mode": mode,
        "X-Served-By": INSTANCE_ID,
    }

    if allowed:
        return JSONResponse(
            status_code=200,
            headers=headers,
            content={
                "status": "ok",
                "message": "Request processed successfully",
                "instance": INSTANCE_ID,
                "mode": mode,
                "remaining": remaining,
                "trace_id": trace_id
            }
        )
    else:
        headers["Retry-After"] = str(int(retry_after) + 1) if retry_after else str(WINDOW_SIZE_SECONDS)
        logger.info(f"Rate limited: client={client_id}, instance={INSTANCE_ID}, mode={mode}")
        return JSONResponse(
            status_code=429,
            headers=headers,
            content={
                "status": "rate_limited",
                "message": "Too Many Requests",
                "instance": INSTANCE_ID,
                "mode": mode,
                "retry_after": retry_after,
                "trace_id": trace_id
            }
        )


@app.get("/api/data")
async def get_data(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key")
):
    """
    A sample protected endpoint that uses rate limiting.
    Simulates returning some data after rate limit check passes.
    """
    # First check rate limit via the main endpoint logic
    client_id = x_api_key or request.headers.get("X-Real-IP") or request.client.host or "unknown"

    if USE_REDIS and USE_LUA_SCRIPT:
        allowed, remaining, retry_after = await check_rate_limit_redis_atomic(client_id)
    elif USE_REDIS:
        allowed, remaining, retry_after = await check_rate_limit_redis_naive(client_id)
    else:
        allowed, remaining = get_local_rate_limit(client_id)
        retry_after = WINDOW_SIZE_SECONDS if not allowed else None

    if not allowed:
        return JSONResponse(
            status_code=429,
            headers={
                "Retry-After": str(int(retry_after) + 1) if retry_after else str(WINDOW_SIZE_SECONDS),
                "X-Served-By": INSTANCE_ID
            },
            content={"status": "rate_limited", "message": "Too Many Requests"}
        )

    # Simulate some data processing
    await asyncio.sleep(0.01)  # 10ms simulated work

    return {
        "status": "ok",
        "data": {
            "items": [
                {"id": 1, "name": "Item 1"},
                {"id": 2, "name": "Item 2"},
                {"id": 3, "name": "Item 3"}
            ],
            "total": 3
        },
        "served_by": INSTANCE_ID,
        "remaining_requests": remaining
    }


@app.post("/admin/config")
async def set_config(config: RateLimitConfig):
    """Update rate limiter configuration at runtime."""
    global USE_REDIS, USE_LUA_SCRIPT

    old_config = {"use_redis": USE_REDIS, "use_lua_script": USE_LUA_SCRIPT}

    USE_REDIS = config.use_redis
    USE_LUA_SCRIPT = config.use_lua_script

    # Initialize Redis if switching to Redis mode
    if USE_REDIS and not redis_client:
        await init_redis()

    logger.info(f"Config updated: {old_config} -> {config.dict()}")

    return {
        "previous": old_config,
        "current": config.dict(),
        "instance": INSTANCE_ID
    }


@app.get("/admin/config")
async def get_config():
    """Get current rate limiter configuration."""
    return RateLimitStats(
        instance=INSTANCE_ID,
        mode="redis_atomic" if USE_REDIS and USE_LUA_SCRIPT else ("redis_naive" if USE_REDIS else "local"),
        use_redis=USE_REDIS,
        use_lua_script=USE_LUA_SCRIPT,
        rate_limit=RATE_LIMIT,
        window_size_seconds=WINDOW_SIZE_SECONDS
    )


@app.get("/admin/stats")
async def get_stats():
    """Get rate limiter statistics from Redis."""
    if not USE_REDIS or not redis_client:
        return {
            "instance": INSTANCE_ID,
            "mode": "local",
            "message": "Redis not enabled, local stats only",
            "local_clients": len(local_rate_limits)
        }

    try:
        # Get all rate limit keys
        atomic_keys = await redis_client.keys("ratelimit:atomic:*")
        naive_keys = await redis_client.keys("ratelimit:naive:*")

        stats = {
            "instance": INSTANCE_ID,
            "mode": "redis_atomic" if USE_LUA_SCRIPT else "redis_naive",
            "atomic_clients": len(atomic_keys),
            "naive_clients": len(naive_keys),
            "rate_limit": RATE_LIMIT,
            "window_size_seconds": WINDOW_SIZE_SECONDS
        }

        # Get counts for first few clients
        client_stats = []
        keys_to_check = (atomic_keys if USE_LUA_SCRIPT else naive_keys)[:5]

        for key in keys_to_check:
            count = await redis_client.zcard(key)
            client_id = key.split(":")[-1]
            client_stats.append({
                "client": client_id,
                "current_count": count,
                "remaining": max(0, RATE_LIMIT - count)
            })

        stats["sample_clients"] = client_stats

        return stats

    except Exception as e:
        return {"error": str(e), "instance": INSTANCE_ID}


@app.post("/admin/reset")
async def reset_rate_limits():
    """Reset all rate limits (for testing)."""
    global local_rate_limits

    local_rate_limits = {}

    if USE_REDIS and redis_client:
        try:
            # Delete all rate limit keys
            atomic_keys = await redis_client.keys("ratelimit:atomic:*")
            naive_keys = await redis_client.keys("ratelimit:naive:*")

            all_keys = atomic_keys + naive_keys
            if all_keys:
                await redis_client.delete(*all_keys)

            logger.info(f"Reset {len(all_keys)} rate limit keys")
            return {
                "status": "ok",
                "message": f"Reset {len(all_keys)} rate limit keys",
                "instance": INSTANCE_ID
            }
        except Exception as e:
            return {"error": str(e), "instance": INSTANCE_ID}

    return {"status": "ok", "message": "Local rate limits reset", "instance": INSTANCE_ID}


@app.get("/admin/redis-info")
async def redis_info():
    """Get Redis server information."""
    if not redis_client:
        return {"error": "Redis not connected"}

    try:
        info = await redis_client.info()
        return {
            "connected_clients": info.get("connected_clients"),
            "used_memory_human": info.get("used_memory_human"),
            "total_commands_processed": info.get("total_commands_processed"),
            "keyspace": info.get("db0"),
            "redis_version": info.get("redis_version")
        }
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
