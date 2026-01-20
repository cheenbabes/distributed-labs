"""
Prometheus metrics for the rate limiter service.
"""
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# Rate limit decision metrics
RATE_LIMIT_DECISIONS = Counter(
    "rate_limit_decisions_total",
    "Total rate limit decisions",
    ["client_id", "resource", "algorithm", "decision"]  # decision: allowed/denied
)

RATE_LIMIT_REMAINING = Gauge(
    "rate_limit_remaining",
    "Remaining requests allowed for client",
    ["client_id", "resource", "algorithm"]
)

RATE_LIMIT_LIMIT = Gauge(
    "rate_limit_limit",
    "Configured limit for client",
    ["client_id", "resource", "algorithm"]
)

# Request latency for rate limit checks
CHECK_LATENCY = Histogram(
    "rate_limit_check_duration_seconds",
    "Duration of rate limit check operations",
    ["algorithm"],
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5]
)

# API metrics
HTTP_REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"]
)

HTTP_REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

# gRPC metrics
GRPC_REQUEST_COUNT = Counter(
    "grpc_requests_total",
    "Total gRPC requests",
    ["method", "status"]
)

GRPC_REQUEST_LATENCY = Histogram(
    "grpc_request_duration_seconds",
    "gRPC request latency",
    ["method"],
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5]
)

# Configuration updates
CONFIG_UPDATES = Counter(
    "rate_limit_config_updates_total",
    "Total configuration updates",
    ["client_id", "resource", "action"]  # action: create/update/delete
)

# Redis connection health
REDIS_HEALTH = Gauge(
    "redis_connection_healthy",
    "Whether Redis connection is healthy",
    []
)


def record_decision(
    client_id: str,
    resource: str,
    algorithm: str,
    allowed: bool,
    remaining: int,
    limit: int
):
    """Record a rate limit decision."""
    decision = "allowed" if allowed else "denied"

    RATE_LIMIT_DECISIONS.labels(
        client_id=client_id,
        resource=resource,
        algorithm=algorithm,
        decision=decision
    ).inc()

    RATE_LIMIT_REMAINING.labels(
        client_id=client_id,
        resource=resource,
        algorithm=algorithm
    ).set(remaining)

    RATE_LIMIT_LIMIT.labels(
        client_id=client_id,
        resource=resource,
        algorithm=algorithm
    ).set(limit)


def record_config_update(client_id: str, resource: str, action: str):
    """Record a configuration update."""
    CONFIG_UPDATES.labels(
        client_id=client_id,
        resource=resource,
        action=action
    ).inc()
