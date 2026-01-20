# Lab 16: Memory Leak Investigation

Watch a service slowly die from a memory leak, learn to detect it through monitoring, diagnose the source, and fix it. This lab demonstrates common memory leak patterns in Python services and how to investigate them using observability tools.

## What You Will Learn

- How memory leaks manifest in production services
- Using metrics to detect memory growth over time
- Correlating request rate with memory consumption
- Using Python's tracemalloc to identify leak sources
- Common memory leak patterns and how to prevent them

## Architecture

```
                                    +-----------------+
                                    |    Grafana      |
                                    |  (Dashboards)   |
                                    +--------+--------+
                                             |
                                    +--------v--------+
                                    |   Prometheus    |
                                    |   (Metrics)     |
                                    +--------+--------+
                                             |
+----------+     +-----------+      +--------v--------+     +-----------+
|   k6     |---->|  Leaky    |----->|  OTEL Collector |---->|  Jaeger   |
| (Load)   |     |  Service  |      |   (Telemetry)   |     |  (Traces) |
+----------+     +-----------+      +-----------------+     +-----------+
                      |
                      v
               Memory grows
               until OOM!
```

The leaky service has three configurable memory leak types:
1. **Unbounded Cache** - Stores every request without eviction
2. **Event Listener Accumulation** - Callbacks that never get cleaned up
3. **Request History** - Keeps full request records forever

## Prerequisites

- Docker and Docker Compose
- curl (for API calls)
- jq (for JSON formatting)

## Quick Start

### 1. Start the Lab

```bash
cd labs/week-16-memory-leak
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
| Leaky Service | http://localhost:8080 | API and admin endpoints |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Establish Baseline Memory Usage

First, understand normal memory usage without any leaks enabled.

```bash
# Check current memory status
curl -s http://localhost:8080/admin/memory | jq

# Check leak status (all should be disabled)
curl -s http://localhost:8080/admin/leaks/status | jq
```

Make a few requests and observe memory stays stable:

```bash
# Generate some traffic
for i in {1..100}; do
  curl -s http://localhost:8080/api/data > /dev/null
done

# Check memory again - should be similar
curl -s http://localhost:8080/admin/memory | jq '.process'
```

Open Grafana at http://localhost:3001 and view the "Memory Leak Investigation" dashboard. The memory line should be flat.

---

### Exercise 2: Enable Unbounded Cache Leak

Enable the unbounded cache leak and watch memory grow.

```bash
# Enable the cache leak
curl -s -X POST http://localhost:8080/admin/leaks/cache \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' | jq

# Verify it's enabled
curl -s http://localhost:8080/admin/leaks/status | jq '.leaks.cache'
```

Now generate traffic and watch memory grow:

```bash
# Generate sustained traffic (run this for 2-3 minutes)
for i in {1..1000}; do
  curl -s http://localhost:8080/api/data > /dev/null
  echo -n "."
done
echo ""

# Check memory growth
curl -s http://localhost:8080/admin/leaks/status | jq
```

In Grafana, you should see:
- Process memory steadily increasing
- Cache entries count growing
- Cache size bytes growing

**Questions:**
- How much memory per request is being leaked?
- At this rate, how long until you hit the 256MB limit?

---

### Exercise 3: Correlate Request Rate with Memory Growth

Use Grafana to see the correlation between traffic and memory growth.

1. Open http://localhost:3001
2. Go to the "Memory Leak Investigation" dashboard
3. Look at the "Request Rate vs Memory Growth" panel
4. Notice how memory increases proportionally to request count

Run a burst of traffic:

```bash
# High-rate burst
for i in {1..500}; do
  curl -s http://localhost:8080/api/data > /dev/null &
done
wait

# Check the spike in Grafana
```

**Key Insight:** Memory growth rate is directly proportional to request rate. This is a hallmark of request-scoped leaks.

---

### Exercise 4: Use tracemalloc to Identify Leak Source

Python's tracemalloc can show exactly where memory is being allocated.

```bash
# Take a baseline snapshot
curl -s http://localhost:8080/admin/memory/diff | jq

# Generate more traffic
for i in {1..200}; do
  curl -s http://localhost:8080/api/data > /dev/null
done

# Now get the diff - this shows what grew
curl -s http://localhost:8080/admin/memory/diff | jq
```

Look at the `growing_allocations` field. You should see allocations in `main.py` around the cache-related lines.

```bash
# Get a detailed memory snapshot
curl -s http://localhost:8080/admin/memory/snapshot | jq
```

**Key Fields:**
- `by_line`: Shows specific lines with highest memory allocation
- `size_mb`: How much memory each allocation site is using
- `count`: How many objects allocated at that line

---

### Exercise 5: Fix the Leak and Verify Memory Stabilizes

Clear the leaked data and disable the leak:

```bash
# Clear accumulated cache data
curl -s -X POST http://localhost:8080/admin/leaks/clear \
  -H "Content-Type: application/json" \
  -d '{"leak_type": "cache"}' | jq

# Trigger garbage collection
curl -s -X POST http://localhost:8080/admin/gc | jq

# Disable the leak
curl -s -X POST http://localhost:8080/admin/leaks/cache \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' | jq
```

Verify memory dropped:

```bash
curl -s http://localhost:8080/admin/memory | jq '.process'
```

Now generate more traffic and confirm memory stays stable:

```bash
for i in {1..500}; do
  curl -s http://localhost:8080/api/data > /dev/null
done

curl -s http://localhost:8080/admin/memory | jq '.process'
```

Watch Grafana - memory should now be flat even under load.

---

### Exercise 6: Compare Different Leak Types

Try the other leak types and compare their growth patterns.

#### Event Listener Leak

```bash
# Enable listener leak
curl -s -X POST http://localhost:8080/admin/leaks/listeners \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' | jq

# Generate traffic
for i in {1..500}; do
  curl -s http://localhost:8080/api/data > /dev/null
done

# Check listener count
curl -s http://localhost:8080/admin/leaks/status | jq '.leaks.listeners'

# Clean up
curl -s -X POST http://localhost:8080/admin/leaks/clear \
  -H "Content-Type: application/json" \
  -d '{"leak_type": "listeners"}' | jq

curl -s -X POST http://localhost:8080/admin/leaks/listeners \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' | jq
```

#### Request History Leak

```bash
# Enable history leak
curl -s -X POST http://localhost:8080/admin/leaks/history \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' | jq

# Generate traffic
for i in {1..500}; do
  curl -s http://localhost:8080/api/data > /dev/null
done

# Check history size
curl -s http://localhost:8080/admin/leaks/status | jq '.leaks.history'

# Clean up
curl -s -X POST http://localhost:8080/admin/leaks/clear \
  -H "Content-Type: application/json" \
  -d '{"leak_type": "all"}' | jq
```

#### Enable All Leaks (Danger Zone!)

```bash
# Enable all leaks - memory will grow fast!
curl -s -X POST http://localhost:8080/admin/leaks/cache \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'

curl -s -X POST http://localhost:8080/admin/leaks/listeners \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'

curl -s -X POST http://localhost:8080/admin/leaks/history \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'

# Run load test and watch the service die
docker compose run --rm lab16-k6 run /scripts/memory-stress.js
```

Watch Grafana - you'll see memory hit the 256MB limit and the container will be killed (OOM).

---

## Key Takeaways

1. **Memory leaks are silent killers** - They don't cause immediate failures, but slowly degrade until OOM

2. **Monitor memory over time** - A steadily increasing line is a red flag, even if current usage looks OK

3. **Correlate with request rate** - Request-proportional growth indicates per-request leaks

4. **Use profiling tools** - tracemalloc, memory_profiler, and similar tools show exactly where allocations happen

5. **Common leak patterns:**
   - Unbounded caches without TTL or size limits
   - Event handlers/callbacks that accumulate
   - Keeping references to request data (logging, debugging)
   - Closures capturing large objects

6. **Prevention strategies:**
   - Always set cache TTLs and max sizes (use `cachetools.TTLCache`, `functools.lru_cache`)
   - Clean up event listeners explicitly
   - Use weak references where appropriate
   - Review code for unbounded collections

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Service keeps restarting
The service hit the memory limit (256MB). Either:
- Clear the leaks: `curl -X POST localhost:8080/admin/leaks/clear -d '{"leak_type":"all"}'`
- Or restart fresh: `docker compose down -v && docker compose up --build -d`

### Metrics not showing in Grafana
- Wait 30 seconds for initial scrape
- Check Prometheus targets: http://localhost:9090/targets
- Verify service is healthy: `curl localhost:8080/health`

### tracemalloc shows no growth
Make sure leaks are enabled and you've generated traffic between snapshots.

## Next Lab

Continue your debugging journey in the next lab!
