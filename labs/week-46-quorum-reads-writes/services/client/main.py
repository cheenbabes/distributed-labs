"""
Client - Interactive client for testing quorum reads and writes.

Provides a simple web UI and API for:
- Writing key-value pairs through the coordinator
- Reading values and observing quorum behavior
- Running consistency tests
- Visualizing replica state
"""
import asyncio
import logging
import os
import time
import random
import string
from contextlib import asynccontextmanager
from typing import Optional, List

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "client")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://coordinator:8000")
CLIENT_PORT = int(os.getenv("CLIENT_PORT", "8080"))

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

# Instrument httpx
HTTPXClientInstrumentor().instrument()

# Prometheus metrics
CLIENT_REQUESTS = Counter(
    "client_requests_total",
    "Total client requests",
    ["operation", "status"]
)
CLIENT_LATENCY = Histogram(
    "client_request_latency_seconds",
    "Client request latency",
    ["operation"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)


class WriteRequest(BaseModel):
    key: str
    value: str


class ConsistencyTestRequest(BaseModel):
    iterations: int = 10
    key_prefix: str = "test"


class ConsistencyTestResult(BaseModel):
    iterations: int
    successful_writes: int
    successful_reads: int
    read_after_write_matches: int
    consistency_rate: float
    avg_write_latency_ms: float
    avg_read_latency_ms: float
    errors: List[str]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Coordinator URL: {COORDINATOR_URL}")
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


@app.get("/", response_class=HTMLResponse)
async def index():
    """Simple web UI for testing."""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Quorum Client</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
            .container { max-width: 1200px; margin: 0 auto; }
            h1 { color: #333; }
            .section { background: white; padding: 20px; margin: 20px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            input, button { padding: 10px; margin: 5px; font-size: 14px; }
            input { border: 1px solid #ddd; border-radius: 4px; }
            button { background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }
            button:hover { background: #0056b3; }
            button.danger { background: #dc3545; }
            button.danger:hover { background: #c82333; }
            button.warning { background: #ffc107; color: #333; }
            pre { background: #f8f9fa; padding: 15px; border-radius: 4px; overflow-x: auto; }
            .result { margin-top: 15px; }
            .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
            .config-item { display: flex; align-items: center; gap: 10px; margin: 10px 0; }
            .config-item label { min-width: 100px; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Quorum Reads/Writes Lab</h1>

            <div class="grid">
                <div class="section">
                    <h2>Write</h2>
                    <input type="text" id="writeKey" placeholder="Key" value="mykey">
                    <input type="text" id="writeValue" placeholder="Value" value="myvalue">
                    <button onclick="write()">Write</button>
                    <div class="result"><pre id="writeResult"></pre></div>
                </div>

                <div class="section">
                    <h2>Read</h2>
                    <input type="text" id="readKey" placeholder="Key" value="mykey">
                    <button onclick="read()">Read</button>
                    <div class="result"><pre id="readResult"></pre></div>
                </div>
            </div>

            <div class="section">
                <h2>Quorum Configuration</h2>
                <div class="config-item">
                    <label>Read Quorum (R):</label>
                    <input type="number" id="readQuorum" min="1" max="5" value="3">
                </div>
                <div class="config-item">
                    <label>Write Quorum (W):</label>
                    <input type="number" id="writeQuorum" min="1" max="5" value="3">
                </div>
                <div class="config-item">
                    <label>Mode:</label>
                    <select id="quorumMode">
                        <option value="strict">Strict</option>
                        <option value="sloppy">Sloppy</option>
                    </select>
                </div>
                <button onclick="updateConfig()">Update Config</button>
                <button onclick="getConfig()">Refresh Config</button>
                <div class="result"><pre id="configResult"></pre></div>
            </div>

            <div class="section">
                <h2>Replica Status</h2>
                <button onclick="getReplicaStatus()">Check Status</button>
                <div class="result"><pre id="replicaStatus"></pre></div>
            </div>

            <div class="section">
                <h2>Failure Injection</h2>
                <div class="config-item">
                    <label>Replica ID:</label>
                    <select id="failureReplica">
                        <option value="1">Replica 1</option>
                        <option value="2">Replica 2</option>
                        <option value="3">Replica 3</option>
                        <option value="4">Replica 4</option>
                        <option value="5">Replica 5</option>
                    </select>
                </div>
                <div class="config-item">
                    <label>Failure Mode:</label>
                    <select id="failureMode">
                        <option value="none">None (Healthy)</option>
                        <option value="timeout">Timeout (Slow)</option>
                        <option value="error">Error (Always Fail)</option>
                        <option value="partial">Partial (50% Fail)</option>
                    </select>
                </div>
                <button class="danger" onclick="injectFailure()">Inject Failure</button>
                <button onclick="resetAllReplicas()">Reset All Replicas</button>
                <div class="result"><pre id="failureResult"></pre></div>
            </div>

            <div class="section">
                <h2>Consistency Test</h2>
                <div class="config-item">
                    <label>Iterations:</label>
                    <input type="number" id="testIterations" min="1" max="100" value="10">
                </div>
                <button onclick="runConsistencyTest()">Run Test</button>
                <div class="result"><pre id="testResult"></pre></div>
            </div>
        </div>

        <script>
            const COORDINATOR = '/api/coordinator';

            async function write() {
                const key = document.getElementById('writeKey').value;
                const value = document.getElementById('writeValue').value;
                const result = document.getElementById('writeResult');
                try {
                    const res = await fetch('/api/write', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({key, value})
                    });
                    result.textContent = JSON.stringify(await res.json(), null, 2);
                } catch (e) {
                    result.textContent = 'Error: ' + e.message;
                }
            }

            async function read() {
                const key = document.getElementById('readKey').value;
                const result = document.getElementById('readResult');
                try {
                    const res = await fetch('/api/read/' + key);
                    result.textContent = JSON.stringify(await res.json(), null, 2);
                } catch (e) {
                    result.textContent = 'Error: ' + e.message;
                }
            }

            async function getConfig() {
                const result = document.getElementById('configResult');
                try {
                    const res = await fetch('/api/config');
                    const data = await res.json();
                    result.textContent = JSON.stringify(data, null, 2);
                    document.getElementById('readQuorum').value = data.r;
                    document.getElementById('writeQuorum').value = data.w;
                    document.getElementById('quorumMode').value = data.mode;
                } catch (e) {
                    result.textContent = 'Error: ' + e.message;
                }
            }

            async function updateConfig() {
                const r = parseInt(document.getElementById('readQuorum').value);
                const w = parseInt(document.getElementById('writeQuorum').value);
                const mode = document.getElementById('quorumMode').value;
                const result = document.getElementById('configResult');
                try {
                    const res = await fetch('/api/config', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({r, w, mode})
                    });
                    result.textContent = JSON.stringify(await res.json(), null, 2);
                } catch (e) {
                    result.textContent = 'Error: ' + e.message;
                }
            }

            async function getReplicaStatus() {
                const result = document.getElementById('replicaStatus');
                try {
                    const res = await fetch('/api/replicas/status');
                    result.textContent = JSON.stringify(await res.json(), null, 2);
                } catch (e) {
                    result.textContent = 'Error: ' + e.message;
                }
            }

            async function injectFailure() {
                const replicaId = document.getElementById('failureReplica').value;
                const mode = document.getElementById('failureMode').value;
                const result = document.getElementById('failureResult');
                try {
                    const res = await fetch('/api/replica/' + replicaId + '/failure', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({mode})
                    });
                    result.textContent = JSON.stringify(await res.json(), null, 2);
                } catch (e) {
                    result.textContent = 'Error: ' + e.message;
                }
            }

            async function resetAllReplicas() {
                const result = document.getElementById('failureResult');
                try {
                    const res = await fetch('/api/replicas/reset', {method: 'POST'});
                    result.textContent = JSON.stringify(await res.json(), null, 2);
                } catch (e) {
                    result.textContent = 'Error: ' + e.message;
                }
            }

            async function runConsistencyTest() {
                const iterations = parseInt(document.getElementById('testIterations').value);
                const result = document.getElementById('testResult');
                result.textContent = 'Running test...';
                try {
                    const res = await fetch('/api/test/consistency', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({iterations})
                    });
                    result.textContent = JSON.stringify(await res.json(), null, 2);
                } catch (e) {
                    result.textContent = 'Error: ' + e.message;
                }
            }

            // Load config on page load
            getConfig();
            getReplicaStatus();
        </script>
    </body>
    </html>
    """


@app.post("/api/write")
async def api_write(request: WriteRequest):
    """Write through coordinator."""
    start_time = time.time()
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                f"{COORDINATOR_URL}/write",
                json={"key": request.key, "value": request.value}
            )
            duration = time.time() - start_time
            CLIENT_LATENCY.labels(operation="write").observe(duration)

            if response.status_code == 200:
                CLIENT_REQUESTS.labels(operation="write", status="success").inc()
                return response.json()
            else:
                CLIENT_REQUESTS.labels(operation="write", status="error").inc()
                return {"error": response.text, "status_code": response.status_code}
        except Exception as e:
            CLIENT_REQUESTS.labels(operation="write", status="error").inc()
            return {"error": str(e)}


@app.get("/api/read/{key}")
async def api_read(key: str):
    """Read through coordinator."""
    start_time = time.time()
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(f"{COORDINATOR_URL}/read/{key}")
            duration = time.time() - start_time
            CLIENT_LATENCY.labels(operation="read").observe(duration)

            if response.status_code == 200:
                CLIENT_REQUESTS.labels(operation="read", status="success").inc()
                return response.json()
            else:
                CLIENT_REQUESTS.labels(operation="read", status="error").inc()
                return {"error": response.text, "status_code": response.status_code}
        except Exception as e:
            CLIENT_REQUESTS.labels(operation="read", status="error").inc()
            return {"error": str(e)}


@app.get("/api/config")
async def api_get_config():
    """Get coordinator config."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{COORDINATOR_URL}/config")
        return response.json()


@app.post("/api/config")
async def api_update_config(config: dict):
    """Update coordinator config."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{COORDINATOR_URL}/config",
            json=config
        )
        return response.json()


@app.get("/api/replicas/status")
async def api_replica_status():
    """Get all replica statuses."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{COORDINATOR_URL}/replicas/status")
        return response.json()


@app.post("/api/replica/{replica_id}/failure")
async def api_inject_failure(replica_id: int, request: dict):
    """Inject failure into a specific replica."""
    replica_urls = {
        1: "http://lab46-replica-1:9001",
        2: "http://lab46-replica-2:9002",
        3: "http://lab46-replica-3:9003",
        4: "http://lab46-replica-4:9004",
        5: "http://lab46-replica-5:9005"
    }

    if replica_id not in replica_urls:
        raise HTTPException(status_code=400, detail=f"Invalid replica ID: {replica_id}")

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(
                f"{replica_urls[replica_id]}/admin/failure",
                json=request
            )
            return response.json()
        except Exception as e:
            return {"error": str(e), "replica_id": replica_id}


@app.post("/api/replicas/reset")
async def api_reset_all_replicas():
    """Reset all replicas to healthy state."""
    results = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for i in range(1, 6):
            try:
                response = await client.post(
                    f"http://lab46-replica-{i}:900{i}/admin/failure",
                    json={"mode": "none"}
                )
                results.append({"replica_id": i, "status": "reset", "response": response.json()})
            except Exception as e:
                results.append({"replica_id": i, "status": "error", "error": str(e)})
    return {"results": results}


@app.post("/api/test/consistency")
async def api_consistency_test(request: ConsistencyTestRequest) -> ConsistencyTestResult:
    """
    Run a consistency test: write then immediately read.
    Measures how often read-after-write returns the same value.
    """
    successful_writes = 0
    successful_reads = 0
    matches = 0
    errors = []
    write_latencies = []
    read_latencies = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(request.iterations):
            key = f"{request.key_prefix}_{i}_{int(time.time()*1000)}"
            value = ''.join(random.choices(string.ascii_letters, k=10))

            # Write
            write_start = time.time()
            try:
                write_response = await client.post(
                    f"{COORDINATOR_URL}/write",
                    json={"key": key, "value": value}
                )
                write_latencies.append((time.time() - write_start) * 1000)

                if write_response.status_code == 200:
                    successful_writes += 1
                else:
                    errors.append(f"Write failed for {key}: {write_response.text}")
                    continue
            except Exception as e:
                errors.append(f"Write error for {key}: {str(e)}")
                continue

            # Small delay to let replicas sync (optional - remove to test strict consistency)
            # await asyncio.sleep(0.01)

            # Read
            read_start = time.time()
            try:
                read_response = await client.get(f"{COORDINATOR_URL}/read/{key}")
                read_latencies.append((time.time() - read_start) * 1000)

                if read_response.status_code == 200:
                    successful_reads += 1
                    read_data = read_response.json()
                    if read_data.get("value") == value:
                        matches += 1
                    else:
                        errors.append(f"Value mismatch for {key}: wrote '{value}', read '{read_data.get('value')}'")
                else:
                    errors.append(f"Read failed for {key}: {read_response.text}")
            except Exception as e:
                errors.append(f"Read error for {key}: {str(e)}")

    return ConsistencyTestResult(
        iterations=request.iterations,
        successful_writes=successful_writes,
        successful_reads=successful_reads,
        read_after_write_matches=matches,
        consistency_rate=matches / request.iterations if request.iterations > 0 else 0,
        avg_write_latency_ms=sum(write_latencies) / len(write_latencies) if write_latencies else 0,
        avg_read_latency_ms=sum(read_latencies) / len(read_latencies) if read_latencies else 0,
        errors=errors[:10]  # Limit errors to prevent huge responses
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=CLIENT_PORT)
