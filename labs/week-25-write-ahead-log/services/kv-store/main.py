"""
KV Store with Write-Ahead Logging - Demonstrates durability and crash recovery.

This service implements a simple key-value store with optional write-ahead logging
to demonstrate how WAL provides durability guarantees and enables crash recovery.
"""
import asyncio
import json
import logging
import os
import signal
import struct
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "kv-store")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
WAL_ENABLED = os.getenv("WAL_ENABLED", "true").lower() == "true"
WAL_PATH = os.getenv("WAL_PATH", "/data/wal.log")
CHECKPOINT_PATH = os.getenv("CHECKPOINT_PATH", "/data/checkpoint.json")
FSYNC_ENABLED = os.getenv("FSYNC_ENABLED", "true").lower() == "true"
CHECKPOINT_THRESHOLD = int(os.getenv("CHECKPOINT_THRESHOLD", "100"))  # entries before checkpoint

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
WAL_WRITES = Counter(
    "wal_writes_total",
    "Total number of WAL write operations",
    ["operation"]
)
WAL_SIZE = Gauge(
    "wal_size_bytes",
    "Current size of the WAL file in bytes"
)
WAL_ENTRIES = Gauge(
    "wal_entries_total",
    "Current number of entries in the WAL"
)
CHECKPOINT_COUNT = Counter(
    "checkpoint_count_total",
    "Total number of checkpoint operations"
)
RECOVERY_TIME = Histogram(
    "recovery_time_seconds",
    "Time taken to recover from WAL",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
KV_OPERATIONS = Counter(
    "kv_operations_total",
    "Total KV store operations",
    ["operation", "wal_enabled"]
)
KV_OPERATION_LATENCY = Histogram(
    "kv_operation_duration_seconds",
    "KV operation latency",
    ["operation", "wal_enabled"],
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1]
)
DATA_ITEMS = Gauge(
    "kv_data_items_total",
    "Number of items in the KV store"
)
DATA_LOSS_EVENTS = Counter(
    "data_loss_events_total",
    "Number of data loss events detected during recovery"
)


class WALOperation(str, Enum):
    SET = "SET"
    DELETE = "DELETE"


@dataclass
class WALEntry:
    """Represents a single entry in the write-ahead log."""
    sequence: int
    operation: WALOperation
    key: str
    value: Any = None
    timestamp: float = field(default_factory=time.time)

    def to_bytes(self) -> bytes:
        """Serialize entry to bytes for disk storage."""
        data = {
            "seq": self.sequence,
            "op": self.operation.value,
            "key": self.key,
            "value": self.value,
            "ts": self.timestamp
        }
        json_bytes = json.dumps(data).encode("utf-8")
        # Format: [length (4 bytes)][json data][checksum (4 bytes)]
        length = len(json_bytes)
        checksum = sum(json_bytes) & 0xFFFFFFFF  # Simple checksum
        return struct.pack(">I", length) + json_bytes + struct.pack(">I", checksum)

    @classmethod
    def from_bytes(cls, data: bytes) -> tuple["WALEntry", int]:
        """Deserialize entry from bytes. Returns (entry, bytes_consumed)."""
        if len(data) < 8:
            raise ValueError("Insufficient data for WAL entry")

        length = struct.unpack(">I", data[:4])[0]
        if len(data) < 8 + length:
            raise ValueError("Incomplete WAL entry")

        json_bytes = data[4:4+length]
        stored_checksum = struct.unpack(">I", data[4+length:8+length])[0]

        # Verify checksum
        computed_checksum = sum(json_bytes) & 0xFFFFFFFF
        if stored_checksum != computed_checksum:
            raise ValueError("WAL entry checksum mismatch")

        entry_data = json.loads(json_bytes.decode("utf-8"))
        return cls(
            sequence=entry_data["seq"],
            operation=WALOperation(entry_data["op"]),
            key=entry_data["key"],
            value=entry_data.get("value"),
            timestamp=entry_data["ts"]
        ), 8 + length


class WriteAheadLog:
    """
    Write-Ahead Log implementation.

    Provides durability by writing operations to disk before applying them.
    Supports crash recovery by replaying the log on startup.
    """

    def __init__(self, path: str, fsync: bool = True):
        self.path = Path(path)
        self.fsync = fsync
        self.sequence = 0
        self.entries_since_checkpoint = 0
        self._file = None

        # Ensure directory exists
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def open(self):
        """Open the WAL file for appending."""
        self._file = open(self.path, "ab")
        logger.info(f"WAL opened at {self.path}")

    def close(self):
        """Close the WAL file."""
        if self._file:
            self._file.close()
            self._file = None

    def append(self, operation: WALOperation, key: str, value: Any = None) -> WALEntry:
        """
        Append an entry to the WAL.
        This is the critical path - we MUST write to disk before returning.
        """
        if not self._file:
            raise RuntimeError("WAL not open")

        self.sequence += 1
        entry = WALEntry(
            sequence=self.sequence,
            operation=operation,
            key=key,
            value=value
        )

        # Write to disk
        data = entry.to_bytes()
        self._file.write(data)

        # Fsync to ensure durability
        if self.fsync:
            self._file.flush()
            os.fsync(self._file.fileno())

        self.entries_since_checkpoint += 1

        # Update metrics
        WAL_WRITES.labels(operation=operation.value).inc()
        self._update_size_metrics()

        return entry

    def read_all(self) -> list[WALEntry]:
        """Read all entries from the WAL file."""
        entries = []

        if not self.path.exists():
            return entries

        with open(self.path, "rb") as f:
            data = f.read()

        offset = 0
        while offset < len(data):
            try:
                entry, consumed = WALEntry.from_bytes(data[offset:])
                entries.append(entry)
                offset += consumed
            except ValueError as e:
                logger.warning(f"Error reading WAL entry at offset {offset}: {e}")
                break

        logger.info(f"Read {len(entries)} entries from WAL")
        return entries

    def truncate(self):
        """Truncate the WAL file (after checkpoint)."""
        self.close()

        # Remove old WAL
        if self.path.exists():
            self.path.unlink()

        # Reset state
        self.sequence = 0
        self.entries_since_checkpoint = 0

        # Reopen
        self.open()
        self._update_size_metrics()

        logger.info("WAL truncated")

    def _update_size_metrics(self):
        """Update WAL size metrics."""
        if self.path.exists():
            WAL_SIZE.set(self.path.stat().st_size)
            WAL_ENTRIES.set(self.sequence)
        else:
            WAL_SIZE.set(0)
            WAL_ENTRIES.set(0)


class KVStore:
    """
    Key-Value Store with optional Write-Ahead Logging.

    Demonstrates the difference between volatile (in-memory only) and
    durable (WAL-backed) storage.
    """

    def __init__(self, wal_enabled: bool = True, wal_path: str = "/data/wal.log",
                 checkpoint_path: str = "/data/checkpoint.json", fsync: bool = True,
                 checkpoint_threshold: int = 100):
        self.data: dict[str, Any] = {}
        self.wal_enabled = wal_enabled
        self.checkpoint_path = Path(checkpoint_path)
        self.checkpoint_threshold = checkpoint_threshold

        if wal_enabled:
            self.wal = WriteAheadLog(wal_path, fsync=fsync)
        else:
            self.wal = None

        # Track operations for comparison
        self.operations_without_wal = 0
        self.last_crash_time: float | None = None
        self.recovered_entries = 0
        self.lost_entries = 0

    def startup(self):
        """Initialize the store and recover from WAL if needed."""
        start_time = time.time()

        if self.wal_enabled:
            self.wal.open()

            # Try to load from checkpoint first
            if self.checkpoint_path.exists():
                try:
                    with open(self.checkpoint_path, "r") as f:
                        checkpoint_data = json.load(f)
                    self.data = checkpoint_data.get("data", {})
                    last_seq = checkpoint_data.get("last_sequence", 0)
                    logger.info(f"Loaded checkpoint with {len(self.data)} items, seq={last_seq}")
                except Exception as e:
                    logger.error(f"Failed to load checkpoint: {e}")
                    self.data = {}
                    last_seq = 0
            else:
                last_seq = 0

            # Replay WAL entries after checkpoint
            entries = self.wal.read_all()
            replayed = 0
            for entry in entries:
                if entry.sequence > last_seq:
                    self._apply_entry(entry)
                    replayed += 1

            self.recovered_entries = replayed

            if replayed > 0:
                recovery_time = time.time() - start_time
                RECOVERY_TIME.observe(recovery_time)
                logger.info(f"Recovered {replayed} entries in {recovery_time*1000:.2f}ms")

        DATA_ITEMS.set(len(self.data))
        logger.info(f"KV Store started with {len(self.data)} items, WAL={'enabled' if self.wal_enabled else 'disabled'}")

    def shutdown(self):
        """Clean shutdown of the store."""
        if self.wal:
            self.wal.close()
        logger.info("KV Store shut down")

    def _apply_entry(self, entry: WALEntry):
        """Apply a WAL entry to the in-memory state."""
        if entry.operation == WALOperation.SET:
            self.data[entry.key] = entry.value
        elif entry.operation == WALOperation.DELETE:
            self.data.pop(entry.key, None)

    def set(self, key: str, value: Any) -> dict:
        """Set a key-value pair."""
        start_time = time.time()
        wal_label = "enabled" if self.wal_enabled else "disabled"

        with tracer.start_as_current_span("kv.set") as span:
            span.set_attribute("kv.key", key)
            span.set_attribute("kv.wal_enabled", self.wal_enabled)

            if self.wal_enabled:
                # Write to WAL first (durability guarantee)
                entry = self.wal.append(WALOperation.SET, key, value)
                span.set_attribute("kv.wal_sequence", entry.sequence)

                # Check if checkpoint is needed
                if self.wal.entries_since_checkpoint >= self.checkpoint_threshold:
                    self._checkpoint()

            # Then update in-memory state
            self.data[key] = value
            DATA_ITEMS.set(len(self.data))

            duration = time.time() - start_time
            KV_OPERATIONS.labels(operation="set", wal_enabled=wal_label).inc()
            KV_OPERATION_LATENCY.labels(operation="set", wal_enabled=wal_label).observe(duration)

            return {"key": key, "value": value, "wal_enabled": self.wal_enabled}

    def get(self, key: str) -> Any:
        """Get a value by key."""
        start_time = time.time()
        wal_label = "enabled" if self.wal_enabled else "disabled"

        with tracer.start_as_current_span("kv.get") as span:
            span.set_attribute("kv.key", key)

            value = self.data.get(key)

            duration = time.time() - start_time
            KV_OPERATIONS.labels(operation="get", wal_enabled=wal_label).inc()
            KV_OPERATION_LATENCY.labels(operation="get", wal_enabled=wal_label).observe(duration)

            if value is None:
                raise KeyError(key)

            return value

    def delete(self, key: str) -> dict:
        """Delete a key."""
        start_time = time.time()
        wal_label = "enabled" if self.wal_enabled else "disabled"

        with tracer.start_as_current_span("kv.delete") as span:
            span.set_attribute("kv.key", key)
            span.set_attribute("kv.wal_enabled", self.wal_enabled)

            if key not in self.data:
                raise KeyError(key)

            if self.wal_enabled:
                entry = self.wal.append(WALOperation.DELETE, key)
                span.set_attribute("kv.wal_sequence", entry.sequence)

            del self.data[key]
            DATA_ITEMS.set(len(self.data))

            duration = time.time() - start_time
            KV_OPERATIONS.labels(operation="delete", wal_enabled=wal_label).inc()
            KV_OPERATION_LATENCY.labels(operation="delete", wal_enabled=wal_label).observe(duration)

            return {"key": key, "deleted": True}

    def list_all(self) -> dict:
        """List all key-value pairs."""
        return dict(self.data)

    def _checkpoint(self):
        """Create a checkpoint and truncate the WAL."""
        start_time = time.time()

        with tracer.start_as_current_span("kv.checkpoint") as span:
            # Write checkpoint
            checkpoint_data = {
                "data": self.data,
                "last_sequence": self.wal.sequence,
                "timestamp": time.time()
            }

            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

            # Write atomically (write to temp, then rename)
            temp_path = self.checkpoint_path.with_suffix(".tmp")
            with open(temp_path, "w") as f:
                json.dump(checkpoint_data, f)
                f.flush()
                os.fsync(f.fileno())

            temp_path.rename(self.checkpoint_path)

            # Truncate WAL
            old_entries = self.wal.entries_since_checkpoint
            self.wal.truncate()

            duration = time.time() - start_time
            span.set_attribute("checkpoint.entries", old_entries)
            span.set_attribute("checkpoint.duration_ms", duration * 1000)

            CHECKPOINT_COUNT.inc()
            logger.info(f"Checkpoint created with {len(self.data)} items, truncated {old_entries} WAL entries")

    def force_crash(self):
        """
        Simulate a crash by immediately terminating.
        This allows us to see what happens when:
        - WAL is enabled: Data can be recovered
        - WAL is disabled: Data is lost
        """
        logger.warning("SIMULATING CRASH - immediate termination!")
        self.last_crash_time = time.time()

        # Close WAL file without flushing pending writes
        if self.wal and self.wal._file:
            self.wal._file.close()
            self.wal._file = None

        # Kill the process immediately
        os._exit(1)

    def get_wal_contents(self) -> list[dict]:
        """Read and return the raw WAL contents for inspection."""
        if not self.wal_enabled:
            return []

        entries = self.wal.read_all()
        return [
            {
                "sequence": e.sequence,
                "operation": e.operation.value,
                "key": e.key,
                "value": e.value,
                "timestamp": e.timestamp
            }
            for e in entries
        ]

    def get_stats(self) -> dict:
        """Get store statistics."""
        stats = {
            "item_count": len(self.data),
            "wal_enabled": self.wal_enabled,
            "recovered_entries": self.recovered_entries,
            "lost_entries": self.lost_entries
        }

        if self.wal_enabled and self.wal:
            stats["wal_sequence"] = self.wal.sequence
            stats["entries_since_checkpoint"] = self.wal.entries_since_checkpoint
            stats["checkpoint_threshold"] = self.checkpoint_threshold
            if self.wal.path.exists():
                stats["wal_size_bytes"] = self.wal.path.stat().st_size

        return stats


# Global store instance
store: KVStore = None


# Pydantic models for API
class SetRequest(BaseModel):
    key: str
    value: Any


class WALConfig(BaseModel):
    enabled: bool


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"WAL enabled: {WAL_ENABLED}")
    logger.info(f"WAL path: {WAL_PATH}")

    store = KVStore(
        wal_enabled=WAL_ENABLED,
        wal_path=WAL_PATH,
        checkpoint_path=CHECKPOINT_PATH,
        fsync=FSYNC_ENABLED,
        checkpoint_threshold=CHECKPOINT_THRESHOLD
    )
    store.startup()

    yield

    store.shutdown()
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "wal_enabled": store.wal_enabled if store else WAL_ENABLED
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# KV Store endpoints
@app.post("/kv")
async def set_value(req: SetRequest):
    """Set a key-value pair."""
    result = store.set(req.key, req.value)
    return result


@app.get("/kv/{key}")
async def get_value(key: str):
    """Get a value by key."""
    try:
        value = store.get(key)
        return {"key": key, "value": value}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Key '{key}' not found")


@app.delete("/kv/{key}")
async def delete_value(key: str):
    """Delete a key."""
    try:
        return store.delete(key)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Key '{key}' not found")


@app.get("/kv")
async def list_values():
    """List all key-value pairs."""
    return {"data": store.list_all(), "count": len(store.data)}


# Admin endpoints
@app.get("/admin/stats")
async def get_stats():
    """Get store statistics."""
    return store.get_stats()


@app.get("/admin/wal")
async def get_wal():
    """View WAL contents."""
    return {
        "wal_enabled": store.wal_enabled,
        "entries": store.get_wal_contents()
    }


@app.post("/admin/checkpoint")
async def trigger_checkpoint():
    """Manually trigger a checkpoint."""
    if not store.wal_enabled:
        raise HTTPException(status_code=400, detail="WAL is not enabled")

    entries_before = store.wal.entries_since_checkpoint
    store._checkpoint()
    return {
        "status": "checkpoint created",
        "entries_truncated": entries_before
    }


@app.post("/admin/crash")
async def crash():
    """Simulate a crash (container will restart)."""
    # Store current state for comparison after restart
    current_count = len(store.data)
    logger.warning(f"Crash triggered with {current_count} items in memory")

    # Run crash in background so response can be sent
    asyncio.get_event_loop().call_later(0.1, store.force_crash)

    return {
        "status": "crashing",
        "items_in_memory": current_count,
        "wal_enabled": store.wal_enabled,
        "message": "Container will restart. Check /admin/stats after restart to see recovery results."
    }


@app.post("/admin/clear")
async def clear_all():
    """Clear all data and WAL."""
    store.data.clear()
    DATA_ITEMS.set(0)

    if store.wal_enabled:
        store.wal.truncate()
        if store.checkpoint_path.exists():
            store.checkpoint_path.unlink()

    return {"status": "cleared", "items": 0}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
