"""
Ring Coordinator - Manages the consistent hash ring and routes requests.

This service provides:
- Visualization of the hash ring state
- Node membership management (add/remove nodes)
- Key lookup with routing information
- Metrics for key distribution analysis
"""
import asyncio
import hashlib
import logging
import os
import time
import bisect
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "ring-coordinator")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
VIRTUAL_NODES = int(os.getenv("VIRTUAL_NODES", "150"))  # Virtual nodes per physical node
RING_SIZE = 2**32  # Standard hash ring size (32-bit)

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
KEYS_PER_NODE = Gauge(
    "hash_ring_keys_per_node",
    "Number of keys assigned to each node",
    ["node_id"]
)
VIRTUAL_NODES_PER_NODE = Gauge(
    "hash_ring_virtual_nodes_per_node",
    "Number of virtual nodes per physical node",
    ["node_id"]
)
RING_SEGMENT_SIZE = Gauge(
    "hash_ring_segment_size",
    "Size of hash ring segment owned by each node (percentage)",
    ["node_id"]
)
NODE_COUNT = Gauge(
    "hash_ring_node_count",
    "Total number of physical nodes in the ring"
)
TOTAL_VIRTUAL_NODES = Gauge(
    "hash_ring_total_virtual_nodes",
    "Total number of virtual nodes in the ring"
)
KEY_LOOKUPS = Counter(
    "hash_ring_key_lookups_total",
    "Total number of key lookups",
    ["target_node"]
)
KEYS_MIGRATED = Counter(
    "hash_ring_keys_migrated_total",
    "Total number of keys migrated during topology changes",
    ["from_node", "to_node"]
)
LOOKUP_LATENCY = Histogram(
    "hash_ring_lookup_duration_seconds",
    "Time to perform key lookup",
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05]
)


def consistent_hash(key: str) -> int:
    """Compute consistent hash for a key using MD5."""
    return int(hashlib.md5(key.encode()).hexdigest(), 16) % RING_SIZE


@dataclass
class VirtualNode:
    """A virtual node on the hash ring."""
    position: int
    physical_node_id: str
    virtual_node_index: int


@dataclass
class PhysicalNode:
    """A physical storage node."""
    node_id: str
    address: str
    virtual_node_count: int = VIRTUAL_NODES
    keys: set = field(default_factory=set)

    def to_dict(self):
        return {
            "node_id": self.node_id,
            "address": self.address,
            "virtual_node_count": self.virtual_node_count,
            "key_count": len(self.keys)
        }


class ConsistentHashRing:
    """
    Implementation of a consistent hash ring with virtual nodes.

    Virtual nodes help distribute keys more evenly across physical nodes
    by creating multiple points on the ring for each physical node.
    """

    def __init__(self):
        self.ring: list[tuple[int, VirtualNode]] = []  # Sorted by position
        self.physical_nodes: dict[str, PhysicalNode] = {}
        self.virtual_nodes_per_physical = VIRTUAL_NODES

    def _get_virtual_node_position(self, node_id: str, index: int) -> int:
        """Calculate position for a virtual node."""
        key = f"{node_id}:vn{index}"
        return consistent_hash(key)

    def add_node(self, node_id: str, address: str, virtual_node_count: Optional[int] = None) -> dict:
        """
        Add a physical node to the ring with its virtual nodes.
        Returns information about keys that need to be migrated.
        """
        if node_id in self.physical_nodes:
            raise ValueError(f"Node {node_id} already exists")

        vn_count = virtual_node_count or self.virtual_nodes_per_physical
        physical_node = PhysicalNode(
            node_id=node_id,
            address=address,
            virtual_node_count=vn_count
        )
        self.physical_nodes[node_id] = physical_node

        # Track keys that will be migrated to the new node
        keys_to_migrate = {}

        # Add virtual nodes to the ring
        for i in range(vn_count):
            position = self._get_virtual_node_position(node_id, i)
            vn = VirtualNode(position=position, physical_node_id=node_id, virtual_node_index=i)

            # Find keys that should now belong to this new virtual node
            if self.ring:
                # Find the next virtual node after this position
                idx = bisect.bisect_left([p for p, _ in self.ring], position)
                if idx < len(self.ring):
                    next_vn = self.ring[idx][1]
                    # Keys in range (prev_position, position] should migrate
                    prev_idx = (idx - 1) % len(self.ring)
                    prev_position = self.ring[prev_idx][0]

                    # Check if any keys need migration
                    from_node = next_vn.physical_node_id
                    if from_node != node_id and from_node in self.physical_nodes:
                        keys_to_check = list(self.physical_nodes[from_node].keys)
                        for key in keys_to_check:
                            key_pos = consistent_hash(key)
                            # Check if key now belongs to new node
                            if self._position_in_range(key_pos, prev_position, position):
                                if from_node not in keys_to_migrate:
                                    keys_to_migrate[from_node] = []
                                keys_to_migrate[from_node].append(key)

            # Insert virtual node in sorted position
            bisect.insort(self.ring, (position, vn))

        self._update_metrics()

        return {
            "node_id": node_id,
            "virtual_nodes_added": vn_count,
            "keys_to_migrate": keys_to_migrate
        }

    def _position_in_range(self, pos: int, start: int, end: int) -> bool:
        """Check if position is in range (start, end] handling wraparound."""
        if start < end:
            return start < pos <= end
        else:  # Wraparound case
            return pos > start or pos <= end

    def remove_node(self, node_id: str) -> dict:
        """
        Remove a physical node from the ring.
        Returns information about keys that need to be migrated.
        """
        if node_id not in self.physical_nodes:
            raise ValueError(f"Node {node_id} does not exist")

        physical_node = self.physical_nodes[node_id]
        keys_to_migrate = {}

        # Find keys that need to be migrated
        for key in physical_node.keys:
            key_pos = consistent_hash(key)
            # After removal, find new owner
            new_owner = self._find_node_after_removal(key_pos, node_id)
            if new_owner:
                if new_owner not in keys_to_migrate:
                    keys_to_migrate[new_owner] = []
                keys_to_migrate[new_owner].append(key)

        # Remove virtual nodes from ring
        self.ring = [(pos, vn) for pos, vn in self.ring if vn.physical_node_id != node_id]

        # Remove physical node
        del self.physical_nodes[node_id]

        self._update_metrics()

        return {
            "node_id": node_id,
            "keys_to_migrate": keys_to_migrate
        }

    def _find_node_after_removal(self, position: int, removed_node: str) -> Optional[str]:
        """Find the node that would own a position after a node is removed."""
        remaining_ring = [(pos, vn) for pos, vn in self.ring if vn.physical_node_id != removed_node]
        if not remaining_ring:
            return None

        positions = [p for p, _ in remaining_ring]
        idx = bisect.bisect_left(positions, position)
        if idx >= len(remaining_ring):
            idx = 0
        return remaining_ring[idx][1].physical_node_id

    def get_node_for_key(self, key: str) -> Optional[PhysicalNode]:
        """Find the physical node responsible for a key."""
        if not self.ring:
            return None

        start_time = time.time()

        key_position = consistent_hash(key)

        # Binary search for the first virtual node with position >= key_position
        positions = [p for p, _ in self.ring]
        idx = bisect.bisect_left(positions, key_position)

        # Wrap around if necessary
        if idx >= len(self.ring):
            idx = 0

        virtual_node = self.ring[idx][1]
        physical_node = self.physical_nodes[virtual_node.physical_node_id]

        # Record metrics
        LOOKUP_LATENCY.observe(time.time() - start_time)
        KEY_LOOKUPS.labels(target_node=physical_node.node_id).inc()

        return physical_node

    def assign_key(self, key: str) -> PhysicalNode:
        """Assign a key to a node and track it."""
        node = self.get_node_for_key(key)
        if node:
            node.keys.add(key)
            self._update_metrics()
        return node

    def get_ring_state(self) -> dict:
        """Get complete state of the hash ring."""
        segments = []
        total_keys = sum(len(n.keys) for n in self.physical_nodes.values())

        if self.ring:
            for i, (pos, vn) in enumerate(self.ring):
                prev_pos = self.ring[i - 1][0] if i > 0 else self.ring[-1][0]
                if prev_pos < pos:
                    segment_size = pos - prev_pos
                else:
                    segment_size = (RING_SIZE - prev_pos) + pos

                segments.append({
                    "position": pos,
                    "position_normalized": pos / RING_SIZE,
                    "physical_node_id": vn.physical_node_id,
                    "virtual_node_index": vn.virtual_node_index,
                    "segment_size": segment_size,
                    "segment_percentage": (segment_size / RING_SIZE) * 100
                })

        return {
            "ring_size": RING_SIZE,
            "total_virtual_nodes": len(self.ring),
            "physical_nodes": [n.to_dict() for n in self.physical_nodes.values()],
            "total_keys": total_keys,
            "segments": segments
        }

    def get_key_distribution(self) -> dict:
        """Get key distribution statistics across nodes."""
        distribution = {}
        total_keys = 0

        for node_id, node in self.physical_nodes.items():
            key_count = len(node.keys)
            total_keys += key_count
            distribution[node_id] = {
                "key_count": key_count,
                "virtual_nodes": node.virtual_node_count
            }

        # Calculate percentages
        for node_id in distribution:
            if total_keys > 0:
                distribution[node_id]["percentage"] = (distribution[node_id]["key_count"] / total_keys) * 100
            else:
                distribution[node_id]["percentage"] = 0

        # Calculate ideal distribution for comparison
        if self.physical_nodes:
            ideal_percentage = 100 / len(self.physical_nodes)
            for node_id in distribution:
                distribution[node_id]["deviation_from_ideal"] = abs(
                    distribution[node_id]["percentage"] - ideal_percentage
                )

        return {
            "total_keys": total_keys,
            "node_count": len(self.physical_nodes),
            "distribution": distribution
        }

    def _update_metrics(self):
        """Update Prometheus metrics."""
        NODE_COUNT.set(len(self.physical_nodes))
        TOTAL_VIRTUAL_NODES.set(len(self.ring))

        for node_id, node in self.physical_nodes.items():
            KEYS_PER_NODE.labels(node_id=node_id).set(len(node.keys))
            VIRTUAL_NODES_PER_NODE.labels(node_id=node_id).set(node.virtual_node_count)

            # Calculate segment size percentage
            node_virtual_nodes = [vn for _, vn in self.ring if vn.physical_node_id == node_id]
            if node_virtual_nodes and self.ring:
                # Sum up all segments owned by this node
                total_segment = 0
                for i, (pos, vn) in enumerate(self.ring):
                    if vn.physical_node_id == node_id:
                        prev_pos = self.ring[i - 1][0] if i > 0 else self.ring[-1][0]
                        if prev_pos < pos:
                            total_segment += pos - prev_pos
                        else:
                            total_segment += (RING_SIZE - prev_pos) + pos

                RING_SEGMENT_SIZE.labels(node_id=node_id).set((total_segment / RING_SIZE) * 100)


# Global hash ring instance
hash_ring = ConsistentHashRing()

# Request/Response models
class AddNodeRequest(BaseModel):
    node_id: str
    address: str
    virtual_nodes: Optional[int] = None


class KeyRequest(BaseModel):
    key: str


class KeyValueRequest(BaseModel):
    key: str
    value: str


class VirtualNodesConfig(BaseModel):
    count: int


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle management - initialize default nodes."""
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Default virtual nodes per physical node: {VIRTUAL_NODES}")

    # Initialize with default storage nodes
    default_nodes = [
        ("node-1", "http://storage-node-1:8080"),
        ("node-2", "http://storage-node-2:8080"),
        ("node-3", "http://storage-node-3:8080"),
    ]

    for node_id, address in default_nodes:
        try:
            hash_ring.add_node(node_id, address)
            logger.info(f"Added default node: {node_id} at {address}")
        except Exception as e:
            logger.error(f"Failed to add default node {node_id}: {e}")

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


# Ring Management Endpoints

@app.get("/ring")
async def get_ring():
    """Get the current state of the hash ring."""
    return hash_ring.get_ring_state()


@app.get("/ring/distribution")
async def get_distribution():
    """Get key distribution across nodes."""
    return hash_ring.get_key_distribution()


@app.get("/ring/nodes")
async def get_nodes():
    """Get list of all physical nodes."""
    return {
        "nodes": [n.to_dict() for n in hash_ring.physical_nodes.values()],
        "virtual_nodes_per_physical": hash_ring.virtual_nodes_per_physical
    }


@app.post("/ring/nodes")
async def add_node(request: AddNodeRequest):
    """Add a new node to the ring."""
    with tracer.start_as_current_span("add_node") as span:
        span.set_attribute("node_id", request.node_id)
        span.set_attribute("address", request.address)

        try:
            result = hash_ring.add_node(
                request.node_id,
                request.address,
                request.virtual_nodes
            )

            # Track key migrations
            for from_node, keys in result["keys_to_migrate"].items():
                for key in keys:
                    KEYS_MIGRATED.labels(from_node=from_node, to_node=request.node_id).inc()

            logger.info(f"Added node {request.node_id} with {result['virtual_nodes_added']} virtual nodes")
            return result
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.delete("/ring/nodes/{node_id}")
async def remove_node(node_id: str):
    """Remove a node from the ring."""
    with tracer.start_as_current_span("remove_node") as span:
        span.set_attribute("node_id", node_id)

        try:
            result = hash_ring.remove_node(node_id)

            # Track key migrations
            for to_node, keys in result["keys_to_migrate"].items():
                for key in keys:
                    KEYS_MIGRATED.labels(from_node=node_id, to_node=to_node).inc()

            logger.info(f"Removed node {node_id}")
            return result
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))


@app.put("/ring/virtual-nodes")
async def set_virtual_nodes(config: VirtualNodesConfig):
    """Set the default number of virtual nodes for new physical nodes."""
    hash_ring.virtual_nodes_per_physical = config.count
    logger.info(f"Updated default virtual nodes per physical node to {config.count}")
    return {"virtual_nodes_per_physical": hash_ring.virtual_nodes_per_physical}


# Key Routing Endpoints

@app.get("/lookup/{key}")
async def lookup_key(key: str):
    """Look up which node should handle a key."""
    with tracer.start_as_current_span("lookup_key") as span:
        span.set_attribute("key", key)

        node = hash_ring.get_node_for_key(key)
        if not node:
            raise HTTPException(status_code=503, detail="No nodes available")

        key_hash = consistent_hash(key)

        span.set_attribute("target_node", node.node_id)
        span.set_attribute("key_hash", key_hash)

        return {
            "key": key,
            "key_hash": key_hash,
            "key_position_normalized": key_hash / RING_SIZE,
            "target_node": node.to_dict()
        }


@app.post("/keys")
async def store_key(request: KeyValueRequest):
    """Store a key-value pair (routes to appropriate node)."""
    with tracer.start_as_current_span("store_key") as span:
        span.set_attribute("key", request.key)

        node = hash_ring.assign_key(request.key)
        if not node:
            raise HTTPException(status_code=503, detail="No nodes available")

        span.set_attribute("target_node", node.node_id)

        # Forward to storage node
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.post(
                    f"{node.address}/store",
                    json={"key": request.key, "value": request.value}
                )
                response.raise_for_status()
                storage_result = response.json()
            except httpx.RequestError as e:
                logger.error(f"Failed to store key {request.key} on {node.node_id}: {e}")
                # Still track the key locally even if storage fails
                storage_result = {"stored": False, "error": str(e)}

        return {
            "key": request.key,
            "key_hash": consistent_hash(request.key),
            "routed_to": node.node_id,
            "storage_result": storage_result
        }


@app.get("/keys/{key}")
async def get_key(key: str):
    """Get a value by key (routes to appropriate node)."""
    with tracer.start_as_current_span("get_key") as span:
        span.set_attribute("key", key)

        node = hash_ring.get_node_for_key(key)
        if not node:
            raise HTTPException(status_code=503, detail="No nodes available")

        span.set_attribute("target_node", node.node_id)

        # Fetch from storage node
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(f"{node.address}/get/{key}")
                if response.status_code == 404:
                    raise HTTPException(status_code=404, detail=f"Key {key} not found")
                response.raise_for_status()
                storage_result = response.json()
            except httpx.RequestError as e:
                logger.error(f"Failed to get key {key} from {node.node_id}: {e}")
                raise HTTPException(status_code=503, detail=f"Storage node unavailable: {e}")

        return {
            "key": key,
            "routed_to": node.node_id,
            "result": storage_result
        }


# Visualization Endpoints

@app.get("/ring/visualization")
async def get_ring_visualization():
    """Get data formatted for ring visualization."""
    state = hash_ring.get_ring_state()
    distribution = hash_ring.get_key_distribution()

    # Create visualization-friendly format
    nodes_summary = []
    for node in state["physical_nodes"]:
        node_id = node["node_id"]
        nodes_summary.append({
            "node_id": node_id,
            "virtual_nodes": node["virtual_node_count"],
            "keys": node["key_count"],
            "key_percentage": distribution["distribution"].get(node_id, {}).get("percentage", 0),
            "ring_ownership_percentage": sum(
                s["segment_percentage"] for s in state["segments"]
                if s["physical_node_id"] == node_id
            )
        })

    return {
        "ring_size": state["ring_size"],
        "total_virtual_nodes": state["total_virtual_nodes"],
        "total_keys": state["total_keys"],
        "nodes": nodes_summary,
        "segments": state["segments"][:50]  # Limit segments for visualization
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
