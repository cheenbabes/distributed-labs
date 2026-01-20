# Lab 14: Eventual Consistency in Action

In this lab, you will visualize what eventual consistency really means in a distributed system. You will write data to a primary node and observe how that data propagates to replicas over time, experiencing stale reads and measuring the "consistency window."

## What You Will Learn

- How data propagates between replicas in an eventually consistent system
- What "stale reads" look like and why they happen
- How to measure the "consistency window" (time until all replicas agree)
- Strategies for achieving read-your-writes consistency
- The tradeoffs between consistency, availability, and latency

## Architecture

```
                                    ┌───────────────┐
                                    │    Client     │
                                    │  (Monitors)   │
                                    └───────┬───────┘
                                            │ polls
                    ┌───────────────────────┼───────────────────────┐
                    │                       │                       │
                    ▼                       ▼                       ▼
            ┌───────────────┐       ┌───────────────┐       ┌───────────────┐
            │    Primary    │       │   Replica 1   │       │   Replica 2   │
            │   (Writes)    │──────▶│  (500ms lag)  │       │ (1500ms lag)  │
            │   Port 8000   │       │   Port 8001   │       │   Port 8002   │
            └───────────────┘       └───────────────┘       └───────────────┘
                    │                       ▲                       ▲
                    │                       │                       │
                    └───────────────────────┴───────────────────────┘
                              async replication with delay
```

**Data Flow:**
1. Writes go to the Primary (single leader)
2. Primary asynchronously replicates to Replica 1 (500ms delay) and Replica 2 (1500ms delay)
3. Reads from replicas may return stale data during the replication window
4. Client service monitors all nodes and detects inconsistencies

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- jq (for JSON parsing)
- k6 (optional, for load testing)

## Quick Start

### 1. Start the Lab

```bash
cd labs/week-14-eventual-consistency
docker compose up --build -d
```

### 2. Verify Services Are Running

```bash
docker compose ps
```

All services should show as "healthy".

### 3. Access the UIs

| Service    | URL                    | Purpose                          |
|------------|------------------------|----------------------------------|
| Primary    | http://localhost:8000  | Write endpoint                   |
| Replica 1  | http://localhost:8001  | Read replica (500ms lag)         |
| Replica 2  | http://localhost:8002  | Read replica (1500ms lag)        |
| Client     | http://localhost:8003  | Consistency monitor              |
| Jaeger     | http://localhost:16686 | Distributed traces               |
| Prometheus | http://localhost:9090  | Metrics                          |
| Grafana    | http://localhost:3001  | Dashboards (admin/admin)         |

## Lab Exercises

### Exercise 1: Write Data and Watch It Propagate

First, let us establish what eventual consistency looks like by writing data and watching it spread to replicas.

```bash
# Write data to the primary
curl -s -X POST http://localhost:8000/data \
  -H "Content-Type: application/json" \
  -d '{"key": "greeting", "value": "Hello, World!"}' | jq

# Immediately check all sources
echo "Primary:"; curl -s http://localhost:8000/version | jq
echo "Replica 1:"; curl -s http://localhost:8001/version | jq
echo "Replica 2:"; curl -s http://localhost:8002/version | jq
```

**Expected:** The primary has version 1 immediately. Replica 1 will have version 1 after ~500ms. Replica 2 will have version 1 after ~1500ms.

Wait a few seconds and check again:

```bash
# After waiting
echo "Primary:"; curl -s http://localhost:8000/version | jq
echo "Replica 1:"; curl -s http://localhost:8001/version | jq
echo "Replica 2:"; curl -s http://localhost:8002/version | jq
```

**Question:** What happens if you write while replicas are still catching up?

---

### Exercise 2: Observe Stale Reads During Replication

Use the client service to write and immediately read, demonstrating stale reads.

```bash
# Write and immediately read from all sources
curl -s -X POST http://localhost:8003/write-and-read \
  -H "Content-Type: application/json" \
  -d '{"key": "test", "value": "immediate-read-test"}' | jq
```

**Expected Output:**
```json
{
  "write_result": { "version": 2, ... },
  "immediate_reads": {
    "lab14-replica1": { "version": 1, "has_new_data": false },
    "lab14-replica2": { "version": 1, "has_new_data": false }
  },
  "stale_read_count": 2,
  "note": "Stale reads are expected - this demonstrates eventual consistency!"
}
```

**Key Insight:** Both replicas returned stale data because replication had not completed yet!

---

### Exercise 3: Measure the Consistency Window

The "consistency window" is the time between a write and when all replicas have that data.

```bash
# Write and wait for all replicas to be consistent
curl -s -X POST "http://localhost:8003/write-and-wait?timeout_ms=5000" \
  -H "Content-Type: application/json" \
  -d '{"key": "measured", "value": "consistency-test"}' | jq
```

**Expected Output:**
```json
{
  "write_result": { "version": 3, ... },
  "consistency_achieved": true,
  "consistency_time_ms": 1523.45,
  "attempts": 31
}
```

The `consistency_time_ms` tells you how long it took for all replicas to catch up. This should be approximately equal to your slowest replica's replication delay (1500ms for Replica 2).

Run this multiple times and observe the consistency window:

```bash
for i in {1..5}; do
  echo "Test $i:"
  curl -s -X POST "http://localhost:8003/write-and-wait?timeout_ms=5000" \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"test-$i\", \"value\": \"run-$i\"}" | jq '.consistency_time_ms'
done
```

---

### Exercise 4: Increase Replication Lag and Observe Impact

Let us increase the replication delay and see how it affects consistency.

```bash
# Check current delays
curl -s http://localhost:8000/admin/replication-delays | jq

# Increase Replica 2's delay to 3 seconds
curl -s -X POST http://localhost:8000/admin/replication-delay \
  -H "Content-Type: application/json" \
  -d '{"replica_url": "http://lab14-replica2:8002", "delay_ms": 3000}' | jq

# Now test the consistency window
curl -s -X POST "http://localhost:8003/write-and-wait?timeout_ms=10000" \
  -H "Content-Type: application/json" \
  -d '{"key": "slow", "value": "slow-replication"}' | jq
```

**Expected:** The consistency window should now be ~3000ms instead of ~1500ms.

**Watch in Grafana:** Open http://localhost:3001 and observe:
- The "Version: Primary vs Replicas" panel showing Replica 2 lagging further behind
- The "Replication Lag" panel showing increased lag
- The "Stale Read Rate %" gauge increasing

Reset to original delays:

```bash
curl -s -X POST http://localhost:8000/admin/replication-delay \
  -H "Content-Type: application/json" \
  -d '{"replica_url": "http://lab14-replica2:8002", "delay_ms": 1500}' | jq
```

---

### Exercise 5: Implement Read-Your-Writes Consistency

There are two strategies for achieving read-your-writes consistency:

**Strategy A: Read from Primary after writing**

```bash
# Write to primary
WRITE_RESULT=$(curl -s -X POST http://localhost:8000/data \
  -H "Content-Type: application/json" \
  -d '{"key": "ryw", "value": "read-your-writes-test"}')

echo "Write result: $WRITE_RESULT"

# Read from primary (guaranteed to see your write)
curl -s http://localhost:8000/data | jq

# Compare to replicas (may be stale)
curl -s http://localhost:8001/data | jq
curl -s http://localhost:8002/data | jq
```

**Strategy B: Wait for consistency**

```bash
# Use the write-and-wait endpoint
curl -s -X POST "http://localhost:8003/write-and-wait?timeout_ms=5000" \
  -H "Content-Type: application/json" \
  -d '{"key": "wait-for-it", "value": "patient-value"}' | jq
```

**Discussion:**
- Strategy A trades availability for consistency (primary is a single point of failure)
- Strategy B trades latency for consistency (you wait for replication)
- Neither is "better" - it depends on your requirements

---

### Exercise 6: Load Test - Stale Read Rate Under Load

Generate sustained load to see consistency patterns:

```bash
# Run the k6 load test
docker compose run --rm lab14-k6 run /scripts/read-consistency.js
```

While the test runs, watch the Grafana dashboard at http://localhost:3001:
- Stale vs Fresh Reads rate
- Consistency Window histogram
- Replication lag trends

**Questions to answer:**
1. What percentage of reads are stale during continuous writes?
2. What is the p99 consistency window?
3. How does the stale read rate change with different replication delays?

---

## Key Takeaways

### 1. Eventual Consistency is a Spectrum

Not all eventually consistent systems are the same. The "eventuality" depends on:
- Network latency
- Replication delays (artificial or real)
- System load
- Number of replicas

### 2. Stale Reads Are Normal

In an eventually consistent system, reading stale data is expected behavior, not a bug. Your application must be designed to handle this.

### 3. Consistency Windows Are Measurable

You can (and should) measure your consistency window:
- Track replication lag metrics
- Set SLOs for maximum staleness
- Alert when replication falls behind

### 4. Read-Your-Writes Requires Effort

Achieving read-your-writes consistency in an eventually consistent system requires one of:
- Reading from the primary (sacrifices availability)
- Waiting for replication (sacrifices latency)
- Sticky sessions/routing (adds complexity)
- Version tracking (client tracks what it wrote)

### 5. The CAP Theorem in Action

This lab demonstrates the consistency-availability tradeoff:
- **High Availability:** Read from any replica (may be stale)
- **Strong Consistency:** Read from primary or wait (higher latency, single point of failure)

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
- Check that otel-collector is running: `docker compose logs lab14-otel-collector`

### Replicas never become consistent

Check that replication is working:

```bash
docker compose logs lab14-primary | grep "Replication"
docker compose logs lab14-replica1 | grep "applied"
```

### Reset all data

```bash
curl -s -X POST http://localhost:8000/admin/reset | jq
```

## Next Steps

After completing this lab, consider exploring:
- **Conflict Resolution:** What happens when you can write to multiple nodes?
- **Vector Clocks:** How to track causality in distributed systems
- **CRDTs:** Conflict-free replicated data types for automatic conflict resolution
- **Consensus Protocols:** Paxos, Raft, and how to achieve strong consistency
