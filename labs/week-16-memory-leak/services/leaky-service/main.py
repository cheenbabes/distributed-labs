"""
Leaky Service - A service with configurable memory leaks for educational purposes.

This service demonstrates various types of memory leaks that can occur in production:
1. Unbounded cache - Stores every unique request without eviction
2. Event listener accumulation - Callbacks that never get cleaned up
3. Request history - Unbounded list that grows forever
"""
import asyncio
import gc
import logging
import os
import sys
import time
import tracemalloc
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import psutil
from fastapi import FastAPI, Request, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from pydantic import BaseModel

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "leaky-service")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab16-otel-collector:4317")

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(SERVICE_NAME)

# OpenTelemetry setup
resource = Resource.create({"service.name": SERVICE_NAME})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# ============================================================================
# Prometheus Metrics
# ============================================================================

# Memory metrics
PROCESS_MEMORY_BYTES = Gauge(
    "process_resident_memory_bytes",
    "Resident memory size in bytes"
)

PROCESS_VIRTUAL_MEMORY_BYTES = Gauge(
    "process_virtual_memory_bytes",
    "Virtual memory size in bytes"
)

HEAP_OBJECTS_COUNT = Gauge(
    "python_gc_objects_count",
    "Number of objects tracked by GC"
)

# Leak-specific metrics
CACHE_ENTRIES_COUNT = Gauge(
    "leak_cache_entries_total",
    "Number of entries in the unbounded cache"
)

CACHE_SIZE_BYTES = Gauge(
    "leak_cache_size_bytes",
    "Estimated size of cache in bytes"
)

LISTENERS_COUNT = Gauge(
    "leak_listeners_total",
    "Number of accumulated event listeners"
)

HISTORY_ENTRIES_COUNT = Gauge(
    "leak_history_entries_total",
    "Number of entries in request history"
)

HISTORY_SIZE_BYTES = Gauge(
    "leak_history_size_bytes",
    "Estimated size of history in bytes"
)

# Request metrics
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "endpoint", "status"]
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["service", "method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

GC_COLLECTIONS = Counter(
    "python_gc_collections_total",
    "Total number of GC collections",
    ["generation"]
)

MEMORY_GROWTH_RATE = Gauge(
    "memory_growth_rate_bytes_per_second",
    "Estimated memory growth rate"
)

# ============================================================================
# Memory Leak Implementations
# ============================================================================

@dataclass
class LeakConfig:
    """Configuration for memory leaks"""
    cache_enabled: bool = False
    listeners_enabled: bool = False
    history_enabled: bool = False


@dataclass
class RequestRecord:
    """A single request record - intentionally large to accelerate leak visibility"""
    request_id: str
    timestamp: datetime
    method: str
    path: str
    headers: dict
    query_params: dict
    body_preview: str
    response_status: int
    response_time_ms: float
    trace_id: str
    # Extra padding to make leak more visible
    padding: bytes = field(default_factory=lambda: b'x' * 1024)  # 1KB padding per record


class MemoryLeaker:
    """
    Manages various memory leak scenarios.
    Each leak type can be independently enabled/disabled.
    """

    def __init__(self):
        self.config = LeakConfig()

        # Leak 1: Unbounded cache - stores response for every unique request
        self._cache: dict[str, bytes] = {}

        # Leak 2: Event listeners that accumulate
        self._listeners: list[Callable] = []
        self._listener_data: list[bytes] = []  # Data captured by closures

        # Leak 3: Unbounded request history
        self._history: list[RequestRecord] = []

        # Memory tracking
        self._start_memory = psutil.Process().memory_info().rss
        self._last_memory_check = time.time()
        self._last_memory_value = self._start_memory

    def process_request(self, request_id: str, data: dict) -> dict:
        """Process a request and potentially leak memory based on enabled leaks"""
        result = {"request_id": request_id, "processed": True, "leaks_triggered": []}

        if self.config.cache_enabled:
            self._leak_cache(request_id, data)
            result["leaks_triggered"].append("cache")

        if self.config.listeners_enabled:
            self._leak_listener(request_id, data)
            result["leaks_triggered"].append("listeners")

        if self.config.history_enabled:
            self._leak_history(request_id, data)
            result["leaks_triggered"].append("history")

        return result

    def _leak_cache(self, request_id: str, data: dict):
        """
        Leak Type 1: Unbounded Cache

        Every unique request gets cached with no eviction policy.
        Real-world cause: Missing cache TTL, no max size limit, caching user-specific data.
        """
        # Create a "cache key" that's unique per request - guarantees no hits
        cache_key = f"{request_id}:{time.time_ns()}"

        # Store a substantial amount of data per entry (simulate caching response)
        cached_data = {
            "request_id": request_id,
            "timestamp": datetime.now().isoformat(),
            "data": data,
            "computed_result": str(data) * 10,  # Inflate size
            "metadata": {
                "cache_time": time.time(),
                "ttl": None,  # Never expires!
            }
        }
        # Convert to bytes to control size better
        import json
        self._cache[cache_key] = json.dumps(cached_data).encode() * 5  # ~5KB per entry

    def _leak_listener(self, request_id: str, data: dict):
        """
        Leak Type 2: Event Listener Accumulation

        Creates callbacks that capture references to large objects.
        Real-world cause: Event handlers not removed, callback accumulation, pub/sub without cleanup.
        """
        # Create data that will be captured in closure
        large_data = b'callback_data_' * 512  # ~6KB per listener

        def callback(event_data: dict) -> dict:
            # This closure captures large_data, preventing it from being GC'd
            return {
                "request_id": request_id,
                "captured_size": len(large_data),
                "event": event_data
            }

        # Add listener but never remove it
        self._listeners.append(callback)
        self._listener_data.append(large_data)

    def _leak_history(self, request_id: str, data: dict):
        """
        Leak Type 3: Unbounded Request History

        Keeps full request details forever without cleanup.
        Real-world cause: Audit logs in memory, request replay buffers, debug logging.
        """
        record = RequestRecord(
            request_id=request_id,
            timestamp=datetime.now(),
            method=data.get("method", "GET"),
            path=data.get("path", "/api/data"),
            headers=data.get("headers", {}),
            query_params=data.get("query_params", {}),
            body_preview=str(data)[:500],
            response_status=200,
            response_time_ms=data.get("response_time", 0),
            trace_id=data.get("trace_id", "")
        )
        self._history.append(record)

    def get_status(self) -> dict:
        """Get current status of all leaks"""
        process = psutil.Process()
        memory_info = process.memory_info()

        return {
            "leaks": {
                "cache": {
                    "enabled": self.config.cache_enabled,
                    "entries": len(self._cache),
                    "estimated_size_mb": self._estimate_cache_size() / (1024 * 1024)
                },
                "listeners": {
                    "enabled": self.config.listeners_enabled,
                    "count": len(self._listeners),
                    "estimated_size_mb": sum(len(d) for d in self._listener_data) / (1024 * 1024)
                },
                "history": {
                    "enabled": self.config.history_enabled,
                    "entries": len(self._history),
                    "estimated_size_mb": self._estimate_history_size() / (1024 * 1024)
                }
            },
            "memory": {
                "rss_mb": memory_info.rss / (1024 * 1024),
                "vms_mb": memory_info.vms / (1024 * 1024),
                "growth_since_start_mb": (memory_info.rss - self._start_memory) / (1024 * 1024)
            }
        }

    def _estimate_cache_size(self) -> int:
        """Estimate memory used by cache"""
        return sum(len(v) for v in self._cache.values())

    def _estimate_history_size(self) -> int:
        """Estimate memory used by history"""
        # Each record has ~1KB padding plus overhead
        return len(self._history) * 1500

    def clear_leak(self, leak_type: str) -> dict:
        """Clear a specific leak type"""
        if leak_type == "cache":
            count = len(self._cache)
            self._cache.clear()
            return {"cleared": "cache", "entries_removed": count}
        elif leak_type == "listeners":
            count = len(self._listeners)
            self._listeners.clear()
            self._listener_data.clear()
            return {"cleared": "listeners", "callbacks_removed": count}
        elif leak_type == "history":
            count = len(self._history)
            self._history.clear()
            return {"cleared": "history", "records_removed": count}
        elif leak_type == "all":
            cache_count = len(self._cache)
            listener_count = len(self._listeners)
            history_count = len(self._history)
            self._cache.clear()
            self._listeners.clear()
            self._listener_data.clear()
            self._history.clear()
            return {
                "cleared": "all",
                "cache_entries_removed": cache_count,
                "listeners_removed": listener_count,
                "history_records_removed": history_count
            }
        else:
            raise ValueError(f"Unknown leak type: {leak_type}")

    def update_metrics(self):
        """Update Prometheus metrics"""
        process = psutil.Process()
        memory_info = process.memory_info()

        # Memory metrics
        PROCESS_MEMORY_BYTES.set(memory_info.rss)
        PROCESS_VIRTUAL_MEMORY_BYTES.set(memory_info.vms)
        HEAP_OBJECTS_COUNT.set(len(gc.get_objects()))

        # Leak metrics
        CACHE_ENTRIES_COUNT.set(len(self._cache))
        CACHE_SIZE_BYTES.set(self._estimate_cache_size())
        LISTENERS_COUNT.set(len(self._listeners))
        HISTORY_ENTRIES_COUNT.set(len(self._history))
        HISTORY_SIZE_BYTES.set(self._estimate_history_size())

        # Calculate memory growth rate
        current_time = time.time()
        current_memory = memory_info.rss
        time_delta = current_time - self._last_memory_check
        if time_delta > 0:
            memory_delta = current_memory - self._last_memory_value
            growth_rate = memory_delta / time_delta
            MEMORY_GROWTH_RATE.set(growth_rate)

        self._last_memory_check = current_time
        self._last_memory_value = current_memory


# Global leaker instance
leaker = MemoryLeaker()

# Start tracemalloc for memory profiling
tracemalloc.start(10)


# ============================================================================
# FastAPI Application
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info("Memory leak investigation lab - use /admin endpoints to control leaks")

    # Start background metrics updater
    async def update_metrics_loop():
        while True:
            leaker.update_metrics()
            await asyncio.sleep(5)

    task = asyncio.create_task(update_metrics_loop())

    yield

    task.cancel()
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


# ============================================================================
# API Models
# ============================================================================

class LeakToggle(BaseModel):
    enabled: bool


class ClearRequest(BaseModel):
    leak_type: str = "all"  # cache, listeners, history, or all


# ============================================================================
# Health & Metrics Endpoints
# ============================================================================

@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    leaker.update_metrics()
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ============================================================================
# Main API Endpoint
# ============================================================================

@app.get("/api/data")
async def get_data(request: Request):
    """
    Main API endpoint that triggers memory leaks based on configuration.
    Each request potentially adds to the memory footprint.
    """
    start_time = time.time()
    request_id = str(uuid.uuid4())

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")
    current_span.set_attribute("request_id", request_id)

    # Simulate some work
    await asyncio.sleep(0.01)  # 10ms base latency

    # Process request (this may leak memory depending on config)
    request_data = {
        "method": request.method,
        "path": str(request.url.path),
        "headers": dict(request.headers),
        "query_params": dict(request.query_params),
        "trace_id": trace_id,
        "timestamp": datetime.now().isoformat()
    }

    result = leaker.process_request(request_id, request_data)

    duration = time.time() - start_time

    # Record metrics
    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/api/data",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/api/data"
    ).observe(duration)

    return {
        "service": SERVICE_NAME,
        "request_id": request_id,
        "trace_id": trace_id,
        "duration_ms": round(duration * 1000, 2),
        "leaks_triggered": result["leaks_triggered"],
        "data": {
            "message": "Hello from the leaky service!",
            "timestamp": datetime.now().isoformat()
        }
    }


# ============================================================================
# Admin Endpoints - Leak Control
# ============================================================================

@app.get("/admin/leaks/status")
async def get_leak_status():
    """Get current status of all memory leaks"""
    return leaker.get_status()


@app.post("/admin/leaks/cache")
async def toggle_cache_leak(toggle: LeakToggle):
    """Enable/disable unbounded cache leak"""
    leaker.config.cache_enabled = toggle.enabled
    logger.info(f"Cache leak {'enabled' if toggle.enabled else 'disabled'}")
    return {
        "leak": "cache",
        "enabled": toggle.enabled,
        "description": "Unbounded cache - stores every unique request without eviction"
    }


@app.post("/admin/leaks/listeners")
async def toggle_listener_leak(toggle: LeakToggle):
    """Enable/disable event listener accumulation leak"""
    leaker.config.listeners_enabled = toggle.enabled
    logger.info(f"Listener leak {'enabled' if toggle.enabled else 'disabled'}")
    return {
        "leak": "listeners",
        "enabled": toggle.enabled,
        "description": "Event listener accumulation - callbacks with captured closures"
    }


@app.post("/admin/leaks/history")
async def toggle_history_leak(toggle: LeakToggle):
    """Enable/disable unbounded history leak"""
    leaker.config.history_enabled = toggle.enabled
    logger.info(f"History leak {'enabled' if toggle.enabled else 'disabled'}")
    return {
        "leak": "history",
        "enabled": toggle.enabled,
        "description": "Unbounded request history - keeps full request records forever"
    }


@app.post("/admin/leaks/clear")
async def clear_leaks(request: ClearRequest):
    """Clear accumulated leak data"""
    result = leaker.clear_leak(request.leak_type)
    gc.collect()  # Force garbage collection after clearing
    logger.info(f"Cleared leak data: {result}")
    return result


# ============================================================================
# Admin Endpoints - Memory Analysis
# ============================================================================

@app.post("/admin/gc")
async def trigger_gc():
    """Manually trigger garbage collection"""
    before = psutil.Process().memory_info().rss

    # Run GC for all generations
    collected = []
    for gen in range(3):
        count = gc.collect(gen)
        collected.append({"generation": gen, "collected": count})
        GC_COLLECTIONS.labels(generation=str(gen)).inc()

    after = psutil.Process().memory_info().rss
    freed = before - after

    logger.info(f"GC triggered: freed {freed / 1024:.1f} KB")

    return {
        "gc_results": collected,
        "memory_before_mb": before / (1024 * 1024),
        "memory_after_mb": after / (1024 * 1024),
        "freed_mb": freed / (1024 * 1024),
        "note": "If leaks are active, GC won't free leaked memory since it's still referenced"
    }


@app.get("/admin/memory")
async def get_memory_info():
    """Get detailed memory usage breakdown"""
    process = psutil.Process()
    memory_info = process.memory_info()

    # Get tracemalloc snapshot
    snapshot = tracemalloc.take_snapshot()
    top_stats = snapshot.statistics('lineno')[:10]

    tracemalloc_top = []
    for stat in top_stats:
        tracemalloc_top.append({
            "file": str(stat.traceback),
            "size_kb": stat.size / 1024,
            "count": stat.count
        })

    # Get GC stats
    gc_stats = []
    for i, stats in enumerate(gc.get_stats()):
        gc_stats.append({
            "generation": i,
            "collections": stats["collections"],
            "collected": stats["collected"],
            "uncollectable": stats["uncollectable"]
        })

    return {
        "process": {
            "pid": process.pid,
            "rss_mb": memory_info.rss / (1024 * 1024),
            "vms_mb": memory_info.vms / (1024 * 1024),
            "percent": process.memory_percent()
        },
        "python": {
            "gc_objects": len(gc.get_objects()),
            "gc_stats": gc_stats
        },
        "tracemalloc": {
            "current_mb": tracemalloc.get_traced_memory()[0] / (1024 * 1024),
            "peak_mb": tracemalloc.get_traced_memory()[1] / (1024 * 1024),
            "top_allocations": tracemalloc_top
        },
        "leaks": leaker.get_status()["leaks"]
    }


@app.get("/admin/memory/snapshot")
async def get_memory_snapshot():
    """
    Get a tracemalloc snapshot showing where memory is being allocated.
    This is key for finding the source of memory leaks.
    """
    snapshot = tracemalloc.take_snapshot()

    # Group by filename
    by_file = snapshot.statistics('filename')[:15]
    file_stats = []
    for stat in by_file:
        file_stats.append({
            "filename": stat.traceback.format()[0] if stat.traceback.format() else "unknown",
            "size_mb": stat.size / (1024 * 1024),
            "count": stat.count
        })

    # Group by line
    by_line = snapshot.statistics('lineno')[:20]
    line_stats = []
    for stat in by_line:
        frames = stat.traceback.format()
        line_stats.append({
            "location": frames[0] if frames else "unknown",
            "size_mb": stat.size / (1024 * 1024),
            "count": stat.count
        })

    return {
        "timestamp": datetime.now().isoformat(),
        "total_traced_mb": tracemalloc.get_traced_memory()[0] / (1024 * 1024),
        "peak_traced_mb": tracemalloc.get_traced_memory()[1] / (1024 * 1024),
        "by_file": file_stats,
        "by_line": line_stats,
        "tip": "Look for lines in main.py with high allocation counts - these are likely leak sources"
    }


@app.get("/admin/memory/diff")
async def get_memory_diff():
    """
    Compare current memory allocation to baseline.
    Call this endpoint twice with some traffic in between to see what's growing.
    """
    global _baseline_snapshot

    current = tracemalloc.take_snapshot()

    if not hasattr(get_memory_diff, 'baseline'):
        get_memory_diff.baseline = current
        return {
            "status": "baseline_set",
            "message": "Baseline snapshot saved. Generate some traffic, then call this endpoint again to see the diff.",
            "baseline_size_mb": tracemalloc.get_traced_memory()[0] / (1024 * 1024)
        }

    # Compare to baseline
    diff = current.compare_to(get_memory_diff.baseline, 'lineno')

    growing = []
    for stat in diff[:20]:
        if stat.size_diff > 0:
            frames = stat.traceback.format()
            growing.append({
                "location": frames[0] if frames else "unknown",
                "size_diff_kb": stat.size_diff / 1024,
                "count_diff": stat.count_diff,
                "current_size_kb": stat.size / 1024
            })

    # Reset baseline for next comparison
    get_memory_diff.baseline = current

    return {
        "status": "diff_computed",
        "growing_allocations": growing,
        "total_growth_mb": sum(s["size_diff_kb"] for s in growing) / 1024,
        "tip": "Locations with highest size_diff are likely leak sources"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
