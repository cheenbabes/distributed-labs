# Lab 46: Quorum Reads/Writes

Understanding how distributed systems achieve consistency through quorum-based replication. In this lab, you'll configure N, R, W parameters and observe how they affect consistency, availability, and latency.

## What You'll Learn

- How quorum math (N, R, W) determines consistency guarantees
- The trade-off between consistency and latency
- How node failures affect quorum availability
- The difference between strict and sloppy quorum
- Version-based conflict resolution

## Background: The Quorum Formula

In a quorum system with N replicas:
- **N** = Total number of replicas
- **R** = Read quorum (minimum replicas that must respond to a read)
- **W** = Write quorum (minimum replicas that must acknowledge a write)

**Key rule for strong consistency:** `R + W > N`

This ensures that any read will see at least one replica that has the latest write.

```
Example with N=5:
- R=3, W=3: 3+3=6 > 5 ✓ Strongly consistent
- R=2, W=2: 2+2=4 < 5 ✗ May return stale data
- R=1, W=5: 1+5=6 > 5 ✓ Consistent (fast reads, slow writes)
- R=5, W=1: 5+1=6 > 5 ✓ Consistent (slow reads, fast writes)
```

## Architecture

```
                         ┌─────────────────────┐
                         │      Client UI      │
                         │    (port 8080)      │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │    Coordinator      │
                         │    (port 8000)      │
                         │  Implements Quorum  │
                         └──────────┬──────────┘
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          │           │             │             │           │
    ┌─────▼─────┐ ┌───▼───┐   ┌─────▼─────┐ ┌─────▼─────┐ ┌───▼───┐
    │ Replica 1 │ │ Rep 2 │   │ Replica 3 │ │ Replica 4 │ │ Rep 5 │
    │  :9001    │ │ :9002 │   │   :9003   │ │   :9004   │ │ :9005 │
    └───────────┘ └───────┘   └───────────┘ └───────────┘ └───────┘
```

Each replica is an independent in-memory key-value store with:
- Versioned writes (timestamp-based)
- Configurable artificial latency
- Failure injection (timeout, error, partial)

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
| Client UI | http://localhost:8080 | Interactive testing interface |
| Coordinator | http://localhost:8000 | Quorum coordinator API |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Strong Consistency (R+W > N)

Default configuration: N=5, R=3, W=3 (3+3=6 > 5)

1. Open the Client UI at http://localhost:8080
2. Write a key-value pair
3. Immediately read it back
4. Run the consistency test (10 iterations)

```bash
# Via curl
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"key": "test1", "value": "hello"}'

curl http://localhost:8000/read/test1
```

**Expected Result:** 100% consistency rate because R+W > N guarantees we always read at least one replica with the latest write.

**Check the math:** With R=3, W=3, at least one replica will have participated in both the write and the subsequent read.

---

### Exercise 2: Weak Consistency (R+W <= N)

Change the configuration to make it fast but potentially inconsistent.

```bash
# Set R=1, W=1 (fast but inconsistent)
curl -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"r": 1, "w": 1}'

# Verify
curl http://localhost:8000/config
```

Now R+W = 2 <= 5, so reads might miss the latest writes.

1. Run many rapid writes followed by reads
2. Observe in the Client UI's consistency test
3. Check Grafana for read conflict metrics

```bash
# Rapid write/read cycle
for i in {1..20}; do
  curl -s -X POST http://localhost:8000/write \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"fast-$i\", \"value\": \"value-$i\"}" &
done
wait
```

**Expected Result:** Some reads may return stale or null data because they might hit replicas that haven't received the write yet.

---

### Exercise 3: Node Failures and Quorum Math

With N=5, R=3, W=3:
- Can tolerate 2 replica failures for both reads and writes
- Minimum replicas needed = max(R, W) = 3

Let's test this:

```bash
# First, restore strong consistency
curl -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"r": 3, "w": 3}'

# Check current replica status
curl http://localhost:8000/replicas/status

# Kill one replica - operations should still work
curl -X POST http://localhost:9001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "error"}'

# Test writes and reads - should succeed
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"key": "after-failure", "value": "still-works"}'

curl http://localhost:8000/read/after-failure
```

Now kill a second replica:

```bash
# Kill second replica
curl -X POST http://localhost:9002/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "error"}'

# Test - should still work (3 replicas remaining >= 3 needed)
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"key": "two-down", "value": "barely-works"}'
```

Now kill a third - quorum should fail:

```bash
# Kill third replica
curl -X POST http://localhost:9003/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "error"}'

# This should fail - not enough replicas for quorum
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"key": "three-down", "value": "fails"}'
```

**Expected Result:** With only 2 healthy replicas, we can't achieve R=3 or W=3 quorum. Operations fail with a 503 error.

Restore replicas:

```bash
for port in 9001 9002 9003; do
  curl -X POST http://localhost:$port/admin/failure \
    -H "Content-Type: application/json" \
    -d '{"mode": "none"}'
done
```

---

### Exercise 4: Strict vs Sloppy Quorum

**Strict Quorum:** Must write to W of the original N replicas. If not enough original replicas are available, the operation fails.

**Sloppy Quorum:** Can write to any W available nodes, including temporary "hint" nodes. Improves availability but can lead to data being on unexpected nodes.

```bash
# Set sloppy quorum mode
curl -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "sloppy"}'

# Now kill 3 replicas
for port in 9001 9002 9003; do
  curl -X POST http://localhost:$port/admin/failure \
    -H "Content-Type: application/json" \
    -d '{"mode": "error"}'
done

# With sloppy quorum, operations might still succeed
# (depends on what W is - if W=3 and only 2 replicas are up, still fails)
curl -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"w": 2}'

curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"key": "sloppy-test", "value": "available"}'
```

**Real-world note:** In production systems like DynamoDB and Cassandra, sloppy quorum writes data to temporary nodes with "hints" to forward data to the original replicas when they recover.

Reset to strict mode and restore replicas:

```bash
curl -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "strict", "r": 3, "w": 3}'

for port in 9001 9002 9003; do
  curl -X POST http://localhost:$port/admin/failure \
    -H "Content-Type: application/json" \
    -d '{"mode": "none"}'
done
```

---

### Exercise 5: Read Conflicts and Version Resolution

When R > 1, the coordinator might receive different versions from different replicas. The coordinator resolves this by returning the highest version.

```bash
# First, write a value
curl -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"key": "conflict-test", "value": "version-1"}'

# Now inject different data directly to one replica (simulating a network partition recovery)
# This bypasses the coordinator and creates an inconsistency
curl -X POST http://localhost:9001/write \
  -H "Content-Type: application/json" \
  -d '{"key": "conflict-test", "value": "rogue-value", "version": 1}'

# Read through coordinator - should get the higher version
curl http://localhost:8000/read/conflict-test
```

Check the response for `had_conflicts: true` - this indicates the coordinator detected and resolved a version conflict.

View the data on each replica:

```bash
for port in 9001 9002 9003 9004 9005; do
  echo "Replica on port $port:"
  curl -s http://localhost:$port/data | jq '.data["conflict-test"]'
done
```

---

### Exercise 6: Latency Trade-offs

Different R/W configurations have different latency characteristics because you wait for R or W responses:

```bash
# Configuration 1: Balanced (R=3, W=3)
curl -X POST http://localhost:8000/config -H "Content-Type: application/json" -d '{"r": 3, "w": 3}'
echo "R=3, W=3 - Balanced latency"
for i in {1..5}; do
  curl -s -o /dev/null -w "Write: %{time_total}s\n" -X POST http://localhost:8000/write \
    -H "Content-Type: application/json" -d "{\"key\": \"latency-$i\", \"value\": \"test\"}"
  curl -s -o /dev/null -w "Read:  %{time_total}s\n" http://localhost:8000/read/latency-$i
done

# Configuration 2: Fast reads (R=1, W=5)
curl -X POST http://localhost:8000/config -H "Content-Type: application/json" -d '{"r": 1, "w": 5}'
echo "R=1, W=5 - Fast reads, slow writes"
for i in {1..5}; do
  curl -s -o /dev/null -w "Write: %{time_total}s\n" -X POST http://localhost:8000/write \
    -H "Content-Type: application/json" -d "{\"key\": \"fast-read-$i\", \"value\": \"test\"}"
  curl -s -o /dev/null -w "Read:  %{time_total}s\n" http://localhost:8000/read/fast-read-$i
done

# Configuration 3: Fast writes (R=5, W=1)
curl -X POST http://localhost:8000/config -H "Content-Type: application/json" -d '{"r": 5, "w": 1}'
echo "R=5, W=1 - Slow reads, fast writes"
for i in {1..5}; do
  curl -s -o /dev/null -w "Write: %{time_total}s\n" -X POST http://localhost:8000/write \
    -H "Content-Type: application/json" -d "{\"key\": \"fast-write-$i\", \"value\": \"test\"}"
  curl -s -o /dev/null -w "Read:  %{time_total}s\n" http://localhost:8000/read/fast-write-$i
done
```

**Observation:** Write latency is dominated by the W slowest replicas. Read latency is dominated by the R slowest replicas.

---

### Exercise 7: Load Testing Under Different Configurations

Run the k6 load test to observe behavior under load:

```bash
# Ensure strong consistency
curl -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"r": 3, "w": 3, "mode": "strict"}'

# Run load test
docker compose run --rm lab46-k6 run /scripts/quorum.js
```

Watch Grafana (http://localhost:3001) during the test to observe:
- Request rates
- Quorum latencies
- Per-replica response distributions
- Any quorum failures

Now introduce a slow replica and rerun:

```bash
# Make replica 3 slow
curl -X POST http://localhost:9003/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "timeout", "timeout_ms": 2000}'

# Rerun load test - observe increased latencies
docker compose run --rm lab46-k6 run /scripts/quorum.js
```

---

## Key Takeaways

1. **R + W > N guarantees strong consistency** - Any read overlaps with any write on at least one replica.

2. **Lower quorum = faster but weaker** - R=1, W=1 is fast but may return stale data.

3. **Fault tolerance = N - max(R, W)** - With N=5, R=3, W=3, you can lose 2 replicas.

4. **The slowest replica determines latency** - Quorum operations wait for the Rth or Wth response, not all responses.

5. **Sloppy quorum improves availability** - At the cost of temporarily writing to "wrong" nodes.

6. **Version conflicts are normal** - Systems must resolve them (last-writer-wins, vector clocks, etc.).

## Common Quorum Configurations

| Config | R+W vs N | Consistency | Availability | Use Case |
|--------|----------|-------------|--------------|----------|
| R=3, W=3, N=5 | 6 > 5 | Strong | Tolerates 2 failures | Default safe choice |
| R=1, W=1, N=3 | 2 < 3 | Eventual | Maximum | Caching, analytics |
| R=1, W=3, N=3 | 4 > 3 | Strong | Fast reads | Read-heavy workloads |
| R=3, W=1, N=3 | 4 > 3 | Strong | Fast writes | Write-heavy workloads |
| R=2, W=2, N=3 | 4 > 3 | Strong | Balanced | General purpose |

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Quorum always failing
Check replica health:
```bash
curl http://localhost:8000/replicas/status | jq
```

### Traces not appearing in Jaeger
- Wait 10-15 seconds after making requests
- Check OTEL collector: `docker compose logs lab46-otel-collector`

## Further Reading

- DDIA Chapter 5: Replication
- DynamoDB paper (Dynamo: Amazon's Highly Available Key-value Store)
- Cassandra consistency levels documentation
