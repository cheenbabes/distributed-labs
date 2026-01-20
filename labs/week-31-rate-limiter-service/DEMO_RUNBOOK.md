# Demo Runbook: Rate Limiter Service

This runbook contains all commands for the video demo. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

---

## Pre-Demo Setup

### Check Docker is running

```bash {"name": "check-docker"}
docker info > /dev/null 2>&1 && echo "Docker is running" || echo "Docker is not running"
```

### Clean any previous lab state

```bash {"name": "clean-previous"}
docker compose down -v 2>/dev/null || true
docker system prune -f
```

---

## Part 1: Start the Lab

### Build and start all services

```bash {"name": "start-lab"}
docker compose up --build -d
```

### Wait for services to be healthy

```bash {"name": "wait-healthy"}
echo "Waiting for services to be healthy..."
sleep 15
docker compose ps
```

### Verify all services are responding

```bash {"name": "verify-services"}
echo "Rate Limiter:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Sample API 1:"
curl -s http://localhost:8001/health | jq
echo ""
echo "Sample API 2:"
curl -s http://localhost:8002/health | jq
echo ""
echo "Sample API via nginx:"
curl -s http://localhost:8080/health
```

---

## Part 2: Explore the Rate Limiter API

### Show available endpoints

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Rate Limiter API:     http://localhost:8000"
echo "  Rate Limiter Docs:    http://localhost:8000/docs"
echo "  Sample API (nginx):   http://localhost:8080"
echo "  Jaeger:               http://localhost:16686"
echo "  Grafana:              http://localhost:3001 (admin/admin)"
echo "  Prometheus:           http://localhost:9090"
```

### Check default configuration

```bash {"name": "check-default-config"}
echo "Default rate limit configuration:"
curl -s http://localhost:8000/v1/config/default | jq
```

### Make a rate limit check

```bash {"name": "check-rate-limit"}
echo "Checking rate limit for test-client:"
curl -s -X POST http://localhost:8000/v1/check \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "test-client",
    "resource": "/api/data",
    "cost": 1
  }' | jq
```

---

## Part 3: Test Rate Limiting

### Make requests until rate limited

```bash {"name": "exhaust-rate-limit"}
echo "Making rapid requests to trigger rate limiting..."
for i in {1..15}; do
  result=$(curl -s -X POST http://localhost:8000/v1/check \
    -H "Content-Type: application/json" \
    -d '{"client_id": "demo-client", "resource": "/api/test", "cost": 10}')
  allowed=$(echo $result | jq -r '.allowed')
  remaining=$(echo $result | jq -r '.remaining')
  echo "Request $i: allowed=$allowed, remaining=$remaining"
done
```

### Test the sample API with rate limiting

```bash {"name": "test-sample-api"}
echo "Testing sample API (should be rate limited):"
for i in {1..10}; do
  response=$(curl -s -w "\n%{http_code}" http://localhost:8080/api/data -H "X-Client-ID: demo-client")
  status=$(echo "$response" | tail -n1)
  body=$(echo "$response" | head -n-1)
  if [ "$status" = "429" ]; then
    echo "Request $i: RATE LIMITED (429)"
  else
    echo "Request $i: OK (200)"
  fi
done
```

### Show rate limit headers

```bash {"name": "show-headers"}
echo "Rate limit headers in response:"
curl -s -v http://localhost:8080/api/data -H "X-Client-ID: new-client" 2>&1 | grep -i "x-ratelimit"
```

---

## Part 4: Configure Different Rate Limits

### Create premium client configuration

```bash {"name": "config-premium"}
curl -s -X PUT http://localhost:8000/v1/config \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "premium-client",
    "resource": "*",
    "algorithm": "token_bucket",
    "limit": 500,
    "refill_rate": 50.0,
    "enabled": true
  }' | jq
```

### Create standard client configuration

```bash {"name": "config-standard"}
curl -s -X PUT http://localhost:8000/v1/config \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "standard-client",
    "resource": "*",
    "algorithm": "token_bucket",
    "limit": 50,
    "refill_rate": 5.0,
    "enabled": true
  }' | jq
```

### Create free tier configuration

```bash {"name": "config-free"}
curl -s -X PUT http://localhost:8000/v1/config \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "free-client",
    "resource": "*",
    "algorithm": "token_bucket",
    "limit": 10,
    "refill_rate": 0.5,
    "enabled": true
  }' | jq
```

### List all configurations

```bash {"name": "list-configs"}
curl -s http://localhost:8000/v1/configs | jq
```

---

## Part 5: Compare Client Tiers

### Test premium client

```bash {"name": "test-premium"}
echo "Testing premium client (limit: 500):"
allowed=0
denied=0
for i in {1..20}; do
  result=$(curl -s http://localhost:8080/api/data -H "X-Client-ID: premium-client" -w "%{http_code}" -o /dev/null)
  if [ "$result" = "200" ]; then
    ((allowed++))
  else
    ((denied++))
  fi
done
echo "Allowed: $allowed, Denied: $denied"
```

### Test free client

```bash {"name": "test-free"}
echo "Testing free client (limit: 10):"
allowed=0
denied=0
for i in {1..20}; do
  result=$(curl -s http://localhost:8080/api/data -H "X-Client-ID: free-client" -w "%{http_code}" -o /dev/null)
  if [ "$result" = "200" ]; then
    ((allowed++))
  else
    ((denied++))
  fi
done
echo "Allowed: $allowed, Denied: $denied"
```

### Compare usage statistics

```bash {"name": "compare-usage"}
echo "Premium client usage:"
curl -s http://localhost:8000/v1/usage/premium-client | jq
echo ""
echo "Free client usage:"
curl -s http://localhost:8000/v1/usage/free-client | jq
```

---

## Part 6: Dynamic Configuration Updates

### Start background load

```bash {"name": "start-load", "background": true}
echo "Starting background load..."
for i in {1..300}; do
  curl -s http://localhost:8080/api/data -H "X-Client-ID: dynamic-client" > /dev/null
  sleep 0.2
done
echo "Load complete"
```

### Apply strict limit (while load is running)

```bash {"name": "apply-strict-limit"}
echo "Applying strict rate limit..."
curl -s -X PUT http://localhost:8000/v1/config \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "dynamic-client",
    "resource": "*",
    "algorithm": "token_bucket",
    "limit": 10,
    "refill_rate": 1.0,
    "enabled": true
  }' | jq
echo ""
echo "Watch Grafana to see the rate limiting take effect"
```

### Apply relaxed limit

```bash {"name": "apply-relaxed-limit"}
echo "Applying relaxed rate limit..."
curl -s -X PUT http://localhost:8000/v1/config \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "dynamic-client",
    "resource": "*",
    "algorithm": "token_bucket",
    "limit": 200,
    "refill_rate": 20.0,
    "enabled": true
  }' | jq
echo ""
echo "Rate limiting should ease up now"
```

---

## Part 7: Algorithm Comparison

### Configure sliding window

```bash {"name": "config-sliding-window"}
curl -s -X PUT http://localhost:8000/v1/config \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "sliding-window-client",
    "resource": "*",
    "algorithm": "sliding_window",
    "limit": 30,
    "window_seconds": 60,
    "enabled": true
  }' | jq
```

### Test sliding window behavior

```bash {"name": "test-sliding-window"}
echo "Testing sliding window (limit: 30 per 60s):"
for i in {1..35}; do
  result=$(curl -s -X POST http://localhost:8000/v1/check \
    -H "Content-Type: application/json" \
    -d '{"client_id": "sliding-window-client", "resource": "/test", "cost": 1}')
  allowed=$(echo $result | jq -r '.allowed')
  remaining=$(echo $result | jq -r '.remaining')
  if [ "$allowed" = "false" ]; then
    echo "Request $i: DENIED (remaining: $remaining)"
    break
  else
    echo "Request $i: allowed, remaining: $remaining"
  fi
done
```

---

## Part 8: Failure Handling

### Stop rate limiter service

```bash {"name": "stop-rate-limiter"}
echo "Stopping rate limiter service..."
docker compose stop rate-limiter
echo "Rate limiter stopped"
```

### Test fail-open behavior

```bash {"name": "test-failopen"}
echo "Testing requests with rate limiter down (fail-open mode):"
for i in {1..5}; do
  result=$(curl -s http://localhost:8080/api/data -H "X-Client-ID: test-client" -w "%{http_code}" -o /dev/null)
  if [ "$result" = "200" ]; then
    echo "Request $i: OK (fail-open working)"
  else
    echo "Request $i: Failed with status $result"
  fi
done
```

### Check sample API logs

```bash {"name": "check-failopen-logs"}
docker compose logs sample-api-1 --tail=10 | grep -i "rate limiter"
```

### Restart rate limiter

```bash {"name": "restart-rate-limiter"}
docker compose start rate-limiter
echo "Waiting for rate limiter to be healthy..."
sleep 5
curl -s http://localhost:8000/health | jq
```

---

## Part 9: Load Testing

### Run basic load test

```bash {"name": "load-test-basic", "background": true}
docker compose run --rm k6 run /scripts/basic.js
```

### Run multi-client load test

```bash {"name": "load-test-multi-client", "background": true}
docker compose run --rm k6 run /scripts/multi-client.js
```

### Run stress test

```bash {"name": "load-test-stress", "background": true}
docker compose run --rm k6 run /scripts/stress.js
```

---

## Part 10: Metrics and Monitoring

### Check Prometheus targets

```bash {"name": "check-prometheus"}
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, health: .health}'
```

### Query rate limit metrics

```bash {"name": "query-metrics"}
echo "Rate limit decisions in last 5 minutes:"
curl -s 'http://localhost:9090/api/v1/query?query=sum(increase(rate_limit_decisions_total[5m]))%20by%20(decision)' | jq '.data.result'
```

### Check Redis state

```bash {"name": "check-redis"}
echo "Redis keys (rate limit state):"
docker compose exec redis redis-cli --scan --pattern "*" | head -20
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo "Lab cleaned up"
```

---

## Troubleshooting Commands

### Check all service logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check rate limiter logs

```bash {"name": "logs-rate-limiter"}
docker compose logs rate-limiter --tail=50
```

### Check Redis logs

```bash {"name": "logs-redis"}
docker compose logs redis --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart specific service

```bash {"name": "restart-rate-limiter-service"}
docker compose restart rate-limiter
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Check rate limit | `curl -X POST localhost:8000/v1/check -d '{"client_id":"x","resource":"y","cost":1}'` |
| Update config | `curl -X PUT localhost:8000/v1/config -d '{"client_id":"x","limit":100,...}'` |
| Get usage | `curl localhost:8000/v1/usage/client-id` |
| Test API | `curl localhost:8080/api/data -H "X-Client-ID: my-client"` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
| API docs | http://localhost:8000/docs |
