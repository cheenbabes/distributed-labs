# Lab 02: What Happens When Your Cache Dies

Your cache has been silently saving you from 10x database load. In this lab, we'll prove it by killing Redis mid-traffic and watching Postgres buckle under the sudden flood - latency spikes from 5ms to 500ms in seconds, visible in real-time dashboards.

## What You'll Learn

- How caching dramatically reduces database load
- What happens to your system when a cache suddenly fails
- How to observe cache hit rates, database latency, and request latency in real-time
- The importance of understanding your system's dependencies

## Architecture

```
                                    ┌─────────────┐
                                    │    Redis    │ ← We'll kill this
                                    │    Cache    │
                                    └──────▲──────┘
                                           │
                                           │ check cache first
                                           │
┌──────────┐    ┌───────────┐    ┌────────┴────────┐    ┌───────────┐
│  Client  │───▶│  Gateway  │───▶│   Application   │───▶│  Postgres │
└──────────┘    └───────────┘    └─────────────────┘    │  Database │
                                                         └───────────┘
```

**Flow:**
1. Gateway receives request
2. Application checks Redis for cached data
3. Cache HIT: Return cached data (~2ms)
4. Cache MISS: Query Postgres (~50-100ms), store in Redis, return

When Redis dies:
- Every request becomes a cache miss
- Database gets 100% of traffic instead of ~10%
- Latency spikes from ~5ms to ~100ms+ per request

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

All services should show as "healthy" (except k6 which only runs on-demand).

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Gateway | http://localhost:8000 | API entry point |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Baseline with Cache Working

Let's establish a baseline with Redis healthy.

```bash
# Make a few requests to warm the cache
for i in {1..5}; do
  curl -s http://localhost:8000/api/data | jq -r '.latency_ms'
done

# Check cache statistics
curl -s http://localhost:8000/api/stats | jq
```

**Expected:**
- First request: ~50-100ms (cache miss, hits database)
- Subsequent requests: ~2-5ms (cache hits)
- Cache hit rate: 80-90%+ after warm-up

Now look at the traces in Jaeger:
1. Open http://localhost:16686
2. Select "gateway" from the Service dropdown
3. Click "Find Traces"
4. Compare a cache hit trace vs cache miss trace

**Question:** How much faster are cache hits vs cache misses?

---

### Exercise 2: Generate Sustained Load

Start generating load so we can observe the impact of cache failure:

```bash
# Run load test in background
docker compose run --rm k6 run /scripts/cache-test.js &
```

While load is running:
1. Open Grafana at http://localhost:3001
2. Navigate to the "Cache Performance" dashboard
3. Observe the metrics:
   - Cache hit rate (should be ~80-90%)
   - Request latency (should be low, ~10-20ms)
   - Database query rate (should be low)

---

### Exercise 3: Kill the Cache

Now the dramatic moment - kill Redis mid-traffic:

```bash
# Kill Redis
docker compose stop redis
```

Immediately watch the dashboards. You should see:

1. **Cache hit rate**: Drops to 0%
2. **Request latency**: Spikes from ~10ms to 100-200ms
3. **Database queries/sec**: Increases dramatically
4. **Error rate**: May increase if database gets overwhelmed

**Key Observation:** The latency spike is immediate and dramatic.

---

### Exercise 4: Observe the Impact

While Redis is down, make some manual requests:

```bash
# Check response time (will be slow)
time curl -s http://localhost:8000/api/data | jq '.latency_ms'

# Check stats - note cache hit rate is now 0%
curl -s http://localhost:8000/api/stats | jq
```

Look at traces in Jaeger:
1. Find recent traces
2. Notice all requests now hit the database
3. Compare total latency to when cache was working

---

### Exercise 5: Restore the Cache

Bring Redis back:

```bash
# Restart Redis
docker compose start redis

# Wait a moment for connection to re-establish
sleep 3

# Make requests to warm cache
for i in {1..10}; do
  curl -s http://localhost:8000/api/data > /dev/null
done

# Check stats
curl -s http://localhost:8000/api/stats | jq
```

Watch the dashboards recover:
- Cache hit rate climbs back up
- Request latency drops
- Database queries decrease

---

### Exercise 6: Quantify the Impact

Calculate the impact of cache failure:

```bash
# With cache (after warm-up)
echo "With cache:"
for i in {1..10}; do
  curl -s -o /dev/null -w "%{time_total}s\n" http://localhost:8000/api/data
done

# Kill cache
docker compose stop redis
sleep 2

# Without cache
echo ""
echo "Without cache:"
for i in {1..10}; do
  curl -s -o /dev/null -w "%{time_total}s\n" http://localhost:8000/api/data
done

# Restore
docker compose start redis
```

**Questions:**
- What was the latency multiplier? (e.g., 10x, 20x?)
- How quickly did the system degrade after cache death?
- What would happen to your users during this time?

---

## Key Takeaways

1. **Caches hide a LOT of load** - Your database might be handling 10% of actual traffic thanks to caching

2. **Cache failure is sudden and dramatic** - Unlike gradual degradation, cache death is an immediate cliff

3. **Monitor cache hit rates** - A dropping hit rate is an early warning sign

4. **Test cache failure scenarios** - Don't learn about this in production

5. **Have a plan for cache failure**:
   - Circuit breakers to prevent database overload
   - Cache warming strategies
   - Graceful degradation

## Advanced Exercises

### A. Cache Stampede

What happens when cache expires and multiple requests hit at once?

```bash
# Clear the cache
curl -X POST http://localhost:8000/api/admin/clear-cache

# Immediately hit with parallel requests
for i in {1..20}; do
  curl -s http://localhost:8000/api/data &
done
wait
```

Watch for duplicate database queries - this is a "thundering herd" problem.

### B. Gradual Cache Failure

Instead of killing Redis, fill it until it evicts:

```bash
# Fill cache with garbage (simulate memory pressure)
curl -X POST http://localhost:8000/api/admin/fill-cache?count=10000
```

Watch the cache hit rate decrease as legitimate data gets evicted.

---

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Redis won't reconnect after restart
The application has automatic reconnection. Wait a few seconds.

### Traces not appearing in Jaeger
- Wait 10-15 seconds after making requests
- Check that otel-collector is running: `docker compose logs otel-collector`

### Port already in use
Check for conflicts: `lsof -i :8000` (or whichever port)

## Next Lab

[Lab 03: Circuit Breakers](../week-03-circuit-breakers/) - We'll add resilience patterns to handle failures gracefully.
