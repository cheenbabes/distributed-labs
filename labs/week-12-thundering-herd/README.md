# Lab 12: Thundering Herd (Cache Stampede)

When a cached value expires under heavy load, every concurrent request simultaneously discovers the cache is empty and tries to regenerate the data. This "thundering herd" overwhelms your database with duplicate work. In this lab, you'll observe the stampede in action and implement solutions.

## What You'll Learn

- How cache expiration triggers thundering herd / cache stampede
- How to detect stampedes using observability metrics
- Lock-based protection: Only one request regenerates the cache
- Probabilistic early refresh: Spread out refreshes before expiry
- Trade-offs between different protection mechanisms

## The Problem

```
Normal Operation (cached):
┌────────┐     ┌───────┐     ┌──────────┐
│ Client │────▶│ Cache │────▶│ Response │  Fast! (~1ms)
└────────┘     └───────┘     └──────────┘

Cache Expiry - The Stampede:
┌────────┐ ─┐
│Client 1│  │
├────────┤  │     ┌───────┐     ┌──────────┐
│Client 2│  ├────▶│ MISS! │────▶│ Database │  All hit DB simultaneously!
├────────┤  │     └───────┘     └──────────┘
│Client 3│  │                        │
├────────┤  │                        ▼
│   ...  │  │               ┌────────────────┐
│Client N│ ─┘               │ DB overwhelmed │
└────────┘                  │ Slow for all!  │
                            └────────────────┘
```

With a 5-second cache TTL and 20 requests/second, every 5 seconds you get ~100 requests all trying to regenerate the cache at once!

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         Load Generator (k6)                       │
│                    20 req/s to single hot key                     │
└───────────────────────────────┬──────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                           lab12-api                               │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                     Protection Layer                         │ │
│  │  ┌─────────────────┐     ┌──────────────────────────────┐  │ │
│  │  │  Lock-based     │     │  Probabilistic Early Refresh │  │ │
│  │  │  (mutex/lock)   │     │  (XFetch algorithm)          │  │ │
│  │  └─────────────────┘     └──────────────────────────────┘  │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                │                                  │
│                                ▼                                  │
│  ┌─────────────────┐     ┌─────────────────┐                    │
│  │  Redis Cache    │────▶│  Database (sim) │                    │
│  │  TTL: 5 seconds │     │  500-1000ms     │                    │
│  └─────────────────┘     └─────────────────┘                    │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
                    ┌───────────────────────┐
                    │   Observability       │
                    │  Prometheus, Grafana  │
                    │  Jaeger               │
                    └───────────────────────┘
```

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- A browser for Grafana

## Quick Start

### 1. Start the Lab

```bash
cd labs/week-12-thundering-herd
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
| API | http://localhost:8000 | Main application |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |
| Prometheus | http://localhost:9090 | Metrics |
| Jaeger | http://localhost:16686 | Distributed traces |

## Lab Exercises

### Exercise 1: Observe Normal Caching

First, let's see the cache working normally with low traffic.

```bash
# Make a single request (first one will be slow - cache miss)
curl -s http://localhost:8000/product/test-123 | jq

# Make it again (should be fast - cache hit)
curl -s http://localhost:8000/product/test-123 | jq

# Check the cache status in the response
# Look for: "cache_status": "hit" vs "miss_no_protection"
```

**What to observe:**
- First request: ~500-1000ms (database query)
- Subsequent requests: ~10ms (cache hit)
- Response includes `cache_status` field

---

### Exercise 2: Watch the Stampede Happen

Now let's create the conditions for a stampede. We'll send concurrent requests while the cache is empty.

```bash
# Clear the cache
curl -s -X DELETE http://localhost:8000/cache | jq

# Send 20 concurrent requests (stampede!)
for i in {1..20}; do
  curl -s http://localhost:8000/product/stampede-test &
done
wait

# Check the stats - look for stampede events
curl -s http://localhost:8000/admin/stats | jq
```

**Open Grafana** (http://localhost:3001) and look at:
1. "Concurrent DB Queries" panel - should spike to ~20
2. "Stampede Events Detected" panel - should show events
3. "Cache Hit Rate" - drops to 0% during stampede

---

### Exercise 3: Measure the Impact with Load Test

Run a sustained load test to see repeated stampedes every 5 seconds (cache TTL).

```bash
# Run the k6 load test (2 minutes)
docker compose run --rm lab12-k6 run /scripts/thundering-herd.js
```

**While the test runs, watch Grafana:**
1. **Concurrent DB Queries**: Spikes every ~5 seconds
2. **Cache Hit Rate**: Drops periodically
3. **Request Latency**: p99 spikes when stampede hits
4. **Stampede Events**: Counter keeps incrementing

**Key metrics to record:**
- How many concurrent DB queries during stampede?
- What's the p99 latency during stampede vs normal?
- How often do stampedes occur?

---

### Exercise 4: Enable Lock-Based Protection

Lock-based protection ensures only ONE request regenerates the cache while others wait.

```bash
# Enable lock protection
curl -s -X POST http://localhost:8000/admin/protection \
  -H "Content-Type: application/json" \
  -d '{"enable_lock_protection": true}' | jq

# Clear cache to test
curl -s -X DELETE http://localhost:8000/cache | jq

# Run the load test again
docker compose run --rm lab12-k6 run /scripts/thundering-herd.js
```

**Compare in Grafana:**
1. **Concurrent DB Queries**: Should stay at 1 (only one request hits DB)
2. **Lock Acquisitions**: Shows "acquired" vs "waited"
3. **Stampede Events**: Should be 0 or near-zero
4. **Request Latency**: More consistent, no spikes

**Trade-off**: Requests that wait for the lock still have high latency (~500-1000ms), but the database isn't overwhelmed.

---

### Exercise 5: Compare with Probabilistic Early Refresh

Probabilistic early refresh randomly refreshes the cache BEFORE it expires, spreading out the load.

```bash
# Disable lock protection, enable probabilistic refresh
curl -s -X POST http://localhost:8000/admin/protection \
  -H "Content-Type: application/json" \
  -d '{
    "enable_lock_protection": false,
    "enable_probabilistic_refresh": true,
    "probabilistic_refresh_factor": 0.5
  }' | jq

# Clear cache and run load test
curl -s -X DELETE http://localhost:8000/cache | jq
docker compose run --rm lab12-k6 run /scripts/thundering-herd.js
```

**What to observe:**
1. **Early Refresh Triggers**: Requests refresh before TTL expires
2. **Concurrent DB Queries**: Lower but not zero (some overlap)
3. **Cache Hit Rate**: Higher overall (refreshes happen proactively)

**Bonus: Combine both protections:**
```bash
curl -s -X POST http://localhost:8000/admin/protection \
  -H "Content-Type: application/json" \
  -d '{
    "enable_lock_protection": true,
    "enable_probabilistic_refresh": true,
    "probabilistic_refresh_factor": 0.3
  }' | jq
```

This gives you the best of both worlds: proactive refresh with lock protection as a fallback.

---

## Key Takeaways

### 1. Cache Stampede is Real and Measurable
Every time your cache expires under load, you get a burst of duplicate database queries. With short TTLs and high traffic, this happens frequently.

### 2. Detection is Critical
Without proper metrics, you might not even know stampedes are happening. Key metrics:
- Concurrent database queries (spike detection)
- Cache hit/miss ratio (periodic drops)
- Request latency percentiles (p99 spikes)

### 3. Lock-Based Protection
**Pros:**
- Guarantees only one regeneration
- Simple to understand
- Database protected from spikes

**Cons:**
- Requests wait, adding latency
- Single point of failure (lock holder)
- Doesn't prevent initial miss

### 4. Probabilistic Early Refresh (XFetch)
**Pros:**
- Spreads load over time
- No waiting for locks
- Proactive, not reactive

**Cons:**
- Some wasted refreshes
- Doesn't guarantee single regeneration
- Tuning required (factor parameter)

### 5. Best Practice: Defense in Depth
Combine multiple strategies:
1. **Probabilistic early refresh** - Proactively refresh before expiry
2. **Lock-based protection** - Fallback when early refresh misses
3. **Reasonable TTLs** - Balance freshness vs. stampede frequency
4. **Circuit breakers** - Protect DB from overload

## Configuration Reference

### Runtime Configuration (via API)

```bash
# View current config
curl http://localhost:8000/admin/config | jq

# Configure protection
curl -X POST http://localhost:8000/admin/protection \
  -H "Content-Type: application/json" \
  -d '{
    "enable_lock_protection": true,
    "enable_probabilistic_refresh": true,
    "probabilistic_refresh_factor": 0.3
  }'

# Configure cache/DB simulation
curl -X POST http://localhost:8000/admin/cache \
  -H "Content-Type: application/json" \
  -d '{
    "cache_ttl_seconds": 10,
    "db_query_min_ms": 200,
    "db_query_max_ms": 500
  }'
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_TTL_SECONDS` | 5 | Cache entry TTL |
| `DB_QUERY_MIN_MS` | 500 | Min simulated DB query time |
| `DB_QUERY_MAX_MS` | 1000 | Max simulated DB query time |
| `ENABLE_LOCK_PROTECTION` | false | Enable mutex-based protection |
| `ENABLE_PROBABILISTIC_REFRESH` | false | Enable XFetch-style early refresh |
| `PROBABILISTIC_REFRESH_FACTOR` | 0.1 | Higher = more early refreshes |

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Metrics not appearing in Grafana
- Wait 30-60 seconds for scrape interval
- Check Prometheus targets: http://localhost:9090/targets

### Cache not expiring
- Default TTL is 5 seconds
- Check config: `curl http://localhost:8000/admin/config`

### Load test not generating enough traffic
- Increase k6 rate in `loadtest/thundering-herd.js`
- Check k6 logs: `docker compose logs lab12-k6`

## Further Reading

- [Cache Stampede on Wikipedia](https://en.wikipedia.org/wiki/Cache_stampede)
- [XFetch: A Probabilistic Strategy for Refreshing Caches](https://www.usenix.org/legacy/publications/library/proceedings/usits03/tech/full_papers/vitter/vitter.pdf)
- [Facebook's Memcache Lease Mechanism](https://www.usenix.org/system/files/conference/nsdi13/nsdi13-final170_update.pdf)

## Next Lab

[Lab 13: Distributed Locking](../week-13-distributed-locking/) - Coordinate between multiple service instances using distributed locks.
