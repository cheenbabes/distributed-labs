# Lab 11: Rate Limiting Fundamentals

Rate limiting is a critical technique for protecting services from overload, ensuring fair resource usage, and maintaining system stability. In this lab, you'll implement and compare four fundamental rate limiting algorithms, observing their different characteristics under load.

## What You'll Learn

- How different rate limiting algorithms work internally
- Trade-offs between accuracy, memory usage, and burst handling
- How to implement rate limiting from scratch (no libraries)
- How to observe rate limiting behavior through metrics and traces
- When to use each algorithm in production

## Rate Limiting Algorithms

### 1. Fixed Window
Divides time into fixed intervals and counts requests per interval.

```
Window 1 (0-1s)     Window 2 (1-2s)
[|||||||   ]        [|||||     ]
 7 requests          5 requests

Limit: 10 per window
```

**Pros:** Simple, O(1) memory
**Cons:** Boundary burst problem - can allow 2x limit briefly at window edges

### 2. Sliding Window Log
Tracks timestamps of all requests within the window period.

```
Time: ----|----|----|----|----|--->
Requests: *  *  *    *  *     *

Window slides with each request, counting only recent ones.
```

**Pros:** Most accurate, no boundary issues
**Cons:** O(n) memory where n = requests in window

### 3. Token Bucket
Tokens replenish at a fixed rate. Each request consumes one token.

```
Bucket Capacity: 10 tokens
Refill Rate: 10 tokens/second

[**********] Full bucket - can handle burst of 10
[***       ] After burst - 3 tokens remaining
[*******   ] After refill - 7 tokens
```

**Pros:** Allows controlled bursts, smooth average rate
**Cons:** Slightly more complex state management

### 4. Leaky Bucket
Requests enter a queue and are processed at a constant rate.

```
     Incoming
        |
        v
    [Request Queue]
        |
        v (constant leak rate)
    Processing
```

**Pros:** Very smooth output rate, predictable
**Cons:** Adds latency (queue wait time), less burst-friendly

## Architecture

```
                                  ┌─────────────────┐
                                  │  Rate Limiter   │
┌──────────┐     GET /api/request │  ┌───────────┐  │
│  Client  │ ────────────────────▶│  │Fixed Wind.│  │
└──────────┘                      │  │Sliding Win│  │
     │                            │  │Token Buck.│  │
     │       POST /admin/algorithm│  │Leaky Buck.│  │
     └───────────────────────────▶│  └───────────┘  │
                                  └─────────────────┘
                                         │
                            ┌────────────┴────────────┐
                            ▼                         ▼
                      ┌──────────┐              ┌──────────┐
                      │Prometheus│              │  Jaeger  │
                      │ Metrics  │              │  Traces  │
                      └──────────┘              └──────────┘
                            │
                            ▼
                      ┌──────────┐
                      │ Grafana  │
                      └──────────┘
```

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

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Rate Limiter | http://localhost:8000 | API endpoint |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## API Reference

### Rate-Limited Endpoint

```bash
GET /api/request
```

Returns 200 if allowed, 429 if rate limited.

Response headers:
- `X-RateLimit-Limit`: Maximum requests per window
- `X-RateLimit-Remaining`: Remaining requests in current window
- `X-RateLimit-Reset`: Unix timestamp when limit resets
- `X-RateLimit-Algorithm`: Current algorithm name

### Admin Endpoints

```bash
# Get current algorithm
GET /admin/algorithm

# Switch algorithm
POST /admin/algorithm
Content-Type: application/json
{"algorithm": "fixed_window|sliding_window|token_bucket|leaky_bucket"}

# Get rate limiter stats
GET /admin/stats

# Reset all rate limiters
POST /admin/reset

# Reset specific algorithm
POST /admin/reset/{algorithm}
```

## Lab Exercises

### Exercise 1: Baseline - Test Fixed Window

First, let's understand how Fixed Window rate limiting works.

```bash
# Check current algorithm
curl -s http://localhost:8000/admin/algorithm | jq

# Set to fixed window
curl -s -X POST http://localhost:8000/admin/algorithm \
  -H "Content-Type: application/json" \
  -d '{"algorithm": "fixed_window"}' | jq

# Reset the limiter
curl -s -X POST http://localhost:8000/admin/reset | jq
```

Now send 15 rapid requests (exceeding the 10/s limit):

```bash
for i in {1..15}; do
  response=$(curl -s -w "\n%{http_code}" http://localhost:8000/api/request)
  code=$(echo "$response" | tail -1)
  body=$(echo "$response" | head -1)
  remaining=$(echo "$body" | jq -r '.remaining // "N/A"')
  echo "Request $i: HTTP $code, remaining: $remaining"
done
```

**Expected:** First 10 requests return 200, remaining return 429.

Check the stats:

```bash
curl -s http://localhost:8000/admin/stats | jq '.stats'
```

---

### Exercise 2: Observe the Boundary Burst Problem

Fixed Window has a known issue: you can exceed the rate limit at window boundaries.

```bash
# Reset the limiter
curl -s -X POST http://localhost:8000/admin/reset

# Send 10 requests (uses full quota)
echo "=== Sending 10 requests (full quota) ==="
for i in {1..10}; do
  curl -s http://localhost:8000/api/request > /dev/null
done
echo "Done. Waiting 0.9 seconds..."

# Wait until just before window resets
sleep 0.9

# Now quickly send 10 more - some will be in old window, some in new
echo "=== Sending 10 more requests at boundary ==="
for i in {1..10}; do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/request)
  echo "Request $i: HTTP $code"
done
```

**Question:** How many of the second batch succeeded? With perfect timing, you could get nearly 20 requests through in ~1.1 seconds!

---

### Exercise 3: Test Sliding Window Log

Now let's see how Sliding Window prevents the boundary problem.

```bash
# Switch to sliding window
curl -s -X POST http://localhost:8000/admin/algorithm \
  -H "Content-Type: application/json" \
  -d '{"algorithm": "sliding_window"}' | jq

# Reset
curl -s -X POST http://localhost:8000/admin/reset

# Run the same boundary test
echo "=== Sliding Window Boundary Test ==="
for i in {1..10}; do
  curl -s http://localhost:8000/api/request > /dev/null
done
echo "Sent 10, waiting 0.9s..."
sleep 0.9

for i in {1..10}; do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/request)
  echo "Request $i: HTTP $code"
done
```

**Expected:** The second batch should have more rejections because Sliding Window accurately tracks request times.

Check the stats to see the request log:

```bash
curl -s http://localhost:8000/admin/stats | jq '.stats'
```

---

### Exercise 4: Test Token Bucket - Observe Burst Allowance

Token Bucket allows bursts up to the bucket capacity.

```bash
# Switch to token bucket
curl -s -X POST http://localhost:8000/admin/algorithm \
  -H "Content-Type: application/json" \
  -d '{"algorithm": "token_bucket"}' | jq

# Reset (refills bucket to 10 tokens)
curl -s -X POST http://localhost:8000/admin/reset

# Check initial tokens
curl -s http://localhost:8000/admin/stats | jq '.stats.current_tokens'

# Send rapid burst of 10
echo "=== Sending burst of 10 ==="
for i in {1..10}; do
  response=$(curl -s http://localhost:8000/api/request)
  tokens=$(echo "$response" | jq -r '.remaining')
  echo "Request $i: tokens remaining = $tokens"
done

# Check tokens depleted
curl -s http://localhost:8000/admin/stats | jq '.stats.current_tokens'

# Wait 0.5 seconds for partial refill
echo "Waiting 0.5 seconds..."
sleep 0.5

# Check tokens after refill
echo "Tokens after 0.5s:"
curl -s http://localhost:8000/admin/stats | jq '.stats.current_tokens'
```

**Expected:** Bucket starts full (10), depletes to 0 after burst, then slowly refills at 10 tokens/second.

---

### Exercise 5: Test Leaky Bucket - See Smooth Rate

Leaky Bucket provides the smoothest output rate.

```bash
# Switch to leaky bucket
curl -s -X POST http://localhost:8000/admin/algorithm \
  -H "Content-Type: application/json" \
  -d '{"algorithm": "leaky_bucket"}' | jq

# Reset
curl -s -X POST http://localhost:8000/admin/reset

# Check queue size
curl -s http://localhost:8000/admin/stats | jq '.stats.current_queue_size'

# Send 15 requests and observe queue behavior
echo "=== Sending 15 requests ==="
for i in {1..15}; do
  response=$(curl -s http://localhost:8000/api/request)
  status=$(echo "$response" | jq -r '.status')
  remaining=$(echo "$response" | jq -r '.remaining')
  echo "Request $i: $status, remaining capacity: $remaining"
done

# Check final queue size
curl -s http://localhost:8000/admin/stats | jq '.stats'
```

**Observation:** Leaky Bucket maintains a "queue" that leaks at a constant rate. When the queue is full, requests are rejected.

---

### Exercise 6: Compare All Four Under Load

Run the k6 comparison test to see all algorithms under identical load:

```bash
docker compose run --rm k6 run /scripts/compare-algorithms.js
```

Watch the Grafana dashboard (http://localhost:3001) while the test runs to see:
- Allowed vs rejected rates for each algorithm
- Token bucket tokens depleting and refilling
- Leaky bucket queue size changes

---

### Exercise 7: Observe the Boundary Burst in k6

Run the boundary test:

```bash
docker compose run --rm k6 run /scripts/boundary-test.js
```

This test specifically targets the Fixed Window boundary problem and compares it to Sliding Window behavior.

---

### Exercise 8: Sustained Load Test

Generate sustained load exceeding the rate limit:

```bash
docker compose run --rm k6 run /scripts/basic.js
```

While running, observe:
1. Grafana: Rate of allowed vs rejected requests
2. The rejection rate should stabilize around 50% (20 req/s attempted, 10 allowed)

---

## Algorithm Comparison Summary

| Algorithm | Memory | Burst Handling | Accuracy | Use Case |
|-----------|--------|----------------|----------|----------|
| Fixed Window | O(1) | Poor (boundary burst) | Low | Simple, non-critical |
| Sliding Window | O(n) | None | High | API rate limits |
| Token Bucket | O(1) | Controlled bursts | Good | CDNs, APIs allowing bursts |
| Leaky Bucket | O(1) | Queue-based | Good | Traffic shaping, smooth output |

## Key Takeaways

1. **Fixed Window is simple but flawed** - Good for basic use cases but has the boundary burst problem

2. **Sliding Window is most accurate** - Use when you need precise rate limiting and can afford memory

3. **Token Bucket allows controlled bursts** - Great for APIs where occasional bursts are acceptable

4. **Leaky Bucket provides smoothest output** - Ideal for traffic shaping and preventing downstream overload

5. **Headers are essential** - Always return X-RateLimit-* headers so clients can adapt

6. **429 is the standard** - Use HTTP 429 Too Many Requests, include Retry-After header

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Service not starting
```bash
docker compose logs rate-limiter
```

### Metrics not showing in Grafana
- Wait 30 seconds for scrape interval
- Check Prometheus targets: http://localhost:9090/targets

### Rate limit seems off
- Reset the limiter: `curl -X POST http://localhost:8000/admin/reset`
- Check current algorithm: `curl http://localhost:8000/admin/algorithm`

## Next Lab

[Lab 12: Distributed Rate Limiting](../week-12-distributed-rate-limiting/) - We'll extend rate limiting to work across multiple service instances using Redis.
