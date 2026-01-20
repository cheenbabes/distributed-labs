# Lab 50: URL Shortener Part 2 - Scaling

In Week 5, you built a URL shortener. Now it's time to scale it. In this lab, you'll take a single-instance service and transform it into a horizontally scaled system with load balancing, distributed caching, and rate limiting.

## What You'll Learn

- How to horizontally scale a stateful service
- How consistent hashing enables cache sharding without hotspots
- How distributed rate limiting works across multiple instances
- How to aggregate analytics from multiple service instances
- How load balancers distribute traffic and handle failures
- How to observe and debug a scaled system with distributed tracing

## Architecture

```
                                    ┌─────────────────────────────────────┐
                                    │         Redis Cluster               │
                                    │   ┌─────┐  ┌─────┐  ┌─────┐        │
                                    │   │Shard│  │Shard│  │Shard│        │
                                    │   │  1  │  │  2  │  │  3  │        │
                                    │   └──▲──┘  └──▲──┘  └──▲──┘        │
                                    │      │       │       │             │
                                    └──────┼───────┼───────┼─────────────┘
                                           │       │       │
                                    Consistent Hashing Ring
                                           │       │       │
┌──────────┐    ┌───────────┐    ┌─────────┴───────┴───────┴──────────┐
│          │    │           │    │                                     │
│  Client  │───▶│   Nginx   │───▶│  ┌──────────┐  ┌──────────┐  ┌──────────┐
│          │    │    LB     │    │  │Shortener │  │Shortener │  │Shortener │
└──────────┘    └───────────┘    │  │    1     │  │    2     │  │    3     │
                                 │  └────┬─────┘  └────┬─────┘  └────┬─────┘
                                 │       │             │             │
                                 └───────┼─────────────┼─────────────┼──────┘
                                         │             │             │
                                         └─────────────┼─────────────┘
                                                       │
                                                       ▼
                                               ┌───────────────┐
                                               │   PostgreSQL  │
                                               │   (Shared)    │
                                               └───────────────┘
```

### Key Components

| Component | Purpose | Scaling Approach |
|-----------|---------|------------------|
| Nginx | Load balancing, rate limiting | Single instance (could be scaled with keepalived) |
| Shortener (x3) | API servers | Horizontal scaling, stateless |
| Redis (x3) | Cache sharding | Consistent hashing across shards |
| PostgreSQL | Persistent storage | Single instance (could use read replicas) |

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- k6 (optional, for load testing)
- Completion of Lab 5 (URL Shortener Part 1) recommended

## Quick Start

### 1. Start the Lab

```bash
cd labs/week-50-url-shortener-part2
docker compose up --build -d
```

### 2. Verify Services Are Running

```bash
docker compose ps
```

You should see 12 containers running:
- 3 shortener instances
- 3 Redis instances
- 1 Nginx load balancer
- 1 PostgreSQL
- 4 observability services (OTEL, Jaeger, Prometheus, Grafana)

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| API (via LB) | http://localhost:8000 | Load-balanced API endpoint |
| Instance 1 | http://localhost:8001 | Direct access to instance 1 |
| Instance 2 | http://localhost:8002 | Direct access to instance 2 |
| Instance 3 | http://localhost:8003 | Direct access to instance 3 |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Observe Load Balancing

Make multiple requests and observe how they're distributed across instances.

```bash
# Make 10 requests and observe which instance handles each
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/shorten \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"https://example.com/page$i\"}" | jq -r '.instance'
done
```

**Expected:** You should see requests distributed across instances 1, 2, and 3.

Now check which instance is returned in the response header:

```bash
# Check the X-Upstream-Server header
curl -s -I -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/test"}' | grep -i x-upstream
```

**Questions:**
- What load balancing algorithm is Nginx using?
- Are the requests evenly distributed?

---

### Exercise 2: Understand Cache Sharding

The Redis cache is split across 3 nodes using consistent hashing. Let's explore how this works.

```bash
# Create some URLs
for i in {1..20}; do
  curl -s -X POST http://localhost:8000/shorten \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"https://shard-test.com/page$i\"}" > /dev/null
done

# Check cache distribution
curl -s http://localhost:8000/admin/cache-stats | jq
```

The response shows how many keys are stored in each shard:

```json
{
  "shards": {
    "shard-1": 7,
    "shard-2": 6,
    "shard-3": 7
  }
}
```

**Question:** Why don't all URLs go to the same Redis node?

**In Jaeger:**
1. Find a redirect trace
2. Look at the `cache.get` span
3. Notice the `cache.shard` attribute showing which Redis node was used

---

### Exercise 3: Test Distributed Rate Limiting

Rate limiting is enforced per client IP across all instances using Redis.

```bash
# Check current rate limit config
curl -s http://localhost:8000/admin/rate-limit | jq

# Make rapid requests until rate limited
for i in {1..150}; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/example1)
  if [ "$STATUS" == "429" ]; then
    echo "Rate limited after $i requests"
    break
  fi
done
```

**Expected:** You should be rate limited after ~100 requests (default config).

Try reducing the rate limit:

```bash
# Set aggressive rate limit (10 requests per 60 seconds)
curl -s -X POST http://localhost:8000/admin/rate-limit \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "requests": 10, "window": 60}' | jq

# Try to exceed it
for i in {1..15}; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/example1)
  echo "Request $i: $STATUS"
done
```

**Key Insight:** Rate limiting uses Redis to track requests, so it works across all instances.

---

### Exercise 4: Simulate Instance Failure

What happens when an instance goes down?

```bash
# Create a URL for testing
RESULT=$(curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://failure-test.com"}')
CODE=$(echo $RESULT | jq -r '.short_code')
echo "Created: $CODE"

# Make several requests (all should succeed)
echo "Before failure:"
for i in {1..5}; do
  curl -s -o /dev/null -w "Request $i: %{http_code}\n" "http://localhost:8000/$CODE"
done

# Stop one instance
docker stop lab50-shortener-2

# Continue making requests (should still work)
echo "After stopping instance 2:"
for i in {1..5}; do
  curl -s -o /dev/null -w "Request $i: %{http_code}\n" "http://localhost:8000/$CODE"
done

# Restart the instance
docker start lab50-shortener-2
```

**Expected:** Requests continue to succeed even with one instance down.

**In Grafana:**
- Watch the "Request Rate per Instance" panel
- See instance 2's line drop to zero
- Observe traffic redistributed to instances 1 and 3

---

### Exercise 5: Analytics Aggregation

When data is spread across instances, you need to aggregate analytics.

```bash
# Generate traffic from different instances
for i in {1..50}; do
  # Some shortens
  curl -s -X POST http://localhost:8000/shorten \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"https://analytics-test.com/page$i\"}" > /dev/null

  # Many redirects
  for j in {1..10}; do
    curl -s -o /dev/null "http://localhost:8000/example$((i % 3 + 1))"
  done
done

# Get cluster-wide analytics
curl -s http://localhost:8000/admin/cluster | jq
```

The response shows aggregated stats:

```json
{
  "analytics": {
    "total_urls": 150,
    "total_redirects": 1500,
    "urls_per_instance": {
      "1": 52,
      "2": 48,
      "3": 50
    },
    "cache_distribution": {
      "shard-1": 45,
      "shard-2": 55,
      "shard-3": 50
    }
  }
}
```

**Question:** Why is `urls_per_instance` tracking useful for debugging?

---

### Exercise 6: High Traffic Load Test

Run the k6 load test to see how the system behaves under heavy load.

```bash
# Run the scaling load test (5 minutes)
docker compose run --rm k6 run /scripts/scaling-test.js
```

While the test runs, watch:

1. **Grafana Dashboard** (http://localhost:3001)
   - Request rate per instance
   - Cache hit rate
   - Latency percentiles
   - Rate limit hits

2. **Jaeger** (http://localhost:16686)
   - Find slow traces
   - Compare cache hits vs misses
   - Track requests across instances

**Questions after the test:**
- What was the maximum RPS achieved?
- Were requests evenly distributed?
- Did rate limiting kick in?
- What was the cache hit rate at peak load?

---

### Exercise 7: Observe Consistent Hashing

Let's prove that consistent hashing keeps cache entries on the same shard.

```bash
# Create a specific URL
RESULT=$(curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://consistent-hash-demo.com"}')
CODE=$(echo $RESULT | jq -r '.short_code')
echo "Short code: $CODE"

# Access it multiple times through different instances
for i in {1..10}; do
  # Direct to different instances
  curl -s -o /dev/null "http://localhost:800$((i % 3 + 1))/$CODE"
done
```

**In Jaeger:**
1. Search for traces with the short_code you created
2. Look at the `cache.shard` attribute in each trace
3. Notice it's **always the same shard** regardless of which instance handled the request

This is the power of consistent hashing - the same key always maps to the same cache node.

---

## Key Takeaways

1. **Stateless Services Scale Horizontally**
   - Keep state in external stores (Redis, PostgreSQL)
   - Any instance can handle any request

2. **Consistent Hashing Prevents Hotspots**
   - Keys are distributed evenly across shards
   - Adding/removing nodes only affects ~1/N of keys

3. **Distributed Rate Limiting Requires Coordination**
   - Use Redis to share rate limit state
   - Sliding window algorithm prevents burst exploitation

4. **Load Balancers Handle Failures Gracefully**
   - Health checks detect failed instances
   - Traffic automatically routes to healthy instances

5. **Observability is Essential for Debugging**
   - Traces show which instance and shard handled each request
   - Metrics reveal load distribution and bottlenecks

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Rate limiting not working across instances
- Verify all instances can reach Redis: `docker compose exec shortener-1 ping redis-1`
- Check rate limit config: `curl localhost:8000/admin/rate-limit`

### Uneven cache distribution
- This is normal with small datasets
- Consistent hashing becomes more even with more keys

### Instance not receiving traffic
- Check Nginx config: `docker compose exec nginx cat /etc/nginx/nginx.conf`
- Check health: `curl localhost:8001/health`

### Traces not showing all instances
- Ensure OTEL collector is running: `docker compose logs otel-collector`
- Check service names in Jaeger dropdown

## Advanced Challenges

1. **Add a 4th instance** and observe how load redistributes
2. **Implement weighted load balancing** - make instance 1 handle 50% of traffic
3. **Add read replicas** for PostgreSQL
4. **Implement circuit breaker** pattern for database failures
5. **Add Redis Cluster** (actual Redis clustering, not just sharding)

## Related Labs

- [Lab 05: URL Shortener Part 1](../week-05-url-shortener/) - Single instance fundamentals
- [Lab 02: Cache Failures](../week-02-cache-dies/) - What happens when Redis dies
