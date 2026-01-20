"""
URL Shortener Service - Demonstrates key generation strategies, caching, and tracing.
"""
import asyncio
import hashlib
import logging
import os
import random
import string
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel, HttpUrl

# ===========================================
# Configuration
# ===========================================

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "url-shortener")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://shortener:shortener@postgres:5432/urlshortener")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
DEFAULT_STRATEGY = os.getenv("DEFAULT_STRATEGY", "counter")
SHORT_CODE_LENGTH = 7
CACHE_TTL_SECONDS = 3600  # 1 hour

# Base62 alphabet for encoding
BASE62_ALPHABET = string.ascii_letters + string.digits

# ===========================================
# Logging
# ===========================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(SERVICE_NAME)

# ===========================================
# OpenTelemetry Setup
# ===========================================

resource = Resource.create({"service.name": SERVICE_NAME})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# ===========================================
# Prometheus Metrics
# ===========================================

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["service", "method", "endpoint"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
SHORTEN_COUNT = Counter(
    "url_shorten_total",
    "Total URLs shortened",
    ["strategy"]
)
REDIRECT_COUNT = Counter(
    "url_redirect_total",
    "Total redirects",
    ["cache_hit"]
)
CACHE_HIT_RATE = Gauge(
    "cache_hit_rate",
    "Cache hit rate percentage"
)
COLLISION_COUNT = Counter(
    "url_collision_total",
    "Total collisions encountered",
    ["strategy"]
)

# ===========================================
# Pydantic Models
# ===========================================

class ShortenRequest(BaseModel):
    url: HttpUrl

class ShortenResponse(BaseModel):
    short_code: str
    short_url: str
    original_url: str
    strategy: str

class StrategyRequest(BaseModel):
    strategy: str  # "counter", "hash", or "random"

class CollisionModeRequest(BaseModel):
    enabled: bool

class UrlStats(BaseModel):
    short_code: str
    original_url: str
    created_at: datetime
    access_count: int
    last_accessed: Optional[datetime]

class CacheStats(BaseModel):
    hits: int
    misses: int
    hit_rate: float
    cached_urls: int

# ===========================================
# Global State
# ===========================================

class AppState:
    db_pool: Optional[asyncpg.Pool] = None
    redis_client: Optional[redis.Redis] = None
    current_strategy: str = DEFAULT_STRATEGY
    collision_mode: bool = False
    cache_hits: int = 0
    cache_misses: int = 0

state = AppState()

# ===========================================
# Key Generation Strategies
# ===========================================

def base62_encode(num: int) -> str:
    """Encode a number to Base62 string."""
    if num == 0:
        return BASE62_ALPHABET[0]

    result = []
    while num:
        result.append(BASE62_ALPHABET[num % 62])
        num //= 62
    return ''.join(reversed(result))

def generate_counter_key(counter_value: int) -> str:
    """Generate key from auto-increment counter using Base62 encoding."""
    return base62_encode(counter_value)

def generate_hash_key(url: str) -> str:
    """Generate key from MD5 hash of URL (first 7 chars)."""
    hash_bytes = hashlib.md5(url.encode()).hexdigest()
    return hash_bytes[:SHORT_CODE_LENGTH]

def generate_random_key() -> str:
    """Generate random Base62 key."""
    return ''.join(random.choices(BASE62_ALPHABET, k=SHORT_CODE_LENGTH))

async def generate_short_code(url: str, strategy: str) -> tuple[str, int]:
    """
    Generate a short code using the specified strategy.
    Returns (short_code, collision_count).
    """
    with tracer.start_as_current_span("generate_short_code") as span:
        span.set_attribute("strategy", strategy)
        collision_count = 0

        if strategy == "counter":
            # Get next counter value from database sequence
            async with state.db_pool.acquire() as conn:
                row = await conn.fetchrow("SELECT nextval('url_counter_seq')")
                counter = row[0]
                short_code = generate_counter_key(counter)
                span.set_attribute("counter_value", counter)

        elif strategy == "hash":
            base_code = generate_hash_key(url)
            short_code = base_code

            # Check for collisions and handle them
            if state.collision_mode:
                async with state.db_pool.acquire() as conn:
                    while True:
                        existing = await conn.fetchrow(
                            "SELECT original_url FROM urls WHERE short_code = $1",
                            short_code
                        )
                        if existing is None:
                            break
                        if existing['original_url'] == url:
                            # Same URL, return existing code
                            break
                        # Collision with different URL
                        collision_count += 1
                        COLLISION_COUNT.labels(strategy=strategy).inc()
                        span.add_event("collision_detected", {"attempt": collision_count})
                        # Add suffix to resolve collision
                        short_code = f"{base_code[:5]}{random.choices(BASE62_ALPHABET, k=2)[0]}{random.choices(BASE62_ALPHABET, k=1)[0]}"
                        if collision_count > 10:
                            raise HTTPException(status_code=500, detail="Too many collisions")

            span.set_attribute("hash_prefix", base_code[:4])

        elif strategy == "random":
            # Generate random codes until we find a unique one
            async with state.db_pool.acquire() as conn:
                while True:
                    short_code = generate_random_key()
                    existing = await conn.fetchrow(
                        "SELECT 1 FROM urls WHERE short_code = $1",
                        short_code
                    )
                    if existing is None:
                        break
                    collision_count += 1
                    COLLISION_COUNT.labels(strategy=strategy).inc()
                    span.add_event("collision_detected", {"attempt": collision_count})
                    if collision_count > 10:
                        raise HTTPException(status_code=500, detail="Too many collisions")
        else:
            raise HTTPException(status_code=400, detail=f"Unknown strategy: {strategy}")

        span.set_attribute("short_code", short_code)
        span.set_attribute("collision_count", collision_count)
        return short_code, collision_count

# ===========================================
# Database Operations
# ===========================================

async def save_url(short_code: str, original_url: str, strategy: str) -> bool:
    """Save URL mapping to database. Returns True if new, False if already exists."""
    with tracer.start_as_current_span("db.save_url") as span:
        span.set_attribute("db.system", "postgresql")
        span.set_attribute("db.operation", "INSERT")

        async with state.db_pool.acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO urls (short_code, original_url, strategy, created_at)
                    VALUES ($1, $2, $3, NOW())
                    ON CONFLICT (short_code) DO NOTHING
                    """,
                    short_code, original_url, strategy
                )
                return True
            except Exception as e:
                span.record_exception(e)
                logger.error(f"Database error: {e}")
                raise HTTPException(status_code=500, detail="Database error")

async def get_url_from_db(short_code: str) -> Optional[str]:
    """Get original URL from database."""
    with tracer.start_as_current_span("db.get_url") as span:
        span.set_attribute("db.system", "postgresql")
        span.set_attribute("db.operation", "SELECT")
        span.set_attribute("short_code", short_code)

        async with state.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT original_url FROM urls WHERE short_code = $1",
                short_code
            )
            if row:
                # Update access count and last accessed
                await conn.execute(
                    """
                    UPDATE urls
                    SET access_count = access_count + 1, last_accessed = NOW()
                    WHERE short_code = $1
                    """,
                    short_code
                )
                return row['original_url']
            return None

# ===========================================
# Cache Operations
# ===========================================

async def get_url_from_cache(short_code: str) -> Optional[str]:
    """Get URL from Redis cache."""
    with tracer.start_as_current_span("cache.get") as span:
        span.set_attribute("cache.system", "redis")
        span.set_attribute("short_code", short_code)

        try:
            url = await state.redis_client.get(f"url:{short_code}")
            if url:
                span.set_attribute("cache.hit", True)
                return url.decode('utf-8')
            span.set_attribute("cache.hit", False)
            return None
        except Exception as e:
            span.record_exception(e)
            logger.warning(f"Redis error: {e}")
            return None

async def cache_url(short_code: str, original_url: str):
    """Cache URL in Redis."""
    with tracer.start_as_current_span("cache.set") as span:
        span.set_attribute("cache.system", "redis")
        span.set_attribute("short_code", short_code)

        try:
            await state.redis_client.setex(
                f"url:{short_code}",
                CACHE_TTL_SECONDS,
                original_url
            )
        except Exception as e:
            span.record_exception(e)
            logger.warning(f"Redis cache error: {e}")

async def delete_from_cache(short_code: str):
    """Delete URL from cache."""
    try:
        await state.redis_client.delete(f"url:{short_code}")
    except Exception as e:
        logger.warning(f"Redis delete error: {e}")

# ===========================================
# Application Lifecycle
# ===========================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")

    # Initialize database connection pool
    state.db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=5,
        max_size=20
    )
    logger.info("Database pool created")

    # Initialize Redis connection
    state.redis_client = redis.from_url(REDIS_URL)
    await state.redis_client.ping()
    logger.info("Redis connected")

    yield

    # Cleanup
    await state.db_pool.close()
    await state.redis_client.close()
    logger.info(f"{SERVICE_NAME} shutting down")

# ===========================================
# FastAPI App
# ===========================================

app = FastAPI(
    title="URL Shortener",
    description="A URL shortening service with multiple key generation strategies",
    version="1.0.0",
    lifespan=lifespan
)

FastAPIInstrumentor.instrument_app(app)

# ===========================================
# API Endpoints
# ===========================================

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": SERVICE_NAME}

@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.post("/shorten", response_model=ShortenResponse)
async def shorten_url(request: ShortenRequest):
    """Create a short URL from a long URL."""
    start_time = time.time()

    with tracer.start_as_current_span("shorten_url") as span:
        original_url = str(request.url)
        strategy = state.current_strategy

        span.set_attribute("original_url_length", len(original_url))
        span.set_attribute("strategy", strategy)

        # Generate short code
        short_code, collisions = await generate_short_code(original_url, strategy)

        # Save to database
        await save_url(short_code, original_url, strategy)

        # Cache the URL
        await cache_url(short_code, original_url)

        # Record metrics
        SHORTEN_COUNT.labels(strategy=strategy).inc()
        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="POST",
            endpoint="/shorten",
            status="200"
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME,
            method="POST",
            endpoint="/shorten"
        ).observe(time.time() - start_time)

        logger.info(f"Shortened URL: {short_code} (strategy={strategy}, collisions={collisions})")

        return ShortenResponse(
            short_code=short_code,
            short_url=f"{BASE_URL}/{short_code}",
            original_url=original_url,
            strategy=strategy
        )

@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "service": SERVICE_NAME,
        "version": "1.0.0",
        "endpoints": {
            "shorten": "POST /shorten",
            "redirect": "GET /{short_code}",
            "stats": "GET /stats/{short_code}",
            "health": "GET /health",
            "docs": "GET /docs"
        }
    }

@app.get("/{short_code}")
async def redirect_to_url(short_code: str):
    """Redirect to the original URL."""
    # Skip API paths that might accidentally match
    if short_code in ["health", "metrics", "shorten", "admin", "stats", "docs", "openapi.json", "favicon.ico"]:
        raise HTTPException(status_code=404, detail="Not found")

    start_time = time.time()

    with tracer.start_as_current_span("redirect") as span:
        span.set_attribute("short_code", short_code)

        # Try cache first
        original_url = await get_url_from_cache(short_code)
        cache_hit = original_url is not None

        if cache_hit:
            state.cache_hits += 1
            span.set_attribute("cache_hit", True)
        else:
            state.cache_misses += 1
            span.set_attribute("cache_hit", False)

            # Get from database
            original_url = await get_url_from_db(short_code)

            if original_url:
                # Cache for future requests
                await cache_url(short_code, original_url)

        if not original_url:
            REQUEST_COUNT.labels(
                service=SERVICE_NAME,
                method="GET",
                endpoint="/redirect",
                status="404"
            ).inc()
            raise HTTPException(status_code=404, detail="Short URL not found")

        # Update cache hit rate metric
        total = state.cache_hits + state.cache_misses
        if total > 0:
            CACHE_HIT_RATE.set(state.cache_hits / total * 100)

        # Record metrics
        REDIRECT_COUNT.labels(cache_hit=str(cache_hit).lower()).inc()
        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/redirect",
            status="302"
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME,
            method="GET",
            endpoint="/redirect"
        ).observe(time.time() - start_time)

        span.set_attribute("original_url", original_url)
        logger.info(f"Redirecting {short_code} -> {original_url} (cache_hit={cache_hit})")

        return RedirectResponse(url=original_url, status_code=302)

@app.get("/stats/{short_code}", response_model=UrlStats)
async def get_url_stats(short_code: str):
    """Get statistics for a short URL."""
    with tracer.start_as_current_span("get_stats") as span:
        span.set_attribute("short_code", short_code)

        async with state.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT short_code, original_url, created_at, access_count, last_accessed
                FROM urls WHERE short_code = $1
                """,
                short_code
            )

            if not row:
                raise HTTPException(status_code=404, detail="Short URL not found")

            return UrlStats(
                short_code=row['short_code'],
                original_url=row['original_url'],
                created_at=row['created_at'],
                access_count=row['access_count'],
                last_accessed=row['last_accessed']
            )

# ===========================================
# Admin Endpoints
# ===========================================

@app.get("/admin/strategy")
async def get_strategy():
    """Get current key generation strategy."""
    return {"strategy": state.current_strategy}

@app.post("/admin/strategy")
async def set_strategy(request: StrategyRequest):
    """Change key generation strategy."""
    if request.strategy not in ["counter", "hash", "random"]:
        raise HTTPException(
            status_code=400,
            detail="Strategy must be 'counter', 'hash', or 'random'"
        )

    old_strategy = state.current_strategy
    state.current_strategy = request.strategy
    logger.info(f"Strategy changed: {old_strategy} -> {request.strategy}")

    return {"strategy": state.current_strategy, "previous": old_strategy}

@app.get("/admin/collision-mode")
async def get_collision_mode():
    """Get collision mode status."""
    return {"enabled": state.collision_mode}

@app.post("/admin/collision-mode")
async def set_collision_mode(request: CollisionModeRequest):
    """Enable/disable collision simulation mode."""
    state.collision_mode = request.enabled
    return {"enabled": state.collision_mode}

@app.get("/admin/cache-stats", response_model=CacheStats)
async def get_cache_stats():
    """Get cache statistics."""
    total = state.cache_hits + state.cache_misses
    hit_rate = (state.cache_hits / total * 100) if total > 0 else 0.0

    # Count cached URLs
    cached_count = 0
    try:
        keys = await state.redis_client.keys("url:*")
        cached_count = len(keys)
    except Exception:
        pass

    return CacheStats(
        hits=state.cache_hits,
        misses=state.cache_misses,
        hit_rate=round(hit_rate, 2),
        cached_urls=cached_count
    )

@app.delete("/admin/cache/{short_code}")
async def clear_cache_entry(short_code: str):
    """Clear a specific cache entry."""
    await delete_from_cache(short_code)
    return {"deleted": short_code}

@app.get("/admin/recent")
async def get_recent_urls(limit: int = 10):
    """Get recently created URLs."""
    async with state.db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT short_code, original_url, strategy, created_at, access_count
            FROM urls ORDER BY created_at DESC LIMIT $1
            """,
            limit
        )
        return [dict(row) for row in rows]

@app.get("/admin/count")
async def get_url_count():
    """Get total URL count."""
    async with state.db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT COUNT(*) as count FROM urls")
        return {"count": row['count']}

# ===========================================
# Main
# ===========================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
