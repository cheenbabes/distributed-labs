# Lab 01: Finding the Slow Service

Your distributed system is slow, but which service is the bottleneck? In this lab, you'll use distributed tracing to pinpoint exactly where latency is introduced in a 5-service chain.

## What You'll Learn

- How distributed tracing works across service boundaries
- How to read traces in Jaeger to identify bottlenecks
- How a small delay in one service compounds through the chain
- Practical debugging techniques for production latency issues

## Architecture

```ini
┌──────────┐    ┌───────────┐    ┌───────────┐    ┌───────────┐    ┌───────────┐
│  Client  │───▶│  Gateway  │───▶│ Service A │───▶│ Service B │───▶│ Service C │
└──────────┘    └───────────┘    └───────────┘    └───────────┘    └───────────┘
                                                                          │
                                                                          ▼
                                                                   ┌───────────┐
                                                                   │ Service D │ ← We'll inject
                                                                   └───────────┘   latency here
```

Each service adds a small artificial latency (10-50ms) to simulate real work. Service D can be configured to add significant extra latency, simulating a slow database or external API call.

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- k6 (optional, for load testing)

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

### 2. Verify Services Are Running

```bash
docker compose ps
```

All services should show as "healthy".

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Gateway | http://localhost:8000 | API entry point |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Baseline Measurement

First, let's establish a baseline. Make a few requests and observe normal latency.

```bash
# Make a single request
curl -s http://localhost:8000/api/process | jq

# Make 10 requests and observe timing
for i in {1..10}; do
  curl -s -o /dev/null -w "Request $i: %{time_total}s\n" http://localhost:8000/api/process
done
```

**Expected:** Each request takes ~100-200ms total (sum of all service latencies).

Now look at a trace in Jaeger:

1. Open http://localhost:16686
2. Select "gateway" from the Service dropdown
3. Click "Find Traces"
4. Click on any trace to see the full breakdown

**Question:** How is the total latency distributed across services?

---

### Exercise 2: Inject Latency

Now let's simulate a slow service. We'll add 500ms latency to Service D using its admin API:

```bash
# Enable slow mode (500ms extra latency)
curl -X POST http://localhost:8004/admin/latency \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "ms": 500}'
```

Make requests again:

```bash
for i in {1..5}; do
  curl -s -o /dev/null -w "Request $i: %{time_total}s\n" http://localhost:8000/api/process
done
```

**Expected:** Each request now takes ~600-700ms.

---

### Exercise 3: Find the Bottleneck

Without knowing which service we made slow, use Jaeger to find it.

1. Open http://localhost:16686
2. Find a recent trace
3. Look at the waterfall view
4. Identify which span takes the most time

**What to look for:**

- The span with the longest duration
- The service name associated with that span
- The gap between when the span started and ended

---

### Exercise 4: Quantify the Impact

Use Jaeger's comparison features:

1. Find a trace from before the latency injection (use the time filter)
2. Find a trace from after
3. Compare the two traces

**Questions:**

- How much did total latency increase?
- What percentage of total time is now spent in Service D?
- How does the latency compound through the chain?

---

### Exercise 5: Load Test Analysis

Generate sustained load to see patterns in metrics:

```bash
# Run k6 load test (2 minutes, 10 virtual users)
docker compose run --rm k6 run /scripts/basic.js
```

While the test runs:

1. Watch Grafana dashboards (http://localhost:3001)
2. Observe p50, p95, p99 latencies
3. See how Service D's latency affects upstream services

---

## Key Takeaways

1. **Distributed tracing is essential** - Without it, you're guessing which service is slow
2. **Latency compounds** - A 500ms delay in a leaf service becomes 500ms+ at the gateway
3. **Look at the waterfall** - The visual representation immediately shows bottlenecks
4. **Check percentiles, not averages** - p99 latency often tells a different story than p50

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting

```bash
docker compose logs -f
```

### Traces not appearing in Jaeger

- Wait 10-15 seconds after making requests
- Check that otel-collector is running: `docker compose logs otel-collector`

### Port already in use

Check for conflicts: `lsof -i :8000` (or whichever port)

## Next Lab

[Lab 02: What Happens When Your Cache Dies](../week-02-cache-dies/) - We'll add Redis caching and then kill it mid-traffic to see what happens.
