# Lab 35: Load Shedding

**Resilience Tier 2: Intentional Request Rejection**

When your system is overwhelmed, trying to serve every request leads to catastrophic failure. Load shedding is the practice of intentionally rejecting requests before your system collapses - it's better to fail fast for some requests than to fail slowly for all of them.

## What You'll Learn

- Why load shedding is essential for system resilience
- How different shedding strategies affect fairness and priorities
- How to implement graceful 503 responses with Retry-After headers
- How to tune shedding thresholds for optimal performance
- The relationship between queue depth, latency, and system collapse

## Key Concepts

### The Overload Problem

When request rate exceeds processing capacity:
1. Queue builds up
2. Latency increases for ALL requests
3. Timeouts cause retries
4. Retries increase load further
5. System collapses completely

**Load shedding breaks this cycle by rejecting excess requests early.**

### Shedding Strategies

| Strategy | How It Works | Best For |
|----------|--------------|----------|
| **Random** | Randomly reject requests above threshold | Fair, unbiased rejection |
| **Priority** | Protect VIP/high-priority traffic | Business-critical traffic |
| **LIFO** | Reject oldest waiting requests first | Minimize wait time |

### Key Metrics

- `requests_shed_total` - Total requests rejected
- `queue_depth` - Current pending request count
- `capacity_utilization_percent` - How much capacity is in use
- `shedding_rate` - Current rejection probability (0-1)
- `shed_by_priority_total` - Breakdown by priority level

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │           API Service                    │
                    │  ┌─────────────────────────────────────┐│
   Requests ───────▶│  │     Load Shedding Middleware       ││
                    │  │  ┌─────────┐ ┌─────────┐ ┌───────┐ ││
                    │  │  │ Random  │ │Priority │ │ LIFO  │ ││
                    │  │  └────┬────┘ └────┬────┘ └───┬───┘ ││
                    │  │       └───────────┴─────────┘      ││
                    │  └────────────────┬────────────────────┘│
                    │                   │                     │
   503 + Retry ◀────│        Shed? ◀────┤                     │
                    │                   │                     │
                    │                   ▼                     │
                    │          ┌───────────────┐              │
                    │          │ Worker Service │              │
                    │          │  (Processing)  │              │
                    │          └───────────────┘              │
                    └─────────────────────────────────────────┘
```

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- k6 (optional, for load testing)

## Quick Start

### 1. Start the Lab

```bash
cd labs/week-35-load-shedding
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
| API Service | http://localhost:8000 | Main API with load shedding |
| Worker Service | http://localhost:8001 | Backend processor |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

---

## Lab Exercises

### Exercise 1: Baseline - Observe System Collapse Without Shedding

First, let's see what happens when a system has no protection against overload.

**Verify shedding is disabled:**

```bash
curl -s http://localhost:8000/admin/config | jq
```

Expected output shows `"enabled": false`.

**Generate normal load:**

```bash
for i in {1..10}; do
  curl -s -o /dev/null -w "Request $i: %{time_total}s, status=%{http_code}\n" \
    http://localhost:8000/api/process
done
```

**Expected:** Each request takes ~200-300ms and succeeds.

**Now generate overload (run this in background):**

```bash
# Start heavy load
for i in {1..100}; do
  curl -s http://localhost:8000/api/process &
done
wait
```

**What to observe:**
1. Open Grafana: http://localhost:3001 (Load Shedding Dashboard)
2. Watch latency percentiles spike
3. Watch concurrent requests hit max capacity
4. Notice requests start timing out or failing

---

### Exercise 2: Enable Random Shedding - Stable Degradation

Enable load shedding with the random strategy:

```bash
curl -s -X POST "http://localhost:8000/admin/config?enabled=true&strategy=random" | jq
```

**Run the overload test again:**

```bash
docker compose run --rm lab35-k6 run /scripts/basic.js
```

**What to observe in Grafana:**
1. Shedding rate increases as load increases
2. Latency stays bounded (doesn't spike to infinity)
3. Some requests get 503, but successful requests are fast
4. System remains stable and recovers quickly

**Key insight:** It's better to reject 30% of requests quickly than to make 100% of requests wait forever.

---

### Exercise 3: Priority-Based Shedding - Protect VIP Traffic

Switch to priority-based shedding:

```bash
curl -s -X POST "http://localhost:8000/admin/config?strategy=priority" | jq
```

**Send requests with different priorities:**

```bash
# VIP request (should rarely be shed)
curl -s -H "X-Priority: vip" http://localhost:8000/api/process | jq

# Normal request
curl -s -H "X-Priority: normal" http://localhost:8000/api/process | jq

# Low priority request (shed first)
curl -s -H "X-Priority: low" http://localhost:8000/api/process | jq
```

**Run the priority load test:**

```bash
docker compose run --rm lab35-k6 run /scripts/priority-test.js
```

**What to observe:**
1. VIP requests have 95%+ success rate
2. LOW priority requests are shed first
3. The "Shed by Priority" panel shows the distribution

**Real-world application:** Use priority shedding for:
- Paying customers vs. free tier
- Critical API operations vs. nice-to-have features
- Authenticated users vs. anonymous traffic

---

### Exercise 4: Compare LIFO vs FIFO Shedding

Switch to LIFO strategy:

```bash
curl -s -X POST "http://localhost:8000/admin/config?strategy=lifo" | jq
```

**Run the overload test:**

```bash
docker compose run --rm lab35-k6 run /scripts/overload-test.js
```

**LIFO behavior:**
- Newest requests are served first
- Old waiting requests are shed
- Prevents "stale" requests from consuming capacity
- Better user experience (no one waits forever)

**Compare with random (already tested):**
- Random is fair but can leave some requests waiting
- LIFO ensures no request waits longer than the shed timeout

---

### Exercise 5: Tune Shedding Thresholds

Experiment with different threshold settings:

**Lower threshold (more aggressive shedding):**

```bash
curl -s -X POST "http://localhost:8000/admin/config?shed_threshold_percent=60" | jq
```

**Higher threshold (less aggressive):**

```bash
curl -s -X POST "http://localhost:8000/admin/config?shed_threshold_percent=90" | jq
```

**Run load tests with each setting and compare:**
1. Shed rate
2. p99 latency
3. Overall throughput

**Finding the optimal threshold:**
- Too low: Shed too early, waste capacity
- Too high: Shed too late, latency spikes before shedding kicks in
- Sweet spot: Usually 70-80% utilization

**Reduce max concurrent to make overload easier to trigger:**

```bash
curl -s -X POST "http://localhost:8000/admin/config?max_concurrent=20" | jq
```

---

## Key Takeaways

1. **Fail fast, not slow** - Rejecting requests quickly (503) is better than making everyone wait

2. **Graceful degradation** - Load shedding keeps your system stable under pressure

3. **Priority matters** - Not all traffic is equal; protect what's important

4. **Monitor shedding metrics** - High shed rates indicate capacity problems

5. **Tune your thresholds** - Start conservative (70-80%) and adjust based on behavior

6. **Return Retry-After** - Help clients know when to retry

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Check shedding configuration
```bash
curl -s http://localhost:8000/admin/config | jq
```

### Reset to defaults
```bash
curl -s -X POST "http://localhost:8000/admin/config?enabled=false&strategy=random&max_concurrent=50&shed_threshold_percent=80" | jq
```

## Further Reading

- [Google SRE: Handling Overload](https://sre.google/sre-book/handling-overload/)
- [AWS: Using load shedding to avoid overload](https://aws.amazon.com/builders-library/using-load-shedding-to-avoid-overload/)
- [Netflix: Adaptive Concurrency Limits](https://netflixtechblog.medium.com/performance-under-load-3e6fa9a60581)

## Next Lab

[Lab 36: Graceful Degradation](../week-36-graceful-degradation/) - Learn how to degrade functionality instead of failing completely.
