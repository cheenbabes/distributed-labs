# Lab 30: Failover Simulation

When your primary database dies, what happens? In this lab, you will build and observe a complete primary/replica failover system, including leader election, automatic failover, and the data consistency challenges that come with it.

## What You Will Learn

- How primary/replica replication works in distributed databases
- Leader election using distributed locks (Redis)
- Automatic failover detection and execution
- Understanding RTO (Recovery Time Objective) and RPO (Recovery Point Objective)
- Split-brain scenarios and how to detect them
- The tradeoffs between availability and consistency during failover

## Architecture

```
                                  ┌─────────────────────┐
                                  │  Failover Controller│
                                  │  (Leader Election)  │
                                  └─────────┬───────────┘
                                            │ monitors
                                            ▼
┌──────────┐     ┌─────────────┐     ┌─────────────┐
│  Client  │────▶│   Gateway   │────▶│  Primary DB │◄───────┐
└──────────┘     │  (Router)   │     │  (Writes)   │        │
                 └──────┬──────┘     └─────────────┘        │
                        │                                    │ replicates
                        │ reads                              │
                        ▼                                    │
                 ┌─────────────┐     ┌─────────────┐        │
                 │  Replica 1  │◄────│  Replica 2  │◄───────┘
                 │  (Reads)    │     │  (Reads)    │
                 └─────────────┘     └─────────────┘
```

### Components

1. **Primary DB**: Accepts all writes, replicates to replicas
2. **Replica 1 & 2**: Read-only replicas with configurable replication lag
3. **Failover Controller**: Monitors primary health, triggers automatic failover
4. **Client Gateway**: Routes reads to replicas, writes to primary
5. **Redis**: Shared state for leader election and replication coordination

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- k6 (optional, for load testing)

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

### 2. Verify All Services Are Running

```bash
docker compose ps
```

All services should show as "healthy".

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Gateway | http://localhost:8000 | API entry point |
| Primary DB | http://localhost:8001 | Primary database |
| Replica 1 | http://localhost:8002 | First replica |
| Replica 2 | http://localhost:8003 | Second replica |
| Failover Controller | http://localhost:8010 | Failover management |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Normal Operation with Reads to Replicas

First, let's understand how the system works under normal conditions.

```bash
# Write some data through the gateway
curl -s -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"key": "user:1", "value": {"name": "Alice", "email": "alice@example.com"}}' | jq

# Read from a replica (default behavior)
curl -s http://localhost:8000/read/user:1 | jq

# Read from primary (for strong consistency)
curl -s "http://localhost:8000/read/user:1?use_primary=true" | jq
```

**Check cluster status:**

```bash
curl -s http://localhost:8010/status | jq
```

**Questions to consider:**
- Which node served your read request?
- What is the current replication lag?
- What happens if you read immediately after writing?

---

### Exercise 2: Kill Primary, Observe Automatic Failover

Now let's see what happens when the primary fails.

```bash
# First, write some data to establish a baseline
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/write \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"test:$i\", \"value\": {\"index\": $i}}" > /dev/null
  echo "Wrote test:$i"
done

# Check current primary sequence
curl -s http://localhost:8001/sequence | jq

# Simulate primary failure
curl -s -X POST http://localhost:8001/admin/simulate-failure \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' | jq

# Watch the failover happen (check every second)
for i in {1..10}; do
  echo "=== Check $i ==="
  curl -s http://localhost:8010/status | jq '.{primary_healthy, consecutive_failures, current_primary}'
  sleep 1
done
```

**Observe in Grafana:**
1. Open http://localhost:3001
2. Go to the "Failover Simulation Dashboard"
3. Watch the "Primary Health" panel turn red
4. See the "Consecutive Failures" increase
5. Observe when failover completes

**Questions:**
- How many health check failures before failover triggered?
- Which replica was promoted?
- Were there any errors during failover?

---

### Exercise 3: Measure Failover Time (RTO)

Recovery Time Objective (RTO) is how long it takes to recover from failure.

```bash
# Reset the cluster first
curl -s -X POST http://localhost:8010/admin/reset-cluster | jq

# Wait for reset to complete
sleep 5

# Start a timer and trigger failure
START_TIME=$(date +%s.%N)

# Simulate failure
curl -s -X POST http://localhost:8001/admin/simulate-failure \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' > /dev/null

# Poll until writes succeed again
while true; do
  RESULT=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/write \
    -H "Content-Type: application/json" \
    -d '{"key": "rto-test", "value": {"test": true}}')
  if [ "$RESULT" = "200" ]; then
    END_TIME=$(date +%s.%N)
    RTO=$(echo "$END_TIME - $START_TIME" | bc)
    echo "RTO: ${RTO} seconds"
    break
  fi
  sleep 0.5
done
```

**Check the RTO metric in Prometheus:**
```
rto_seconds
```

**What affects RTO?**
- Health check interval (default: 1 second)
- Failover threshold (default: 3 failures)
- Time to promote replica
- Time for gateway to discover new primary

---

### Exercise 4: Check Data Loss During Failover (RPO)

Recovery Point Objective (RPO) measures potential data loss.

```bash
# Reset cluster
curl -s -X POST http://localhost:8010/admin/reset-cluster | jq
sleep 5

# Write data rapidly while simulating failure
echo "Writing data rapidly..."
for i in {1..50}; do
  curl -s -X POST http://localhost:8000/write \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"rpo-test:$i\", \"value\": {\"seq\": $i}}" &

  # Trigger failure after 25 writes
  if [ $i -eq 25 ]; then
    curl -s -X POST http://localhost:8001/admin/simulate-failure \
      -H "Content-Type: application/json" \
      -d '{"enabled": true}' > /dev/null &
  fi
done

wait
sleep 5

# Check which writes succeeded
echo "Checking which writes were persisted..."
FOUND=0
MISSING=0
for i in {1..50}; do
  RESULT=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/read/rpo-test:$i)
  if [ "$RESULT" = "200" ]; then
    FOUND=$((FOUND + 1))
  else
    MISSING=$((MISSING + 1))
    echo "Missing: rpo-test:$i"
  fi
done

echo "Found: $FOUND, Missing: $MISSING"
echo "Data loss (RPO): $MISSING writes"
```

**Check RPO metric:**
```
rpo_sequences_lost
```

**Factors affecting RPO:**
- Replication lag
- In-flight writes during failure
- Async vs sync replication

---

### Exercise 5: Simulate Split-Brain, Observe Detection

Split-brain occurs when multiple nodes think they are the primary.

```bash
# Check for split-brain
curl -s http://localhost:8010/admin/split-brain-status | jq

# Simulate split-brain scenario
curl -s -X POST http://localhost:8010/admin/simulate-split-brain \
  -H "Content-Type: application/json" \
  -d '{"simulate": true}' | jq

# Check detection
curl -s http://localhost:8010/admin/split-brain-status | jq
```

**In Grafana:**
- Look at the "Split-Brain Detected" panel
- It should turn red when split-brain is simulated

**To understand real split-brain scenarios:**

1. Split-brain can occur during network partitions
2. Both old primary (if it recovers) and new primary accept writes
3. Data diverges and cannot be automatically reconciled
4. Prevention requires:
   - Fencing (STONITH - Shoot The Other Node In The Head)
   - Quorum-based decisions
   - Distributed consensus (Raft, Paxos)

```bash
# Disable split-brain simulation
curl -s -X POST http://localhost:8010/admin/simulate-split-brain \
  -H "Content-Type: application/json" \
  -d '{"simulate": false}' | jq
```

---

## Key Metrics

| Metric | Description | Location |
|--------|-------------|----------|
| `primary_health` | Primary node health (1=healthy, 0=unhealthy) | Prometheus/Grafana |
| `replication_lag_ms` | Current replication lag in milliseconds | Prometheus/Grafana |
| `failover_count_total` | Total number of failovers performed | Prometheus/Grafana |
| `split_brain_detected` | Whether split-brain was detected | Prometheus/Grafana |
| `rto_seconds` | Recovery Time Objective | Prometheus/Grafana |
| `rpo_sequences_lost` | Sequences potentially lost during failover | Prometheus/Grafana |

## Key Takeaways

1. **Failover is not instant** - There is always some downtime (RTO) while the system detects failure and promotes a replica.

2. **Data loss is possible** - Unless you use synchronous replication, some writes may be lost during failover (RPO > 0).

3. **Reads can continue** - Even when the primary is down, reads can be served from replicas (eventual consistency).

4. **Leader election is critical** - Without proper leader election, you risk split-brain scenarios.

5. **Monitor everything** - You cannot improve what you do not measure. Track RTO, RPO, and replication lag.

6. **Test your failover** - Regularly practice failover in non-production environments. Chaos engineering!

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Failover not triggering
- Check the failover controller logs: `docker compose logs lab30-failover-controller`
- Verify health check is working: `curl http://localhost:8001/health`
- Check consecutive failures: `curl http://localhost:8010/status | jq '.consecutive_failures'`

### High replication lag
- This is intentional for the lab to demonstrate RPO
- Check replica logs: `docker compose logs lab30-replica-1`

### Split-brain detection false positives
- Reset the cluster: `curl -X POST http://localhost:8010/admin/reset-cluster`

## Next Steps

After completing this lab, consider exploring:
- Implementing synchronous replication for zero RPO
- Adding more failover controllers for high availability
- Implementing automatic fencing mechanisms
- Testing with real network partitions using tools like `tc` or Toxiproxy
