"""
Snowflake ID Generator Service

Implements Twitter's Snowflake ID generation algorithm:
- 64-bit IDs
- Bit layout: 1 bit unused | 41 bits timestamp | 5 bits datacenter | 5 bits worker | 12 bits sequence

This allows:
- ~69 years of timestamps (from custom epoch)
- 32 datacenters
- 32 workers per datacenter
- 4096 IDs per millisecond per worker
"""
import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "id-generator")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://coordinator:8100")
DATACENTER_ID = int(os.getenv("DATACENTER_ID", "1"))
PORT = int(os.getenv("PORT", "8001"))
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "generator-1")

# Snowflake constants
SNOWFLAKE_EPOCH = 1609459200000  # 2021-01-01 00:00:00 UTC in milliseconds
DATACENTER_ID_BITS = 5
WORKER_ID_BITS = 5
SEQUENCE_BITS = 12

MAX_DATACENTER_ID = (1 << DATACENTER_ID_BITS) - 1  # 31
MAX_WORKER_ID = (1 << WORKER_ID_BITS) - 1  # 31
MAX_SEQUENCE = (1 << SEQUENCE_BITS) - 1  # 4095

WORKER_ID_SHIFT = SEQUENCE_BITS
DATACENTER_ID_SHIFT = SEQUENCE_BITS + WORKER_ID_BITS
TIMESTAMP_SHIFT = SEQUENCE_BITS + WORKER_ID_BITS + DATACENTER_ID_BITS

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
IDS_GENERATED = Counter(
    "snowflake_ids_generated_total",
    "Total number of IDs generated",
    ["worker_id", "datacenter_id"]
)
SEQUENCE_EXHAUSTED = Counter(
    "snowflake_sequence_exhausted_total",
    "Number of times sequence was exhausted within a millisecond",
    ["worker_id"]
)
CLOCK_SKEW_EVENTS = Counter(
    "snowflake_clock_skew_total",
    "Number of clock skew events detected",
    ["worker_id"]
)
GENERATION_LATENCY = Histogram(
    "snowflake_generation_duration_seconds",
    "Time to generate an ID",
    ["worker_id"],
    buckets=[0.00001, 0.00005, 0.0001, 0.0005, 0.001, 0.005, 0.01]
)
CURRENT_SEQUENCE = Gauge(
    "snowflake_current_sequence",
    "Current sequence number",
    ["worker_id"]
)
LAST_TIMESTAMP = Gauge(
    "snowflake_last_timestamp_ms",
    "Last timestamp used for ID generation",
    ["worker_id"]
)


@dataclass
class ParsedSnowflakeID:
    """Parsed components of a Snowflake ID"""
    id: int
    timestamp_ms: int
    timestamp_utc: str
    datacenter_id: int
    worker_id: int
    sequence: int
    age_ms: int


class SnowflakeGenerator:
    """Thread-safe Snowflake ID generator"""

    def __init__(self, datacenter_id: int, worker_id: int):
        if datacenter_id < 0 or datacenter_id > MAX_DATACENTER_ID:
            raise ValueError(f"Datacenter ID must be between 0 and {MAX_DATACENTER_ID}")
        if worker_id < 0 or worker_id > MAX_WORKER_ID:
            raise ValueError(f"Worker ID must be between 0 and {MAX_WORKER_ID}")

        self.datacenter_id = datacenter_id
        self.worker_id = worker_id
        self.sequence = 0
        self.last_timestamp = -1
        self._lock = threading.Lock()

        # For simulating clock skew
        self._clock_offset_ms = 0

        logger.info(f"Initialized Snowflake generator: datacenter={datacenter_id}, worker={worker_id}")

    def _current_time_ms(self) -> int:
        """Get current time in milliseconds with optional offset for testing"""
        return int(time.time() * 1000) + self._clock_offset_ms

    def _wait_for_next_ms(self, last_timestamp: int) -> int:
        """Wait until the next millisecond"""
        timestamp = self._current_time_ms()
        while timestamp <= last_timestamp:
            time.sleep(0.0001)  # Sleep 0.1ms
            timestamp = self._current_time_ms()
        return timestamp

    def generate(self) -> int:
        """Generate a new unique Snowflake ID"""
        with self._lock:
            timestamp = self._current_time_ms()

            # Handle clock skew (clock moving backwards)
            if timestamp < self.last_timestamp:
                skew_ms = self.last_timestamp - timestamp
                CLOCK_SKEW_EVENTS.labels(worker_id=str(self.worker_id)).inc()
                logger.warning(f"Clock skew detected: {skew_ms}ms backwards")

                # Wait for clock to catch up (small skew) or raise error (large skew)
                if skew_ms <= 5:
                    timestamp = self._wait_for_next_ms(self.last_timestamp)
                else:
                    raise ClockSkewError(f"Clock moved backwards by {skew_ms}ms")

            # Same millisecond: increment sequence
            if timestamp == self.last_timestamp:
                self.sequence = (self.sequence + 1) & MAX_SEQUENCE
                if self.sequence == 0:
                    # Sequence exhausted, wait for next millisecond
                    SEQUENCE_EXHAUSTED.labels(worker_id=str(self.worker_id)).inc()
                    timestamp = self._wait_for_next_ms(self.last_timestamp)
            else:
                # New millisecond: reset sequence
                self.sequence = 0

            self.last_timestamp = timestamp

            # Update metrics
            CURRENT_SEQUENCE.labels(worker_id=str(self.worker_id)).set(self.sequence)
            LAST_TIMESTAMP.labels(worker_id=str(self.worker_id)).set(timestamp)

            # Generate ID
            snowflake_id = (
                ((timestamp - SNOWFLAKE_EPOCH) << TIMESTAMP_SHIFT) |
                (self.datacenter_id << DATACENTER_ID_SHIFT) |
                (self.worker_id << WORKER_ID_SHIFT) |
                self.sequence
            )

            IDS_GENERATED.labels(
                worker_id=str(self.worker_id),
                datacenter_id=str(self.datacenter_id)
            ).inc()

            return snowflake_id

    def set_clock_offset(self, offset_ms: int):
        """Set clock offset for testing clock skew scenarios"""
        with self._lock:
            self._clock_offset_ms = offset_ms
            logger.info(f"Clock offset set to {offset_ms}ms")

    @staticmethod
    def parse(snowflake_id: int) -> ParsedSnowflakeID:
        """Parse a Snowflake ID into its components"""
        timestamp_ms = ((snowflake_id >> TIMESTAMP_SHIFT) & 0x1FFFFFFFFFF) + SNOWFLAKE_EPOCH
        datacenter_id = (snowflake_id >> DATACENTER_ID_SHIFT) & MAX_DATACENTER_ID
        worker_id = (snowflake_id >> WORKER_ID_SHIFT) & MAX_WORKER_ID
        sequence = snowflake_id & MAX_SEQUENCE

        timestamp_utc = datetime.utcfromtimestamp(timestamp_ms / 1000).isoformat() + "Z"
        age_ms = int(time.time() * 1000) - timestamp_ms

        return ParsedSnowflakeID(
            id=snowflake_id,
            timestamp_ms=timestamp_ms,
            timestamp_utc=timestamp_utc,
            datacenter_id=datacenter_id,
            worker_id=worker_id,
            sequence=sequence,
            age_ms=age_ms
        )


class ClockSkewError(Exception):
    """Raised when clock skew is too large"""
    pass


# Global generator instance (initialized at startup)
generator: Optional[SnowflakeGenerator] = None
worker_id: Optional[int] = None


async def register_with_coordinator() -> int:
    """Register with coordinator to get a unique worker ID"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(
                f"{COORDINATOR_URL}/register",
                json={"instance_name": INSTANCE_NAME, "datacenter_id": DATACENTER_ID}
            )
            response.raise_for_status()
            data = response.json()
            return data["worker_id"]
        except Exception as e:
            logger.error(f"Failed to register with coordinator: {e}")
            raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    global generator, worker_id

    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Coordinator URL: {COORDINATOR_URL}")

    # Register with coordinator to get worker ID
    try:
        worker_id = await register_with_coordinator()
        logger.info(f"Registered with coordinator, got worker_id={worker_id}")
    except Exception as e:
        logger.warning(f"Could not register with coordinator: {e}, using fallback worker_id=0")
        worker_id = 0

    # Initialize generator
    generator = SnowflakeGenerator(datacenter_id=DATACENTER_ID, worker_id=worker_id)

    yield

    # Cleanup: deregister from coordinator
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{COORDINATOR_URL}/deregister",
                json={"worker_id": worker_id, "instance_name": INSTANCE_NAME}
            )
            logger.info(f"Deregistered worker_id={worker_id}")
    except Exception as e:
        logger.warning(f"Failed to deregister: {e}")

    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "worker_id": worker_id,
        "datacenter_id": DATACENTER_ID
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/generate")
async def generate_id():
    """Generate a single Snowflake ID"""
    current_span = trace.get_current_span()
    start_time = time.time()

    try:
        snowflake_id = generator.generate()
        duration = time.time() - start_time

        GENERATION_LATENCY.labels(worker_id=str(worker_id)).observe(duration)

        current_span.set_attribute("snowflake.id", str(snowflake_id))
        current_span.set_attribute("snowflake.worker_id", worker_id)
        current_span.set_attribute("snowflake.datacenter_id", DATACENTER_ID)

        return {
            "id": snowflake_id,
            "id_str": str(snowflake_id),
            "worker_id": worker_id,
            "datacenter_id": DATACENTER_ID,
            "instance": INSTANCE_NAME,
            "generation_time_us": round(duration * 1_000_000, 2)
        }
    except ClockSkewError as e:
        current_span.set_attribute("error", True)
        current_span.set_attribute("error.message", str(e))
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/generate/batch")
async def generate_batch(count: int = 10):
    """Generate multiple Snowflake IDs in a batch"""
    if count < 1 or count > 1000:
        raise HTTPException(status_code=400, detail="Count must be between 1 and 1000")

    current_span = trace.get_current_span()
    start_time = time.time()
    ids = []

    try:
        for _ in range(count):
            ids.append(generator.generate())

        duration = time.time() - start_time
        current_span.set_attribute("batch.size", count)
        current_span.set_attribute("batch.duration_ms", round(duration * 1000, 2))

        return {
            "ids": ids,
            "ids_str": [str(id) for id in ids],
            "count": count,
            "worker_id": worker_id,
            "datacenter_id": DATACENTER_ID,
            "instance": INSTANCE_NAME,
            "generation_time_ms": round(duration * 1000, 2),
            "avg_time_per_id_us": round((duration * 1_000_000) / count, 2)
        }
    except ClockSkewError as e:
        current_span.set_attribute("error", True)
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/parse/{snowflake_id}")
async def parse_id(snowflake_id: int):
    """Parse a Snowflake ID to extract its components"""
    parsed = SnowflakeGenerator.parse(snowflake_id)
    return {
        "id": parsed.id,
        "id_str": str(parsed.id),
        "components": {
            "timestamp_ms": parsed.timestamp_ms,
            "timestamp_utc": parsed.timestamp_utc,
            "datacenter_id": parsed.datacenter_id,
            "worker_id": parsed.worker_id,
            "sequence": parsed.sequence
        },
        "age_ms": parsed.age_ms,
        "binary": format(parsed.id, '064b')
    }


@app.get("/info")
async def info():
    """Get information about this generator instance"""
    return {
        "instance_name": INSTANCE_NAME,
        "worker_id": worker_id,
        "datacenter_id": DATACENTER_ID,
        "epoch": SNOWFLAKE_EPOCH,
        "epoch_utc": datetime.utcfromtimestamp(SNOWFLAKE_EPOCH / 1000).isoformat() + "Z",
        "max_datacenter_id": MAX_DATACENTER_ID,
        "max_worker_id": MAX_WORKER_ID,
        "max_sequence": MAX_SEQUENCE,
        "ids_per_ms": MAX_SEQUENCE + 1,
        "bits": {
            "timestamp": 41,
            "datacenter": DATACENTER_ID_BITS,
            "worker": WORKER_ID_BITS,
            "sequence": SEQUENCE_BITS
        }
    }


@app.post("/admin/clock-offset")
async def set_clock_offset(offset_ms: int):
    """Set clock offset for testing clock skew scenarios"""
    generator.set_clock_offset(offset_ms)
    return {
        "status": "ok",
        "clock_offset_ms": offset_ms,
        "message": f"Clock offset set to {offset_ms}ms"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
