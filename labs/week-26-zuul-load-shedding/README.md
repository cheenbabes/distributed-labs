# Lab 26: Zuul Load Shedding

When your backend service is overloaded, should the gateway keep sending requests until everything crashes? Or should it protect the backend by rejecting requests early? In this lab, you'll implement Netflix Zuul-style load shedding to keep your system healthy under extreme load.

## What You'll Learn

- How load shedding protects backend services from cascading failures
- Concurrency-based load shedding (max in-flight requests)
- Latency-based load shedding (p99 threshold)
- Error-rate-based load shedding
- Adaptive concurrency limits (Vegas algorithm style)
- Priority-based shedding (VIP traffic gets preference)
- How to observe and tune load shedding behavior

## Architecture

```
                                                    Load Shedding
                                                    Decisions
                                                        |
                                                        v
┌──────────┐    ┌─────────────────────────────────────────────┐    ┌─────────────┐
│  Client  │───>│                 Gateway                     │───>│   Backend   │
│  (k6)    │    │  - Concurrency limit                        │    │  - Limited  │
│          │<───│  - Latency-based shedding                   │<───│    capacity │
│          │    │  - Error-rate shedding                      │    │  - Degrades │
│          │    │  - Adaptive (Vegas-style)                   │    │    under    │
│          │    │  - Priority-based (VIP vs normal)           │    │    load     │
└──────────┘    └─────────────────────────────────────────────┘    └─────────────┘
                         │                    │
                         v                    v
                   ┌──────────┐         ┌──────────┐
                   │ 503 Shed │         │   200    │
                   │ Response │         │ Success  │
                   └──────────┘         └──────────┘
```

The gateway implements multiple load shedding strategies:

1. **Concurrency Limit**: Reject requests when too many are in-flight
2. **Latency-Based**: Shed when p99 latency exceeds threshold
3. **Error-Rate-Based**: Shed when backend error rate is too high
4. **Adaptive (Vegas)**: Automatically adjust limits based on latency signals
5. **Priority-Based**: VIP traffic gets 2x the normal limits

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- k6 (optional, runs in container)

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
| Backend | http://localhost:8001 | Backend service |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Baseline Without Load Shedding

First, let's see what happens when we overwhelm the backend without any protection.

```bash
# Check current config (load shedding disabled by default)
curl -s http://localhost:8000/config | jq

# Check backend stats
curl -s http://localhost:8001/stats | jq
```

Run a load test to overwhelm the backend:

```bash
docker compose run --rm lab26-k6 run /scripts/basic.js
```

**Watch Grafana** (http://localhost:3001) during the test:
- Backend capacity utilization goes above 100%
- Latency spikes dramatically (5x-10x normal)
- Some requests timeout or error

**Questions:**
- What happens to latency when the backend is at 150%+ capacity?
- How many requests fail vs succeed?
- Would a real user be happy with this experience?

---

### Exercise 2: Enable Concurrency Limit

Now let's protect the backend with a simple concurrency limit.

```bash
# Enable load shedding with concurrency limit
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "max_concurrent": 25,
    "concurrency_limit_enabled": true
  }' | jq

# Verify config
curl -s http://localhost:8000/config | jq
```

Run the same load test:

```bash
docker compose run --rm lab26-k6 run /scripts/basic.js
```

**Watch Grafana** during the test:
- The "Shed Requests by Reason" panel shows `concurrency_limit` rejections
- Backend capacity stays below 100%
- Latency stays controlled for requests that get through

**Questions:**
- How many requests are shed vs succeeded?
- What's the difference in latency for successful requests?
- Is the backend healthier now?

---

### Exercise 3: Latency-Based Shedding

Concurrency limits are static. What if we want to shed based on actual latency?

```bash
# Enable latency-based shedding
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "latency_shedding_enabled": true,
    "latency_threshold_ms": 300,
    "concurrency_limit_enabled": false
  }' | jq
```

Run the load test:

```bash
docker compose run --rm lab26-k6 run /scripts/basic.js
```

**Watch Grafana**:
- Check "Gateway Calculated P99 Latency" stat
- Shed reason changes to `high_latency` when p99 exceeds 300ms
- Gateway latency percentiles stay more consistent

**Questions:**
- How does latency-based shedding compare to concurrency limits?
- When does shedding kick in (what latency triggers it)?

---

### Exercise 4: Adaptive Concurrency (Vegas Algorithm)

Static limits require tuning. Adaptive algorithms find the right limit automatically.

```bash
# Enable adaptive concurrency
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "adaptive_enabled": true,
    "concurrency_limit_enabled": true,
    "latency_shedding_enabled": false
  }' | jq
```

Run the adaptive test with variable load:

```bash
docker compose run --rm lab26-k6 run /scripts/adaptive-test.js
```

**Watch Grafana**:
- The "Adaptive Concurrency Limit" stat changes over time
- During low load: limit increases (finding headroom)
- During high load: limit decreases (protecting backend)

**How Vegas-style adaptive works:**
1. Target latency = 50% of threshold
2. If actual latency < target: increase limit (we have headroom)
3. If actual latency > target: decrease limit (we're overloaded)
4. Smooth changes to avoid oscillation

---

### Exercise 5: Priority-Based Shedding (VIP Traffic)

Not all traffic is equal. Let's give VIP users priority.

```bash
# Enable priority-based shedding
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "priority_enabled": true,
    "concurrency_limit_enabled": true,
    "max_concurrent": 20
  }' | jq
```

Test priority traffic manually:

```bash
# Normal request
curl -s -w "\nStatus: %{http_code}\n" http://localhost:8000/api/process

# VIP request (add X-Priority header)
curl -s -w "\nStatus: %{http_code}\n" \
  -H "X-Priority: vip" \
  http://localhost:8000/api/process
```

Run the priority test with mixed traffic:

```bash
docker compose run --rm lab26-k6 run /scripts/priority-test.js
```

**Watch Grafana**:
- "Success Rate by Priority" shows VIP vs Normal
- "Shed Rate by Priority" shows VIP is shed less often
- VIP gets 2x the effective limits

**Questions:**
- What's the success rate ratio between VIP and normal traffic?
- Is this fair? When would you use this in production?

---

### Exercise 6: Compare Backend Health

Let's compare backend health with and without load shedding.

**Without load shedding:**

```bash
# Disable load shedding
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' | jq

# Start load test in background
docker compose run --rm lab26-k6 run /scripts/basic.js &

# Watch backend stats during test
watch -n 1 'curl -s http://localhost:8001/stats | jq'
```

Note the `load_factor` and `status` during the test.

**With load shedding:**

```bash
# Enable load shedding
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "concurrency_limit_enabled": true,
    "max_concurrent": 25
  }' | jq

# Run the same test
docker compose run --rm lab26-k6 run /scripts/basic.js
```

**Questions:**
- What's the maximum `load_factor` in each case?
- Does the backend ever report status "overloaded" with shedding enabled?
- Which scenario has better p99 latency for requests that succeed?

---

## Configuration Reference

### Gateway Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `enabled` | false | Master switch for load shedding |
| `max_concurrent` | 50 | Static concurrency limit |
| `latency_threshold_ms` | 500 | P99 latency threshold for shedding |
| `error_rate_threshold` | 0.1 | Error rate threshold (0-1) |
| `adaptive_enabled` | false | Enable Vegas-style adaptive limits |
| `priority_enabled` | false | Enable VIP priority handling |
| `concurrency_limit_enabled` | true | Enable concurrency-based shedding |
| `latency_shedding_enabled` | false | Enable latency-based shedding |
| `error_rate_shedding_enabled` | false | Enable error-rate-based shedding |

### Backend Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_capacity` | 20 | Max concurrent requests before degradation |
| `base_latency_min_ms` | 20 | Minimum processing latency |
| `base_latency_max_ms` | 50 | Maximum processing latency |
| `overload_latency_multiplier` | 5.0 | Latency multiplier when overloaded |
| `inject_latency` | false | Enable injected latency |
| `injected_latency_ms` | 0 | Additional latency to inject |

## Key Metrics

| Metric | Description |
|--------|-------------|
| `requests_shed_total` | Counter of shed requests by reason and priority |
| `concurrent_requests` | Current in-flight requests |
| `adaptive_concurrency_limit` | Current adaptive limit |
| `latency_p99_ms` | Calculated p99 latency |
| `error_rate` | Calculated error rate |
| `capacity_utilization` | Backend capacity usage (0-1+) |

## Key Takeaways

1. **Load shedding protects everyone** - By rejecting some requests, the requests that get through have a much better experience

2. **Fast failure is better than slow failure** - A quick 503 is better than waiting 10 seconds for a timeout

3. **Multiple strategies complement each other** - Concurrency limits, latency-based, and adaptive can work together

4. **Adaptive limits reduce tuning** - Vegas-style algorithms find the right limit automatically

5. **Priority enables graceful degradation** - Protect your most important traffic when under pressure

6. **Monitor your shed rate** - High shed rates mean you need more capacity or need to reduce demand

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### No shed events showing
- Make sure load shedding is enabled: `curl -s http://localhost:8000/config | jq`
- Check if load is high enough to trigger shedding

### Metrics not appearing in Grafana
- Wait 30 seconds for Prometheus to scrape
- Check Prometheus targets: http://localhost:9090/targets

## Next Lab

Continue your journey into distributed systems resilience with more advanced patterns!
