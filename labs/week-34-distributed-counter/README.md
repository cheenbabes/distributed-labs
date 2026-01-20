# Lab 34: Netflix Distributed Counter

How does Netflix count billions of video views across multiple regions without losing data or sacrificing performance? In this lab, you will build and experiment with a CRDT-based distributed counter system that demonstrates the same principles Netflix uses at scale.

## What You Will Learn

- How CRDTs (Conflict-free Replicated Data Types) enable conflict-free counting
- The trade-offs between eventual consistency and strong consistency
- How distributed systems maintain availability during network partitions
- Why Netflix chooses eventual consistency for high-volume counting

## Architecture

```
                           ┌─────────────────┐
                           │    Gateway      │
                           │  (Load Balancer)│
                           └────────┬────────┘
                                    │
           ┌────────────────────────┼────────────────────────┐
           │                        │                        │
           ▼                        ▼                        ▼
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│  Counter US-East    │  │  Counter US-West    │  │  Counter EU-West    │
│  (G-Counter CRDT)   │  │  (G-Counter CRDT)   │  │  (G-Counter CRDT)   │
└─────────┬───────────┘  └─────────┬───────────┘  └─────────┬───────────┘
          │                        │                        │
          └────────────────────────┼────────────────────────┘
                                   │
                          Background Sync (5s)
                                   │
                           ┌───────▼───────┐
                           │  Coordinator  │
                           │ (Global View) │
                           └───────────────┘
```

### How It Works

1. **G-Counter CRDT**: Each region maintains a vector of counts indexed by node ID
   - Increment: Add to your own count
   - Merge: Take max of each node's count
   - Query: Sum all counts

2. **Background Sync**: Regions periodically exchange state and merge
   - During normal operation: All regions converge within seconds
   - During partition: Local counting continues, sync resumes when healed

3. **Conflict Resolution**: CRDTs guarantee automatic, deterministic conflict resolution
   - No coordination needed during writes
   - Merge is commutative, associative, and idempotent

## Prerequisites

- Docker and Docker Compose
- curl (for API calls)
- jq (for JSON formatting)
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
| Gateway | http://localhost:8080 | API entry point |
| Coordinator | http://localhost:8000 | Global view |
| US-East Counter | http://localhost:8001 | Regional counter |
| US-West Counter | http://localhost:8002 | Regional counter |
| EU-West Counter | http://localhost:8003 | Regional counter |
| Consistent Counter | http://localhost:8004 | Strong consistency comparison |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Basic Counting

First, let us see how the distributed counter works under normal conditions.

```bash
# Increment the counter
curl -s -X POST http://localhost:8080/api/increment \
  -H "Content-Type: application/json" \
  -d '{"counter_name": "views", "amount": 1}' | jq

# Check which region handled it
curl -s http://localhost:8080/api/counter/views | jq
```

**Expected:** You will see the increment routed to one region, with the total count shown.

Make several increments and observe:
```bash
for i in {1..10}; do
  curl -s -X POST http://localhost:8080/api/increment \
    -H "Content-Type: application/json" \
    -d '{"counter_name": "views", "amount": 1}' | jq '.region, .local_count'
done
```

**Question:** Are increments distributed across regions? Check the global view to see all regions:
```bash
curl -s http://localhost:8080/api/counter/views | jq
```

---

### Exercise 2: Observe Eventual Consistency

Let us see how counts converge across regions.

```bash
# Increment directly on US-East
curl -s -X POST http://localhost:8001/increment \
  -H "Content-Type: application/json" \
  -d '{"counter_name": "test", "amount": 100}' | jq

# Immediately check US-West (may not have synced yet)
curl -s http://localhost:8002/counter/test | jq

# Wait for sync (5 seconds) then check again
sleep 6
curl -s http://localhost:8002/counter/test | jq
```

**Expected:** Initially, US-West may show a lower count. After sync, both regions show the same total.

**Observation:** Check the "Synced Count by Region" panel in Grafana to visualize convergence.

---

### Exercise 3: Simulate Network Partition

This is where CRDTs really shine. Let us isolate one region and see what happens.

```bash
# Enable partition on US-East (it stops syncing but keeps counting)
curl -s -X POST http://localhost:8080/admin/partition/us-east?enabled=true | jq

# Increment on all regions
for i in {1..5}; do
  curl -s -X POST http://localhost:8080/api/increment \
    -H "Content-Type: application/json" \
    -d '{"counter_name": "partition_test", "amount": 1}' > /dev/null
done

# Check global status - note the convergence lag
curl -s http://localhost:8080/api/counter/partition_test | jq
```

**Expected:**
- `converged: false` - regions have different counts
- US-East continues accepting writes locally
- Other regions sync normally with each other

Now let us heal the partition:
```bash
# Disable partition
curl -s -X POST http://localhost:8080/admin/partition/us-east?enabled=false | jq

# Force immediate sync
curl -s -X POST http://localhost:8080/admin/force-sync | jq

# Check convergence
sleep 2
curl -s http://localhost:8080/api/counter/partition_test | jq
```

**Expected:** After sync, all regions converge to the same total. No data is lost!

**Key Insight:** This is the CAP theorem in action - we chose Availability and Partition tolerance (AP), accepting eventual consistency.

---

### Exercise 4: Compare Consistency Models

Let us compare eventual consistency (fast) vs strong consistency (slow but always current).

```bash
# Run the built-in comparison
curl -s -X POST http://localhost:8080/api/compare \
  -H "Content-Type: application/json" \
  -d '{"counter_name": "benchmark", "iterations": 20}' | jq
```

**Expected output:**
```json
{
  "eventual_consistency": {
    "description": "Single region write, background sync",
    "avg_ms": 5.2,
    "p99_ms": 12.3
  },
  "strong_consistency": {
    "description": "Write to all regions, wait for all",
    "avg_ms": 45.8,
    "p99_ms": 89.2
  },
  "speedup": 8.8,
  "conclusion": "Eventual consistency is ~8.8x faster"
}
```

**Question:** Why would Netflix choose eventual consistency for view counts? Consider:
- View counts do not need to be immediately accurate
- Users do not notice if the count is a few seconds behind
- The performance difference at scale is enormous

---

### Exercise 5: Load Test and Observe Metrics

Generate sustained load to see the system under stress.

```bash
# Run basic load test
docker compose run --rm lab34-k6 run /scripts/basic.js
```

While the test runs, observe in Grafana (http://localhost:3001):
1. **Increment Rate** - How many writes per second
2. **Sync Lag by Region** - How far behind each region is
3. **Sync Operations Rate** - Success vs errors

For a more advanced test comparing consistency models:
```bash
docker compose run --rm lab34-k6 run /scripts/comparison.js
```

---

### Exercise 6: Deep Dive - CRDT Mechanics

Let us examine the G-Counter state directly.

```bash
# Get raw counter state from each region
echo "=== US-East ==="
curl -s http://localhost:8001/counter/views | jq '.nodes'

echo "=== US-West ==="
curl -s http://localhost:8002/counter/views | jq '.nodes'

echo "=== EU-West ==="
curl -s http://localhost:8003/counter/views | jq '.nodes'
```

**Expected:** Each region knows about all nodes' counts. The `nodes` object shows:
```json
{
  "us-east-1": 15,
  "us-west-1": 12,
  "eu-west-1": 18
}
```

**Key Properties:**
1. **Commutative**: Merge order does not matter
2. **Associative**: Grouping does not matter
3. **Idempotent**: Merging twice has no effect

---

## Key Metrics to Watch

| Metric | Description | Normal Value |
|--------|-------------|--------------|
| `counter_local_count` | This node's contribution | Increases with local writes |
| `counter_synced_count` | Total after merge | Converges across regions |
| `counter_sync_lag_ms` | Time since last sync | < sync_interval (5000ms) |
| `counter_conflicts_resolved` | Merge conflicts | Low is good |
| `counter_sync_operations_total` | Sync attempts by status | Most should be success |

## Key Takeaways

1. **CRDTs enable conflict-free distributed counting** - No coordination needed for writes

2. **Eventual consistency is a trade-off** - Faster writes, but reads may be stale

3. **Partitions are survivable** - Local counting continues, data merges when healed

4. **Netflix chooses availability** - For view counts, eventual consistency is the right choice

5. **The math is beautiful** - G-Counters are provably convergent

## Real-World Applications at Netflix

- **Video view counts**: Billions of increments per day, eventual consistency is fine
- **API rate limiting**: Per-region counting with periodic sync
- **A/B test metrics**: Distributed counters for experiment data
- **Playback analytics**: Real-time counting across CDN edges

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Counters not syncing
```bash
# Check partition status
curl -s http://localhost:8080/api/status | jq

# Force sync
curl -s -X POST http://localhost:8080/admin/force-sync | jq
```

### Metrics not appearing
```bash
# Check Prometheus targets
open http://localhost:9090/targets
```

## Further Reading

- [CRDTs: Consistency without consensus](https://arxiv.org/abs/1805.06358)
- [Netflix Tech Blog: Distributed Systems](https://netflixtechblog.com/)
- [CAP Theorem Explained](https://en.wikipedia.org/wiki/CAP_theorem)

## Next Lab

[Lab 35: Circuit Breakers and Bulkheads](../week-35-circuit-breakers/) - Learn how Netflix prevents cascading failures.
