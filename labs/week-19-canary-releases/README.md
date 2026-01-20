# Lab 19: Canary Releases

Deploy new versions safely with canary releases. In this lab, you'll learn how to gradually shift traffic to a new version, monitor error rates, and automatically rollback when things go wrong.

## What You'll Learn

- How canary deployments work with weighted traffic splitting
- How to gradually increase traffic to a new version (1% -> 10% -> 50% -> 100%)
- How to monitor error rates per version during deployment
- How automatic rollback protects users from bad deployments
- When to use canary releases vs blue-green deployments

## Architecture

```
                                   ┌─────────────┐
                                   │   Stable    │
                              ┌───▶│   (v1)      │ Weight: 90%
                              │    │   Always    │
┌──────────┐    ┌──────────┐  │    │   Healthy   │
│          │    │          │──┘    └─────────────┘
│  Client  │───▶│  Nginx   │
│          │    │   LB     │──┐    ┌─────────────┐
└──────────┘    └──────────┘  │    │   Canary    │
                              └───▶│   (v2)      │ Weight: 10%
                                   │ Configurable│
                                   │ Error Rate  │
                                   └─────────────┘
                                          │
                                          ▼
                              ┌─────────────────────┐
                              │  Canary Controller  │
                              │  - Traffic weights  │
                              │  - Error monitoring │
                              │  - Auto-rollback    │
                              └─────────────────────┘
```

**Components:**
- **Stable (v1)**: The current production version - always returns successful responses
- **Canary (v2)**: The new version being tested - has configurable error rate
- **Nginx**: Load balancer with weighted traffic splitting
- **Canary Controller**: Manages weights, monitors errors, triggers rollback

## Prerequisites

- Docker and Docker Compose
- curl (for API calls)
- jq (for JSON parsing)
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
| Gateway (via Nginx) | http://localhost:8000 | API entry point |
| Stable (v1) direct | http://localhost:8081 | Direct access to v1 |
| Canary (v2) direct | http://localhost:8082 | Direct access to v2 |
| Canary Controller | http://localhost:8090 | Deployment control |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Baseline - All Traffic to Stable

Initially, all traffic goes to the stable version (v1).

```bash
# Check current status
curl -s http://localhost:8090/status | jq

# Make several requests - all should go to v1
for i in {1..10}; do
  curl -s http://localhost:8000/api/process | jq -r '.version'
done
```

**Expected:** All requests return `v1` and succeed.

Open Grafana at http://localhost:3001 and navigate to the "Canary Releases Lab" dashboard.

---

### Exercise 2: Deploy Canary at 1% Traffic

Start the canary deployment with minimal traffic.

```bash
# Promote to first stage (1%)
./scripts/promote-canary.sh

# Or set directly
curl -s -X POST http://localhost:8090/weight \
  -H "Content-Type: application/json" \
  -d '{"canary_weight": 1}' | jq
```

Generate traffic to observe the split:

```bash
# Count versions over 100 requests
for i in {1..100}; do
  curl -s http://localhost:8000/api/process | jq -r '.version'
done | sort | uniq -c
```

**Expected:** ~99 requests to v1, ~1 request to v2.

**Question:** Why start with just 1% traffic?

---

### Exercise 3: Gradually Increase Canary Traffic

Continue promoting the canary through the stages.

```bash
# Check current status
./scripts/status.sh

# Promote to next stage
./scripts/promote-canary.sh

# Repeat to go through stages: 1% -> 5% -> 10% -> 25% -> 50%
```

At each stage, observe in Grafana:
1. The traffic distribution chart
2. Error rates for each version (should both be 0%)
3. Latency comparison between versions

---

### Exercise 4: Observe Error Rates Per Version

Let's see what happens when the canary has issues.

First, start generating sustained traffic:

```bash
# In one terminal, generate continuous traffic
while true; do
  curl -s http://localhost:8000/api/process > /dev/null
  sleep 0.1
done
```

Now inject errors into the canary:

```bash
# Inject 30% error rate into canary
curl -s -X POST http://localhost:8082/admin/config \
  -H "Content-Type: application/json" \
  -d '{"error_rate": 0.3}' | jq

# Or use the script
./scripts/inject-errors.sh 0.3
```

**Observe in Grafana:**
1. The "Error Rates by Version" panel shows v2 errors rising
2. The "Canary Error Rate" stat turns yellow/red
3. The "Success vs Errors by Version" shows the error distribution

**Key insight:** Errors are contained to the canary percentage. If canary has 10% traffic and 30% error rate, only 3% of total users are affected.

---

### Exercise 5: Automatic Rollback

The canary controller monitors error rates and can automatically rollback.

```bash
# Check auto-rollback configuration
curl -s http://localhost:8090/status | jq

# The threshold is 5% by default
```

With error injection still active:

```bash
# Set canary weight high enough for errors to trigger threshold
curl -s -X POST http://localhost:8090/weight \
  -H "Content-Type: application/json" \
  -d '{"canary_weight": 25}' | jq
```

Wait 10-15 seconds (the monitoring interval), then:

```bash
# Check status - should show rollback occurred
curl -s http://localhost:8090/status | jq
```

**Expected:** The controller detected the error rate exceeded 5% and automatically rolled back to 0%.

**Observe in Grafana:**
1. The "Rollbacks & Promotions" panel shows the rollback
2. Traffic distribution snaps back to 100% stable
3. Error rate drops to 0%

---

### Exercise 6: Complete Successful Canary Promotion

Let's do a successful full promotion.

```bash
# First, ensure canary is healthy (disable errors)
curl -s -X POST http://localhost:8082/admin/config \
  -H "Content-Type: application/json" \
  -d '{"error_rate": 0, "extra_latency_ms": 0}' | jq

# Promote through all stages
./scripts/promote-canary.sh  # 0% -> 1%
./scripts/promote-canary.sh  # 1% -> 5%
./scripts/promote-canary.sh  # 5% -> 10%
./scripts/promote-canary.sh  # 10% -> 25%
./scripts/promote-canary.sh  # 25% -> 50%
./scripts/promote-canary.sh  # 50% -> 75%
./scripts/promote-canary.sh  # 75% -> 100%
```

At 100%, the canary is now the new stable version!

```bash
# All traffic now goes to v2
for i in {1..10}; do
  curl -s http://localhost:8000/api/process | jq -r '.version'
done
```

---

### Exercise 7: Explore Traces by Version

Use Jaeger to compare traces between versions.

1. Open http://localhost:16686
2. Search for traces from both `app-v1` and `app-v2` services
3. Compare latency and structure
4. Look for any errors in canary traces

**Question:** How would you use traces to debug a problematic canary?

---

## Advanced Exercises

### Latency-Based Rollback

The canary can also inject latency:

```bash
# Inject 500ms extra latency into canary
curl -s -X POST http://localhost:8082/admin/config \
  -H "Content-Type: application/json" \
  -d '{"error_rate": 0, "extra_latency_ms": 500}' | jq
```

Observe in Grafana how the latency percentiles differ between versions.

**Discussion:** Should you rollback on latency increases? What threshold would you use?

### Custom Rollback Thresholds

```bash
# Set a stricter 1% error threshold
./scripts/configure-auto-rollback.sh true 0.01

# Or more lenient 10%
./scripts/configure-auto-rollback.sh true 0.10

# Disable auto-rollback for manual control
./scripts/configure-auto-rollback.sh false
```

### Load Testing

Run sustained load while performing canary operations:

```bash
# Run load test (in background)
docker compose run --rm k6 run /scripts/sustained-load.js &

# Perform canary operations while load is running
./scripts/promote-canary.sh
./scripts/inject-errors.sh 0.2
# ... observe behavior
./scripts/rollback-canary.sh
```

---

## Key Takeaways

1. **Start small, grow gradually** - 1% traffic catches issues with minimal user impact

2. **Monitor per-version metrics** - Aggregate error rates can hide problems in the canary

3. **Automate rollback** - Humans are too slow to react; let the system protect itself

4. **Canary vs Blue-Green:**
   - Blue-Green: Instant cutover, instant rollback
   - Canary: Gradual rollout, catches issues early, limits blast radius

5. **Error budget matters** - A canary with 10% traffic and 30% error rate only affects 3% of users

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Nginx not updating weights
The canary controller writes to a shared volume. Restart nginx:
```bash
docker compose restart nginx
```

### Auto-rollback not triggering
Check the monitoring interval and error rate:
```bash
curl -s http://localhost:8090/status | jq
```

### Services not healthy
```bash
docker compose logs -f [service-name]
```

## Next Lab

[Lab 20: Feature Flags](../week-20-feature-flags/) - Control feature rollout with runtime configuration instead of deployments.
