"""
Client Service - Sends distributed transactions to the 2PC coordinator.

This simulates an application that needs to perform distributed transactions
across multiple databases.
"""
import asyncio
import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from pydantic import BaseModel


# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "client")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
COORDINATOR_URL = os.getenv("COORDINATOR_URL", "http://coordinator:8000")

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
TRANSACTION_REQUESTS = Counter(
    "client_transaction_requests_total",
    "Total transaction requests",
    ["result"]  # success, failure, blocked
)
TRANSACTION_LATENCY = Histogram(
    "client_transaction_duration_seconds",
    "Transaction request latency",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0]
)


class TransferRequest(BaseModel):
    from_account: str
    to_account: str
    amount: float


class BatchTransferRequest(BaseModel):
    count: int = 5
    delay_ms: int = 100


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Coordinator URL: {COORDINATOR_URL}")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title="2PC Client", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/transfer")
async def transfer(request: TransferRequest):
    """
    Perform a distributed transfer across accounts.

    This is a common use case for 2PC: updating balances in multiple databases
    as part of a single atomic transaction.
    """
    current_span = trace.get_current_span()

    transaction_id = str(uuid.uuid4())[:8]
    current_span.set_attribute("transfer.id", transaction_id)
    current_span.set_attribute("transfer.from", request.from_account)
    current_span.set_attribute("transfer.to", request.to_account)
    current_span.set_attribute("transfer.amount", request.amount)

    start_time = time.time()

    logger.info(
        f"Transfer {transaction_id}: {request.from_account} -> {request.to_account}, "
        f"amount: {request.amount}"
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{COORDINATOR_URL}/transaction",
                json={
                    "data": {
                        "type": "transfer",
                        "transfer_id": transaction_id,
                        "from_account": request.from_account,
                        "to_account": request.to_account,
                        "amount": request.amount,
                        "timestamp": time.time()
                    }
                }
            )

            duration = time.time() - start_time
            TRANSACTION_LATENCY.observe(duration)

            if response.status_code == 200:
                result = response.json()
                status = result.get("status", "unknown")

                if status == "committed":
                    TRANSACTION_REQUESTS.labels(result="success").inc()
                    logger.info(f"Transfer {transaction_id}: COMMITTED in {duration*1000:.0f}ms")
                elif status == "aborted":
                    TRANSACTION_REQUESTS.labels(result="failure").inc()
                    logger.warning(f"Transfer {transaction_id}: ABORTED in {duration*1000:.0f}ms")
                else:
                    TRANSACTION_REQUESTS.labels(result="failure").inc()
                    logger.warning(f"Transfer {transaction_id}: Unknown status {status}")

                return {
                    "transfer_id": transaction_id,
                    "status": status,
                    "duration_ms": round(duration * 1000, 2),
                    "coordinator_response": result
                }
            else:
                error = response.json() if response.content else {"error": "unknown"}
                error_detail = error.get("detail", {})

                if isinstance(error_detail, dict) and "blocked" in error_detail.get("error", ""):
                    TRANSACTION_REQUESTS.labels(result="blocked").inc()
                    logger.error(f"Transfer {transaction_id}: BLOCKED - coordinator failed!")
                else:
                    TRANSACTION_REQUESTS.labels(result="failure").inc()
                    logger.error(f"Transfer {transaction_id}: Failed with {response.status_code}")

                return {
                    "transfer_id": transaction_id,
                    "status": "error",
                    "duration_ms": round(duration * 1000, 2),
                    "error": error
                }

    except httpx.TimeoutException:
        duration = time.time() - start_time
        TRANSACTION_LATENCY.observe(duration)
        TRANSACTION_REQUESTS.labels(result="blocked").inc()
        logger.error(f"Transfer {transaction_id}: TIMEOUT after {duration*1000:.0f}ms - likely blocked!")
        return {
            "transfer_id": transaction_id,
            "status": "timeout",
            "duration_ms": round(duration * 1000, 2),
            "message": "Request timed out - the transaction may be blocked waiting for coordinator"
        }

    except Exception as e:
        duration = time.time() - start_time
        TRANSACTION_LATENCY.observe(duration)
        TRANSACTION_REQUESTS.labels(result="failure").inc()
        logger.error(f"Transfer {transaction_id}: Error - {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/batch-transfer")
async def batch_transfer(request: BatchTransferRequest):
    """
    Perform multiple transfers to generate load.
    Useful for demonstrating blocking scenarios.
    """
    accounts = ["account-A", "account-B", "account-C", "account-D", "account-E"]
    results = []

    for i in range(request.count):
        from_acc = random.choice(accounts)
        to_acc = random.choice([a for a in accounts if a != from_acc])
        amount = round(random.uniform(10, 1000), 2)

        try:
            result = await transfer(TransferRequest(
                from_account=from_acc,
                to_account=to_acc,
                amount=amount
            ))
            results.append(result)
        except Exception as e:
            results.append({
                "error": str(e),
                "from_account": from_acc,
                "to_account": to_acc,
                "amount": amount
            })

        if request.delay_ms > 0:
            await asyncio.sleep(request.delay_ms / 1000.0)

    # Summarize results
    committed = sum(1 for r in results if r.get("status") == "committed")
    aborted = sum(1 for r in results if r.get("status") == "aborted")
    blocked = sum(1 for r in results if r.get("status") in ["blocked", "timeout"])
    errors = sum(1 for r in results if r.get("status") == "error" or "error" in r)

    return {
        "total": request.count,
        "committed": committed,
        "aborted": aborted,
        "blocked": blocked,
        "errors": errors,
        "results": results
    }


@app.get("/coordinator/status")
async def get_coordinator_status():
    """Get the coordinator's current state."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Get transactions
            txn_response = await client.get(f"{COORDINATOR_URL}/transactions")
            # Get config
            config_response = await client.get(f"{COORDINATOR_URL}/admin/config")

            return {
                "coordinator_url": COORDINATOR_URL,
                "transactions": txn_response.json() if txn_response.status_code == 200 else None,
                "config": config_response.json() if config_response.status_code == 200 else None
            }
    except Exception as e:
        return {
            "coordinator_url": COORDINATOR_URL,
            "error": str(e),
            "status": "unreachable"
        }


@app.get("/participants/status")
async def get_participants_status():
    """Get all participants' current state including blocked transactions."""
    participants = [
        "http://lab41-participant-a:8001",
        "http://lab41-participant-b:8002",
        "http://lab41-participant-c:8003"
    ]

    results = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for url in participants:
            try:
                blocked_response = await client.get(f"{url}/blocked")
                config_response = await client.get(f"{url}/admin/config")

                results[url] = {
                    "blocked": blocked_response.json() if blocked_response.status_code == 200 else None,
                    "config": config_response.json() if config_response.status_code == 200 else None,
                    "status": "reachable"
                }
            except Exception as e:
                results[url] = {
                    "error": str(e),
                    "status": "unreachable"
                }

    return results


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
