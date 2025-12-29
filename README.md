# Distributed Lab

A minimal distributed system for learning observability, tracing, and metrics.

## Architecture

```
                                    ┌─────────────────┐
                                    │   k6 (load)     │
                                    └────────┬────────┘
                                             │
                                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Docker Network                            │
│                                                                  │
│  ┌──────────┐      ┌──────────┐      ┌──────────┐               │
│  │ Gateway  │─────▶│ Service  │─────▶│ Service  │               │
│  │  :8000   │      │    A     │      │    B     │               │
│  └──────────┘      │  :8001   │      │  :8002   │               │
│       │            └──────────┘      └──────────┘               │
│       │                 │                 │                      │
│       └─────────────────┴─────────────────┘                      │
│                         │                                        │
│                         ▼                                        │
│            ┌─────────────────────────┐                          │
│            │  OpenTelemetry Collector │                          │
│            │         :4317            │                          │
│            └─────────────────────────┘                          │
│                    │         │                                   │
│          ┌─────────┘         └──────────┐                       │
│          ▼                              ▼                        │
│   ┌────────────┐                ┌─────────────┐                 │
│   │   Jaeger   │                │  Prometheus │                 │
│   │   :16686   │                │    :9090    │                 │
│   └────────────┘                └─────────────┘                 │
│                                        │                         │
│   ┌────────────┐                       │                         │
│   │    Loki    │                       │                         │
│   │   :3100    │                       │                         │
│   └────────────┘                       │                         │
│          │                             │                         │
│          └──────────┬──────────────────┘                        │
│                     ▼                                            │
│              ┌────────────┐                                      │
│              │  Grafana   │                                      │
│              │   :3001    │                                      │
│              └────────────┘                                      │
└─────────────────────────────────────────────────────────────────┘
```

## Quick Start

### Option 1: Base Lab Only (Observability Infrastructure)

Run just the observability stack without application services:

```bash
docker compose -f docker-compose.base.yml up
```

This is useful as a reusable foundation for other distributed system experiments.

### Option 2: Full System (Infrastructure + Services)

Run the complete Echo Chain distributed system:

```bash
docker compose up --build
```

## Access URLs

| Service    | URL                        | Purpose                    |
|------------|----------------------------|----------------------------|
| Gateway    | http://localhost:8000      | API entry point            |
| Jaeger     | http://localhost:16686     | Distributed traces         |
| Prometheus | http://localhost:9090      | Raw metrics queries        |
| Grafana    | http://localhost:3001      | Dashboards (admin/admin)   |
| Service A  | http://localhost:8001      | Direct access (debugging)  |
| Service B  | http://localhost:8002      | Direct access (debugging)  |

## Testing the System

### Make a Single Request

```bash
curl http://localhost:8000/api/process | jq
```

Expected response:
```json
{
  "service": "gateway",
  "total_duration_ms": 125.5,
  "chain": {
    "service": "service-a",
    "processed": true,
    "latency_ms": 45,
    "downstream": {
      "service": "service-b",
      "processed": true,
      "latency_ms": 35
    }
  }
}
```

### Run Load Tests

Install k6 first: https://k6.io/docs/getting-started/installation/

```bash
# Basic load test (10 users, 30 seconds)
k6 run loadtest/script.js

# Custom configuration
k6 run --vus 20 --duration 60s loadtest/script.js

# With environment variable for different target
k6 run -e BASE_URL=http://localhost:8000 loadtest/script.js
```

## Observability Features

### Distributed Tracing (Jaeger)

1. Open http://localhost:16686
2. Select "gateway" from the Service dropdown
3. Click "Find Traces"
4. Click on a trace to see the full request flow:
   - gateway → service-a → service-b
5. Each span shows:
   - Duration
   - Custom attributes (e.g., `artificial_latency_ms`)
   - Service name

### Metrics (Grafana)

1. Open http://localhost:3001 (login: admin/admin)
2. Go to Dashboards → "Echo Chain Services"
3. See real-time:
   - Request rate per service
   - Latency percentiles (p50, p95, p99)
   - Service health status
4. Run load tests to see metrics update live

### Logs (Grafana/Loki)

1. In Grafana, go to Explore
2. Select "Loki" as the datasource
3. Query: `{job=~"gateway|service-a|service-b"}`
4. See structured JSON logs with trace IDs

### Correlating Traces, Metrics, and Logs

Each log entry includes a `trace_id` field. You can:
1. Find a trace in Jaeger
2. Copy the trace ID
3. Search in Loki: `{job=~".+"} |= "TRACE_ID_HERE"`

## Git Checkpoints

| Tag | Description | Command |
|-----|-------------|---------|
| `v0.1.0-base-lab` | Base observability infrastructure | `docker compose -f docker-compose.base.yml up` |
| `v0.2.0-echo-chain` | Full distributed system | `docker compose up --build` |

### Starting a New Experiment from Base Lab

```bash
git checkout v0.1.0-base-lab -b my-new-experiment
# Add your services, then commit
```

## Service Details

### Gateway (Port 8000)
- Entry point for all requests
- Minimal processing, just routing
- Initiates traces

### Service A (Port 8001)
- Middle tier service
- Adds 30-70ms artificial latency
- Calls Service B

### Service B (Port 8002)
- Leaf service
- Adds 20-50ms artificial latency
- Returns final response

## Stopping the System

```bash
# Stop all containers
docker compose down

# Stop and remove volumes (clean slate)
docker compose down -v
```

## Troubleshooting

### Containers not starting
```bash
# Check logs
docker compose logs -f

# Check specific service
docker compose logs gateway
```

### Port already in use
The system uses ports 3001, 4317, 4318, 8000-8002, 8889, 9090, 16686.
Check for conflicts: `lsof -i :PORT`

### Grafana datasources not connecting
Wait for all services to be healthy:
```bash
docker compose ps
```
All should show "healthy" status.
