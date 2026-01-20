# Lab 06: Retry Storms

Retries are supposed to help, but when every client retries a failed request 3 times, you've just 3x'd your traffic to an already struggling service. In this lab, we'll trigger a brief failure and watch retries amplify it into a complete outage--then implement exponential backoff with jitter to fix it.

## What You'll Learn

- How naive retry strategies can amplify failures instead of recovering from them
- Why synchronized retries create "thundering herd" problems
- How exponential backoff gives services time to recover
- Why jitter is essential to prevent retry waves
- How to read metrics that reveal retry storm patterns

## The Problem

When a backend service fails:
1. 10 clients each make a request
2. Each client retries 3 times (naive strategy)
3. The backend now receives 30+ requests instead of 10
4. The overload prevents recovery
5. More timeouts cause more retries
6. The system enters a death spiral

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │            Client Service               │
                    │                                         │
                    │  Retry Strategies:                      │
                    │  - naive: immediate, 3 attempts         │
                    │  - backoff: exponential (1s, 2s, 4s)   │
                    │  - jitter: backoff + random delay       │
                    └────────────────┬────────────────────────┘
                                     │
                                     ▼
                    ┌─────────────────────────────────────────┐
                    │           Backend Service               │
                    │                                         │
                    │  /admin/fail - trigger failures         │
                    │  /admin/status - check state            │
                    │  /process - main endpoint               │
                    └─────────────────────────────────────────┘
```

## Prerequisites

- Docker and Docker Compose
- curl (for API calls)
- A browser (for Grafana/Jaeger)

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
| Client | http://localhost:8000 | Gateway/client service |
| Backend | http://localhost:8001 | Backend service |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Establish Baseline (Healthy System)

First, let's see normal operation with no failures.

```bash
# Make a request through the client
curl -s http://localhost:8000/process | jq

# Check client metrics
curl -s http://localhost:8000/metrics | grep -E "retry|request"
```

**Expected:** Requests succeed on first attempt, no retries needed.

---

### Exercise 2: Naive Retries - The Amplification Problem

Now let's see what happens with naive retries (immediate, 3 attempts).

#### Step 1: Configure naive retry strategy

```bash
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "naive"}' | jq
```

#### Step 2: Trigger a brief backend failure (fails for 10 seconds)

```bash
curl -s -X POST http://localhost:8001/admin/fail \
  -H "Content-Type: application/json" \
  -d '{"duration_seconds": 10}' | jq
```

#### Step 3: Immediately send traffic

```bash
# Watch the amplification in real-time
for i in {1..10}; do
  curl -s http://localhost:8000/process &
done
wait
```

#### Step 4: Check the backend metrics

```bash
curl -s http://localhost:8001/metrics | grep http_requests_total
```

**What you'll see:**
- 10 original requests
- Each fails and retries 3 times = 30+ total backend requests
- The backend, already struggling, gets hammered

**Check Grafana:** Open http://localhost:3001 and look at the "Retry Storms" dashboard.

---

### Exercise 3: Watch the Death Spiral

Let's create a sustained failure and watch the system collapse.

#### Step 1: Start load testing

```bash
docker compose run --rm k6 run /scripts/retry-storm.js &
```

#### Step 2: Trigger backend failure during load

```bash
# Wait 10 seconds for load to stabilize, then fail for 30 seconds
sleep 10
curl -s -X POST http://localhost:8001/admin/fail \
  -H "Content-Type: application/json" \
  -d '{"duration_seconds": 30}' | jq
```

#### Step 3: Observe in Grafana

Watch these metrics:
- **Backend Request Rate**: Should spike 3x during failure
- **Client Error Rate**: Should spike to near 100%
- **Retry Count by Strategy**: Shows amplification

**Question:** Even after the failure ends, how long does it take to recover?

---

### Exercise 4: Exponential Backoff - Give Time to Recover

Now let's implement exponential backoff.

#### Step 1: Switch to backoff strategy

```bash
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "backoff"}' | jq
```

#### Step 2: Trigger failure and observe

```bash
curl -s -X POST http://localhost:8001/admin/fail \
  -H "Content-Type: application/json" \
  -d '{"duration_seconds": 15}' | jq

# Send requests
for i in {1..10}; do
  curl -s http://localhost:8000/process &
done
wait
```

#### Step 3: Compare metrics

```bash
curl -s http://localhost:8001/metrics | grep http_requests_total
```

**What's different:**
- Retries are spaced out (1s, 2s, 4s)
- Backend gets fewer requests during failure window
- Recovery starts sooner

**But there's still a problem:** All clients back off on the same schedule!

---

### Exercise 5: Add Jitter - Break the Synchronization

The final piece: randomized delays to prevent synchronized retry waves.

#### Step 1: Switch to jitter strategy

```bash
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "jitter"}' | jq
```

#### Step 2: Trigger failure and observe

```bash
curl -s -X POST http://localhost:8001/admin/fail \
  -H "Content-Type: application/json" \
  -d '{"duration_seconds": 15}' | jq

# Send requests
for i in {1..10}; do
  curl -s http://localhost:8000/process &
done
wait
```

#### Step 3: Compare patterns in Grafana

Look at the **Request Pattern** panel. With jitter:
- Retries are spread out over time
- No synchronized spikes
- Backend load stays more even
- Recovery is much smoother

---

### Exercise 6: Full Comparison Under Load

Run the comparison test to see all three strategies side by side.

```bash
docker compose run --rm k6 run /scripts/comparison.js
```

This test:
1. Runs load with naive retries, triggers failure
2. Runs load with backoff, triggers failure
3. Runs load with jitter, triggers failure
4. Outputs comparison metrics

**Key metrics to compare:**
- Total backend requests during failure
- Time to recovery after failure ends
- Success rate after recovery

---

## Key Metrics to Watch

### In Grafana Dashboard

1. **Backend Request Rate**
   - Spike during failure = retry amplification
   - Goal: Keep this close to normal during failures

2. **Retry Attempts by Strategy**
   - Shows which strategy is active
   - Compare attempt patterns

3. **Request Latency Distribution**
   - Naive: Spikes then crashes
   - Backoff: Gradual increase
   - Jitter: Smooth distribution

4. **Error Rate**
   - Watch how quickly each strategy recovers

### Prometheus Queries

```promql
# Backend request rate (should not spike with good retry strategy)
rate(http_requests_total{service="backend"}[30s])

# Retry attempt distribution
sum by (attempt) (rate(retry_attempts_total[1m]))

# Client error rate
rate(http_requests_total{service="client", status=~"5.."}[30s])
  / rate(http_requests_total{service="client"}[30s])
```

---

## Key Takeaways

1. **Naive retries are dangerous** - They amplify failures instead of recovering from them

2. **Exponential backoff helps** - Gives the backend time to recover between retry waves

3. **Jitter is essential** - Without it, all clients retry at the same time, creating spikes

4. **Monitor retry patterns** - Your metrics should show retry counts and patterns

5. **Consider circuit breakers** - Sometimes it's better to fail fast than retry (see Week 7)

## Retry Strategy Comparison

| Strategy | Retry Timing | Amplification | Recovery |
|----------|--------------|---------------|----------|
| Naive | Immediate, immediate, immediate | 3x+ | Poor - prevents recovery |
| Backoff | 1s, 2s, 4s | ~2x | Better - gives time |
| Jitter | 1s+random, 2s+random, 4s+random | ~1.5x | Best - no spikes |

## Configuration Reference

### Client Service

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/process` | GET | Make request to backend (with retries) |
| `/admin/strategy` | POST | Set retry strategy: naive, backoff, jitter |
| `/admin/strategy` | GET | Get current strategy |
| `/health` | GET | Health check |
| `/metrics` | GET | Prometheus metrics |

### Backend Service

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/process` | GET | Process request (may fail if failure active) |
| `/admin/fail` | POST | Trigger failure: `{"duration_seconds": N}` |
| `/admin/status` | GET | Check failure status |
| `/health` | GET | Health check |
| `/metrics` | GET | Prometheus metrics |

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Retries not visible in metrics

Make sure you're looking at the client service metrics (port 8000), not the backend.

### Backend won't recover

Check if there's still an active failure:
```bash
curl -s http://localhost:8001/admin/status | jq
```

### Metrics not updating

Prometheus scrapes every 15 seconds. Wait and refresh.

### Services not starting

```bash
docker compose logs -f
```

## Next Lab

[Lab 07: Circuit Breakers](../week-07-circuit-breakers/) - Sometimes the best retry strategy is to not retry at all. We'll implement circuit breakers to fail fast when a service is down.
