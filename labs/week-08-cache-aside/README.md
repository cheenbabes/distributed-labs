# Lab 08: Cache-Aside Pattern

The cache-aside pattern (also known as lazy loading) is one of the most common caching strategies in distributed systems. Unlike write-through caching where the application writes to both cache and database, cache-aside puts the application in control of managing the cache separately from the database.

## What You'll Learn

- How the cache-aside (lazy loading) pattern works
- The difference between cache-aside and write-through patterns
- How to measure cache hit rates and their impact on latency
- Stale read scenarios and cache invalidation strategies
- TTL-based expiration and its trade-offs
- How to observe cache behavior using distributed tracing

## Architecture

```
                    READ PATH (Cache-Aside)
                    ========================

                              |
                              v
┌──────────┐           ┌───────────┐
│  Client  │──────────>│    API    │
└──────────┘           └───────────┘
                              |
                              v
                       ┌───────────┐
                       │   Redis   │ <-- 1. Check cache first
                       │  (Cache)  │
                       └───────────┘
                              |
                         HIT? ──> Return immediately (fast!)
                              |
                         MISS ──> Continue to database
                              |
                              v
                       ┌───────────┐
                       │  Postgres │ <-- 2. Read from database
                       │   (DB)    │
                       └───────────┘
                              |
                              v
                       ┌───────────┐
                       │   Redis   │ <-- 3. Populate cache for next time
                       │  (Cache)  │
                       └───────────┘


                    WRITE PATH (Cache-Aside)
                    =========================

                              |
                              v
┌──────────┐           ┌───────────┐
│  Client  │──────────>│    API    │
└──────────┘           └───────────┘
                              |
                              v
                       ┌───────────┐
                       │  Postgres │ <-- 1. Write to DB ONLY
                       │   (DB)    │
                       └───────────┘
                              |
                              v
                       ┌───────────┐
                       │   Redis   │ <-- 2. Invalidate cache
                       │  (Cache)  │     (don't update)
                       └───────────┘
```

### Cache-Aside vs Write-Through

| Aspect | Cache-Aside (This Lab) | Write-Through (Lab 03) |
|--------|------------------------|------------------------|
| **Write Path** | Write DB, invalidate cache | Write both DB and cache |
| **Write Latency** | Lower (one write) | Higher (two writes) |
| **Read After Write** | Cache miss on first read | Cache hit immediately |
| **Consistency** | Eventual (TTL-based) or explicit invalidation | Strong (always in sync) |
| **Complexity** | Application manages cache | Simpler mental model |
| **Best For** | Read-heavy, latency-sensitive writes | Consistency-critical apps |

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

### Exercise 1: Understand Cache Miss vs Cache Hit

Let's observe the latency difference between cache hits and misses.

```bash
# First, clear the cache to start fresh
curl -X POST http://localhost:8000/admin/cache/clear | jq

# Read a product (MISS - goes to database)
echo "First read (cache miss):"
time curl -s http://localhost:8000/products/prod-001 | jq '{id, name, price, cache_status}'

# Read the same product again (HIT - from cache)
echo "Second read (cache hit):"
time curl -s http://localhost:8000/products/prod-001 | jq '{id, name, price, cache_status}'
```

**What to observe:**
- First read shows `cache_status: "miss"` - data came from Postgres
- Second read shows `cache_status: "hit"` - data came from Redis
- The cache hit should be noticeably faster

**In Jaeger** (http://localhost:16686):
1. Select "product-service" from the Service dropdown
2. Find traces for both reads
3. Compare: cache miss has `db_read` span, cache hit does not

**Question:** How much faster is a cache hit compared to a cache miss?

---

### Exercise 2: Observe Cache-Aside Write Behavior

Unlike write-through, cache-aside does NOT write to cache on create/update.

```bash
# Create a new product
echo "Creating new product..."
RESPONSE=$(curl -s -X POST http://localhost:8000/products \
  -H "Content-Type: application/json" \
  -d '{"name": "New Widget", "description": "A new product", "price": 99.99}')
PRODUCT_ID=$(echo $RESPONSE | jq -r '.id')
echo "Created product: $PRODUCT_ID"
echo $RESPONSE | jq

# Check if it's in the cache
echo "Checking cache for new product..."
curl -s http://localhost:8000/admin/cache/key/$PRODUCT_ID | jq
```

**Expected behavior:**
- The product is created in Postgres
- The cache does NOT contain the product yet (cache-aside!)
- The product will be cached on first read

```bash
# Now read the product (this will cache it)
echo "Reading product (will populate cache)..."
curl -s http://localhost:8000/products/$PRODUCT_ID | jq

# Check cache again
echo "Checking cache after read..."
curl -s http://localhost:8000/admin/cache/key/$PRODUCT_ID | jq
```

**Key insight:** Cache-aside is "lazy" - items are cached only when read.

---

### Exercise 3: Cache Invalidation on Update

When we update a product, we invalidate (delete) the cache entry rather than updating it.

```bash
# Read a product to cache it
echo "Step 1: Cache the product..."
curl -s http://localhost:8000/products/prod-002 | jq '{price, cache_status}'

# Check it's in cache
echo "Step 2: Verify cache entry..."
curl -s http://localhost:8000/admin/cache/key/prod-002 | jq

# Update the product
echo "Step 3: Update the product..."
curl -s -X PUT http://localhost:8000/products/prod-002 \
  -H "Content-Type: application/json" \
  -d '{"name": "Mechanical Keyboard", "description": "Updated!", "price": 179.99}' | jq '{price, cache_status}'

# Check cache - should be gone
echo "Step 4: Check cache after update..."
curl -s http://localhost:8000/admin/cache/key/prod-002 | jq

# Read again - will repopulate cache
echo "Step 5: Read again (cache miss, will repopulate)..."
curl -s http://localhost:8000/products/prod-002 | jq '{price, cache_status}'
```

**What to observe:**
- After update, `cache_status: "invalidated"`
- Cache no longer contains the entry
- Next read is a cache miss, then cache is repopulated

---

### Exercise 4: Explore Stale Read Scenarios

The key trade-off of cache-aside is the potential for stale reads.

```bash
# First, read a product to cache it
echo "Step 1: Cache the product..."
curl -s http://localhost:8000/products/prod-003 | jq '{name, price, cache_status}'

# Check current TTL
echo "Step 2: Check cache TTL..."
curl -s http://localhost:8000/admin/cache/key/prod-003 | jq '{exists, ttl_seconds}'

# Simulate an external update (bypasses our service)
echo "Step 3: Simulating external database update..."
curl -s -X POST "http://localhost:8000/admin/simulate-stale-read/prod-003?new_price=999.99" | jq

# Read the product - will get STALE data from cache!
echo "Step 4: Read product (will get stale cached data)..."
curl -s http://localhost:8000/products/prod-003 | jq '{name, price, cache_status}'
```

**This demonstrates the stale read problem:**
- Database was updated to 999.99
- Cache still has the old price
- Reads return stale data until TTL expires

```bash
# Fix: Manually invalidate the cache
echo "Step 5: Manually invalidate cache..."
curl -s -X POST http://localhost:8000/admin/invalidate/prod-003 | jq

# Now read again - will get fresh data
echo "Step 6: Read after invalidation..."
curl -s http://localhost:8000/products/prod-003 | jq '{name, price, cache_status}'
```

**Key insight:** Stale reads occur when:
1. Another service updates the database directly
2. A database admin makes manual changes
3. The cache entry hasn't expired yet

---

### Exercise 5: Measure Hit Rate Over Time

Watch how the cache hit rate improves as the cache warms up.

```bash
# Clear cache
curl -s -X POST http://localhost:8000/admin/cache/clear | jq

# Check initial stats
echo "Initial stats (empty cache):"
curl -s http://localhost:8000/admin/cache-stats | jq '.service_stats'

# Make 20 reads (cycling through products)
echo "Making 20 reads..."
for i in {1..20}; do
  PROD_ID="prod-00$((($i % 5) + 1))"
  curl -s http://localhost:8000/products/$PROD_ID > /dev/null
  echo -n "."
done
echo ""

# Check stats after reads
echo "Stats after reads:"
curl -s http://localhost:8000/admin/cache-stats | jq '.service_stats'
```

**Expected results:**
- First 5 reads: all misses (each product read once)
- Next 15 reads: all hits (products already cached)
- Final hit rate: 75% (15 hits / 20 total)

---

### Exercise 6: Compare TTL Impact

The default TTL is 60 seconds. Let's observe what happens when entries expire.

```bash
# Clear cache and read a product
curl -s -X POST http://localhost:8000/admin/cache/clear | jq
curl -s http://localhost:8000/products/prod-001 | jq '{cache_status}'

# Check TTL
echo "Initial TTL:"
curl -s http://localhost:8000/admin/cache/key/prod-001 | jq '{ttl_seconds}'

# Wait 10 seconds and check again
echo "Waiting 10 seconds..."
sleep 10
echo "TTL after 10 seconds:"
curl -s http://localhost:8000/admin/cache/key/prod-001 | jq '{ttl_seconds}'

# Read refreshes TTL (because we repopulate on miss)
# But since it's a hit, TTL doesn't reset
curl -s http://localhost:8000/products/prod-001 | jq '{cache_status}'
```

**Note:** In our implementation, cache hits don't refresh TTL. This is a design choice. Some implementations use "sliding expiration" where every access resets the TTL.

---

### Exercise 7: Load Test Analysis

Run a load test to see cache-aside behavior under sustained traffic.

```bash
# Run the cache warmup test
docker compose run --rm lab08-k6 run /scripts/cache-warmup.js
```

While the test runs, watch:
1. **Grafana** (http://localhost:3001) - Cache hit rate over time
2. **Jaeger** (http://localhost:16686) - Trace patterns

```bash
# Run the mixed workload test (90% reads, 10% writes)
docker compose run --rm lab08-k6 run /scripts/basic.js
```

**Questions to answer:**
- What's the steady-state cache hit rate?
- How do writes (which invalidate cache) affect the hit rate?
- What's the p95 latency for cache hits vs misses?

---

### Exercise 8: Debug with Traces

Use Jaeger to understand the full request lifecycle.

1. Open http://localhost:16686
2. Select "product-service" from Service dropdown
3. Click "Find Traces"

**For a cache hit:**
- Look for traces with only `cache_lookup` span
- No `db_read` span present
- Total duration should be very short

**For a cache miss:**
- Look for traces with both `cache_lookup` and `db_read` spans
- Also has `cache_populate` span
- Total duration is higher

**For a write:**
- `db_insert` or `db_update` span
- `cache_invalidate` span (not `cache_set`!)
- No `cache_lookup` span on write path

---

## Key Takeaways

1. **Cache-aside is lazy** - Data is only cached when it's read, not when it's written.

2. **Lower write latency** - Writes only go to the database, not to both cache and DB.

3. **Stale reads are possible** - If the database is updated externally, the cache may serve stale data until TTL expires or explicit invalidation.

4. **Hit rate improves over time** - As more items are read, they get cached. After warmup, hit rate stabilizes.

5. **Invalidation is critical** - Without proper invalidation, stale data persists. Options:
   - Explicit invalidation on every write (what we do)
   - TTL-based expiration only (simpler but staler)
   - Pub/sub for cache invalidation events (more complex)

6. **Good for read-heavy workloads** - The pattern shines when reads >> writes.

## When to Use Cache-Aside

**Good fit:**
- Read-heavy workloads (>80% reads)
- Can tolerate eventual consistency
- Need fast write latency
- Complex caching logic (computed values, partial updates)
- Multiple services reading same data

**Poor fit:**
- Write-heavy workloads
- Strict consistency requirements (use write-through)
- Simple caching needs (write-through is simpler)

## Comparison with Other Patterns

| Pattern | Write Behavior | Read Behavior | Consistency | Use Case |
|---------|---------------|---------------|-------------|----------|
| **Cache-Aside** | DB only, invalidate cache | Cache first, then DB | Eventual | Read-heavy, complex caching |
| **Write-Through** | Both DB and cache | Cache first, then DB | Strong | Consistency-critical |
| **Write-Behind** | Cache first, async DB | Cache first, then DB | Eventual | Write-heavy, can lose data |
| **Read-Through** | N/A | Cache handles DB reads | Depends | Simple read caching |

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
docker compose logs lab08-redis
docker compose exec lab08-redis redis-cli ping
```

### Postgres connection errors
```bash
docker compose logs lab08-postgres
docker compose exec lab08-postgres pg_isready -U labuser -d products_db
```

### Traces not appearing in Jaeger
- Wait 10-15 seconds after making requests
- Check that otel-collector is running: `docker compose logs lab08-otel-collector`

### Cache not behaving as expected
```bash
# Check cache stats
curl -s http://localhost:8000/admin/cache-stats | jq

# Inspect specific key
curl -s http://localhost:8000/admin/cache/key/prod-001 | jq

# Clear cache and start fresh
curl -s -X POST http://localhost:8000/admin/cache/clear | jq
```

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/products` | GET | List all products |
| `/products` | POST | Create a new product |
| `/products/{id}` | GET | Get a product (cache-aside read) |
| `/products/{id}` | PUT | Update a product (invalidates cache) |
| `/products/{id}` | DELETE | Delete a product |
| `/admin/cache-stats` | GET | View cache statistics |
| `/admin/cache/clear` | POST | Clear all cache entries |
| `/admin/cache/key/{id}` | GET | Inspect a specific cache key |
| `/admin/invalidate/{id}` | POST | Manually invalidate a cache entry |
| `/admin/simulate-stale-read/{id}` | POST | Simulate stale read scenario |

## Next Lab

[Lab 09: TBD](../week-09-tbd/) - Stay tuned for the next distributed systems pattern!
