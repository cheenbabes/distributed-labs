# Lab 31: Rate Limiter Service

Build and operate a production-grade rate limiting microservice that protects your APIs from abuse while ensuring fair resource distribution across clients.

## What You'll Learn

- How to build a standalone rate limiter service with REST and gRPC APIs
- Token bucket vs sliding window algorithms and their trade-offs
- Dynamic rate limit configuration per client and API endpoint
- Redis as a distributed state backend for multi-instance rate limiting
- Proper rate limit headers (X-RateLimit-*) and client retry handling
- Monitoring rate limit decisions and per-client usage patterns
- Graceful degradation when the rate limiter service fails

## Architecture

```
                                    ┌─────────────────────────┐
                                    │   Rate Limiter Service  │
                                    │   (REST + gRPC APIs)    │
                                    │  ┌─────────────────┐    │
                                    │  │  Token Bucket   │    │
            ┌───────────┐           │  │  Sliding Window │    │
            │  Client   │           │  └────────┬────────┘    │
            └─────┬─────┘           └───────────┼─────────────┘
                  │                             │
                  ▼                             ▼
            ┌───────────┐           ┌───────────────────────┐
            │   nginx   │           │        Redis          │
            │ (LB)      │           │  (Distributed State)  │
            └─────┬─────┘           └───────────────────────┘
                  │
         ┌───────┴────────┐
         ▼                ▼
  ┌─────────────┐  ┌─────────────┐
  │ Sample API  │  │ Sample API  │
  │  Instance 1 │  │  Instance 2 │
  └──────┬──────┘  └──────┬──────┘
         │                │
         └───────┬────────┘
                 │ Check rate limit
                 ▼
         ┌─────────────────────────┐
         │   Rate Limiter Service  │
         └─────────────────────────┘
```

### Components

- **Rate Limiter Service**: Standalone microservice providing rate limit checks via REST and gRPC
- **Sample API**: Example API service that integrates with the rate limiter
- **Redis**: Distributed state store for rate limit counters
- **nginx**: Load balancer for the sample API instances
- **Observability Stack**: Jaeger, Prometheus, Grafana for monitoring

## Prerequisites

- Docker and Docker Compose
- curl (for API testing)
- grpcurl (optional, for gRPC testing)
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
| Rate Limiter API | http://localhost:8000 | Rate limiter REST API |
| Sample API (via nginx) | http://localhost:8080 | Rate-limited sample API |
| Sample API 1 | http://localhost:8001 | Direct access to instance 1 |
| Sample API 2 | http://localhost:8002 | Direct access to instance 2 |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Rate Limiter API Reference

### Check Rate Limit

```bash
# Check if a request should be allowed
curl -X POST http://localhost:8000/v1/check \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "my-client",
    "resource": "/api/data",
    "cost": 1
  }'
```

Response:
```json
{
  "allowed": true,
  "limit": 100,
  "remaining": 99,
  "reset_at": 1705680000,
  "algorithm": "token_bucket",
  "retry_after": 0
}
```

### Get Configuration

```bash
curl "http://localhost:8000/v1/config/my-client?resource=/api/data"
```

### Update Configuration

```bash
curl -X PUT http://localhost:8000/v1/config \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "premium-client",
    "resource": "*",
    "algorithm": "token_bucket",
    "limit": 1000,
    "refill_rate": 50.0,
    "enabled": true
  }'
```

### Get Usage Statistics

```bash
curl "http://localhost:8000/v1/usage/my-client?resource=/api/data"
```

---

## Lab Exercises

### Exercise 1: Configure Rate Limits for Different API Endpoints

Set up different rate limits for different types of operations.

**Step 1**: Create a strict limit for the expensive operation endpoint:

```bash
curl -X PUT http://localhost:8000/v1/config \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "api-service",
    "resource": "expensive",
    "algorithm": "token_bucket",
    "limit": 10,
    "refill_rate": 0.5,
    "enabled": true
  }'
```

**Step 2**: Test the expensive endpoint:

```bash
for i in {1..15}; do
  echo "Request $i:"
  curl -s http://localhost:8080/api/expensive | jq -c '{status: .result.computed_value // "rate limited"}'
  sleep 0.1
done
```

**Expected**: After ~10 requests, you should start seeing 429 responses.

**Step 3**: Check the rate limit headers in the response:

```bash
curl -v http://localhost:8080/api/expensive 2>&1 | grep -i "x-ratelimit"
```

**Questions**:
- How many requests were allowed before rate limiting?
- What does the `refill_rate` of 0.5 mean for recovery?

---

### Exercise 2: Test Rate Limiting via REST and gRPC

Compare the REST and gRPC interfaces for rate limit checks.

**Step 1**: Test via REST API:

```bash
time for i in {1..100}; do
  curl -s -X POST http://localhost:8000/v1/check \
    -H "Content-Type: application/json" \
    -d '{"client_id": "test-client", "resource": "/api/test", "cost": 1}' > /dev/null
done
```

**Step 2**: Install grpcurl and test via gRPC:

```bash
# If you have grpcurl installed:
time for i in {1..100}; do
  grpcurl -plaintext -d '{"client_id": "test-client", "resource": "/api/test", "cost": 1}' \
    localhost:50051 ratelimiter.RateLimiterService/CheckRateLimit > /dev/null
done
```

**Note**: If grpcurl is not available, you can compare by looking at the latency metrics in Grafana.

**Questions**:
- Which interface is faster for rate limit checks?
- When would you choose gRPC over REST?

---

### Exercise 3: Update Rate Limits Dynamically Without Restart

Demonstrate dynamic configuration updates taking effect immediately.

**Step 1**: Check current configuration:

```bash
curl -s http://localhost:8000/v1/config/api-service | jq
```

**Step 2**: Run a continuous load in the background:

```bash
(for i in {1..200}; do
  curl -s http://localhost:8080/api/data > /dev/null
  sleep 0.2
done) &
LOAD_PID=$!
```

**Step 3**: While load is running, update the rate limit:

```bash
# First, set a low limit
curl -X PUT http://localhost:8000/v1/config \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "api-service",
    "resource": "*",
    "algorithm": "token_bucket",
    "limit": 10,
    "refill_rate": 2.0,
    "enabled": true
  }'

sleep 5

# Now increase it
curl -X PUT http://localhost:8000/v1/config \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "api-service",
    "resource": "*",
    "algorithm": "token_bucket",
    "limit": 100,
    "refill_rate": 20.0,
    "enabled": true
  }'
```

**Step 4**: Watch Grafana dashboard to see the rate limit changes take effect.

**Step 5**: Stop the background load:

```bash
kill $LOAD_PID 2>/dev/null
```

**Questions**:
- How quickly did the new limits take effect?
- What happened to in-flight requests during the configuration change?

---

### Exercise 4: Monitor Per-Client Usage Patterns

Set up different rate limits for different clients and observe their patterns.

**Step 1**: Configure different client tiers:

```bash
# Premium client - high limits
curl -X PUT http://localhost:8000/v1/config \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "premium-client",
    "resource": "*",
    "algorithm": "token_bucket",
    "limit": 500,
    "refill_rate": 50.0,
    "enabled": true
  }'

# Standard client - medium limits
curl -X PUT http://localhost:8000/v1/config \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "standard-client",
    "resource": "*",
    "algorithm": "token_bucket",
    "limit": 100,
    "refill_rate": 10.0,
    "enabled": true
  }'

# Free tier - strict limits
curl -X PUT http://localhost:8000/v1/config \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "free-client",
    "resource": "*",
    "algorithm": "token_bucket",
    "limit": 20,
    "refill_rate": 1.0,
    "enabled": true
  }'
```

**Step 2**: Simulate different clients making requests:

```bash
# Premium client
for i in {1..50}; do
  curl -s http://localhost:8080/api/data -H "X-Client-ID: premium-client" > /dev/null &
done

# Standard client
for i in {1..50}; do
  curl -s http://localhost:8080/api/data -H "X-Client-ID: standard-client" > /dev/null &
done

# Free client
for i in {1..50}; do
  curl -s http://localhost:8080/api/data -H "X-Client-ID: free-client" > /dev/null &
done

wait
```

**Step 3**: Open Grafana (http://localhost:3001) and observe:
- Rate Limit Decisions per Second (should show different patterns per client)
- Rate Limit Usage (%) for each client
- Remaining Quota over time

**Step 4**: Get usage statistics via API:

```bash
echo "Premium client usage:"
curl -s http://localhost:8000/v1/usage/premium-client | jq

echo "Standard client usage:"
curl -s http://localhost:8000/v1/usage/standard-client | jq

echo "Free client usage:"
curl -s http://localhost:8000/v1/usage/free-client | jq
```

**Questions**:
- Which client hit their rate limit first?
- How does the remaining quota differ between tiers?

---

### Exercise 5: Handle Rate Limiter Service Failure Gracefully

Test what happens when the rate limiter service becomes unavailable.

**Step 1**: Verify normal operation:

```bash
curl -s http://localhost:8080/api/data | jq
```

**Step 2**: Stop the rate limiter service:

```bash
docker compose stop rate-limiter
```

**Step 3**: Try making requests (should still work due to fail-open):

```bash
for i in {1..5}; do
  echo "Request $i:"
  curl -s http://localhost:8080/api/data | jq -c '{data: .data.id, instance: .instance}'
done
```

**Step 4**: Check the sample API logs to see fail-open behavior:

```bash
docker compose logs sample-api-1 --tail=20 | grep -i "rate limiter"
```

**Step 5**: Restart the rate limiter:

```bash
docker compose start rate-limiter
sleep 5

# Verify rate limiting is working again
for i in {1..5}; do
  curl -s http://localhost:8080/api/data | jq -c '{data: .data.id}'
done
```

**Step 6**: Test fail-closed mode (optional):

Modify the sample API configuration to use `FAILOPEN=false` and repeat the test.

**Questions**:
- What is the difference between fail-open and fail-closed?
- When would you choose fail-closed over fail-open?
- What metrics would alert you to rate limiter failures?

---

## Algorithm Comparison

### Token Bucket

- Allows burst traffic up to bucket capacity
- Smooth refill at constant rate
- Good for APIs that can handle occasional bursts
- Lower memory usage

### Sliding Window Log

- Precise request counting within the window
- No burst allowance
- Higher memory for high-volume clients
- Better for strict rate enforcement

### Sliding Window Counter

- Hybrid approach (approximation)
- Lower memory than log-based
- Small inaccuracy at window boundaries
- Good balance for most use cases

Run the algorithm comparison load test:

```bash
docker compose run --rm k6 run /scripts/algorithms.js
```

---

## Key Metrics to Monitor

| Metric | Description | Alert Threshold |
|--------|-------------|-----------------|
| `rate_limit_decisions_total{decision="denied"}` | Rate limited requests | Sudden spike |
| `rate_limit_check_duration_seconds` | Check latency | p99 > 10ms |
| `redis_connection_healthy` | Redis connectivity | 0 |
| `rate_limit_remaining` | Remaining quota | Near 0 for extended periods |

---

## Key Takeaways

1. **Centralized rate limiting** enables consistent enforcement across distributed services
2. **Dynamic configuration** allows adjusting limits without service restarts
3. **Algorithm choice matters** - token bucket for burst tolerance, sliding window for strict limits
4. **Fail-open vs fail-closed** is a critical decision based on your availability requirements
5. **Observability is essential** - monitor decisions, latency, and per-client usage patterns
6. **Rate limit headers** help clients implement proper retry logic

---

## Cleanup

```bash
docker compose down -v
```

---

## Troubleshooting

### Rate limiter not responding

```bash
docker compose logs rate-limiter --tail=50
```

### Redis connection issues

```bash
docker compose exec redis redis-cli ping
docker compose logs redis
```

### High check latency

- Check Redis memory usage: `docker compose exec redis redis-cli info memory`
- Verify network connectivity between services

### Traces not appearing in Jaeger

- Wait 10-15 seconds after making requests
- Check otel-collector: `docker compose logs otel-collector`

---

## Next Steps

- Integrate with your actual API gateway
- Add rate limit bypass for internal services
- Implement rate limit quotas that reset monthly
- Add support for distributed rate limiting across regions
