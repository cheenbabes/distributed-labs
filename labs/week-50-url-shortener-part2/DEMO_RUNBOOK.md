# Demo Runbook: URL Shortener Part 2 - Scaling

This runbook contains all commands for demonstrating the scaled URL shortener. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

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
sleep 20
docker compose ps
```

### Verify all services are responding

```bash {"name": "verify-services"}
echo "=== Load Balancer (Nginx) ==="
curl -s http://localhost:8000/health | jq
echo ""
echo "=== Instance 1 ==="
curl -s http://localhost:8001/health | jq
echo ""
echo "=== Instance 2 ==="
curl -s http://localhost:8002/health | jq
echo ""
echo "=== Instance 3 ==="
curl -s http://localhost:8003/health | jq
echo ""
echo "=== Redis Nodes ==="
docker compose exec redis-1 redis-cli ping
docker compose exec redis-2 redis-cli ping
docker compose exec redis-3 redis-cli ping
echo ""
echo "=== PostgreSQL ==="
docker compose exec postgres psql -U shortener -c "SELECT 'connected' as status;" -t
```

---

## Part 2: Show Observability UIs

### Print all URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Load Balancer:   http://localhost:8000"
echo "  Instance 1:      http://localhost:8001"
echo "  Instance 2:      http://localhost:8002"
echo "  Instance 3:      http://localhost:8003"
echo "  API Docs:        http://localhost:8000/docs"
echo "  Jaeger:          http://localhost:16686"
echo "  Grafana:         http://localhost:3001 (admin/admin)"
echo "  Prometheus:      http://localhost:9090"
```

---

## Part 3: Demonstrate Load Balancing

### Show requests distributed across instances

```bash {"name": "demo-load-balancing"}
echo "Making 10 requests through load balancer..."
echo ""
for i in {1..10}; do
  RESULT=$(curl -s -X POST http://localhost:8000/shorten \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"https://lb-demo.com/page$i\"}")
  INSTANCE=$(echo $RESULT | jq -r '.instance')
  CODE=$(echo $RESULT | jq -r '.short_code')
  echo "Request $i: Instance $INSTANCE handled it (code: $CODE)"
done
```

### Check upstream header from Nginx

```bash {"name": "check-upstream-header"}
echo "Checking X-Upstream-Server header..."
for i in {1..5}; do
  UPSTREAM=$(curl -s -I -X POST http://localhost:8000/shorten \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"https://header-test.com/$i\"}" | grep -i x-upstream | cut -d' ' -f2)
  echo "Request $i routed to: $UPSTREAM"
done
```

---

## Part 4: Demonstrate Cache Sharding

### Create URLs and check shard distribution

```bash {"name": "demo-cache-sharding"}
echo "Creating 30 URLs to demonstrate cache sharding..."
for i in {1..30}; do
  curl -s -X POST http://localhost:8000/shorten \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"https://shard-demo.com/page$i\"}" > /dev/null
  echo -n "."
done
echo ""
echo ""
echo "Cache distribution across shards:"
curl -s http://localhost:8000/admin/cache-stats | jq '.shards'
```

### Access URLs and verify consistent sharding

```bash {"name": "verify-consistent-hashing"}
# Create a specific URL
RESULT=$(curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://consistent-hash-test.com/demo"}')
CODE=$(echo $RESULT | jq -r '.short_code')
echo "Created short code: $CODE"
echo ""
echo "Accessing through each instance (same shard should be used):"
echo ""

# Access through each instance
for instance in 1 2 3; do
  curl -s "http://localhost:800$instance/$CODE" > /dev/null
  echo "Accessed via instance $instance"
done

echo ""
echo "Check Jaeger for traces - all should show same cache.shard value"
echo "Search for: url-shortener (select any instance)"
```

---

## Part 5: Demonstrate Distributed Rate Limiting

### Show current rate limit config

```bash {"name": "show-rate-limit"}
echo "Current rate limit configuration:"
curl -s http://localhost:8000/admin/rate-limit | jq
```

### Test rate limiting

```bash {"name": "test-rate-limit"}
echo "Making rapid requests to trigger rate limit..."
echo ""

for i in {1..120}; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/example1)
  if [ "$STATUS" == "429" ]; then
    echo "Rate limited after $i requests!"
    echo "Response code: 429 Too Many Requests"
    break
  fi
  if [ $((i % 20)) -eq 0 ]; then
    echo "Made $i requests (status: $STATUS)..."
  fi
done
```

### Demonstrate aggressive rate limit

```bash {"name": "aggressive-rate-limit"}
echo "Setting aggressive rate limit (10 requests per 60 seconds)..."
curl -s -X POST http://localhost:8000/admin/rate-limit \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "requests": 10, "window": 60}' | jq

echo ""
echo "Testing with aggressive limit:"
for i in {1..15}; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/example1)
  echo "Request $i: HTTP $STATUS"
done
```

### Reset rate limit to default

```bash {"name": "reset-rate-limit"}
curl -s -X POST http://localhost:8000/admin/rate-limit \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "requests": 100, "window": 60}' | jq
echo "Rate limit reset to default (100 requests per 60 seconds)"
```

---

## Part 6: Simulate Instance Failure

### Create test URL

```bash {"name": "create-failure-test-url"}
RESULT=$(curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://failure-test.example.com/important-page"}')
echo $RESULT | jq
export FAILURE_TEST_CODE=$(echo $RESULT | jq -r '.short_code')
echo ""
echo "Saved short code: $FAILURE_TEST_CODE"
```

### Test before failure

```bash {"name": "test-before-failure"}
echo "Testing access before failure (5 requests):"
for i in {1..5}; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000/example1")
  echo "Request $i: HTTP $STATUS"
done
```

### Stop instance 2

```bash {"name": "stop-instance-2"}
echo "Stopping instance 2..."
docker stop lab50-shortener-2
echo ""
docker compose ps | grep shortener
```

### Test after failure

```bash {"name": "test-after-failure"}
echo "Testing access after stopping instance 2 (5 requests):"
for i in {1..5}; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000/example1")
  echo "Request $i: HTTP $STATUS (still working!)"
done
```

### Restart instance 2

```bash {"name": "restart-instance-2"}
echo "Restarting instance 2..."
docker start lab50-shortener-2
sleep 5
echo ""
docker compose ps | grep shortener
```

---

## Part 7: Analytics Aggregation

### Generate traffic for analytics

```bash {"name": "generate-analytics-traffic"}
echo "Generating traffic across instances..."
echo ""

for i in {1..30}; do
  # Create URLs
  curl -s -X POST http://localhost:8000/shorten \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"https://analytics.example.com/page$i\"}" > /dev/null

  # Generate some redirects
  for j in 1 2 3; do
    curl -s -o /dev/null "http://localhost:8000/example$j"
  done

  if [ $((i % 10)) -eq 0 ]; then
    echo "Generated traffic batch $i..."
  fi
done
echo ""
echo "Traffic generation complete."
```

### View cluster-wide analytics

```bash {"name": "view-cluster-analytics"}
echo "Cluster-wide analytics:"
curl -s http://localhost:8000/admin/cluster | jq
```

### View recent URLs showing instance distribution

```bash {"name": "view-recent-urls"}
echo "Recent URLs (notice created_by_instance column):"
curl -s http://localhost:8000/admin/recent?limit=10 | jq
```

---

## Part 8: Load Testing

### Run the full load test

```bash {"name": "run-load-test"}
echo "Running scaling load test (this takes ~5 minutes)..."
echo "Watch Grafana at http://localhost:3001 during the test"
echo ""
docker compose run --rm k6 run /scripts/scaling-test.js
```

### Quick inline load test

```bash {"name": "quick-load-test"}
echo "Running 30-second quick load test..."
echo ""

# Pre-create some URLs
for i in {1..20}; do
  curl -s -X POST http://localhost:8000/shorten \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"https://quickload.com/page$i\"}" > /dev/null
done

writes=0
reads=0
end=$((SECONDS+30))

while [ $SECONDS -lt $end ]; do
  # 1 write
  curl -s -X POST http://localhost:8000/shorten \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"https://load-test.com/url$RANDOM\"}" > /dev/null &
  writes=$((writes+1))

  # 10 reads
  for i in {1..10}; do
    curl -s -o /dev/null "http://localhost:8000/example$((RANDOM % 3 + 1))" &
    reads=$((reads+1))
  done

  wait
done

echo ""
echo "Completed: $writes writes, $reads reads"
echo "Read/Write ratio: $((reads/writes)):1"
```

---

## Part 9: Direct Instance Access

### Compare responses from different instances

```bash {"name": "compare-instances"}
echo "Accessing the same endpoint through different instances:"
echo ""
echo "=== Instance 1 ==="
curl -s http://localhost:8001/admin/instance | jq
echo ""
echo "=== Instance 2 ==="
curl -s http://localhost:8002/admin/instance | jq
echo ""
echo "=== Instance 3 ==="
curl -s http://localhost:8003/admin/instance | jq
```

### Check cache stats per instance

```bash {"name": "cache-stats-per-instance"}
echo "Cache stats from each instance:"
echo ""
for i in 1 2 3; do
  echo "=== Instance $i ==="
  curl -s "http://localhost:800$i/admin/cache-stats" | jq '{hits, misses, hit_rate}'
  echo ""
done
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

### Check specific instance logs

```bash {"name": "logs-instance-1"}
docker compose logs shortener-1 --tail=50
```

### Check Nginx logs

```bash {"name": "logs-nginx"}
docker compose logs nginx --tail=50
```

### Check Redis status

```bash {"name": "check-redis"}
echo "=== Redis Node 1 ==="
docker compose exec redis-1 redis-cli INFO clients | grep connected
docker compose exec redis-1 redis-cli DBSIZE
echo ""
echo "=== Redis Node 2 ==="
docker compose exec redis-2 redis-cli INFO clients | grep connected
docker compose exec redis-2 redis-cli DBSIZE
echo ""
echo "=== Redis Node 3 ==="
docker compose exec redis-3 redis-cli INFO clients | grep connected
docker compose exec redis-3 redis-cli DBSIZE
```

### Check database content

```bash {"name": "check-db"}
docker compose exec postgres psql -U shortener -c "
SELECT created_by_instance, COUNT(*) as url_count
FROM urls
WHERE created_by_instance IS NOT NULL
GROUP BY created_by_instance
ORDER BY created_by_instance;
"
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

### Restart all shortener instances

```bash {"name": "restart-shorteners"}
docker compose restart shortener-1 shortener-2 shortener-3
sleep 5
docker compose ps | grep shortener
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Shorten URL | `curl -X POST localhost:8000/shorten -d '{"url":"..."}' -H "Content-Type: application/json"` |
| Check instance | `curl localhost:800X/admin/instance` (X = 1, 2, or 3) |
| Cache stats | `curl localhost:8000/admin/cache-stats` |
| Cluster analytics | `curl localhost:8000/admin/cluster` |
| Rate limit config | `curl localhost:8000/admin/rate-limit` |
| View traces | http://localhost:16686 |
| View metrics | http://localhost:3001 |
