# Lab 38: Exactly-Once Processing

The holy grail of stream processing: ensuring each message is processed exactly once, no matter what fails. In this lab, you'll explore the three delivery semantics (at-most-once, at-least-once, exactly-once) and understand the tradeoffs and implementation patterns for achieving true exactly-once processing.

## What You'll Learn

- The differences between at-most-once, at-least-once, and exactly-once semantics
- How Kafka's transactional producer provides exactly-once publishing
- How to implement idempotent consumers using database deduplication
- What happens during consumer crashes and how different modes recover
- The performance tradeoffs between delivery guarantees

## Architecture

```
                                    ┌─────────────────────┐
                                    │      Kafka          │
                                    │  ┌───────────────┐  │
┌──────────┐    ┌───────────┐       │  │    orders     │  │       ┌───────────┐    ┌────────────┐
│  Client  │───>│  Producer │──────>│  │  (3 parts)    │──┼──────>│  Consumer │───>│  Postgres  │
└──────────┘    └───────────┘       │  └───────────────┘  │       └───────────┘    └────────────┘
                     │              │                     │             │               │
                     │              │  Transactions       │             │   Idempotent  │
                     │              │  Idempotent writes  │             │   Processing  │
                     │              └─────────────────────┘             │               │
                     │                                                  │               │
                     │              ┌─────────────────────┐             │               │
                     └─────────────>│   processed_msgs    │<────────────┘               │
                                    │   (deduplication)   │                             │
                                    └─────────────────────┘                             │
                                                                                        │
                                    ┌─────────────────────┐                             │
                                    │       orders        │<────────────────────────────┘
                                    │  (business data)    │
                                    └─────────────────────┘
```

## Key Concepts

### Delivery Semantics

| Semantic | Message Loss | Duplicates | Use Case |
|----------|-------------|------------|----------|
| At-most-once | Possible | None | Metrics, logs where loss is acceptable |
| At-least-once | None | Possible | Notifications, events (idempotent downstream) |
| Exactly-once | None | None | Financial transactions, order processing |

### How Exactly-Once Works

**Producer Side (Kafka Transactions):**
1. Producer assigned a unique `transactional.id`
2. All messages in a transaction succeed or fail atomically
3. Kafka deduplicates using sequence numbers (idempotence)

**Consumer Side (Idempotent Processing):**
1. Each message has a unique ID
2. Before processing, check if ID exists in processed_messages table
3. Process and mark as processed in a single transaction
4. Commit Kafka offset only after database transaction succeeds

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- k6 (optional, for load testing)

## Quick Start

### 1. Start the Lab

```bash
cd /Users/ebaibourine/github/distributed-lab/labs/week-38-exactly-once-processing
docker compose up --build -d
```

### 2. Wait for Services

```bash
# Wait for Kafka to be fully ready
docker compose logs -f kafka-setup
# Should see "Topics created successfully"

# Check all services
docker compose ps
```

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Producer API | http://localhost:8000 | Send orders |
| Consumer API | http://localhost:8001 | Consumer stats |
| Query API | http://localhost:8003 | Statistics & queries |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

---

## Lab Exercises

### Exercise 1: Understanding the Three Semantics

First, let's understand what each delivery mode means in practice.

**View the semantic comparison:**
```bash
curl -s http://localhost:8003/compare/semantics | jq
```

**Check current configuration:**
```bash
# Producer config
curl -s http://localhost:8000/config | jq

# Consumer config
curl -s http://localhost:8001/config | jq
```

The lab starts in exactly-once mode by default.

---

### Exercise 2: Exactly-Once in Action

Let's verify that exactly-once is working correctly.

**Send an order:**
```bash
curl -s -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "cust-001",
    "items": [{"name": "Widget", "quantity": 2, "price": 19.99}],
    "total_amount": 39.98
  }' | jq
```

**Check the database:**
```bash
curl -s http://localhost:8003/stats | jq
```

**Simulate duplicate messages:**
```bash
# This sends the same order 3 times
curl -s -X POST http://localhost:8000/simulate/duplicate \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "cust-002",
    "items": [{"name": "Gadget", "quantity": 1, "price": 99.99}],
    "total_amount": 99.99
  }' | jq
```

**Verify idempotent processing:**
```bash
# Check duplicate statistics
curl -s http://localhost:8003/stats/duplicates | jq

# Consumer should show duplicates detected
docker compose logs consumer --tail=20 | grep -i duplicate
```

**Expected Result:** Despite sending 3 identical messages, only 1 order should exist in the database.

---

### Exercise 3: Transactional Batch Processing

Exactly-once shines when you need atomic batch operations.

**Send a batch of orders:**
```bash
curl -s -X POST http://localhost:8000/orders/batch \
  -H "Content-Type: application/json" \
  -d '{
    "orders": [
      {"customer_id": "batch-001", "items": [{"name": "A", "quantity": 1, "price": 10}], "total_amount": 10},
      {"customer_id": "batch-002", "items": [{"name": "B", "quantity": 1, "price": 20}], "total_amount": 20},
      {"customer_id": "batch-003", "items": [{"name": "C", "quantity": 1, "price": 30}], "total_amount": 30}
    ]
  }' | jq
```

All three orders are sent in a single Kafka transaction - they either all succeed or all fail.

**Check the orders:**
```bash
curl -s "http://localhost:8003/orders?limit=5" | jq
```

---

### Exercise 4: Consumer Crash Recovery

This is where delivery semantics really matter. Let's see what happens when a consumer crashes.

**1. First, reset the state:**
```bash
curl -s -X POST http://localhost:8003/reset | jq
curl -s -X POST http://localhost:8001/reset-stats | jq
```

**2. Configure consumer to crash after 5 messages:**
```bash
curl -s -X POST http://localhost:8001/config \
  -H "Content-Type: application/json" \
  -d '{"crash_after_messages": 5}' | jq
```

**3. Send 10 orders:**
```bash
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/orders \
    -H "Content-Type: application/json" \
    -d "{\"customer_id\": \"crash-test-$i\", \"items\": [{\"name\": \"Item $i\", \"quantity\": 1, \"price\": 10}], \"total_amount\": 10}" > /dev/null
  echo "Sent order $i"
  sleep 0.2
done
```

**4. Watch the consumer crash:**
```bash
docker compose logs consumer --tail=30
# You should see "Simulating crash after 5 messages!"
```

**5. Restart the consumer:**
```bash
docker compose up -d consumer
sleep 5
```

**6. Check the results:**
```bash
curl -s http://localhost:8003/stats | jq
```

**Expected Result (Exactly-Once Mode):**
- Exactly 10 unique orders in the database
- No duplicates (idempotency ensures crashed messages are deduplicated on retry)

---

### Exercise 5: Compare At-Least-Once Behavior

Now let's see how at-least-once mode handles the same crash scenario.

**1. Reset state:**
```bash
curl -s -X POST http://localhost:8003/reset | jq
```

**2. Switch to at-least-once mode (disable idempotency):**
```bash
# Note: Processing mode change requires restart
docker compose stop consumer
docker compose up -d consumer
sleep 5

# Disable idempotency check
curl -s -X POST http://localhost:8001/config \
  -H "Content-Type: application/json" \
  -d '{"enable_idempotency": false, "crash_after_messages": 5}' | jq
```

**3. Repeat the crash test:**
```bash
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/orders \
    -H "Content-Type: application/json" \
    -d "{\"customer_id\": \"atleast-test-$i\", \"items\": [{\"name\": \"Item $i\", \"quantity\": 1, \"price\": 10}], \"total_amount\": 10}" > /dev/null
  echo "Sent order $i"
  sleep 0.2
done
```

**4. Restart and check:**
```bash
docker compose up -d consumer
sleep 5
curl -s http://localhost:8003/stats | jq
```

**Expected Result (At-Least-Once without Idempotency):**
- More than 10 orders in the database
- The orders that were processed before the crash will be processed AGAIN

---

### Exercise 6: At-Most-Once Mode

Let's see what happens with at-most-once semantics.

**1. Switch producer to at-most-once:**
```bash
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"delivery_mode": "at-most-once"}' | jq
```

**2. Reset and test:**
```bash
curl -s -X POST http://localhost:8003/reset | jq

# Send 20 rapid-fire requests
for i in {1..20}; do
  curl -s -X POST http://localhost:8000/orders \
    -H "Content-Type: application/json" \
    -d "{\"customer_id\": \"atmost-$i\", \"items\": [{\"name\": \"Fast\", \"quantity\": 1, \"price\": 5}], \"total_amount\": 5}" &
done
wait

sleep 3
curl -s http://localhost:8003/stats | jq
```

**Observe:**
- At-most-once is fastest (no waiting for acknowledgment)
- But under load, some messages may be lost
- Check producer logs: `docker compose logs producer --tail=20`

---

### Exercise 7: Performance Comparison

Let's measure the latency differences between modes.

**1. Reset to exactly-once:**
```bash
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"delivery_mode": "exactly-once", "enable_transactions": true}' | jq
```

**2. Measure exactly-once latency:**
```bash
echo "Exactly-Once Latency:"
for i in {1..5}; do
  time=$(curl -s -X POST http://localhost:8000/orders \
    -H "Content-Type: application/json" \
    -d '{"customer_id": "perf", "items": [{"name": "Test", "quantity": 1, "price": 10}], "total_amount": 10}' \
    | jq -r '.latency_ms')
  echo "  Request $i: ${time}ms"
done
```

**3. Measure at-most-once latency:**
```bash
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"delivery_mode": "at-most-once"}' | jq

echo "At-Most-Once Latency:"
for i in {1..5}; do
  time=$(curl -s -X POST http://localhost:8000/orders \
    -H "Content-Type: application/json" \
    -d '{"customer_id": "perf", "items": [{"name": "Test", "quantity": 1, "price": 10}], "total_amount": 10}' \
    | jq -r '.latency_ms')
  echo "  Request $i: ${time}ms"
done
```

**Expected:** At-most-once is significantly faster because it doesn't wait for acknowledgment.

---

### Exercise 8: Load Testing

Run a comprehensive load test to observe the system under pressure.

**1. Reset to exactly-once:**
```bash
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"delivery_mode": "exactly-once", "enable_transactions": true}' | jq

curl -s -X POST http://localhost:8001/config \
  -H "Content-Type: application/json" \
  -d '{"enable_idempotency": true, "crash_after_messages": 0, "failure_rate": 0.0}' | jq
```

**2. Run basic load test:**
```bash
docker compose run --rm k6 run /scripts/basic.js
```

**3. Watch metrics in Grafana:**
- Open http://localhost:3001
- Go to "Exactly-Once Processing" dashboard
- Observe:
  - Message production rate
  - Processing rate
  - Duplicate detection
  - Latency percentiles

---

## Key Takeaways

1. **At-most-once** is fastest but can lose messages - use only when loss is acceptable (metrics, logs)

2. **At-least-once** guarantees no loss but may duplicate - requires idempotent consumers or downstream systems

3. **Exactly-once** provides the strongest guarantee but has:
   - Higher latency (transactions, acknowledgments)
   - More complexity (idempotency tracking, transaction management)
   - Use when correctness is critical (financial, orders, inventory)

4. **Idempotent consumers are essential** - even with Kafka's exactly-once semantics, consumers must be idempotent because:
   - Network partitions can cause reprocessing
   - Consumer group rebalancing can cause reprocessing
   - Application bugs can cause reprocessing

5. **The database is the source of truth** - store processed message IDs and business data atomically

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Kafka not starting
```bash
docker compose logs kafka
docker compose logs zookeeper
```

### Consumer not processing
```bash
docker compose logs consumer
# Check if connected to Kafka
curl -s http://localhost:8001/config | jq
```

### Messages not appearing in database
```bash
# Check database directly
docker compose exec postgres psql -U lab -d exactly_once -c "SELECT COUNT(*) FROM processed_messages"
```

### Traces not appearing in Jaeger
- Wait 10-15 seconds after making requests
- Check otel-collector: `docker compose logs otel-collector`

## Next Lab

[Lab 39: TBD](../week-39-tbd/) - Coming soon!
