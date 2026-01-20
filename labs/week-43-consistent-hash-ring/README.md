# Lab 43: Consistent Hash Ring

Visualize key distribution and observe node changes in a consistent hashing system. In this lab, you'll see how consistent hashing minimizes key redistribution when nodes are added or removed from a distributed storage cluster.

## What You'll Learn

- How consistent hashing distributes keys across nodes
- The role of virtual nodes in improving balance
- What happens to keys when nodes join or leave the cluster
- How to measure and visualize key distribution uniformity

## Architecture

```
                                    ┌─────────────────────┐
                                    │   Ring Coordinator  │
                                    │                     │
                              ┌─────┤  - Hash Ring State  │
                              │     │  - Key Routing      │
                              │     │  - Node Management  │
                              │     └─────────────────────┘
                              │               │
                              │     ┌─────────┴─────────┐
                              │     │                   │
                    ┌─────────▼─────▼───┐   ┌──────────▼────────┐
                    │                   │   │                   │
              ┌─────┤   Storage Node 1  │   │   Storage Node 2  ├─────┐
              │     │                   │   │                   │     │
              │     └───────────────────┘   └───────────────────┘     │
              │                                                       │
              │               ┌───────────────────┐                   │
              │               │   Storage Node 3  │                   │
              │               │                   │                   │
              │               └───────────────────┘                   │
              │                                                       │
              └───────────────────────────────────────────────────────┘
                                Hash Ring (0 to 2^32-1)
```

### Components

| Component | Description |
|-----------|-------------|
| **Ring Coordinator** | Manages the consistent hash ring, routes keys to nodes, handles topology changes |
| **Storage Nodes** | In-memory key-value stores that receive routed requests |
| **Client Service** | Utilities for batch operations and migration testing |

### Hash Ring Concept

```
                    0 (= 2^32)
                       ●
                   ╱       ╲
              N1●           ●N2
             ╱                 ╲
           ●                     ●
          ╱                       ╲
        N3●           ●           ●N1
          ╲         (key)        ╱
           ●                    ●
            ╲                  ╱
              N2●          ●N3
                  ╲      ╱
                    ●  ●
                   N1  N2

Keys are assigned to the first node encountered
when walking clockwise from the key's position.
```

## Prerequisites

- Docker and Docker Compose
- curl (for API testing)
- jq (for JSON formatting)
- k6 (optional, for load testing)

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

### 2. Verify Services

```bash
docker compose ps
```

All services should show as "healthy".

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Ring Coordinator | http://localhost:8080 | Hash ring API |
| Client Service | http://localhost:8090 | Test utilities |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Understanding Basic Consistent Hashing

First, let's see how keys are distributed across the initial 3-node ring.

```bash
# Check the ring state
curl -s http://localhost:8080/ring | jq

# Look up which node would handle specific keys
curl -s http://localhost:8080/lookup/user:123 | jq
curl -s http://localhost:8080/lookup/user:456 | jq
curl -s http://localhost:8080/lookup/session:abc | jq
```

**Questions to explore:**
- What information does the ring state show?
- How does the key's hash determine its node assignment?
- Are nearby keys (like user:123 and user:124) assigned to the same node?

Store some keys and observe distribution:

```bash
# Store 100 keys via the client service
curl -s -X POST http://localhost:8090/generate-keys \
  -H "Content-Type: application/json" \
  -d '{"count": 100, "prefix": "test_"}' | jq

# Check distribution
curl -s http://localhost:8080/ring/distribution | jq
```

**Expected:** Keys should be roughly evenly distributed across the 3 nodes (~33% each).

---

### Exercise 2: Virtual Nodes for Better Balance

Virtual nodes create multiple positions on the ring for each physical node, improving distribution uniformity.

```bash
# Check how many virtual nodes each physical node has
curl -s http://localhost:8080/ring/nodes | jq

# Analyze distribution with current virtual nodes
curl -s http://localhost:8090/analyze-hash-distribution?sample_size=1000 | jq
```

**Observe:**
- The `coefficient_of_variation` indicates how evenly distributed keys are
- Lower values = more uniform distribution

Now let's see what happens with fewer virtual nodes. Create a new node with fewer virtual nodes:

```bash
# First, check current virtual nodes setting (default is 150)
curl -s http://localhost:8080/ring/nodes | jq '.virtual_nodes_per_physical'

# Reduce default for new nodes
curl -s -X PUT http://localhost:8080/ring/virtual-nodes \
  -H "Content-Type: application/json" \
  -d '{"count": 10}' | jq
```

**Questions:**
- How does reducing virtual nodes affect distribution uniformity?
- What trade-off exists between virtual node count and lookup complexity?

---

### Exercise 3: Adding a Node

Let's add a 4th node and observe how keys are redistributed.

First, start the 4th storage node:

```bash
docker compose --profile scale up -d storage-node-4
```

Now add it to the ring:

```bash
# Add node-4 to the ring
curl -s -X POST http://localhost:8080/ring/nodes \
  -H "Content-Type: application/json" \
  -d '{"node_id": "node-4", "address": "http://storage-node-4:8080", "virtual_nodes": 150}' | jq
```

**Observe:**
- The response shows which keys should migrate to the new node
- Check Grafana dashboard for key distribution changes

```bash
# Check new distribution
curl -s http://localhost:8080/ring/distribution | jq
```

**Key insight:** With consistent hashing, only ~K/N keys need to move (where K is total keys, N is new node count), not K/N total keys like with simple modulo hashing.

---

### Exercise 4: Removing a Node

Now let's remove a node and see what happens.

```bash
# Before removal - check current state
curl -s http://localhost:8080/ring/distribution | jq

# Remove node-2
curl -s -X DELETE http://localhost:8080/ring/nodes/node-2 | jq

# After removal - check new state
curl -s http://localhost:8080/ring/distribution | jq
```

**Observe:**
- The response shows which keys need to migrate and to where
- Keys from node-2 are distributed to remaining nodes, not all shuffled

---

### Exercise 5: Key Migration During Topology Changes

Use the client service to test a complete migration scenario:

```bash
# Run migration test (stores keys, changes topology, verifies)
curl -s -X POST http://localhost:8090/test-migration \
  -H "Content-Type: application/json" \
  -d '{
    "key_count": 500,
    "node_to_remove": "node-3"
  }' | jq
```

**Analyze the results:**
- `phase1_store`: Initial key distribution
- `phase2_topology_change`: What the coordinator reported
- `phase3_verify`: Actual new distribution
- `migration_analysis`: How many keys actually moved

**Compare:**
- Actual keys moved vs. theoretical minimum
- Is the movement percentage close to K/N?

---

### Exercise 6: Load Testing with Topology Changes

Run a load test while changing the topology:

```bash
# In terminal 1: Start load test
docker compose run --rm k6 run /scripts/migration-test.js

# In terminal 2: Add a node mid-test (after ~1 minute)
docker compose --profile scale up -d storage-node-4
curl -s -X POST http://localhost:8080/ring/nodes \
  -H "Content-Type: application/json" \
  -d '{"node_id": "node-4", "address": "http://storage-node-4:8080"}' | jq
```

**Watch in Grafana:**
- Open http://localhost:3001 (admin/admin)
- Navigate to the "Consistent Hash Ring" dashboard
- Observe key distribution shift in real-time

---

### Exercise 7: Observing with Traces

Use Jaeger to understand the request flow:

1. Make a few requests:
```bash
curl -s -X POST http://localhost:8080/keys \
  -H "Content-Type: application/json" \
  -d '{"key": "traced-key", "value": "traced-value"}'

curl -s http://localhost:8080/keys/traced-key
```

2. Open Jaeger at http://localhost:16686
3. Select "ring-coordinator" service
4. Find traces and observe:
   - Hash computation
   - Node selection
   - Storage node communication

---

## Key Concepts

### Why Consistent Hashing?

**Problem with simple modulo:**
- Hash(key) % N = node
- When N changes, almost ALL keys remap

**Consistent hashing advantage:**
- Keys mapped to ring positions
- Only keys in affected ranges move
- ~K/N keys move when adding a node

### Virtual Nodes

Without virtual nodes:
- 3 nodes = 3 points on ring
- Poor distribution likely

With virtual nodes:
- 3 nodes x 150 virtual = 450 points
- Much better distribution
- Statistical smoothing

### Key Distribution Metrics

| Metric | Meaning | Ideal |
|--------|---------|-------|
| Coefficient of Variation | Std dev / mean | < 0.1 |
| Max Deviation | Furthest from ideal % | < 5% |

## Cleanup

```bash
docker compose --profile scale down -v
```

## Troubleshooting

### Services not starting

```bash
docker compose logs -f ring-coordinator
```

### Keys not distributing

Check ring state:
```bash
curl -s http://localhost:8080/ring | jq '.physical_nodes'
```

### Traces not appearing

Wait 10-15 seconds, then check OTEL collector:
```bash
docker compose logs otel-collector
```

## Advanced Exercises

### Challenge 1: Hot Spot Analysis

Identify if certain key patterns cause hot spots:

```bash
# Generate keys with specific patterns
curl -s -X POST http://localhost:8090/generate-keys \
  -H "Content-Type: application/json" \
  -d '{"count": 500, "prefix": "user:1:"}' | jq

# Check if one node gets more than others
curl -s http://localhost:8080/ring/distribution | jq
```

### Challenge 2: Replication Considerations

In a real system, you'd replicate to N successor nodes. Think about:
- How would you modify key lookup to return N nodes?
- What happens when a node fails?

### Challenge 3: Weighted Nodes

Consider how you'd support nodes with different capacities:
- More powerful nodes should handle more keys
- How would you implement this with virtual nodes?

## Next Lab

[Lab 44: TBD] - Continue exploring distributed systems concepts.
