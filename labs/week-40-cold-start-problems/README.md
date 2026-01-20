# Lab 40: Cold Start Problems

Why are deploys so slow? When you deploy a new instance, users experience latency spikes even though your code is "faster." In this lab, you'll explore the hidden costs of cold starts: empty caches, cold connection pools, and unoptimized JIT code.

## What You'll Learn

- Why fresh deployments cause latency spikes
- How cold caches impact request latency (10-100x slower)
- How connection pool cold starts affect database performance
- Cache warming strategies and their trade-offs
- Gradual traffic shifting to minimize user impact
- How to measure and visualize cold start penalties

## The Problem

When you deploy a new instance of your service, several things are "cold":

1. **Cache is Empty** - Every request is a cache miss, hitting slow backends
2. **Connection Pools are Empty** - First requests must establish new connections (TCP handshake, TLS, authentication)
3. **JIT Compiler Hasn't Optimized** - Code runs interpreted before hot paths are compiled
4. **Local State is Gone** - Any in-memory optimizations (compiled regexes, parsed configs) must be rebuilt

This lab focuses on the first two - cache and connection pool warming.

## Architecture

```
                    ┌─────────────────────────────────────────────────────────────┐
                    │                        NGINX                                 │
                    │                    Load Balancer                             │
                    │    (weighted routing between warm and cold instances)        │
                    └──────────────────────────┬──────────────────────────────────┘
                                               │
                    ┌──────────────────────────┼──────────────────────────────────┐
                    │                          │                                   │
          ┌─────────▼─────────┐      ┌─────────▼─────────┐      ┌─────────────────┐
          │    API (WARM)     │      │    API (COLD)     │      │ Warmup Service  │
          │                   │      │                   │      │                 │
          │ - Pre-warmed cache│      │ - Empty cache     │      │ - Cache warming │
          │ - Ready pool      │      │ - Cold pool       │      │ - Traffic shift │
          └─────────┬─────────┘      └─────────┬─────────┘      └────────┬────────┘
                    │                          │                          │
                    └──────────────────────────┼──────────────────────────┘
                                               │
          ┌────────────────────────────────────┼────────────────────────────────────┐
          │                                    │                                     │
     ┌────▼────┐                         ┌─────▼─────┐                         ┌─────▼─────┐
     │  Redis  │                         │  Backend  │                         │ Postgres  │
     │ (cache) │                         │           │                         │   (DB)    │
     └─────────┘                         └───────────┘                         └───────────┘
```

### Services

| Service | Port | Description |
|---------|------|-------------|
| nginx | 8080 | Load balancer with weighted routing |
| api-warm | 8000 | API instance with pre-warmed cache and connections |
| api-cold | 8002 | API instance with cold cache (simulates fresh deploy) |
| backend | 8001 | Backend service with database access |
| warmup-service | 8003 | Cache warming and traffic shifting control |
| redis | 6379 | Cache store |
| postgres | 5432 | Database |

### Observability

| Service | URL | Purpose |
|---------|-----|---------|
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |

## Prerequisites

- Docker and Docker Compose
- curl and jq
- k6 (for load testing)

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

### 3. Open the Dashboards

- Grafana: http://localhost:3001 (admin/admin)
- Jaeger: http://localhost:16686

## Lab Exercises

### Exercise 1: Observe Cold vs Warm Instance Latency

First, let's see the difference between a warm and cold instance.

#### Check instance status

```bash
# Warm instance (should show cache_warmed: true)
curl -s http://localhost:8000/admin/status | jq

# Cold instance (should show cache_warmed: false)
curl -s http://localhost:8002/admin/status | jq
```

#### Compare product lookup latency

```bash
# Product lookup on WARM instance (should be fast - cache hit)
echo "=== WARM INSTANCE ==="
for i in {1..5}; do
  curl -s http://localhost:8000/api/product/$i | jq '{instance, cache_hit, duration_ms}'
done

# Product lookup on COLD instance (should be slow - cache miss)
echo "=== COLD INSTANCE ==="
for i in {1..5}; do
  curl -s http://localhost:8002/api/product/$i | jq '{instance, cache_hit, duration_ms}'
done
```

**Observe:**
- Warm instance: ~5-20ms (cache hits)
- Cold instance: ~50-150ms (cache misses, backend calls)

#### Compare database query latency

```bash
# DB query on warm instance (connection already in pool)
echo "=== WARM INSTANCE DB ==="
for i in {1..3}; do
  curl -s http://localhost:8000/api/db-query | jq '{instance, acquire_time_ms, query_time_ms, pool_size}'
done

# DB query on cold instance (must establish connection)
echo "=== COLD INSTANCE DB ==="
for i in {1..3}; do
  curl -s http://localhost:8002/api/db-query | jq '{instance, acquire_time_ms, query_time_ms, pool_size}'
done
```

**Observe:**
- Warm instance: acquire_time_ms < 1ms
- Cold instance: First requests show higher acquire_time as connections are established

---

### Exercise 2: Simulate a Fresh Deploy (Clear Cache)

Let's simulate what happens when you deploy a new version - the cache is empty.

#### Clear the cold instance cache

```bash
# Clear cache on cold instance
curl -s -X POST http://localhost:8002/admin/clear-cache | jq

# Verify cache is cleared
curl -s http://localhost:8002/admin/status | jq '{cache_warmed}'
```

#### Make requests and observe cache misses

```bash
echo "=== AFTER CACHE CLEAR ==="
for i in {1..10}; do
  result=$(curl -s http://localhost:8002/api/product/$i)
  echo "Product $i: $(echo $result | jq -r '{cache_hit, duration_ms}')"
done
```

**Observe:** All requests are cache misses with higher latency.

#### Check Jaeger for traces

1. Open http://localhost:16686
2. Select "api-cold" service
3. Look for traces with "redis_get" followed by "backend_call"
4. Compare with "api-warm" traces that only have "redis_get"

---

### Exercise 3: Cache Warming Strategies

Now let's see how cache warming can help.

#### Strategy 1: Eager Warming (Warm everything at startup)

This is already enabled for the warm instance. Check the startup time:

```bash
curl -s http://localhost:8000/admin/status | jq '{cache_warmed, connections_warmed, uptime_seconds}'
```

#### Strategy 2: On-Demand Warming (Warm before traffic shift)

Use the warmup service to warm the cold instance:

```bash
# Start cache warming (async)
curl -s -X POST http://localhost:8003/warmup/cache/cold | jq

# Check warming status
curl -s http://localhost:8003/status | jq '.warmup_in_progress'

# Or run synchronously and wait
curl -s -X POST http://localhost:8003/warmup/cache/cold/sync | jq
```

After warming, compare latencies again:

```bash
echo "=== COLD INSTANCE AFTER WARMING ==="
for i in {1..5}; do
  curl -s http://localhost:8002/api/product/$i | jq '{cache_hit, duration_ms}'
done
```

**Observe:** Latencies should now be similar to the warm instance.

---

### Exercise 4: Gradual Traffic Shifting

Instead of switching all traffic to a new instance immediately (which causes latency spikes), use gradual traffic shifting.

#### Check current traffic weights

```bash
curl -s http://localhost:8003/traffic/weights | jq
```

#### Shift traffic gradually

```bash
# Start a 60-second gradual shift from warm to cold
# (In production, this might take 5-10 minutes)
curl -s -X POST "http://localhost:8003/traffic/shift?from_instance=warm&to_instance=cold&duration_seconds=60&step_percent=10" | jq
```

#### Watch the traffic weights change

```bash
while true; do
  curl -s http://localhost:8003/traffic/weights | jq -c '.weights'
  sleep 5
done
```

#### Monitor in Grafana

Open the "Lab 40: Cold Start Problems" dashboard and watch:
- Traffic weight distribution
- Latency comparison between instances
- Cache hit rates

---

### Exercise 5: Full Deploy Simulation

Now let's simulate a complete deployment scenario with proper warming.

#### Step 1: Reset to initial state

```bash
# Clear cold instance cache
curl -s -X POST http://localhost:8002/admin/clear-cache

# Reset traffic to 100% warm
curl -s -X POST "http://localhost:8003/traffic/set?warm=100&cold=0" | jq
```

#### Step 2: Run the deploy simulation

```bash
# This will:
# 1. Clear the cold instance cache (fresh deploy)
# 2. Warm the cache
# 3. Gradually shift traffic
curl -s -X POST "http://localhost:8003/simulate/deploy?warmup_first=true&shift_duration=60" | jq
```

#### Step 3: Observe in Grafana

Watch the dashboard to see:
1. Initial spike in cold start latency during warming
2. Cache hit rate increasing on cold instance
3. Traffic weight gradually shifting
4. Overall latency remaining stable

---

### Exercise 6: Load Testing Cold Starts

Run k6 to generate sustained load and measure cold start impact.

#### Basic comparison test

```bash
docker compose run --rm k6 run /scripts/cold-start-comparison.js
```

#### Deploy simulation under load

In one terminal, start the load test:
```bash
docker compose run --rm k6 run /scripts/deploy-simulation.js
```

In another terminal, trigger a deploy:
```bash
# Clear cache and shift traffic
curl -s -X POST http://localhost:8002/admin/clear-cache
curl -s -X POST "http://localhost:8003/traffic/shift?from_instance=warm&to_instance=cold&duration_seconds=60"
```

**Observe the k6 output and Grafana dashboard** to see latency impact during the shift.

---

## Key Takeaways

### 1. Cold Caches are Expensive
- Cache misses can be 10-100x slower than hits
- A fresh deploy means 100% cache miss rate initially
- Impact compounds with traffic - more requests = more cache misses hitting backends

### 2. Connection Pool Warming Matters
- First connection requires: DNS, TCP handshake, TLS handshake, authentication
- This can add 10-50ms per new connection
- Pre-warming connections before traffic arrives eliminates this penalty

### 3. Gradual Traffic Shifting is Critical
- Shifting 100% traffic instantly amplifies cold start penalties
- Gradual shifting (canary deploys) limits blast radius
- Gives caches time to warm under lighter load

### 4. Warm Before You Shift
- Eager warming at startup adds deployment latency but helps users
- On-demand warming before traffic shift is a good compromise
- Monitor cache hit rates to know when an instance is "ready"

### 5. Measure Everything
- Track cache hit/miss ratios per instance
- Monitor connection pool sizes and acquire times
- Compare latency percentiles (p50, p95, p99) between instances

## Real-World Considerations

### Things This Lab Doesn't Cover

1. **JIT Compilation** - In JVM/V8/etc., code runs slower until hot paths are compiled
2. **DNS Caching** - First requests may need DNS resolution
3. **OS-Level Caches** - Filesystem caches, page caches, etc.
4. **CDN/Edge Caching** - Origin traffic spikes when edge cache expires
5. **Database Query Plan Caches** - First queries may need plan compilation

### Production Best Practices

1. **Readiness Probes** - Don't send traffic until instance is warmed
2. **Pre-Production Warming** - Replay traffic or run synthetic requests
3. **Feature Flags** - Roll out to small percentage first
4. **Auto-scaling Awareness** - Warm instances before auto-scaler adds them
5. **Deployment Windows** - Deploy during low-traffic periods when possible

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Redis connection issues
```bash
docker compose logs redis
docker compose exec redis redis-cli ping
```

### Database issues
```bash
docker compose logs postgres
docker compose exec postgres pg_isready -U labuser -d labdb
```

### Check specific service status
```bash
curl -s http://localhost:8000/admin/status | jq  # api-warm
curl -s http://localhost:8002/admin/status | jq  # api-cold
curl -s http://localhost:8003/status | jq        # warmup-service
```

## Next Steps

- [Lab 41: Circuit Breakers](../week-41-circuit-breakers/) - Protecting services from cascade failures
