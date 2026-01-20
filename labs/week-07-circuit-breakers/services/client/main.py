"""
Client Service - Implements a circuit breaker pattern from scratch.

This service demonstrates the three states of a circuit breaker:
- CLOSED: Normal operation, requests pass through
- OPEN: Circuit is tripped, requests are rejected immediately
- HALF_OPEN: Testing if backend has recovered

The circuit breaker tracks failures and opens when threshold is exceeded,
then periodically allows test requests to check for recovery.
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from enum import Enum
from threading import Lock
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
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "client")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8001")
PORT = int(os.getenv("PORT", "8000"))

# Circuit breaker configuration
FAILURE_THRESHOLD = int(os.getenv("CIRCUIT_FAILURE_THRESHOLD", "5"))
SUCCESS_THRESHOLD = int(os.getenv("CIRCUIT_SUCCESS_THRESHOLD", "3"))
TIMEOUT_SECONDS = int(os.getenv("CIRCUIT_TIMEOUT_SECONDS", "30"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "5.0"))

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
REQUEST_COUNT = Counter(
    "client_requests_total",
    "Total client requests",
    ["result"]  # success, failure, rejected
)
CIRCUIT_STATE = Gauge(
    "circuit_breaker_state",
    "Circuit breaker state (0=CLOSED, 1=OPEN, 2=HALF_OPEN)"
)
CIRCUIT_FAILURES = Gauge(
    "circuit_breaker_failure_count",
    "Current failure count in circuit breaker"
)
CIRCUIT_SUCCESSES = Gauge(
    "circuit_breaker_success_count",
    "Current success count in circuit breaker (for half-open state)"
)
CIRCUIT_STATE_CHANGES = Counter(
    "circuit_breaker_state_changes_total",
    "Total circuit breaker state changes",
    ["from_state", "to_state"]
)
REQUEST_LATENCY = Histogram(
    "client_request_duration_seconds",
    "Client request latency",
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """
    A circuit breaker implementation with three states.

    State Transitions:
    - CLOSED -> OPEN: When failure_count >= failure_threshold
    - OPEN -> HALF_OPEN: After timeout_seconds have passed
    - HALF_OPEN -> CLOSED: When success_count >= success_threshold
    - HALF_OPEN -> OPEN: On any failure
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        success_threshold: int = 3,
        timeout_seconds: int = 30
    ):
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout_seconds = timeout_seconds

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._last_state_change: float = time.time()
        self._lock = Lock()

        # Initialize metrics
        CIRCUIT_STATE.set(0)
        CIRCUIT_FAILURES.set(0)
        CIRCUIT_SUCCESSES.set(0)

        logger.info(
            f"Circuit breaker initialized: failure_threshold={failure_threshold}, "
            f"success_threshold={success_threshold}, timeout={timeout_seconds}s"
        )

    @property
    def state(self) -> CircuitState:
        """Get current state, checking for timeout transition."""
        with self._lock:
            if self._state == CircuitState.OPEN:
                # Check if we should transition to HALF_OPEN
                if self._last_failure_time is not None:
                    elapsed = time.time() - self._last_failure_time
                    if elapsed >= self.timeout_seconds:
                        self._transition_to(CircuitState.HALF_OPEN)
            return self._state

    def _transition_to(self, new_state: CircuitState):
        """Transition to a new state and update metrics."""
        old_state = self._state
        if old_state == new_state:
            return

        self._state = new_state
        self._last_state_change = time.time()

        # Update Prometheus metrics
        state_value = {
            CircuitState.CLOSED: 0,
            CircuitState.OPEN: 1,
            CircuitState.HALF_OPEN: 2
        }[new_state]
        CIRCUIT_STATE.set(state_value)
        CIRCUIT_STATE_CHANGES.labels(
            from_state=old_state.value,
            to_state=new_state.value
        ).inc()

        logger.info(f"Circuit state changed: {old_state.value} -> {new_state.value}")

    def can_execute(self) -> bool:
        """Check if a request can be executed."""
        current_state = self.state  # This may trigger OPEN -> HALF_OPEN transition

        if current_state == CircuitState.CLOSED:
            return True
        elif current_state == CircuitState.HALF_OPEN:
            return True  # Allow test requests in half-open state
        else:  # OPEN
            return False

    def record_success(self):
        """Record a successful request."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                CIRCUIT_SUCCESSES.set(self._success_count)
                logger.info(
                    f"Success in HALF_OPEN state: {self._success_count}/{self.success_threshold}"
                )

                if self._success_count >= self.success_threshold:
                    self._success_count = 0
                    self._failure_count = 0
                    CIRCUIT_FAILURES.set(0)
                    CIRCUIT_SUCCESSES.set(0)
                    self._transition_to(CircuitState.CLOSED)
            else:
                # In CLOSED state, a success resets the failure count
                if self._failure_count > 0:
                    self._failure_count = 0
                    CIRCUIT_FAILURES.set(0)

    def record_failure(self):
        """Record a failed request."""
        with self._lock:
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                # Any failure in HALF_OPEN immediately opens the circuit
                self._success_count = 0
                CIRCUIT_SUCCESSES.set(0)
                self._transition_to(CircuitState.OPEN)
            else:
                # CLOSED state
                self._failure_count += 1
                CIRCUIT_FAILURES.set(self._failure_count)
                logger.info(
                    f"Failure recorded: {self._failure_count}/{self.failure_threshold}"
                )

                if self._failure_count >= self.failure_threshold:
                    self._transition_to(CircuitState.OPEN)

    def get_status(self) -> dict:
        """Get current circuit breaker status."""
        with self._lock:
            return {
                "state": self.state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "failure_threshold": self.failure_threshold,
                "success_threshold": self.success_threshold,
                "timeout_seconds": self.timeout_seconds,
                "last_failure_time": (
                    datetime.fromtimestamp(self._last_failure_time).isoformat()
                    if self._last_failure_time else None
                ),
                "last_state_change": datetime.fromtimestamp(self._last_state_change).isoformat(),
                "seconds_since_last_failure": (
                    round(time.time() - self._last_failure_time, 1)
                    if self._last_failure_time else None
                )
            }

    def reset(self):
        """Reset circuit breaker to initial state."""
        with self._lock:
            old_state = self._state
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None
            self._last_state_change = time.time()

            CIRCUIT_STATE.set(0)
            CIRCUIT_FAILURES.set(0)
            CIRCUIT_SUCCESSES.set(0)

            logger.info(f"Circuit breaker reset from {old_state.value} to CLOSED")


# Global circuit breaker instance
circuit_breaker = CircuitBreaker(
    failure_threshold=FAILURE_THRESHOLD,
    success_threshold=SUCCESS_THRESHOLD,
    timeout_seconds=TIMEOUT_SECONDS
)


class CircuitConfig(BaseModel):
    failure_threshold: int = FAILURE_THRESHOLD
    success_threshold: int = SUCCESS_THRESHOLD
    timeout_seconds: int = TIMEOUT_SECONDS


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up on port {PORT}")
    logger.info(f"Backend URL: {BACKEND_URL}")
    logger.info(
        f"Circuit breaker config: failure_threshold={FAILURE_THRESHOLD}, "
        f"success_threshold={SUCCESS_THRESHOLD}, timeout={TIMEOUT_SECONDS}s"
    )
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


@app.get("/circuit/status")
async def circuit_status():
    """Get current circuit breaker status."""
    return circuit_breaker.get_status()


@app.post("/circuit/reset")
async def circuit_reset():
    """Reset circuit breaker to closed state."""
    circuit_breaker.reset()
    return circuit_breaker.get_status()


@app.post("/circuit/config")
async def circuit_config(config: CircuitConfig):
    """Update circuit breaker configuration and reset."""
    global circuit_breaker
    circuit_breaker = CircuitBreaker(
        failure_threshold=config.failure_threshold,
        success_threshold=config.success_threshold,
        timeout_seconds=config.timeout_seconds
    )
    return circuit_breaker.get_status()


@app.get("/api/data")
async def get_data():
    """
    Main API endpoint that calls the backend through the circuit breaker.

    Returns:
    - Success response from backend if circuit allows and backend responds
    - 503 if circuit is OPEN (fast fail)
    - 503 if backend fails (and records failure to circuit breaker)
    """
    start_time = time.time()
    current_span = trace.get_current_span()

    state = circuit_breaker.state
    current_span.set_attribute("circuit_state", state.value)

    # Check if circuit allows the request
    if not circuit_breaker.can_execute():
        REQUEST_COUNT.labels(result="rejected").inc()
        current_span.set_attribute("circuit_rejected", True)
        logger.warning(f"Request rejected: circuit is {state.value}")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Circuit breaker is OPEN",
                "message": "Service temporarily unavailable, failing fast",
                "circuit_state": state.value,
                "retry_after_seconds": circuit_breaker.timeout_seconds
            }
        )

    # Attempt the request
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(f"{BACKEND_URL}/api/data")

            if response.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"Backend returned {response.status_code}",
                    request=response.request,
                    response=response
                )

            result = response.json()

        # Success!
        circuit_breaker.record_success()
        REQUEST_COUNT.labels(result="success").inc()

        duration = time.time() - start_time
        REQUEST_LATENCY.observe(duration)
        current_span.set_attribute("success", True)

        return {
            "service": SERVICE_NAME,
            "circuit_state": circuit_breaker.state.value,
            "backend_response": result,
            "latency_ms": round(duration * 1000, 2)
        }

    except (httpx.HTTPError, httpx.TimeoutException) as e:
        # Failure - record it
        circuit_breaker.record_failure()
        REQUEST_COUNT.labels(result="failure").inc()

        duration = time.time() - start_time
        REQUEST_LATENCY.observe(duration)

        error_type = type(e).__name__
        current_span.set_attribute("error", True)
        current_span.set_attribute("error_type", error_type)

        logger.error(f"Backend request failed: {error_type} - {str(e)}")

        raise HTTPException(
            status_code=503,
            detail={
                "error": "Backend request failed",
                "error_type": error_type,
                "circuit_state": circuit_breaker.state.value,
                "failure_count": circuit_breaker.get_status()["failure_count"]
            }
        )


@app.get("/api/data/batch")
async def get_data_batch(count: int = 10):
    """
    Make multiple requests to demonstrate circuit breaker behavior.

    This endpoint is useful for demos - it makes `count` sequential requests
    and returns the results of each, showing how the circuit breaker state
    changes over time.
    """
    results = []

    for i in range(count):
        start = time.time()
        try:
            # Make request through the main endpoint logic
            if not circuit_breaker.can_execute():
                results.append({
                    "request": i + 1,
                    "status": "rejected",
                    "circuit_state": circuit_breaker.state.value,
                    "latency_ms": round((time.time() - start) * 1000, 2)
                })
                continue

            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(f"{BACKEND_URL}/api/data")

                if response.status_code >= 500:
                    circuit_breaker.record_failure()
                    results.append({
                        "request": i + 1,
                        "status": "failure",
                        "status_code": response.status_code,
                        "circuit_state": circuit_breaker.state.value,
                        "latency_ms": round((time.time() - start) * 1000, 2)
                    })
                else:
                    circuit_breaker.record_success()
                    results.append({
                        "request": i + 1,
                        "status": "success",
                        "circuit_state": circuit_breaker.state.value,
                        "latency_ms": round((time.time() - start) * 1000, 2)
                    })

        except Exception as e:
            circuit_breaker.record_failure()
            results.append({
                "request": i + 1,
                "status": "failure",
                "error": str(e),
                "circuit_state": circuit_breaker.state.value,
                "latency_ms": round((time.time() - start) * 1000, 2)
            })

        # Small delay between requests
        await asyncio.sleep(0.1)

    return {
        "total_requests": count,
        "final_circuit_state": circuit_breaker.state.value,
        "circuit_status": circuit_breaker.get_status(),
        "results": results
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
