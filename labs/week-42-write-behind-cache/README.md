# Lab 42: Write-Behind Cache (Write-Back)

Completing the cache trilogy! In this lab, you will explore write-behind caching, where writes go to the cache first and are asynchronously persisted to the database by a background worker. This pattern offers the lowest write latency but comes with the risk of data loss.

## What You Will Learn

- The difference between write-behind, write-through, and cache-aside patterns
- How write coalescing reduces database load
- The data loss risk when cache fails before persistence
- Implementing retry logic for failed writes
- Monitoring async write pipelines with observability tools

## Architecture

```
┌─────────┐     ┌─────────────────────────────────────────────────────────┐
│  Client │     │                      Write Path                         │
└────┬────┘     │                                                         │
     │          │  ┌──────┐   ┌───────┐   ┌─────────┐   ┌────────────┐   │
     │ POST     │  │      │   │       │   │  Write  │   │            │   │
     ├──────────┼─►│  API ├──►│ Redis ├──►│  Queue  ├──►│   Worker   │   │
     │          │  │      │   │(Cache)│   │         │   │            │   │
     │          │  └──────┘   └───────┘   └─────────┘   └──────┬─────┘   │
     │          │       │                                      │         │
     │          │       │ Response                             │ Async   │
     │◄─────────┼───────┘ (immediate)                          │ Write   │
     │          │                                              ▼         │
     │          │                                        ┌──────────┐    │
     │          │                                        │PostgreSQL│    │
     │          │                                        └──────────┘    │
     │          └─────────────────────────────────────────────────────────┘
     │
     │          ┌─────────────────────────────────────────────────────────┐
     │          │                      Read Path                          │
     │ GET      │  ┌──────┐   ┌───────┐                                   │
     ├──────────┼─►│      ├──►│ Redis │─── Hit ──► Return (fast)          │
     │          │  │  API │   │(Cache)│                                   │
     │          │  │      │   └───┬───┘                                   │
     │          │  │      │       │                                       │
     │          │  │      │      Miss                                     │
     │          │  │      │       │      ┌──────────┐                     │
     │          │  │      │       └─────►│PostgreSQL│──► Return + Cache   │
     │          │  └──────┘              └──────────┘                     │
     └──────────┴─────────────────────────────────────────────────────────┘
```

### Key Components

| Component | Purpose |
|-----------|---------|
| **API Service** | Handles HTTP requests, writes to cache, queues for persistence |
| **Redis** | Write buffer (queue) and read cache |
| **Worker** | Background process that persists queued writes to PostgreSQL |
| **PostgreSQL** | Persistent data store |

## Cache Pattern Comparison

| Pattern | Write Latency | Read Latency | Data Safety | Complexity |
|---------|--------------|--------------|-------------|------------|
| **Cache-Aside** | High (DB write) | Low (if cached) | High | Low |
| **Write-Through** | High (DB write) | Low (always cached) | High | Medium |
| **Write-Behind** | Low (cache only) | Low (always cached) | **LOW** | High |

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

All services should show as "healthy" or "running".

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| API | http://localhost:8000 | API entry point |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Understanding Write-Behind Flow

First, let's see write-behind in action with a simple product creation.

```bash
# Create a product
curl -s -X POST http://localhost:8000/products \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Widget", "price": 29.99, "quantity": 100}' | jq
```

Notice the response includes `"source": "cache"` - the write was acknowledged immediately from cache.

```bash
# Check the write queue
curl -s http://localhost:8000/admin/queue | jq
```

```bash
# Wait a moment, then verify it's in the database
sleep 2
# Read it back - might come from cache or DB
curl -s http://localhost:8000/products/<id-from-above> | jq
```

**Question:** What is the latency of the POST request? Compare to a synchronous database write.

---

### Exercise 2: Write-Through vs Write-Behind Comparison

Let's compare write latency between the two modes.

```bash
# Current mode (write-behind)
curl -s http://localhost:8000/admin/config | jq

# Time a write-behind request
for i in {1..5}; do
  curl -s -o /dev/null -w "Write-behind: %{time_total}s\n" \
    -X POST http://localhost:8000/products \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"WB-Product-$i\", \"price\": 19.99, \"quantity\": 50}"
done
```

Now restart the API with write-through mode:

```bash
docker compose stop lab42-api
docker compose run -d --name lab42-api-wt \
  -e WRITE_BEHIND_ENABLED=false \
  -p 8000:8000 \
  --network lab42-network \
  lab42-api

# Time write-through requests
for i in {1..5}; do
  curl -s -o /dev/null -w "Write-through: %{time_total}s\n" \
    -X POST http://localhost:8000/products \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"WT-Product-$i\", \"price\": 19.99, \"quantity\": 50}"
done
```

**Expected:** Write-behind should be 2-10x faster than write-through.

---

### Exercise 3: Write Coalescing

When multiple updates hit the same key within a short window, write coalescing merges them into a single database write.

```bash
# Rapidly update the same product 10 times
for i in {1..10}; do
  curl -s -X PUT http://localhost:8000/products/seed-001 \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"Coalesced-$i\", \"price\": $((RANDOM % 100)).99, \"quantity\": $i}" &
done
wait

# Check the queue - should have fewer than 10 items
curl -s http://localhost:8000/admin/queue | jq '.queue_size'
```

Look at Grafana dashboard - the "Writes Coalesced" metric shows how many writes were merged.

**Why This Matters:**
- 10 rapid API updates might result in only 1-2 database writes
- Reduces database load significantly for "hot" keys
- Final state is consistent (last write wins)

---

### Exercise 4: Data Loss Risk - The Dark Side

This is the critical tradeoff of write-behind caching. Let's simulate what happens when the cache fails before writes are persisted.

**Step 1:** Generate some writes

```bash
# Create 20 products rapidly
for i in {1..20}; do
  curl -s -X POST http://localhost:8000/products \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"AtRisk-$i\", \"price\": 99.99, \"quantity\": $i}" &
done
wait

# Check queue size
curl -s http://localhost:8000/admin/queue | jq
```

**Step 2:** Simulate cache failure (BEFORE worker persists)

```bash
# Stop the worker so writes queue up
docker compose stop lab42-worker

# Create more products (these will queue but never persist)
for i in {21..30}; do
  result=$(curl -s -X POST http://localhost:8000/products \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"WillBeLost-$i\", \"price\": 99.99, \"quantity\": $i}")
  echo "Created: $(echo $result | jq -r '.id')"
done

# Now simulate Redis crash - flush the queue
docker compose exec lab42-redis redis-cli FLUSHALL

# Restart worker
docker compose start lab42-worker
```

**Step 3:** Verify data loss

```bash
# Try to read the "WillBeLost" products
# They were acknowledged to clients but are now GONE
curl -s http://localhost:8000/products/<one-of-the-lost-ids> | jq
```

**This is the fundamental tradeoff:** Lower latency = potential data loss.

---

### Exercise 5: Retry Logic for Failed Writes

The worker implements retry logic with exponential backoff. Let's see it in action.

```bash
# Enable database failure simulation
docker compose stop lab42-worker
docker compose up -d lab42-worker --env SIMULATE_DB_FAILURE=true --env FAILURE_RATE=0.5

# Generate some writes
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/products \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"RetryTest-$i\", \"price\": 49.99, \"quantity\": $i}"
done

# Watch worker logs
docker compose logs -f lab42-worker
```

You should see:
- Some writes succeed on first attempt
- Some fail and retry (up to 3 times)
- Persistent failures go to Dead Letter Queue (DLQ)

Check the DLQ:

```bash
docker compose exec lab42-redis redis-cli LRANGE write_behind:dlq 0 -1
```

---

### Exercise 6: Load Test and Observe

Run a comprehensive load test while watching the Grafana dashboard.

```bash
# Run the basic load test
docker compose run --rm lab42-k6 run /scripts/basic.js

# Or run the coalescing-specific test
docker compose run --rm lab42-k6 run /scripts/coalescing-test.js
```

**What to observe in Grafana:**
1. Write Queue Size - should stay low if worker keeps up
2. API Latency - should be consistently low (write-behind benefit)
3. Cache Hit Rate - should be high for reads
4. Worker Write Rate - shows actual database writes
5. Writes Coalesced - shows efficiency gains

---

## Key Takeaways

1. **Write-behind provides lowest write latency** - Clients get immediate acknowledgment from cache

2. **Data loss is a real risk** - If cache fails before persistence, acknowledged writes are lost

3. **Write coalescing reduces database load** - Multiple updates to same key become single write

4. **Retry logic is essential** - Transient failures should be retried, not lost

5. **Dead Letter Queues capture permanent failures** - For manual intervention or alerting

6. **Monitoring is critical** - Queue depth, failure rates, and latency tell you if the system is healthy

## When to Use Write-Behind

**Good fit:**
- High write throughput requirements
- Can tolerate eventual consistency
- Data can be reconstructed (logs, analytics)
- Need to absorb write spikes

**Bad fit:**
- Financial transactions
- Inventory that must be accurate
- Any data where loss is unacceptable
- Regulatory compliance requirements

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Queue growing unbounded
- Check worker logs: `docker compose logs lab42-worker`
- Verify PostgreSQL is healthy: `docker compose exec lab42-postgres pg_isready`

### Data not appearing in database
```bash
# Check queue status
curl -s http://localhost:8000/admin/queue | jq

# Check for DLQ entries
docker compose exec lab42-redis redis-cli LRANGE write_behind:dlq 0 -1
```

## Next Steps

- [Lab 43: Cache Stampede Prevention](../week-43-cache-stampede/) - What happens when cache expires and 1000 requests hit the database simultaneously?
