"""
Visitor Counter Service - Demonstrates HyperLogLog vs Exact Counting.

This service counts unique page visitors using two methods:
1. Exact counting with Python sets (memory grows linearly)
2. HyperLogLog with Redis PFADD/PFCOUNT (~12KB constant memory)

The service exposes metrics to compare memory usage and accuracy.
"""
import asyncio
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Dict, Set

import redis.asyncio as redis
from fastapi import FastAPI, Query
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "visitor-counter")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab32-otel-collector:4317")
REDIS_HOST = os.getenv("REDIS_HOST", "lab32-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# Logging
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(SERVICE_NAME)

# OpenTelemetry setup
resource = Resource.create({"service.name": SERVICE_NAME})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# ===========================================
# In-Memory Exact Counter (for comparison)
# ===========================================

# Stores for exact counting (simulates what you'd do without HyperLogLog)
exact_visitors: Dict[str, Set[str]] = {}  # page_id -> set of visitor_ids
exact_visitors_by_window: Dict[str, Set[str]] = {}  # time_window -> set of visitor_ids

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

# Core metrics for the lab
UNIQUE_VISITORS_EXACT = Gauge(
    "unique_visitors_exact",
    "Unique visitors counted exactly (using sets)",
    ["page_id"]
)

UNIQUE_VISITORS_HLL = Gauge(
    "unique_visitors_hll",
    "Unique visitors counted using HyperLogLog",
    ["page_id"]
)

MEMORY_USAGE_EXACT = Gauge(
    "memory_usage_bytes_exact",
    "Memory used by exact counting (approximate)",
    ["page_id"]
)

MEMORY_USAGE_HLL = Gauge(
    "memory_usage_bytes_hll",
    "Memory used by HyperLogLog (constant ~12KB)",
    ["page_id"]
)

COUNTING_ERROR_RATE = Gauge(
    "counting_error_rate",
    "Error rate of HyperLogLog vs exact count",
    ["page_id"]
)

TOTAL_VISITORS_PROCESSED = Counter(
    "total_visitors_processed",
    "Total visitor events processed",
    ["method"]
)

# Redis connection
redis_client: redis.Redis = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for startup and shutdown."""
    global redis_client
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")

    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        decode_responses=True
    )

    # Test connection
    try:
        await redis_client.ping()
        logger.info("Redis connection successful")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}")
        raise

    yield

    logger.info(f"{SERVICE_NAME} shutting down")
    await redis_client.close()


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


def estimate_set_memory(s: Set[str]) -> int:
    """
    Estimate memory used by a Python set of strings.
    This is approximate: ~56 bytes per set entry + string size.
    """
    if not s:
        return 0
    # Base set overhead (~224 bytes) + entry overhead (~56 bytes each) + string content
    base_overhead = 224
    entry_overhead = 56
    avg_string_size = 50  # UUID is ~36 chars, estimate ~50 bytes with overhead
    return base_overhead + len(s) * (entry_overhead + avg_string_size)


async def get_hll_memory(key: str) -> int:
    """
    Get the memory used by a HyperLogLog in Redis.
    HyperLogLog uses ~12KB regardless of cardinality.
    """
    try:
        # Redis DEBUG OBJECT gives memory info, but MEMORY USAGE is cleaner
        memory = await redis_client.memory_usage(f"hll:{key}")
        return memory if memory else 0
    except Exception:
        # If MEMORY USAGE fails, HLL uses ~12KB constant
        return 12288


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/visit")
async def record_visit(
    page_id: str = Query(default="homepage", description="Page being visited"),
    visitor_id: str = Query(default=None, description="Unique visitor ID (generated if not provided)")
):
    """
    Record a page visit using both exact counting and HyperLogLog.

    This endpoint demonstrates the difference between:
    - Exact counting (memory grows with unique visitors)
    - HyperLogLog (constant ~12KB memory, ~0.81% error rate)
    """
    start_time = time.time()

    with tracer.start_as_current_span("record_visit") as span:
        # Generate visitor ID if not provided
        if visitor_id is None:
            visitor_id = str(uuid.uuid4())

        span.set_attribute("page_id", page_id)
        span.set_attribute("visitor_id", visitor_id)

        # ===========================================
        # Method 1: Exact counting with Python set
        # ===========================================
        with tracer.start_as_current_span("exact_counting"):
            if page_id not in exact_visitors:
                exact_visitors[page_id] = set()

            exact_visitors[page_id].add(visitor_id)
            exact_count = len(exact_visitors[page_id])
            exact_memory = estimate_set_memory(exact_visitors[page_id])

            # Update metrics
            UNIQUE_VISITORS_EXACT.labels(page_id=page_id).set(exact_count)
            MEMORY_USAGE_EXACT.labels(page_id=page_id).set(exact_memory)
            TOTAL_VISITORS_PROCESSED.labels(method="exact").inc()

        # ===========================================
        # Method 2: HyperLogLog counting with Redis
        # ===========================================
        with tracer.start_as_current_span("hyperloglog_counting"):
            hll_key = f"hll:{page_id}"

            # PFADD adds the element to the HyperLogLog
            await redis_client.pfadd(hll_key, visitor_id)

            # PFCOUNT returns the approximate cardinality
            hll_count = await redis_client.pfcount(hll_key)

            # Get memory usage
            hll_memory = await get_hll_memory(page_id)

            # Update metrics
            UNIQUE_VISITORS_HLL.labels(page_id=page_id).set(hll_count)
            MEMORY_USAGE_HLL.labels(page_id=page_id).set(hll_memory)
            TOTAL_VISITORS_PROCESSED.labels(method="hll").inc()

        # Calculate error rate
        if exact_count > 0:
            error_rate = abs(hll_count - exact_count) / exact_count
            COUNTING_ERROR_RATE.labels(page_id=page_id).set(error_rate)
        else:
            error_rate = 0.0

        duration = time.time() - start_time

        # Record request metrics
        REQUEST_COUNT.labels(
            service=SERVICE_NAME,
            method="POST",
            endpoint="/visit",
            status="200"
        ).inc()
        REQUEST_LATENCY.labels(
            service=SERVICE_NAME,
            method="POST",
            endpoint="/visit"
        ).observe(duration)

        span.set_attribute("exact_count", exact_count)
        span.set_attribute("hll_count", hll_count)
        span.set_attribute("error_rate", error_rate)

        logger.info(
            f"Visit recorded: page={page_id}, visitor={visitor_id[:8]}..., "
            f"exact={exact_count}, hll={hll_count}, error={error_rate:.4f}"
        )

        return {
            "page_id": page_id,
            "visitor_id": visitor_id,
            "exact_count": exact_count,
            "hll_count": hll_count,
            "error_rate": round(error_rate, 6),
            "memory": {
                "exact_bytes": exact_memory,
                "hll_bytes": hll_memory,
                "ratio": round(exact_memory / hll_memory, 2) if hll_memory > 0 else 0
            }
        }


@app.get("/stats")
async def get_stats(page_id: str = Query(default="homepage")):
    """
    Get current statistics for a page.

    Shows the comparison between exact counting and HyperLogLog.
    """
    with tracer.start_as_current_span("get_stats") as span:
        span.set_attribute("page_id", page_id)

        # Exact count
        exact_count = len(exact_visitors.get(page_id, set()))
        exact_memory = estimate_set_memory(exact_visitors.get(page_id, set()))

        # HyperLogLog count
        hll_key = f"hll:{page_id}"
        hll_count = await redis_client.pfcount(hll_key)
        hll_memory = await get_hll_memory(page_id)

        # Calculate error
        error_rate = 0.0
        if exact_count > 0:
            error_rate = abs(hll_count - exact_count) / exact_count

        return {
            "page_id": page_id,
            "exact": {
                "count": exact_count,
                "memory_bytes": exact_memory,
                "memory_human": f"{exact_memory / 1024:.2f} KB"
            },
            "hyperloglog": {
                "count": hll_count,
                "memory_bytes": hll_memory,
                "memory_human": f"{hll_memory / 1024:.2f} KB"
            },
            "comparison": {
                "error_rate": round(error_rate, 6),
                "error_percentage": f"{error_rate * 100:.4f}%",
                "memory_savings_ratio": round(exact_memory / hll_memory, 2) if hll_memory > 0 else 0
            }
        }


@app.get("/stats/all")
async def get_all_stats():
    """Get statistics for all tracked pages."""
    with tracer.start_as_current_span("get_all_stats"):
        all_pages = list(exact_visitors.keys())

        # Also check Redis for any HLL keys we might have missed
        cursor = 0
        while True:
            cursor, keys = await redis_client.scan(cursor, match="hll:*", count=100)
            for key in keys:
                page_id = key.replace("hll:", "")
                if page_id not in all_pages:
                    all_pages.append(page_id)
            if cursor == 0:
                break

        stats = {}
        total_exact_memory = 0
        total_hll_memory = 0

        for page_id in all_pages:
            exact_count = len(exact_visitors.get(page_id, set()))
            exact_memory = estimate_set_memory(exact_visitors.get(page_id, set()))

            hll_count = await redis_client.pfcount(f"hll:{page_id}")
            hll_memory = await get_hll_memory(page_id)

            error_rate = 0.0
            if exact_count > 0:
                error_rate = abs(hll_count - exact_count) / exact_count

            stats[page_id] = {
                "exact_count": exact_count,
                "hll_count": hll_count,
                "error_rate": round(error_rate, 6),
                "exact_memory_bytes": exact_memory,
                "hll_memory_bytes": hll_memory
            }

            total_exact_memory += exact_memory
            total_hll_memory += hll_memory

        return {
            "pages": stats,
            "totals": {
                "exact_memory_bytes": total_exact_memory,
                "exact_memory_human": f"{total_exact_memory / 1024:.2f} KB",
                "hll_memory_bytes": total_hll_memory,
                "hll_memory_human": f"{total_hll_memory / 1024:.2f} KB",
                "memory_savings_ratio": round(total_exact_memory / total_hll_memory, 2) if total_hll_memory > 0 else 0
            }
        }


@app.post("/merge")
async def merge_hyperloglog(
    source_pages: str = Query(description="Comma-separated list of page IDs to merge"),
    target_key: str = Query(default="merged", description="Key for the merged HyperLogLog")
):
    """
    Merge multiple HyperLogLogs into one.

    This is a key feature of HyperLogLog - you can union multiple HLLs
    to count unique visitors across time windows or pages efficiently.

    For example: Merge hourly HLLs to get daily unique visitors without
    double-counting visitors who visited in multiple hours.
    """
    with tracer.start_as_current_span("merge_hyperloglog") as span:
        pages = [p.strip() for p in source_pages.split(",")]
        span.set_attribute("source_pages", pages)
        span.set_attribute("target_key", target_key)

        # Get counts before merge
        source_counts = {}
        for page_id in pages:
            hll_key = f"hll:{page_id}"
            count = await redis_client.pfcount(hll_key)
            source_counts[page_id] = count

        # Sum of individual counts (may have duplicates)
        sum_of_counts = sum(source_counts.values())

        # Merge using PFMERGE
        source_keys = [f"hll:{page_id}" for page_id in pages]
        target_hll_key = f"hll:merged:{target_key}"

        await redis_client.pfmerge(target_hll_key, *source_keys)

        # Get merged count (duplicates removed!)
        merged_count = await redis_client.pfcount(target_hll_key)

        # Calculate overlap
        overlap = sum_of_counts - merged_count
        overlap_percentage = (overlap / sum_of_counts * 100) if sum_of_counts > 0 else 0

        logger.info(
            f"Merged HLLs: sources={pages}, sum={sum_of_counts}, "
            f"merged={merged_count}, overlap={overlap}"
        )

        return {
            "source_pages": pages,
            "source_counts": source_counts,
            "sum_of_individual_counts": sum_of_counts,
            "merged_unique_count": merged_count,
            "overlap_detected": overlap,
            "overlap_percentage": f"{overlap_percentage:.2f}%",
            "target_key": target_key,
            "insight": (
                f"Without merging, you'd count {sum_of_counts} visitors. "
                f"HyperLogLog merge reveals only {merged_count} unique visitors "
                f"(saved from double-counting {overlap} visitors)."
            )
        }


@app.delete("/reset")
async def reset_counters(page_id: str = Query(default=None, description="Page to reset (all if not specified)")):
    """Reset counters for testing."""
    with tracer.start_as_current_span("reset_counters"):
        if page_id:
            # Reset specific page
            if page_id in exact_visitors:
                del exact_visitors[page_id]
            await redis_client.delete(f"hll:{page_id}")

            # Reset metrics
            UNIQUE_VISITORS_EXACT.labels(page_id=page_id).set(0)
            UNIQUE_VISITORS_HLL.labels(page_id=page_id).set(0)
            MEMORY_USAGE_EXACT.labels(page_id=page_id).set(0)
            MEMORY_USAGE_HLL.labels(page_id=page_id).set(0)
            COUNTING_ERROR_RATE.labels(page_id=page_id).set(0)

            logger.info(f"Reset counters for page: {page_id}")
            return {"status": "reset", "page_id": page_id}
        else:
            # Reset all
            exact_visitors.clear()

            # Delete all HLL keys
            cursor = 0
            deleted = 0
            while True:
                cursor, keys = await redis_client.scan(cursor, match="hll:*", count=100)
                if keys:
                    await redis_client.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break

            logger.info(f"Reset all counters, deleted {deleted} HLL keys")
            return {"status": "reset_all", "hll_keys_deleted": deleted}


@app.post("/simulate")
async def simulate_visitors(
    page_id: str = Query(default="homepage"),
    total_visitors: int = Query(default=10000, description="Total visitor events to simulate"),
    unique_visitors: int = Query(default=1000, description="Number of unique visitors"),
    batch_size: int = Query(default=100, description="Visitors per batch")
):
    """
    Simulate visitor traffic to demonstrate HyperLogLog at scale.

    This generates a controlled number of unique visitors with repeat visits
    to show how HyperLogLog handles large cardinalities.
    """
    with tracer.start_as_current_span("simulate_visitors") as span:
        span.set_attribute("total_visitors", total_visitors)
        span.set_attribute("unique_visitors", unique_visitors)
        span.set_attribute("page_id", page_id)

        # Generate pool of unique visitor IDs
        visitor_pool = [str(uuid.uuid4()) for _ in range(unique_visitors)]

        start_time = time.time()
        processed = 0

        # Process in batches
        for batch_start in range(0, total_visitors, batch_size):
            batch_end = min(batch_start + batch_size, total_visitors)

            for _ in range(batch_end - batch_start):
                # Pick a random visitor from the pool
                visitor_id = visitor_pool[processed % unique_visitors]

                # Record with exact counting
                if page_id not in exact_visitors:
                    exact_visitors[page_id] = set()
                exact_visitors[page_id].add(visitor_id)

                # Record with HyperLogLog
                await redis_client.pfadd(f"hll:{page_id}", visitor_id)

                processed += 1

            # Yield control periodically
            if batch_start % 1000 == 0:
                await asyncio.sleep(0)

        duration = time.time() - start_time

        # Get final stats
        exact_count = len(exact_visitors.get(page_id, set()))
        exact_memory = estimate_set_memory(exact_visitors.get(page_id, set()))
        hll_count = await redis_client.pfcount(f"hll:{page_id}")
        hll_memory = await get_hll_memory(page_id)

        error_rate = abs(hll_count - exact_count) / exact_count if exact_count > 0 else 0

        # Update metrics
        UNIQUE_VISITORS_EXACT.labels(page_id=page_id).set(exact_count)
        UNIQUE_VISITORS_HLL.labels(page_id=page_id).set(hll_count)
        MEMORY_USAGE_EXACT.labels(page_id=page_id).set(exact_memory)
        MEMORY_USAGE_HLL.labels(page_id=page_id).set(hll_memory)
        COUNTING_ERROR_RATE.labels(page_id=page_id).set(error_rate)

        logger.info(
            f"Simulation complete: page={page_id}, events={total_visitors}, "
            f"unique={unique_visitors}, exact={exact_count}, hll={hll_count}, "
            f"duration={duration:.2f}s"
        )

        return {
            "simulation": {
                "total_events": total_visitors,
                "unique_visitors_target": unique_visitors,
                "duration_seconds": round(duration, 2),
                "events_per_second": round(total_visitors / duration, 2)
            },
            "results": {
                "page_id": page_id,
                "exact_count": exact_count,
                "hll_count": hll_count,
                "error_rate": round(error_rate, 6),
                "error_percentage": f"{error_rate * 100:.4f}%"
            },
            "memory": {
                "exact_bytes": exact_memory,
                "exact_human": f"{exact_memory / 1024:.2f} KB",
                "hll_bytes": hll_memory,
                "hll_human": f"{hll_memory / 1024:.2f} KB",
                "savings_ratio": f"{exact_memory / hll_memory:.1f}x" if hll_memory > 0 else "N/A"
            },
            "insight": (
                f"Exact counting used {exact_memory / 1024:.2f} KB while HyperLogLog used only "
                f"{hll_memory / 1024:.2f} KB ({exact_memory / hll_memory:.1f}x memory savings). "
                f"HLL error was {error_rate * 100:.4f}% (expected ~0.81%)."
            )
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
