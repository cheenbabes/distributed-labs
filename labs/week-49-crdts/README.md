# Lab 49: CRDTs (Conflict-free Replicated Data Types)

How do you maintain consistency across distributed replicas without coordination? CRDTs are self-merging data structures that automatically resolve conflicts, enabling eventual consistency without consensus protocols.

## What You'll Learn

- How CRDTs achieve eventual consistency without coordination
- The difference between state-based and operation-based CRDTs
- Implementing G-Counters (grow-only), PN-Counters (increment/decrement), and LWW-Registers (last-write-wins)
- How CRDTs handle network partitions and merge divergent state
- The mathematical properties that make CRDTs work (commutativity, associativity, idempotence)

## Architecture

```
                                    ┌─────────────────┐
                                    │  Client Service │
                                    │   (Port 8020)   │
                                    └────────┬────────┘
                                             │
                    ┌────────────────────────┼────────────────────────┐
                    │                        │                        │
                    ▼                        ▼                        ▼
           ┌───────────────┐        ┌───────────────┐        ┌───────────────┐
           │   Replica 1   │        │   Replica 2   │        │   Replica 3   │
           │  (Port 8001)  │        │  (Port 8002)  │        │  (Port 8003)  │
           │               │        │               │        │               │
           │ ┌───────────┐ │        │ ┌───────────┐ │        │ ┌───────────┐ │
           │ │ G-Counter │ │        │ │ G-Counter │ │        │ │ G-Counter │ │
           │ ├───────────┤ │        │ ├───────────┤ │        │ ├───────────┤ │
           │ │PN-Counter │ │        │ │PN-Counter │ │        │ │PN-Counter │ │
           │ ├───────────┤ │        │ ├───────────┤ │        │ ├───────────┤ │
           │ │LWW-Register│ │        │ │LWW-Register│ │        │ │LWW-Register│ │
           │ └───────────┘ │        │ └───────────┘ │        │ └───────────┘ │
           └───────┬───────┘        └───────┬───────┘        └───────┬───────┘
                   │                        │                        │
                   └────────────────────────┼────────────────────────┘
                                            │
                                   ┌────────▼────────┐
                                   │  Sync Service   │
                                   │  (Port 8010)    │
                                   │                 │
                                   │  Anti-entropy   │
                                   │  reconciliation │
                                   └─────────────────┘
```

Each replica maintains its own state that can be independently updated. The sync service periodically merges state across replicas using anti-entropy protocol.

## Prerequisites

- Docker and Docker Compose
- curl and jq (for API testing)
- Basic understanding of distributed systems concepts

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
| Client API | http://localhost:8020 | Client for CRDT operations |
| Replica 1 | http://localhost:8001 | Direct replica access |
| Replica 2 | http://localhost:8002 | Direct replica access |
| Replica 3 | http://localhost:8003 | Direct replica access |
| Sync Service | http://localhost:8010 | Sync control |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

---

## Background: What Are CRDTs?

CRDTs are data structures designed for distributed systems where:
- Replicas can be updated independently (no coordination needed)
- Updates are eventually propagated to all replicas
- All replicas converge to the same state

### Key Properties

1. **Commutativity**: The order of operations doesn't matter
2. **Associativity**: Grouping of operations doesn't matter
3. **Idempotence**: Applying the same operation multiple times has the same effect as applying it once

### Types of CRDTs

- **State-based (CvRDT)**: Replicas exchange full state and merge using a join function
- **Operation-based (CmRDT)**: Replicas exchange operations that are applied in causal order

This lab implements state-based CRDTs.

---

## Lab Exercises

### Exercise 1: G-Counter (Grow-Only Counter)

The simplest CRDT - a counter that can only increase. Each replica maintains a map of `{replica_id: count}`. The total value is the sum of all counts.

**Increment on different replicas:**

```bash
# Increment on replica-1
curl -X POST http://localhost:8001/gcounter/visits/increment \
  -H "Content-Type: application/json" \
  -d '{"amount": 5}' | jq

# Increment on replica-2
curl -X POST http://localhost:8002/gcounter/visits/increment \
  -H "Content-Type: application/json" \
  -d '{"amount": 3}' | jq

# Increment on replica-3
curl -X POST http://localhost:8003/gcounter/visits/increment \
  -H "Content-Type: application/json" \
  -d '{"amount": 7}' | jq
```

**Check values before sync:**

```bash
echo "Replica 1:" && curl -s http://localhost:8001/gcounter/visits | jq
echo "Replica 2:" && curl -s http://localhost:8002/gcounter/visits | jq
echo "Replica 3:" && curl -s http://localhost:8003/gcounter/visits | jq
```

Notice each replica only sees its own increments.

**Trigger sync and check again:**

```bash
curl -X POST http://localhost:8010/sync | jq

# Wait for sync
sleep 2

echo "After sync:"
echo "Replica 1:" && curl -s http://localhost:8001/gcounter/visits | jq '.value'
echo "Replica 2:" && curl -s http://localhost:8002/gcounter/visits | jq '.value'
echo "Replica 3:" && curl -s http://localhost:8003/gcounter/visits | jq '.value'
```

**Expected:** All replicas converge to 15 (5 + 3 + 7).

**How G-Counter merge works:**
```
Replica 1: {replica-1: 5}           → value = 5
Replica 2: {replica-2: 3}           → value = 3
Replica 3: {replica-3: 7}           → value = 7

After merge (take max of each key):
All replicas: {replica-1: 5, replica-2: 3, replica-3: 7} → value = 15
```

---

### Exercise 2: PN-Counter (Positive-Negative Counter)

A counter that supports both increment and decrement. Internally uses two G-Counters: one for increments (P) and one for decrements (N). Value = P - N.

**Create a PN-Counter with mixed operations:**

```bash
# Replica 1: +10
curl -X POST http://localhost:8001/pncounter/stock/increment \
  -H "Content-Type: application/json" -d '{"amount": 10}' | jq

# Replica 2: +5, then -3
curl -X POST http://localhost:8002/pncounter/stock/increment \
  -H "Content-Type: application/json" -d '{"amount": 5}' | jq
curl -X POST http://localhost:8002/pncounter/stock/decrement \
  -H "Content-Type: application/json" -d '{"amount": 3}' | jq

# Replica 3: -2
curl -X POST http://localhost:8003/pncounter/stock/decrement \
  -H "Content-Type: application/json" -d '{"amount": 2}' | jq
```

**Check internal state:**

```bash
curl -s http://localhost:8001/pncounter/stock | jq
```

Notice the `state` object with `p` (positive) and `n` (negative) maps.

**Sync and verify:**

```bash
curl -X POST http://localhost:8010/sync | jq
sleep 2

# Expected: 10 + 5 - 3 - 2 = 10
echo "Final value:" && curl -s http://localhost:8001/pncounter/stock | jq '.value'
```

---

### Exercise 3: LWW-Register (Last-Write-Wins)

A register that holds a single value. On conflict, the value with the highest timestamp wins.

**Write concurrent values:**

```bash
# Write to each replica with different values
curl -X POST http://localhost:8001/lww/config/set \
  -H "Content-Type: application/json" \
  -d '{"value": "value-from-replica-1"}' | jq

sleep 0.1  # Small delay to create timestamp ordering

curl -X POST http://localhost:8002/lww/config/set \
  -H "Content-Type: application/json" \
  -d '{"value": "value-from-replica-2"}' | jq

sleep 0.1

curl -X POST http://localhost:8003/lww/config/set \
  -H "Content-Type: application/json" \
  -d '{"value": "value-from-replica-3"}' | jq
```

**Check which value "won":**

```bash
curl -X POST http://localhost:8010/sync | jq
sleep 2

echo "Replica 1:" && curl -s http://localhost:8001/lww/config | jq '.current'
echo "Replica 2:" && curl -s http://localhost:8002/lww/config | jq '.current'
echo "Replica 3:" && curl -s http://localhost:8003/lww/config | jq '.current'
```

**Expected:** The value with the highest timestamp wins. With the delays above, replica-3's value should win.

**Question:** What happens with truly concurrent writes (same timestamp)?

The implementation uses replica ID as a tiebreaker - higher replica ID wins.

---

### Exercise 4: Network Partition and Merge

This is where CRDTs shine - they can continue operating during partitions and automatically merge when connectivity is restored.

**Step 1: Create baseline state**

```bash
# Initialize counter
curl -X POST http://localhost:8001/gcounter/partition-test/increment \
  -H "Content-Type: application/json" -d '{"amount": 10}' | jq

curl -X POST http://localhost:8010/sync
sleep 2

echo "Initial state:"
curl -s http://localhost:8001/gcounter/partition-test | jq '.value'
```

**Step 2: Partition replica-1**

```bash
# Enable network partition on replica-1
curl -X POST http://localhost:8001/admin/partition \
  -H "Content-Type: application/json" -d '{"enabled": true}' | jq

echo "Replica-1 partition status:"
curl -s http://localhost:8001/admin/partition | jq
```

**Step 3: Make updates during partition**

```bash
# Update partitioned replica
curl -X POST http://localhost:8001/gcounter/partition-test/increment \
  -H "Content-Type: application/json" -d '{"amount": 100}' | jq

# Update connected replicas
curl -X POST http://localhost:8002/gcounter/partition-test/increment \
  -H "Content-Type: application/json" -d '{"amount": 200}' | jq
curl -X POST http://localhost:8003/gcounter/partition-test/increment \
  -H "Content-Type: application/json" -d '{"amount": 300}' | jq

# Sync (only replica-2 and replica-3 will participate)
curl -X POST http://localhost:8010/sync
sleep 2

echo "During partition:"
echo "Replica 1 (partitioned):" && curl -s http://localhost:8001/gcounter/partition-test | jq
echo "Replica 2:" && curl -s http://localhost:8002/gcounter/partition-test | jq
echo "Replica 3:" && curl -s http://localhost:8003/gcounter/partition-test | jq
```

**Step 4: Heal partition and merge**

```bash
# Disable partition
curl -X POST http://localhost:8001/admin/partition \
  -H "Content-Type: application/json" -d '{"enabled": false}' | jq

# Trigger sync
curl -X POST http://localhost:8010/sync
sleep 2

echo "After healing:"
echo "Replica 1:" && curl -s http://localhost:8001/gcounter/partition-test | jq '.value'
echo "Replica 2:" && curl -s http://localhost:8002/gcounter/partition-test | jq '.value'
echo "Replica 3:" && curl -s http://localhost:8003/gcounter/partition-test | jq '.value'
```

**Expected:** All replicas converge to 610 (10 + 100 + 200 + 300) - no data is lost!

---

### Exercise 5: Concurrent Update Test

Use the client service to run a concurrent update test:

```bash
# Run concurrent G-Counter test
curl -X POST http://localhost:8020/test/concurrent \
  -H "Content-Type: application/json" \
  -d '{
    "crdt_type": "gcounter",
    "name": "concurrent-test",
    "operations_per_replica": 20,
    "operation_delay_ms": 10
  }' | jq

# Run concurrent PN-Counter test
curl -X POST http://localhost:8020/test/concurrent \
  -H "Content-Type: application/json" \
  -d '{
    "crdt_type": "pncounter",
    "name": "concurrent-pn-test",
    "operations_per_replica": 20,
    "operation_delay_ms": 10
  }' | jq

# Run concurrent LWW-Register test
curl -X POST http://localhost:8020/test/concurrent \
  -H "Content-Type: application/json" \
  -d '{
    "crdt_type": "lww",
    "name": "concurrent-lww-test",
    "operations_per_replica": 10,
    "operation_delay_ms": 50
  }' | jq
```

---

### Exercise 6: Run the Full Test Suite

```bash
chmod +x scripts/test-concurrent.sh
./scripts/test-concurrent.sh
```

This runs:
1. G-Counter concurrent increments
2. PN-Counter mixed operations
3. LWW-Register concurrent writes
4. Network partition and merge
5. High concurrency stress test

---

## Key Takeaways

1. **No coordination needed**: CRDTs allow updates without consensus, locks, or leader election

2. **Eventual consistency guaranteed**: Mathematical properties ensure all replicas converge

3. **Partition tolerant**: CRDTs continue working during network partitions and merge automatically

4. **Trade-offs exist**:
   - G-Counter can only grow (no reset)
   - PN-Counter uses more space (two counters)
   - LWW-Register loses concurrent updates (only one "wins")

5. **Real-world uses**:
   - Distributed counters (view counts, likes)
   - Shopping carts (OR-Set CRDT)
   - Collaborative text editing (CRDT strings)
   - Eventually consistent databases (Riak, Cassandra)

---

## CRDT Cheat Sheet

| CRDT | Operations | Merge Strategy | Use Case |
|------|------------|----------------|----------|
| G-Counter | Increment | Max per replica | View counts, monotonic counters |
| PN-Counter | Inc/Dec | Max of P and N separately | Inventory, bidirectional counters |
| LWW-Register | Set | Highest timestamp wins | Config values, single values |
| G-Set | Add | Union | Tags, seen items |
| OR-Set | Add/Remove | Union with tombstones | Shopping carts, collections |

---

## Cleanup

```bash
docker compose down -v
```

---

## Further Reading

- [A Comprehensive Study of CRDTs](https://hal.inria.fr/inria-00555588/document) - Shapiro et al.
- [CRDTs: Consistency without Consensus](https://www.youtube.com/watch?v=B5NULPSiOGw) - Martin Kleppmann
- [Automerge](https://automerge.org/) - CRDT-based data sync library

## Next Lab

[Lab 50: Distributed Transactions](../week-50-distributed-transactions/) - How do you maintain ACID properties across multiple services?
