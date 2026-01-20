"""
Coordinator Service - Assigns unique worker IDs to ID generator instances.

This service ensures that each ID generator gets a unique worker ID,
preventing ID collisions in a distributed system. It tracks active
workers and can reassign IDs when workers leave.
"""
import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Set

from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from pydantic import BaseModel

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "coordinator")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "32"))  # Maximum worker IDs per datacenter
PORT = int(os.getenv("PORT", "8100"))

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
REGISTRATIONS_TOTAL = Counter(
    "coordinator_registrations_total",
    "Total worker registrations",
    ["datacenter_id"]
)
DEREGISTRATIONS_TOTAL = Counter(
    "coordinator_deregistrations_total",
    "Total worker deregistrations",
    ["datacenter_id"]
)
ACTIVE_WORKERS = Gauge(
    "coordinator_active_workers",
    "Number of active workers",
    ["datacenter_id"]
)
AVAILABLE_WORKER_IDS = Gauge(
    "coordinator_available_worker_ids",
    "Number of available worker IDs",
    ["datacenter_id"]
)


@dataclass
class WorkerRegistration:
    """Information about a registered worker"""
    worker_id: int
    instance_name: str
    datacenter_id: int
    registered_at: float
    last_heartbeat: float


class WorkerRegistry:
    """Thread-safe registry of worker IDs"""

    def __init__(self, max_workers: int = 32):
        self.max_workers = max_workers
        self._lock = threading.Lock()
        # Datacenter ID -> Set of available worker IDs
        self._available: Dict[int, Set[int]] = {}
        # Datacenter ID -> Worker ID -> Registration
        self._registrations: Dict[int, Dict[int, WorkerRegistration]] = {}
        # Instance name -> (datacenter_id, worker_id)
        self._instance_map: Dict[str, tuple] = {}

    def _ensure_datacenter(self, datacenter_id: int):
        """Initialize tracking for a datacenter if not present"""
        if datacenter_id not in self._available:
            self._available[datacenter_id] = set(range(self.max_workers))
            self._registrations[datacenter_id] = {}
            AVAILABLE_WORKER_IDS.labels(datacenter_id=str(datacenter_id)).set(self.max_workers)
            ACTIVE_WORKERS.labels(datacenter_id=str(datacenter_id)).set(0)

    def register(self, instance_name: str, datacenter_id: int) -> int:
        """Register a new worker and return its assigned worker ID"""
        with self._lock:
            self._ensure_datacenter(datacenter_id)

            # Check if instance is already registered
            if instance_name in self._instance_map:
                existing_dc, existing_worker = self._instance_map[instance_name]
                if existing_dc == datacenter_id:
                    # Update heartbeat and return existing ID
                    if existing_worker in self._registrations[datacenter_id]:
                        self._registrations[datacenter_id][existing_worker].last_heartbeat = time.time()
                        logger.info(f"Instance {instance_name} already registered with worker_id={existing_worker}")
                        return existing_worker
                else:
                    # Different datacenter - deregister from old one first
                    self._do_deregister(instance_name)

            # Assign new worker ID
            if not self._available[datacenter_id]:
                raise ValueError(f"No available worker IDs in datacenter {datacenter_id}")

            worker_id = min(self._available[datacenter_id])  # Assign lowest available
            self._available[datacenter_id].remove(worker_id)

            now = time.time()
            registration = WorkerRegistration(
                worker_id=worker_id,
                instance_name=instance_name,
                datacenter_id=datacenter_id,
                registered_at=now,
                last_heartbeat=now
            )
            self._registrations[datacenter_id][worker_id] = registration
            self._instance_map[instance_name] = (datacenter_id, worker_id)

            # Update metrics
            REGISTRATIONS_TOTAL.labels(datacenter_id=str(datacenter_id)).inc()
            ACTIVE_WORKERS.labels(datacenter_id=str(datacenter_id)).set(
                len(self._registrations[datacenter_id])
            )
            AVAILABLE_WORKER_IDS.labels(datacenter_id=str(datacenter_id)).set(
                len(self._available[datacenter_id])
            )

            logger.info(f"Registered {instance_name} with worker_id={worker_id} in datacenter={datacenter_id}")
            return worker_id

    def _do_deregister(self, instance_name: str) -> Optional[int]:
        """Internal deregister without lock (must be called with lock held)"""
        if instance_name not in self._instance_map:
            return None

        datacenter_id, worker_id = self._instance_map[instance_name]
        del self._instance_map[instance_name]

        if datacenter_id in self._registrations and worker_id in self._registrations[datacenter_id]:
            del self._registrations[datacenter_id][worker_id]
            self._available[datacenter_id].add(worker_id)

            # Update metrics
            DEREGISTRATIONS_TOTAL.labels(datacenter_id=str(datacenter_id)).inc()
            ACTIVE_WORKERS.labels(datacenter_id=str(datacenter_id)).set(
                len(self._registrations[datacenter_id])
            )
            AVAILABLE_WORKER_IDS.labels(datacenter_id=str(datacenter_id)).set(
                len(self._available[datacenter_id])
            )

        logger.info(f"Deregistered {instance_name} (worker_id={worker_id}, datacenter={datacenter_id})")
        return worker_id

    def deregister(self, instance_name: str) -> Optional[int]:
        """Deregister a worker by instance name"""
        with self._lock:
            return self._do_deregister(instance_name)

    def deregister_by_worker_id(self, worker_id: int, datacenter_id: int) -> bool:
        """Deregister a worker by worker ID"""
        with self._lock:
            self._ensure_datacenter(datacenter_id)

            if worker_id not in self._registrations[datacenter_id]:
                return False

            registration = self._registrations[datacenter_id][worker_id]
            return self._do_deregister(registration.instance_name) is not None

    def heartbeat(self, instance_name: str) -> bool:
        """Update heartbeat for a worker"""
        with self._lock:
            if instance_name not in self._instance_map:
                return False

            datacenter_id, worker_id = self._instance_map[instance_name]
            if worker_id in self._registrations[datacenter_id]:
                self._registrations[datacenter_id][worker_id].last_heartbeat = time.time()
                return True
            return False

    def get_all_registrations(self) -> Dict[int, list]:
        """Get all active registrations grouped by datacenter"""
        with self._lock:
            result = {}
            for dc_id, registrations in self._registrations.items():
                result[dc_id] = [
                    {
                        "worker_id": reg.worker_id,
                        "instance_name": reg.instance_name,
                        "registered_at": datetime.utcfromtimestamp(reg.registered_at).isoformat() + "Z",
                        "last_heartbeat": datetime.utcfromtimestamp(reg.last_heartbeat).isoformat() + "Z",
                        "age_seconds": round(time.time() - reg.registered_at, 1)
                    }
                    for reg in registrations.values()
                ]
            return result

    def get_stats(self) -> dict:
        """Get registry statistics"""
        with self._lock:
            stats = {
                "max_workers_per_datacenter": self.max_workers,
                "datacenters": {}
            }
            for dc_id in set(list(self._available.keys()) + list(self._registrations.keys())):
                self._ensure_datacenter(dc_id)
                stats["datacenters"][dc_id] = {
                    "active_workers": len(self._registrations.get(dc_id, {})),
                    "available_ids": len(self._available.get(dc_id, set()))
                }
            return stats


# Global registry instance
registry = WorkerRegistry(max_workers=MAX_WORKERS)


# Request/Response models
class RegisterRequest(BaseModel):
    instance_name: str
    datacenter_id: int


class DeregisterRequest(BaseModel):
    worker_id: Optional[int] = None
    instance_name: Optional[str] = None


class HeartbeatRequest(BaseModel):
    instance_name: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Max workers per datacenter: {MAX_WORKERS}")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "max_workers": MAX_WORKERS
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/register")
async def register(request: RegisterRequest):
    """Register a new ID generator instance"""
    current_span = trace.get_current_span()

    try:
        worker_id = registry.register(
            instance_name=request.instance_name,
            datacenter_id=request.datacenter_id
        )

        current_span.set_attribute("worker.id", worker_id)
        current_span.set_attribute("worker.instance_name", request.instance_name)
        current_span.set_attribute("worker.datacenter_id", request.datacenter_id)

        return {
            "status": "registered",
            "worker_id": worker_id,
            "instance_name": request.instance_name,
            "datacenter_id": request.datacenter_id
        }
    except ValueError as e:
        current_span.set_attribute("error", True)
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/deregister")
async def deregister(request: DeregisterRequest):
    """Deregister an ID generator instance"""
    current_span = trace.get_current_span()

    if request.instance_name:
        worker_id = registry.deregister(request.instance_name)
        if worker_id is not None:
            current_span.set_attribute("worker.id", worker_id)
            return {"status": "deregistered", "worker_id": worker_id}
    elif request.worker_id is not None:
        # Need datacenter_id for this - check all datacenters
        for dc_id in range(32):
            if registry.deregister_by_worker_id(request.worker_id, dc_id):
                current_span.set_attribute("worker.id", request.worker_id)
                return {"status": "deregistered", "worker_id": request.worker_id}

    raise HTTPException(status_code=404, detail="Worker not found")


@app.post("/heartbeat")
async def heartbeat(request: HeartbeatRequest):
    """Send heartbeat for a registered worker"""
    if registry.heartbeat(request.instance_name):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Worker not registered")


@app.get("/workers")
async def list_workers():
    """List all registered workers"""
    return {
        "workers": registry.get_all_registrations(),
        "stats": registry.get_stats()
    }


@app.get("/stats")
async def stats():
    """Get coordinator statistics"""
    return registry.get_stats()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
