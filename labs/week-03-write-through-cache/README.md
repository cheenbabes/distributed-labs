# Lab 03: Write-Through Cache

The simplest caching pattern: every write goes to both cache and database. In this lab, you'll implement a write-through cache with Redis and Postgres, trace requests through both paths, and understand exactly why this pattern guarantees consistency but adds write latency.

## What You'll Learn

- How write-through caching works
- Why this pattern guarantees cache-database consistency
- The latency trade-off: writes are slower, but reads are fast and consistent
- How to observe cache behavior using distributed tracing
- What happens when the cache fails (graceful degradation)

## Architecture

```
                    WRITE PATH (Create/Update)
                    ==========================
                              |
                              v
┌──────────┐           ┌───────────┐
│  Client  │──────────>│    API    │
└──────────┘           └───────────┘
                              |
                    ┌─────────┴─────────┐
                    |                   |
                    v                   v
              ┌───────────┐       ┌───────────┐
              │  Postgres │       │   Redis   │
              │   (DB)    │       │  (Cache)  │
              └───────────┘       └───────────┘
                    ^                   ^
                    |                   |
                    └─────────┬─────────┘
                              |
                    Both get the SAME data
                    at the SAME time


                    READ PATH
                    =========
                              |
                              v
┌──────────┐           ┌───────────┐
│  Client  │──────────>│    API    │
└──────────┘           └───────────┘
                              |
                              v
                       ┌───────────┐
                       │   Redis   │ <-- Check cache first
                       │  (Cache)  │
                       └───────────┘
                              |
                         HIT? ──> Return immediately
                              |
                         MISS ──> Read from Postgres
                              |   and cache for next time
                              v
                       ┌───────────┐
                       │  Postgres │
                       │   (DB)    │
                       └───────────┘
```

### Key Characteristics

| Aspect | Behavior |
|--------|----------|
| **Consistency** | Strong - cache and DB are always in sync |
| **Write Latency** | Higher - must wait for both Redis AND Postgres |
| **Read Latency** | Low on cache hit, normal on miss |
| **Failure Mode** | Graceful - if Redis fails, DB still works |

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
| API | http://localhost:8000 | REST API endpoint |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Understand the Write Path

Let's create an item and trace the write through both Redis and Postgres.

```bash
# Create a new item
curl -X POST http://localhost:8000/items \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Widget", "description": "A test item", "price": 29.99}' | jq
```

**Now look at the trace in Jaeger:**
1. Open http://localhost:16686
2. Select "api" from the Service dropdown
3. Click "Find Traces"
4. Click on the POST trace

**What to observe:**
- Two child spans: `postgres_insert` and `redis_set`
- Both operations happen sequentially
- Total write time = Postgres time + Redis time
- This is the "cost" of write-through: every write does double work

**Question:** What's the total write latency? How much time is spent on each?

---

### Exercise 2: Compare Read Latency (Hit vs Miss)

First, read an item that's already cached:

```bash
# Get the item ID from Exercise 1 and read it
ITEM_ID="<paste-id-here>"
curl -s http://localhost:8000/items/$ITEM_ID | jq

# Read it again - this time it's cached
curl -s http://localhost:8000/items/$ITEM_ID | jq
```

Now clear the cache and read again:

```bash
# Clear the cache
curl -X POST http://localhost:8000/cache/clear | jq

# Read the same item - now it's a cache miss
curl -s http://localhost:8000/items/$ITEM_ID | jq
```

**Compare traces in Jaeger:**
1. Find a trace with just `redis_get` (cache hit)
2. Find a trace with both `redis_get` and `postgres_select` (cache miss)

**Question:** How much faster is a cache hit vs a cache miss?

---

### Exercise 3: Observe Write-Through Consistency

The key benefit of write-through is consistency. Let's verify this.

```bash
# Create an item
RESPONSE=$(curl -s -X POST http://localhost:8000/items \
  -H "Content-Type: application/json" \
  -d '{"name": "Consistency Test", "price": 50.00}')
ITEM_ID=$(echo $RESPONSE | jq -r '.id')
echo "Created item: $ITEM_ID"

# Read it immediately - should get the SAME data
curl -s http://localhost:8000/items/$ITEM_ID | jq

# Update it
curl -s -X PUT http://localhost:8000/items/$ITEM_ID \
  -H "Content-Type: application/json" \
  -d '{"name": "Updated Name", "price": 75.00}' | jq

# Read again - should see the update immediately
curl -s http://localhost:8000/items/$ITEM_ID | jq
```

**In Jaeger**, look at the UPDATE trace:
- `postgres_update` and `redis_set` both happen
- The cache is updated at the same time as the database
- No window for stale data!

---

### Exercise 4: What Happens When Redis Dies?

This is where write-through shines - graceful degradation.

```bash
# First, verify everything works
curl -s http://localhost:8000/health | jq

# Kill Redis
docker compose stop redis

# Check health - should show degraded
curl -s http://localhost:8000/health | jq
```

Now try operations:

```bash
# Read an item - still works! (from Postgres)
curl -s http://localhost:8000/items/550e8400-e29b-41d4-a716-446655440001 | jq

# Create a new item - still works! (writes to Postgres, skips Redis)
curl -s -X POST http://localhost:8000/items \
  -H "Content-Type: application/json" \
  -d '{"name": "Created without cache", "price": 99.99}' | jq
```

**Key insight:** The system degrades gracefully. Reads are slower (all go to Postgres), but nothing breaks.

Bring Redis back:

```bash
docker compose start redis
sleep 5
curl -s http://localhost:8000/health | jq
```

---

### Exercise 5: Measure Write Latency Impact

Let's quantify the write latency overhead.

```bash
# Create 10 items and measure time
echo "Creating 10 items..."
for i in {1..10}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" -X POST http://localhost:8000/items \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"Item $i\", \"price\": $i.99}")
  echo "Write $i: ${time}s"
done
```

Compare to read latency:

```bash
# Read items (should be faster due to cache)
echo "Reading items (from cache)..."
for i in {1..10}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/items)
  echo "Read $i: ${time}s"
done
```

**Question:** How much slower are writes compared to reads?

---

### Exercise 6: Cache Statistics

Monitor cache behavior:

```bash
# View cache stats
curl -s http://localhost:8000/cache/stats | jq

# Make some reads
for i in {1..20}; do
  curl -s http://localhost:8000/items/550e8400-e29b-41d4-a716-446655440001 > /dev/null
done

# Check stats again
curl -s http://localhost:8000/cache/stats | jq
```

**What to observe:**
- `cached_items`: Number of items in cache
- `total_hits` / `total_misses`: Cache effectiveness
- `hit_rate`: Percentage of reads served from cache

---

### Exercise 7: Load Test Analysis

Run a sustained load test to see patterns:

```bash
docker compose run --rm k6 run /scripts/basic.js
```

While the test runs:
1. Open Jaeger and observe trace patterns
2. Compare read vs write latencies
3. Watch cache hit rates over time

**Questions:**
- What's the p95 latency for reads vs writes?
- What's the cache hit rate under load?
- How does write latency compare to read latency?

---

## Key Takeaways

1. **Write-through guarantees consistency** - The cache and database are always in sync because every write updates both.

2. **The trade-off is write latency** - Every write operation takes longer because it must complete TWO operations (cache + database).

3. **Graceful degradation** - If the cache fails, the system still works. Reads fall back to the database; writes complete to the database and skip the cache.

4. **Best for read-heavy workloads** - Write-through shines when you read much more often than you write. The write overhead is amortized across many fast cached reads.

5. **Simple to reason about** - Unlike other patterns (write-behind, cache-aside), write-through is straightforward: write to both, read from cache first.

## When to Use Write-Through

**Good fit:**
- Read-heavy workloads (>80% reads)
- Strong consistency requirements
- Simple caching logic needed
- When cache failures should degrade gracefully

**Poor fit:**
- Write-heavy workloads (writes will be slow)
- When write latency is critical
- Complex caching scenarios (computed values, aggregations)

## Alternative Patterns

| Pattern | Write Behavior | Consistency | Complexity |
|---------|---------------|-------------|------------|
| **Write-Through** | Write both | Strong | Low |
| Write-Behind | Write cache, async DB | Eventual | Medium |
| Cache-Aside | App manages cache | Varies | Medium |
| Read-Through | Cache reads on miss | N/A (reads only) | Low |

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Redis connection errors
```bash
docker compose logs redis
docker compose exec redis redis-cli ping
```

### Postgres connection errors
```bash
docker compose logs postgres
docker compose exec postgres pg_isready -U labuser -d items_db
```

### Traces not appearing in Jaeger
- Wait 10-15 seconds after making requests
- Check that otel-collector is running: `docker compose logs otel-collector`

## Next Lab

[Lab 04: Write-Behind Cache](../week-04-write-behind-cache/) - We'll explore the opposite trade-off: fast writes with eventual consistency.
