"""
Username Registration Service with Bloom Filter optimization.

Demonstrates how bloom filters can reduce database queries for checking
if a username is already taken. The bloom filter provides fast "definitely not taken"
responses, only querying the database when the filter indicates "probably taken".
"""
import asyncio
import hashlib
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import asyncpg
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "username-service")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab22-otel-collector:4317")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://lab22:lab22pass@lab22-postgres:5432/usernames")

# Bloom filter configuration (can be changed at runtime)
BLOOM_FILTER_ENABLED = os.getenv("BLOOM_FILTER_ENABLED", "false").lower() == "true"
BLOOM_FILTER_SIZE = int(os.getenv("BLOOM_FILTER_SIZE", "10000"))
BLOOM_FILTER_HASH_COUNT = int(os.getenv("BLOOM_FILTER_HASH_COUNT", "7"))

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

# Prometheus metrics
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

# Bloom filter specific metrics
BLOOM_CHECKS = Counter(
    "bloom_checks_total",
    "Total bloom filter checks",
    ["result"]  # "probably_exists", "definitely_not_exists"
)
BLOOM_FALSE_POSITIVES = Counter(
    "bloom_false_positives_total",
    "Bloom filter false positives (filter said exists, but DB said no)"
)
DB_QUERIES_TOTAL = Counter(
    "db_queries_total",
    "Total database queries for username checks",
    ["source"]  # "direct" (no bloom), "bloom_positive" (bloom said maybe exists)
)
DB_QUERIES_SAVED = Counter(
    "db_queries_saved_total",
    "Database queries saved by bloom filter"
)
BLOOM_FILTER_SIZE_GAUGE = Gauge(
    "bloom_filter_size_bits",
    "Size of bloom filter in bits"
)
BLOOM_FILTER_ITEMS = Gauge(
    "bloom_filter_items_count",
    "Number of items in bloom filter"
)
BLOOM_FILTER_FILL_RATIO = Gauge(
    "bloom_filter_fill_ratio",
    "Ratio of set bits in bloom filter"
)
USERNAMES_REGISTERED = Counter(
    "usernames_registered_total",
    "Total usernames registered"
)


class BloomFilter:
    """
    Simple bloom filter implementation for educational purposes.

    A bloom filter is a space-efficient probabilistic data structure that
    tests whether an element is a member of a set. False positives are
    possible, but false negatives are not.
    """

    def __init__(self, size: int, hash_count: int):
        self.size = size
        self.hash_count = hash_count
        self.bit_array = [False] * size
        self.items_count = 0

    def _get_hash_values(self, item: str) -> list[int]:
        """Generate multiple hash values for an item using double hashing."""
        # Use SHA-256 for better distribution
        h1 = int(hashlib.md5(item.encode()).hexdigest(), 16)
        h2 = int(hashlib.sha256(item.encode()).hexdigest(), 16)

        # Double hashing: h(i) = h1 + i * h2
        return [(h1 + i * h2) % self.size for i in range(self.hash_count)]

    def add(self, item: str) -> None:
        """Add an item to the bloom filter."""
        for index in self._get_hash_values(item):
            self.bit_array[index] = True
        self.items_count += 1

    def probably_contains(self, item: str) -> bool:
        """
        Check if an item is probably in the set.

        Returns:
            True if the item is PROBABLY in the set (might be false positive)
            False if the item is DEFINITELY NOT in the set
        """
        return all(self.bit_array[index] for index in self._get_hash_values(item))

    def get_fill_ratio(self) -> float:
        """Calculate the ratio of set bits."""
        if self.size == 0:
            return 0.0
        return sum(self.bit_array) / self.size

    def reset(self) -> None:
        """Clear the bloom filter."""
        self.bit_array = [False] * self.size
        self.items_count = 0

    def get_stats(self) -> dict:
        """Get statistics about the bloom filter."""
        set_bits = sum(self.bit_array)
        fill_ratio = self.get_fill_ratio()

        # Theoretical false positive probability
        # p = (1 - e^(-k*n/m))^k
        # where k = hash_count, n = items, m = size
        import math
        if self.items_count > 0 and self.size > 0:
            exponent = -self.hash_count * self.items_count / self.size
            fp_probability = (1 - math.exp(exponent)) ** self.hash_count
        else:
            fp_probability = 0.0

        return {
            "size_bits": self.size,
            "hash_count": self.hash_count,
            "items_count": self.items_count,
            "set_bits": set_bits,
            "fill_ratio": round(fill_ratio, 4),
            "theoretical_false_positive_rate": round(fp_probability, 6)
        }


# Global state
db_pool: Optional[asyncpg.Pool] = None
bloom_filter: Optional[BloomFilter] = None
bloom_enabled: bool = BLOOM_FILTER_ENABLED


class UsernameRequest(BaseModel):
    username: str


class BloomConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    size: Optional[int] = None
    hash_count: Optional[int] = None


async def init_database():
    """Initialize database and create tables."""
    global db_pool

    # Wait for database to be ready
    for attempt in range(30):
        try:
            db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
            break
        except Exception as e:
            logger.warning(f"Database connection attempt {attempt + 1} failed: {e}")
            await asyncio.sleep(1)
    else:
        raise RuntimeError("Could not connect to database")

    # Create table
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS usernames (
                id SERIAL PRIMARY KEY,
                username VARCHAR(255) UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_username ON usernames(username)")

    logger.info("Database initialized")


async def init_bloom_filter():
    """Initialize bloom filter and populate from database."""
    global bloom_filter

    bloom_filter = BloomFilter(BLOOM_FILTER_SIZE, BLOOM_FILTER_HASH_COUNT)

    # Populate from existing usernames
    if db_pool:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT username FROM usernames")
            for row in rows:
                bloom_filter.add(row['username'].lower())

    # Update metrics
    BLOOM_FILTER_SIZE_GAUGE.set(bloom_filter.size)
    BLOOM_FILTER_ITEMS.set(bloom_filter.items_count)
    BLOOM_FILTER_FILL_RATIO.set(bloom_filter.get_fill_ratio())

    logger.info(f"Bloom filter initialized with {bloom_filter.items_count} items")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    await init_database()
    await init_bloom_filter()
    yield
    logger.info(f"{SERVICE_NAME} shutting down")
    if db_pool:
        await db_pool.close()


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/check-username")
async def check_username(request: UsernameRequest):
    """
    Check if a username is available.

    With bloom filter disabled: Always queries database
    With bloom filter enabled: First checks bloom filter, only queries DB if filter says "maybe exists"
    """
    start_time = time.time()
    username = request.username.lower().strip()

    current_span = trace.get_current_span()
    current_span.set_attribute("username_length", len(username))
    current_span.set_attribute("bloom_filter_enabled", bloom_enabled)

    result = {
        "username": username,
        "bloom_filter_used": bloom_enabled,
        "bloom_result": None,
        "db_queried": False,
        "available": None
    }

    if bloom_enabled and bloom_filter:
        # Check bloom filter first
        with tracer.start_as_current_span("bloom_filter_check") as span:
            probably_exists = bloom_filter.probably_contains(username)
            span.set_attribute("bloom_result", "probably_exists" if probably_exists else "definitely_not_exists")

            if probably_exists:
                BLOOM_CHECKS.labels(result="probably_exists").inc()
                result["bloom_result"] = "probably_exists"
            else:
                BLOOM_CHECKS.labels(result="definitely_not_exists").inc()
                result["bloom_result"] = "definitely_not_exists"
                # Bloom filter says definitely not in set - no DB query needed
                DB_QUERIES_SAVED.inc()
                result["available"] = True

                duration = time.time() - start_time
                REQUEST_LATENCY.labels(
                    service=SERVICE_NAME,
                    method="POST",
                    endpoint="/check-username"
                ).observe(duration)
                REQUEST_COUNT.labels(
                    service=SERVICE_NAME,
                    method="POST",
                    endpoint="/check-username",
                    status="200"
                ).inc()

                current_span.set_attribute("db_queried", False)
                current_span.set_attribute("available", True)

                return result

    # Query database
    with tracer.start_as_current_span("database_query") as span:
        result["db_queried"] = True

        if bloom_enabled:
            DB_QUERIES_TOTAL.labels(source="bloom_positive").inc()
        else:
            DB_QUERIES_TOTAL.labels(source="direct").inc()

        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM usernames WHERE username = $1",
                username
            )
            exists_in_db = row is not None
            result["available"] = not exists_in_db
            span.set_attribute("exists_in_db", exists_in_db)

        # Check for false positive
        if bloom_enabled and result["bloom_result"] == "probably_exists" and not exists_in_db:
            BLOOM_FALSE_POSITIVES.inc()
            current_span.set_attribute("bloom_false_positive", True)

    duration = time.time() - start_time
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="POST",
        endpoint="/check-username"
    ).observe(duration)
    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="POST",
        endpoint="/check-username",
        status="200"
    ).inc()

    current_span.set_attribute("db_queried", True)
    current_span.set_attribute("available", result["available"])

    return result


@app.post("/register-username")
async def register_username(request: UsernameRequest):
    """
    Register a new username.

    First checks availability, then registers if available.
    Also adds to bloom filter.
    """
    start_time = time.time()
    username = request.username.lower().strip()

    current_span = trace.get_current_span()
    current_span.set_attribute("username", username)

    # Validate username
    if len(username) < 3 or len(username) > 30:
        raise HTTPException(status_code=400, detail="Username must be 3-30 characters")
    if not username.isalnum():
        raise HTTPException(status_code=400, detail="Username must be alphanumeric")

    # Check availability first
    check_result = await check_username(UsernameRequest(username=username))

    if not check_result["available"]:
        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="POST",
            endpoint="/register-username",
            status="409"
        ).inc()
        raise HTTPException(status_code=409, detail="Username already taken")

    # Register in database
    with tracer.start_as_current_span("database_insert"):
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO usernames (username) VALUES ($1)",
                    username
                )
        except asyncpg.UniqueViolationError:
            # Race condition - username was taken between check and insert
            REQUEST_COUNT.labels(
                service=SERVICE_NAME,
                method="POST",
                endpoint="/register-username",
                status="409"
            ).inc()
            raise HTTPException(status_code=409, detail="Username already taken")

    # Add to bloom filter
    if bloom_filter:
        with tracer.start_as_current_span("bloom_filter_add"):
            bloom_filter.add(username)
            BLOOM_FILTER_ITEMS.set(bloom_filter.items_count)
            BLOOM_FILTER_FILL_RATIO.set(bloom_filter.get_fill_ratio())

    USERNAMES_REGISTERED.inc()

    duration = time.time() - start_time
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="POST",
        endpoint="/register-username"
    ).observe(duration)
    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="POST",
        endpoint="/register-username",
        status="201"
    ).inc()

    return {
        "username": username,
        "registered": True,
        "bloom_filter_updated": bloom_filter is not None
    }


@app.get("/admin/bloom-stats")
async def get_bloom_stats():
    """Get current bloom filter statistics."""
    if not bloom_filter:
        return {"error": "Bloom filter not initialized"}

    stats = bloom_filter.get_stats()
    stats["enabled"] = bloom_enabled
    return stats


@app.post("/admin/bloom-config")
async def configure_bloom_filter(config: BloomConfigRequest):
    """Configure bloom filter settings."""
    global bloom_enabled, bloom_filter

    result = {"changes": []}

    if config.enabled is not None:
        old_value = bloom_enabled
        bloom_enabled = config.enabled
        result["changes"].append(f"enabled: {old_value} -> {bloom_enabled}")
        logger.info(f"Bloom filter {'enabled' if bloom_enabled else 'disabled'}")

    if config.size is not None or config.hash_count is not None:
        new_size = config.size if config.size is not None else bloom_filter.size
        new_hash_count = config.hash_count if config.hash_count is not None else bloom_filter.hash_count

        # Recreate bloom filter with new parameters
        bloom_filter = BloomFilter(new_size, new_hash_count)

        # Repopulate from database
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT username FROM usernames")
            for row in rows:
                bloom_filter.add(row['username'].lower())

        BLOOM_FILTER_SIZE_GAUGE.set(bloom_filter.size)
        BLOOM_FILTER_ITEMS.set(bloom_filter.items_count)
        BLOOM_FILTER_FILL_RATIO.set(bloom_filter.get_fill_ratio())

        result["changes"].append(f"size: {new_size}, hash_count: {new_hash_count}")
        result["repopulated_items"] = bloom_filter.items_count
        logger.info(f"Bloom filter resized to {new_size} bits with {new_hash_count} hash functions")

    result["current_stats"] = bloom_filter.get_stats()
    result["current_stats"]["enabled"] = bloom_enabled

    return result


@app.post("/admin/bloom-reset")
async def reset_bloom_filter():
    """Reset bloom filter and repopulate from database."""
    global bloom_filter

    if not bloom_filter:
        return {"error": "Bloom filter not initialized"}

    bloom_filter.reset()

    # Repopulate from database
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT username FROM usernames")
        for row in rows:
            bloom_filter.add(row['username'].lower())

    BLOOM_FILTER_ITEMS.set(bloom_filter.items_count)
    BLOOM_FILTER_FILL_RATIO.set(bloom_filter.get_fill_ratio())

    return {
        "reset": True,
        "repopulated_items": bloom_filter.items_count,
        "stats": bloom_filter.get_stats()
    }


@app.delete("/admin/clear-all")
async def clear_all_data():
    """Clear all usernames and reset bloom filter. For testing only."""
    global bloom_filter

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM usernames")

    if bloom_filter:
        bloom_filter.reset()
        BLOOM_FILTER_ITEMS.set(0)
        BLOOM_FILTER_FILL_RATIO.set(0)

    return {"cleared": True}


@app.get("/admin/usernames")
async def list_usernames(limit: int = 100):
    """List registered usernames."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT username, created_at FROM usernames ORDER BY created_at DESC LIMIT $1",
            limit
        )
        return {
            "count": len(rows),
            "usernames": [{"username": r["username"], "created_at": str(r["created_at"])} for r in rows]
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
