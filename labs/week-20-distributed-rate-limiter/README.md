# Lab 20: Distributed Rate Limiter

Rate limiting is easy with a single instance. But what happens when you scale to multiple instances behind a load balancer? In this lab, you'll build a distributed rate limiter using Redis and learn about the challenges of shared state in distributed systems.

## What You'll Learn

- Why local rate limiting fails with multiple instances
- How to use Redis as shared state for rate limiting
- The sliding window algorithm for accurate rate limiting
- Race conditions in distributed systems
- Atomic operations with Redis Lua scripts
- Per-client vs global rate limiting

## Architecture

```
                           ┌─────────────┐
                           │   Client    │
                           └──────┬──────┘
                                  │
                           ┌──────▼──────┐
                           │    Nginx    │
                           │ (Load Bal.) │
                           └──────┬──────┘
                                  │
            ┌─────────────────────┼─────────────────────┐
            │                     │                     │
      ┌─────▼─────┐         ┌─────▼─────┐         ┌─────▼─────┐
      │   API-1   │         │   API-2   │         │   API-3   │
      │ (Replica) │         │ (Replica) │         │ (Replica) │
      └─────┬─────┘         └─────┬─────┘         └─────┬─────┘
            │                     │                     │
            └─────────────────────┼─────────────────────┘
                                  │
                           ┌──────▼──────┐
                           │    Redis    │
                           │(Shared State)│
                           └─────────────┘
```

Each API instance can operate in three modes:
1. **Local mode**: In-memory rate limiting (doesn't share state)
2. **Redis naive mode**: Shared Redis, but non-atomic operations (race conditions)
3. **Redis atomic mode**: Shared Redis with Lua scripts (correct behavior)

## Prerequisites

- Docker and Docker Compose
- curl (for API testing)
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
| Load Balancer | http://localhost:8080 | API entry point (routes to replicas) |
| API-1 Direct | http://localhost:8001 | Direct access to replica 1 |
| API-2 Direct | http://localhost:8002 | Direct access to replica 2 |
| API-3 Direct | http://localhost:8003 | Direct access to replica 3 |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Understand the Rate Limit Configuration

First, let's understand the current configuration.

```bash
# Check the configuration on any instance
curl -s http://localhost:8080/admin/config | jq
```

**Expected output:**
```json
{
  "instance": "api-1",
  "mode": "redis_atomic",
  "use_redis": true,
  "use_lua_script": true,
  "rate_limit": 100,
  "window_size_seconds": 60
}
```

This means: 100 requests allowed per 60-second sliding window, per client.

---

### Exercise 2: Test Single Instance Rate Limiting (Local Mode)

Let's start by seeing what happens with local rate limiting (no shared state).

```bash
# Switch all instances to local mode
for port in 8001 8002 8003; do
  curl -s -X POST "http://localhost:$port/admin/config" \
    -H "Content-Type: application/json" \
    -d '{"use_redis": false, "use_lua_script": false}'
done
```

Now send 50 requests through the load balancer:

```bash
# Send 50 requests (should all be allowed since they're spread across 3 instances)
for i in {1..50}; do
  response=$(curl -s -H "X-API-Key: test-client" http://localhost:8080/api/request)
  status=$(echo $response | jq -r '.status')
  instance=$(echo $response | jq -r '.instance')
  echo "Request $i: $status (served by $instance)"
done
```

**Observation:** Notice how requests are distributed across all three instances. Each instance maintains its own counter, so the client effectively gets 3x the rate limit!

Check the stats on each instance:

```bash
for port in 8001 8002 8003; do
  echo "=== Instance on port $port ==="
  curl -s "http://localhost:$port/admin/stats" | jq '.local_clients'
done
```

**Question:** Why is this a problem in production?

---

### Exercise 3: Enable Shared State with Redis

Now let's enable Redis-based rate limiting to share state across instances.

```bash
# Switch to Redis atomic mode (with Lua scripts)
for port in 8001 8002 8003; do
  curl -s -X POST "http://localhost:$port/admin/config" \
    -H "Content-Type: application/json" \
    -d '{"use_redis": true, "use_lua_script": true}'
done

# Reset rate limits
curl -s -X POST http://localhost:8080/admin/reset
```

Send 150 requests (rate limit is 100):

```bash
allowed=0
rejected=0
for i in {1..150}; do
  response=$(curl -s -H "X-API-Key: test-client" http://localhost:8080/api/request)
  status=$(echo $response | jq -r '.status')
  if [ "$status" == "ok" ]; then
    allowed=$((allowed + 1))
  else
    rejected=$((rejected + 1))
  fi
done
echo "Allowed: $allowed, Rejected: $rejected"
```

**Expected:** Approximately 100 allowed, 50 rejected (depending on timing).

---

### Exercise 4: Demonstrate Race Conditions (Naive Mode)

The "naive" Redis mode uses separate read-check-write operations instead of atomic Lua scripts. This creates a window for race conditions.

```bash
# Switch to Redis naive mode (non-atomic)
for port in 8001 8002 8003; do
  curl -s -X POST "http://localhost:$port/admin/config" \
    -H "Content-Type: application/json" \
    -d '{"use_redis": true, "use_lua_script": false}'
done

# Reset rate limits
curl -s -X POST http://localhost:8080/admin/reset
```

Run a high-concurrency test:

```bash
docker compose run --rm k6 run /scripts/high-concurrency.js
```

**Observation:** With 200 req/s and a limit of 100/minute, you might see MORE than 100 requests allowed in the first minute. This is a race condition!

Check the race conditions counter:

```bash
curl -s http://localhost:9090/api/v1/query?query=race_conditions_detected_total | jq
```

---

### Exercise 5: Fix Race Conditions with Lua Scripts

Switch back to atomic mode and see the difference:

```bash
# Switch to Redis atomic mode
for port in 8001 8002 8003; do
  curl -s -X POST "http://localhost:$port/admin/config" \
    -H "Content-Type: application/json" \
    -d '{"use_redis": true, "use_lua_script": true}'
done

# Reset rate limits
curl -s -X POST http://localhost:8080/admin/reset
```

Run the same high-concurrency test:

```bash
docker compose run --rm k6 run /scripts/high-concurrency.js
```

**Expected:** Exactly 100 (or very close) requests allowed per minute, with no race condition warnings.

**Why does this work?** The Lua script executes atomically on Redis:
1. Remove expired entries
2. Count current entries
3. Add new entry IF under limit
4. Return result

All these steps happen as one atomic operation - no other request can interfere.

---

### Exercise 6: Per-Client vs Shared Client Rate Limiting

Test that different clients get separate rate limits:

```bash
# Reset rate limits
curl -s -X POST http://localhost:8080/admin/reset

# Send requests from multiple clients
for client in client-A client-B client-C; do
  echo "=== Testing $client ==="
  allowed=0
  for i in {1..120}; do
    response=$(curl -s -H "X-API-Key: $client" http://localhost:8080/api/request)
    status=$(echo $response | jq -r '.status')
    if [ "$status" == "ok" ]; then
      allowed=$((allowed + 1))
    fi
  done
  echo "$client: $allowed requests allowed"
done
```

**Expected:** Each client should get approximately 100 requests allowed.

---

### Exercise 7: Observe Load Balancing

Watch which instance handles each request:

```bash
for i in {1..20}; do
  response=$(curl -s http://localhost:8080/api/request)
  instance=$(echo $response | jq -r '.instance')
  echo "Request $i served by: $instance"
done
```

**Observation:** Requests are distributed across all three instances via nginx round-robin.

---

### Exercise 8: Monitor with Grafana

1. Open Grafana: http://localhost:3001 (admin/admin)
2. Navigate to the "Distributed Rate Limiter" dashboard
3. While the dashboard is open, run load tests:

```bash
docker compose run --rm k6 run /scripts/basic.js
```

**Things to observe:**
- Rate limit decisions by instance (should be similar across instances)
- Race conditions detected (should be 0 in atomic mode)
- Rate limit check latency (atomic vs naive)
- Total allowed vs rejected requests

---

## Key Concepts

### Sliding Window Algorithm

Unlike a fixed window that resets at intervals (allowing bursts at boundaries), the sliding window tracks individual request timestamps:

```
Time:    |----60s window----|
         [req1][req2]...[req100]
                              ^
                              Window slides with time
```

Each new request:
1. Removes entries older than `now - window_size`
2. Counts remaining entries
3. Allows if count < limit

### Redis Lua Scripts

Lua scripts in Redis are atomic - they execute without interruption:

```lua
-- This all happens atomically
local count = redis.call('ZCARD', key)
if count < limit then
    redis.call('ZADD', key, timestamp, request_id)
    return 1  -- allowed
else
    return 0  -- rejected
end
```

### Race Condition in Naive Approach

Without atomic operations:
```
Instance 1: READ count = 99
Instance 2: READ count = 99   <- Both read same value!
Instance 1: count < 100? Yes, WRITE count = 100
Instance 2: count < 100? Yes, WRITE count = 101  <- Over limit!
```

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Redis connection errors
```bash
docker compose logs redis
docker compose exec redis redis-cli ping
```

### Rate limits not working
```bash
# Check current mode
curl -s http://localhost:8080/admin/config | jq

# Check Redis keys
docker compose exec redis redis-cli keys "ratelimit:*"
```

### Instances not healthy
```bash
docker compose logs api-1 api-2 api-3
```

## Challenge Exercises

1. **Implement token bucket**: Modify the service to support token bucket algorithm alongside sliding window
2. **Add rate limit tiers**: Different clients get different rate limits based on their API key
3. **Implement rate limit bypass**: Add a special header that bypasses rate limiting for admin users
4. **Test Redis failure**: Kill Redis and verify the service fails open (allows requests)

## Next Lab

Continue exploring distributed systems concepts with more advanced patterns!
