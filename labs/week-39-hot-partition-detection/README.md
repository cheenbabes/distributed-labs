# Lab 39: Hot Partition Detection

A "hot partition" occurs when one shard or partition in a distributed system receives disproportionately more traffic than others. This lab teaches you how to detect, diagnose, and mitigate hot partitions using real-world scenarios.

## What You'll Learn

- How consistent hashing distributes data across shards
- Why hot keys cause hot partitions and how they impact system performance
- How to detect hot partitions using metrics and distributed tracing
- Remediation strategies: caching, key splitting, and request routing
- Understanding Zipfian distributions in real-world access patterns

## Architecture

```
                                    ┌──────────────┐
                                    │   Shard 0    │
                                    │   (Keys:     │
                              ┌────▶│   hash%4=0)  │
                              │     └──────────────┘
                              │
┌──────────┐    ┌───────────┐ │     ┌──────────────┐
│  Client  │───▶│    API    │─┼────▶│   Shard 1    │
│  (k6)    │    │  Gateway  │ │     │   (Keys:     │
└──────────┘    └───────────┘ │     │   hash%4=1)  │
                     │        │     └──────────────┘
                     │        │
               ┌─────┴─────┐  │     ┌──────────────┐
               │  Cache    │  ├────▶│   Shard 2    │
               │ (optional)│  │     │   (Keys:     │
               └───────────┘  │     │   hash%4=2)  │
                              │     └──────────────┘
                              │
                              │     ┌──────────────┐
                              └────▶│   Shard 3    │◀── Hot partition!
                                    │   (Keys:     │    (celebrity key)
                                    │   hash%4=3)  │
                                    └──────────────┘
```

The API Gateway uses consistent hashing (MD5 % num_shards) to route requests to the appropriate shard. When a "hot key" (like a celebrity's profile) receives massive traffic, its shard becomes overloaded.

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- k6 (optional, for load testing)

## Quick Start

### 1. Start the Lab

```bash
cd /Users/ebaibourine/github/distributed-lab/labs/week-39-hot-partition-detection
docker compose up --build -d
```

### 2. Verify Services Are Running

```bash
docker compose ps
```

All 4 shards, the API gateway, and observability stack should be healthy.

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| API Gateway | http://localhost:8000 | Entry point for data access |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

---

## Lab Exercises

### Exercise 1: Understand the Sharding Scheme

First, let's understand how keys are distributed across shards.

```bash
# Check which shard a key maps to
curl -s http://localhost:8000/admin/shard-mapping/user-123 | jq

# Try several keys and observe the distribution
for key in user-0 user-1 user-2 user-3 celebrity-kardashian; do
  echo -n "$key -> "
  curl -s "http://localhost:8000/admin/shard-mapping/$key" | jq -r '.shard_id'
done
```

**Key Insight:** The consistent hash function determines which shard owns each key. The `celebrity-kardashian` key always maps to the same shard.

---

### Exercise 2: Baseline - Uniform Traffic Distribution

Generate traffic with uniform key distribution and observe balanced load.

```bash
# Run uniform traffic test (2 minutes)
docker compose run --rm lab39-k6 run /scripts/uniform.js
```

While the test runs, open Grafana (http://localhost:3001):

1. Navigate to the "Hot Partition Detection" dashboard
2. Observe the "Request Distribution" pie chart - it should show ~25% per shard
3. Check "Shard Latency p95" - all shards should have similar latency
4. Look at "Active Requests per Shard" - should be relatively balanced

**Expected Behavior:**
- Equal distribution across all 4 shards (~25% each)
- Similar p95 latency for all shards (~10-20ms)
- No hot keys detected

---

### Exercise 3: Create a Hot Partition with Zipfian Distribution

Real-world access patterns follow Zipfian distribution (power law) - a small number of items receive most of the traffic.

```bash
# Run Zipfian traffic test (2 minutes)
docker compose run --rm lab39-k6 run /scripts/zipfian.js
```

While running, observe in Grafana:
1. "Request Distribution" pie chart becomes uneven
2. One shard receives significantly more traffic
3. "Shard Latency p95" shows the hot shard with higher latency
4. "Top 10 Keys by Access Rate" shows user-0 dominating

**Key Observations:**
- `user-0` receives ~25-30% of all traffic
- The shard containing `user-0` shows elevated latency
- Other shards remain at baseline latency

```bash
# Check the shard statistics
for shard in 0 1 2 3; do
  echo "=== Shard $shard ==="
  curl -s "http://localhost:900$shard/stats" | jq '.hot_keys, .top_keys[:3]'
done
```

---

### Exercise 4: The Celebrity User Scenario

This is the classic "hot partition" scenario: a celebrity posts something viral, and millions of users access their profile simultaneously.

```bash
# Run celebrity user simulation
docker compose run --rm lab39-k6 run /scripts/celebrity.js
```

This test simulates:
- Background traffic: 30 rps across 500 normal users (uniform)
- Celebrity traffic: 100 rps to a single celebrity key

**What to observe in Grafana:**
1. **Shard Load %** - One shard jumps to 70-80% of total traffic
2. **Active Requests per Shard** - Celebrity's shard has 3-4x more concurrent requests
3. **Shard Latency p95** - Celebrity's shard latency spikes

```bash
# Find which shard has the celebrity
curl -s "http://localhost:8000/admin/shard-mapping/celebrity-user-kardashian" | jq

# Check that shard's stats
curl -s http://localhost:9000/stats | jq  # Adjust port based on shard
```

---

### Exercise 5: Detecting Hot Partitions with Traces

Use Jaeger to visualize the hot partition problem:

1. Open Jaeger: http://localhost:16686
2. Select service: `api-gateway`
3. Find a trace and note the `shard.id` attribute
4. Compare traces to the celebrity shard vs other shards
5. Notice the latency difference

**What to look for:**
- Traces to the hot shard show longer duration
- The `shard.id` span attribute identifies which shard handled the request
- Many traces will share the same `shard.id` value (the hot one)

---

### Exercise 6: Mitigation Strategy - Local Caching

One common mitigation for hot keys is to cache them at the gateway level.

```bash
# First, reset stats to get a clean baseline
curl -X POST http://localhost:8000/admin/reset
for shard in 0 1 2 3; do
  curl -X POST "http://localhost:900$shard/admin/reset"
done

# Run celebrity test WITH caching enabled
docker compose run --rm lab39-k6 run /scripts/with-caching.js
```

**What changes with caching:**
1. First request to celebrity key goes to shard (cache miss)
2. Subsequent requests served from gateway cache (cache hits)
3. Hot shard load drops dramatically
4. Overall latency improves

**Verify in Grafana:**
- "Cache Hit Rate" panel shows high hit rate for celebrity traffic
- "Shard Load %" becomes more balanced
- p95 latency drops significantly

```bash
# Check cache effectiveness
curl -s http://localhost:8000/stats | jq
```

---

### Exercise 7: Compare Before/After Caching

Let's quantify the improvement:

```bash
# Disable caching and run celebrity test
curl -X POST http://localhost:8000/admin/cache \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

echo "=== WITHOUT CACHING ==="
docker compose run --rm lab39-k6 run /scripts/celebrity.js 2>&1 | tail -20

# Enable caching and run again
curl -X POST http://localhost:8000/admin/cache \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "ttl_seconds": 60}'

echo "=== WITH CACHING ==="
docker compose run --rm lab39-k6 run /scripts/celebrity.js 2>&1 | tail -20
```

**Expected Results:**
| Metric | Without Caching | With Caching |
|--------|-----------------|--------------|
| p95 Latency | 200-500ms | 10-50ms |
| Hot Shard Load | 70-80% | 25-30% |
| Error Rate | Possible | Near zero |

---

## Key Takeaways

### 1. Hot Partitions Are Inevitable
Real-world access patterns are skewed. Popular items (celebrity users, viral content, trending products) will create hot spots.

### 2. Detection is Critical
You need visibility into per-partition metrics:
- Request rate per shard
- Latency per shard
- Active connections per shard
- Top-K key access patterns

### 3. Mitigation Strategies

| Strategy | Pros | Cons | Best For |
|----------|------|------|----------|
| **Caching** | Simple, effective | Stale data risk | Read-heavy workloads |
| **Key Splitting** | Spreads load | Application complexity | Write-heavy workloads |
| **Request Rate Limiting** | Protects backend | User experience | Abuse prevention |
| **Dynamic Rebalancing** | Optimal distribution | Complex implementation | Large-scale systems |

### 4. Metrics to Monitor

```promql
# Shard imbalance ratio (1.0 = perfect balance)
max(rate(shard_requests_total[1m])) / avg(rate(shard_requests_total[1m]))

# Hot key detection (keys getting >10% of traffic)
topk(5, rate(shard_key_access_total[1m]) / ignoring(key) sum(rate(shard_key_access_total[1m]))) > 0.1

# Latency disparity between shards
max(histogram_quantile(0.95, rate(shard_request_duration_seconds_bucket[1m]))) -
min(histogram_quantile(0.95, rate(shard_request_duration_seconds_bucket[1m])))
```

---

## Cleanup

```bash
docker compose down -v
```

---

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Metrics not appearing in Grafana
- Wait 15-30 seconds after starting traffic
- Check Prometheus targets: http://localhost:9090/targets

### High latency even with low traffic
- Check if previous test's hot keys are still in memory
- Reset all services: `docker compose restart`

### k6 connection errors
- Ensure all services are healthy: `docker compose ps`
- Check gateway health: `curl http://localhost:8000/health`

---

## Next Steps

- Implement key splitting for write-heavy hot keys
- Add circuit breakers to protect overloaded shards
- Explore consistent hashing with virtual nodes for better distribution
- Build alerting rules for hot partition detection

---

## Further Reading

- [The Anatomy of Hot Spotting in DynamoDB](https://aws.amazon.com/blogs/database/hot-partition-issues-in-amazon-dynamodb/)
- [Zipf's Law and Internet Traffic](https://www.cs.cornell.edu/home/kleinber/www00.pdf)
- [Consistent Hashing Explained](https://www.toptal.com/big-data/consistent-hashing)
