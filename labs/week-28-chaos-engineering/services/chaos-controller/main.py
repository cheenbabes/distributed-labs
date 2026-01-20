"""
Chaos Controller - Central chaos engineering management service.

This service manages chaos experiments across the distributed system:
- Latency injection
- Error injection
- Network partition simulation (via connection dropping)
- Resource exhaustion simulation

It tracks experiments, steady state metrics, and provides rollback capabilities.
"""
import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel, Field
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "chaos-controller")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab28-otel-collector:4317")
TARGET_SERVICES = os.getenv("TARGET_SERVICES", "").split(",")

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
EXPERIMENTS_TOTAL = Counter(
    "chaos_experiments_total",
    "Total chaos experiments executed",
    ["experiment_type", "target_service", "status"]
)
ACTIVE_EXPERIMENTS = Gauge(
    "chaos_active_experiments",
    "Currently active chaos experiments",
    ["experiment_type", "target_service"]
)
STEADY_STATE_VIOLATIONS = Counter(
    "steady_state_violations_total",
    "Total steady state hypothesis violations",
    ["metric_name", "target_service"]
)
EXPERIMENT_DURATION = Histogram(
    "chaos_experiment_duration_seconds",
    "Duration of chaos experiments",
    ["experiment_type"],
    buckets=[10, 30, 60, 120, 300, 600, 1800]
)
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


# Enums and Models
class ChaosType(str, Enum):
    LATENCY = "latency"
    ERROR = "error"
    NETWORK_PARTITION = "network_partition"
    RESOURCE_EXHAUSTION = "resource_exhaustion"


class ExperimentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class SteadyStateHypothesis(BaseModel):
    """Defines what 'normal' looks like for the system."""
    name: str = Field(..., description="Name of the hypothesis")
    description: str = Field(..., description="Human-readable description")
    metric_name: str = Field(..., description="Metric to monitor")
    threshold_type: str = Field(..., description="Type: max, min, range")
    threshold_value: float = Field(..., description="Threshold value")
    threshold_max: Optional[float] = Field(None, description="Max value for range type")


class ChaosExperiment(BaseModel):
    """A chaos experiment definition."""
    name: str = Field(..., description="Experiment name")
    hypothesis: SteadyStateHypothesis = Field(..., description="Steady state hypothesis")
    chaos_type: ChaosType = Field(..., description="Type of chaos to inject")
    target_service: str = Field(..., description="Service to target")
    magnitude: float = Field(..., description="Magnitude of chaos (ms for latency, % for errors)")
    duration_seconds: int = Field(60, description="How long to run the experiment")
    rollback_on_violation: bool = Field(True, description="Auto-rollback if hypothesis violated")


class ExperimentResult(BaseModel):
    """Result of a chaos experiment."""
    experiment_id: str
    name: str
    status: ExperimentStatus
    started_at: str
    ended_at: Optional[str] = None
    hypothesis_violated: bool = False
    violation_details: Optional[str] = None
    metrics_collected: Dict = {}


# In-memory state (in production, use a database)
active_chaos: Dict[str, Dict] = {}  # service -> chaos config
experiments: Dict[str, ExperimentResult] = {}
steady_state_baselines: Dict[str, Dict] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Target services: {TARGET_SERVICES}")
    yield
    # Cleanup: disable all chaos on shutdown
    logger.info("Cleaning up active chaos experiments...")
    active_chaos.clear()
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(
    title="Chaos Controller",
    description="Central chaos engineering management for distributed systems",
    lifespan=lifespan
)
FastAPIInstrumentor.instrument_app(app)


# ===========================================
# Health and Metrics Endpoints
# ===========================================

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "active_experiments": len([e for e in experiments.values() if e.status == ExperimentStatus.RUNNING])
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ===========================================
# Chaos State Query (for target services)
# ===========================================

@app.get("/chaos/state/{service_name}")
async def get_chaos_state(service_name: str):
    """
    Target services poll this endpoint to get their current chaos configuration.
    This allows centralized chaos control without modifying service code.
    """
    if service_name in active_chaos:
        return active_chaos[service_name]
    return {
        "chaos_enabled": False,
        "latency_ms": 0,
        "error_rate": 0.0,
        "network_partition": False,
        "cpu_load": 0.0,
        "memory_load": 0.0
    }


# ===========================================
# Direct Chaos Injection (Simple API)
# ===========================================

class LatencyInjection(BaseModel):
    enabled: bool
    latency_ms: int = 0
    jitter_ms: int = 0  # Random variation


class ErrorInjection(BaseModel):
    enabled: bool
    error_rate: float = 0.0  # 0.0 to 1.0
    error_code: int = 500
    error_message: str = "Chaos-induced error"


class NetworkPartition(BaseModel):
    enabled: bool
    drop_rate: float = 0.0  # 0.0 to 1.0 (probability of dropping request)
    timeout_ms: int = 30000


class ResourceExhaustion(BaseModel):
    enabled: bool
    cpu_load: float = 0.0  # 0.0 to 1.0
    memory_mb: int = 0


@app.post("/chaos/inject/latency/{service_name}")
async def inject_latency(service_name: str, config: LatencyInjection):
    """Inject latency into a specific service."""
    if service_name not in active_chaos:
        active_chaos[service_name] = {"chaos_enabled": False}

    active_chaos[service_name].update({
        "chaos_enabled": config.enabled,
        "latency_ms": config.latency_ms if config.enabled else 0,
        "jitter_ms": config.jitter_ms if config.enabled else 0
    })

    status = "enabled" if config.enabled else "disabled"
    EXPERIMENTS_TOTAL.labels(
        experiment_type="latency",
        target_service=service_name,
        status=status
    ).inc()

    if config.enabled:
        ACTIVE_EXPERIMENTS.labels(
            experiment_type="latency",
            target_service=service_name
        ).set(1)
    else:
        ACTIVE_EXPERIMENTS.labels(
            experiment_type="latency",
            target_service=service_name
        ).set(0)

    logger.info(f"Latency injection {status} for {service_name}: {config.latency_ms}ms (+/- {config.jitter_ms}ms)")
    return {"status": status, "service": service_name, "config": config.dict()}


@app.post("/chaos/inject/error/{service_name}")
async def inject_error(service_name: str, config: ErrorInjection):
    """Inject errors into a specific service."""
    if service_name not in active_chaos:
        active_chaos[service_name] = {"chaos_enabled": False}

    active_chaos[service_name].update({
        "chaos_enabled": config.enabled,
        "error_rate": config.error_rate if config.enabled else 0.0,
        "error_code": config.error_code,
        "error_message": config.error_message
    })

    status = "enabled" if config.enabled else "disabled"
    EXPERIMENTS_TOTAL.labels(
        experiment_type="error",
        target_service=service_name,
        status=status
    ).inc()

    if config.enabled:
        ACTIVE_EXPERIMENTS.labels(
            experiment_type="error",
            target_service=service_name
        ).set(1)
    else:
        ACTIVE_EXPERIMENTS.labels(
            experiment_type="error",
            target_service=service_name
        ).set(0)

    logger.info(f"Error injection {status} for {service_name}: {config.error_rate*100}% rate, code {config.error_code}")
    return {"status": status, "service": service_name, "config": config.dict()}


@app.post("/chaos/inject/network/{service_name}")
async def inject_network_partition(service_name: str, config: NetworkPartition):
    """Simulate network partition for a specific service."""
    if service_name not in active_chaos:
        active_chaos[service_name] = {"chaos_enabled": False}

    active_chaos[service_name].update({
        "chaos_enabled": config.enabled,
        "network_partition": config.enabled,
        "drop_rate": config.drop_rate if config.enabled else 0.0,
        "timeout_ms": config.timeout_ms
    })

    status = "enabled" if config.enabled else "disabled"
    EXPERIMENTS_TOTAL.labels(
        experiment_type="network_partition",
        target_service=service_name,
        status=status
    ).inc()

    if config.enabled:
        ACTIVE_EXPERIMENTS.labels(
            experiment_type="network_partition",
            target_service=service_name
        ).set(1)
    else:
        ACTIVE_EXPERIMENTS.labels(
            experiment_type="network_partition",
            target_service=service_name
        ).set(0)

    logger.info(f"Network partition {status} for {service_name}: {config.drop_rate*100}% drop rate")
    return {"status": status, "service": service_name, "config": config.dict()}


@app.post("/chaos/inject/resource/{service_name}")
async def inject_resource_exhaustion(service_name: str, config: ResourceExhaustion):
    """Simulate resource exhaustion for a specific service."""
    if service_name not in active_chaos:
        active_chaos[service_name] = {"chaos_enabled": False}

    active_chaos[service_name].update({
        "chaos_enabled": config.enabled,
        "cpu_load": config.cpu_load if config.enabled else 0.0,
        "memory_mb": config.memory_mb if config.enabled else 0
    })

    status = "enabled" if config.enabled else "disabled"
    EXPERIMENTS_TOTAL.labels(
        experiment_type="resource_exhaustion",
        target_service=service_name,
        status=status
    ).inc()

    if config.enabled:
        ACTIVE_EXPERIMENTS.labels(
            experiment_type="resource_exhaustion",
            target_service=service_name
        ).set(1)
    else:
        ACTIVE_EXPERIMENTS.labels(
            experiment_type="resource_exhaustion",
            target_service=service_name
        ).set(0)

    logger.info(f"Resource exhaustion {status} for {service_name}: CPU {config.cpu_load*100}%, Memory {config.memory_mb}MB")
    return {"status": status, "service": service_name, "config": config.dict()}


# ===========================================
# Chaos Rollback
# ===========================================

@app.post("/chaos/rollback/{service_name}")
async def rollback_chaos(service_name: str):
    """Disable all chaos for a specific service."""
    if service_name in active_chaos:
        active_chaos[service_name] = {
            "chaos_enabled": False,
            "latency_ms": 0,
            "jitter_ms": 0,
            "error_rate": 0.0,
            "network_partition": False,
            "drop_rate": 0.0,
            "cpu_load": 0.0,
            "memory_mb": 0
        }

        # Reset all active experiment gauges
        for exp_type in ["latency", "error", "network_partition", "resource_exhaustion"]:
            ACTIVE_EXPERIMENTS.labels(
                experiment_type=exp_type,
                target_service=service_name
            ).set(0)

        logger.info(f"Rolled back all chaos for {service_name}")
        return {"status": "rolled_back", "service": service_name}

    return {"status": "no_active_chaos", "service": service_name}


@app.post("/chaos/rollback-all")
async def rollback_all_chaos():
    """Emergency: Disable all chaos across all services."""
    rolled_back = list(active_chaos.keys())
    active_chaos.clear()

    logger.warning("EMERGENCY ROLLBACK: All chaos disabled across all services")
    return {"status": "all_rolled_back", "services": rolled_back}


# ===========================================
# Experiment Management (Hypothesis-Driven)
# ===========================================

@app.post("/experiments/run")
async def run_experiment(experiment: ChaosExperiment):
    """
    Run a complete chaos experiment with hypothesis validation.

    This follows the chaos engineering process:
    1. Define steady state hypothesis
    2. Inject chaos
    3. Monitor for violations
    4. Auto-rollback if needed
    5. Record results
    """
    experiment_id = str(uuid.uuid4())[:8]

    result = ExperimentResult(
        experiment_id=experiment_id,
        name=experiment.name,
        status=ExperimentStatus.RUNNING,
        started_at=datetime.utcnow().isoformat()
    )
    experiments[experiment_id] = result

    logger.info(f"Starting experiment {experiment_id}: {experiment.name}")
    logger.info(f"Hypothesis: {experiment.hypothesis.description}")
    logger.info(f"Target: {experiment.target_service}, Type: {experiment.chaos_type}")

    try:
        # Start chaos injection based on type
        if experiment.chaos_type == ChaosType.LATENCY:
            await inject_latency(
                experiment.target_service,
                LatencyInjection(enabled=True, latency_ms=int(experiment.magnitude))
            )
        elif experiment.chaos_type == ChaosType.ERROR:
            await inject_error(
                experiment.target_service,
                ErrorInjection(enabled=True, error_rate=experiment.magnitude / 100.0)
            )
        elif experiment.chaos_type == ChaosType.NETWORK_PARTITION:
            await inject_network_partition(
                experiment.target_service,
                NetworkPartition(enabled=True, drop_rate=experiment.magnitude / 100.0)
            )
        elif experiment.chaos_type == ChaosType.RESOURCE_EXHAUSTION:
            await inject_resource_exhaustion(
                experiment.target_service,
                ResourceExhaustion(enabled=True, cpu_load=experiment.magnitude / 100.0)
            )

        # Record start time for duration tracking
        start_time = time.time()

        # Run for specified duration
        # In a real implementation, we'd monitor metrics here
        await asyncio.sleep(min(experiment.duration_seconds, 300))  # Cap at 5 minutes

        # Record duration
        duration = time.time() - start_time
        EXPERIMENT_DURATION.labels(experiment_type=experiment.chaos_type.value).observe(duration)

        # Rollback chaos
        await rollback_chaos(experiment.target_service)

        result.status = ExperimentStatus.COMPLETED
        result.ended_at = datetime.utcnow().isoformat()
        result.metrics_collected = {
            "duration_seconds": duration,
            "chaos_type": experiment.chaos_type.value,
            "magnitude": experiment.magnitude
        }

        logger.info(f"Experiment {experiment_id} completed successfully")

    except Exception as e:
        result.status = ExperimentStatus.FAILED
        result.ended_at = datetime.utcnow().isoformat()
        result.violation_details = str(e)

        # Always rollback on failure
        await rollback_chaos(experiment.target_service)

        logger.error(f"Experiment {experiment_id} failed: {e}")

    return result


@app.get("/experiments")
async def list_experiments():
    """List all experiments."""
    return list(experiments.values())


@app.get("/experiments/{experiment_id}")
async def get_experiment(experiment_id: str):
    """Get a specific experiment's details."""
    if experiment_id not in experiments:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return experiments[experiment_id]


@app.delete("/experiments/{experiment_id}")
async def stop_experiment(experiment_id: str):
    """Stop a running experiment and rollback chaos."""
    if experiment_id not in experiments:
        raise HTTPException(status_code=404, detail="Experiment not found")

    exp = experiments[experiment_id]
    if exp.status != ExperimentStatus.RUNNING:
        return {"status": "not_running", "experiment_id": experiment_id}

    # Find and rollback the target service
    # In a real implementation, we'd track this better
    for service_name in active_chaos:
        await rollback_chaos(service_name)

    exp.status = ExperimentStatus.ROLLED_BACK
    exp.ended_at = datetime.utcnow().isoformat()

    return {"status": "stopped", "experiment_id": experiment_id}


# ===========================================
# Steady State Management
# ===========================================

@app.post("/steady-state/record/{service_name}")
async def record_steady_state(service_name: str, metrics: Dict):
    """
    Record baseline steady state metrics for a service.
    These are used to validate hypotheses during experiments.
    """
    steady_state_baselines[service_name] = {
        "recorded_at": datetime.utcnow().isoformat(),
        "metrics": metrics
    }
    logger.info(f"Recorded steady state baseline for {service_name}")
    return {"status": "recorded", "service": service_name, "baseline": steady_state_baselines[service_name]}


@app.get("/steady-state/{service_name}")
async def get_steady_state(service_name: str):
    """Get recorded steady state baseline for a service."""
    if service_name not in steady_state_baselines:
        raise HTTPException(status_code=404, detail="No baseline recorded")
    return steady_state_baselines[service_name]


@app.post("/steady-state/check/{service_name}")
async def check_steady_state(service_name: str, current_metrics: Dict):
    """
    Check if current metrics are within steady state bounds.
    Returns violations if any thresholds are exceeded.
    """
    if service_name not in steady_state_baselines:
        return {"status": "no_baseline", "violations": []}

    baseline = steady_state_baselines[service_name]["metrics"]
    violations = []

    for metric_name, current_value in current_metrics.items():
        if metric_name in baseline:
            baseline_value = baseline[metric_name]
            # Simple threshold: 50% deviation
            threshold = baseline_value * 1.5

            if current_value > threshold:
                violations.append({
                    "metric": metric_name,
                    "baseline": baseline_value,
                    "current": current_value,
                    "threshold": threshold
                })
                STEADY_STATE_VIOLATIONS.labels(
                    metric_name=metric_name,
                    target_service=service_name
                ).inc()

    return {
        "status": "violated" if violations else "healthy",
        "violations": violations
    }


# ===========================================
# Status Overview
# ===========================================

@app.get("/status")
async def get_status():
    """Get overall chaos controller status."""
    return {
        "service": SERVICE_NAME,
        "active_chaos": active_chaos,
        "running_experiments": len([e for e in experiments.values() if e.status == ExperimentStatus.RUNNING]),
        "total_experiments": len(experiments),
        "steady_state_baselines": list(steady_state_baselines.keys())
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
