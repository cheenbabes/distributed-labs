# Demo Runbook: URL Shortener - Part 1

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
echo "Shortener API:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Redis:"
docker compose exec redis redis-cli ping
echo ""
echo "Postgres:"
docker compose exec postgres psql -U shortener -c "SELECT 'connected' as status;" -t
```

---

## Part 2: Show Observability UIs

### Print all URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Shortener API: http://localhost:8000"
echo "  API Docs:      http://localhost:8000/docs"
echo "  Jaeger:        http://localhost:16686"
echo "  Grafana:       http://localhost:3001 (admin/admin)"
echo "  Prometheus:    http://localhost:9090"
```

---

## Part 3: Basic URL Shortening

### Shorten your first URL

```bash {"name": "shorten-first"}
curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.example.com/very/long/path/that/needs/shortening"}' | jq
```

### Test the redirect

```bash {"name": "test-redirect"}
# Get the first short code
CODE=$(curl -s http://localhost:8000/admin/recent?limit=1 | jq -r '.[0].short_code')
echo "Testing redirect for code: $CODE"
curl -L -s -o /dev/null -w "Final URL: %{url_effective}\nStatus: %{http_code}\nTime: %{time_total}s\n" \
  "http://localhost:8000/$CODE"
```

### View URL statistics

```bash {"name": "view-stats"}
CODE=$(curl -s http://localhost:8000/admin/recent?limit=1 | jq -r '.[0].short_code')
curl -s "http://localhost:8000/stats/$CODE" | jq
```

---

## Part 4: Compare Key Generation Strategies

### Show current strategy

```bash {"name": "show-strategy"}
curl -s http://localhost:8000/admin/strategy | jq
```

### Demo Counter Strategy

```bash {"name": "demo-counter"}
echo "=== Counter Strategy (Base62 encoded sequential IDs) ==="
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "counter"}' | jq

echo ""
echo "Creating 5 URLs with counter strategy:"
for i in {1..5}; do
  CODE=$(curl -s -X POST http://localhost:8000/shorten \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"https://counter-test.com/page$i\"}" | jq -r '.short_code')
  echo "  URL $i -> $CODE"
done
```

### Demo Hash Strategy

```bash {"name": "demo-hash"}
echo "=== Hash Strategy (MD5 prefix) ==="
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "hash"}' | jq

echo ""
echo "Creating same URL twice - should get same code:"
CODE1=$(curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://hash-test.com/same-page"}' | jq -r '.short_code')
CODE2=$(curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://hash-test.com/same-page"}' | jq -r '.short_code')
echo "  First request:  $CODE1"
echo "  Second request: $CODE2"
if [ "$CODE1" = "$CODE2" ]; then
  echo "  Result: SAME (deterministic!)"
else
  echo "  Result: DIFFERENT (already existed with different URL)"
fi
```

### Demo Random Strategy

```bash {"name": "demo-random"}
echo "=== Random Strategy (Random Base62) ==="
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "random"}' | jq

echo ""
echo "Creating same URL twice - should get different codes:"
CODE1=$(curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://random-test.com/same-page"}' | jq -r '.short_code')
CODE2=$(curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://random-test.com/same-page"}' | jq -r '.short_code')
echo "  First request:  $CODE1"
echo "  Second request: $CODE2"
echo "  Result: Always DIFFERENT (non-deterministic)"
```

---

## Part 5: Trace the Full Flow

### Generate traced requests

```bash {"name": "generate-traces"}
echo "Generating requests for tracing..."

# Create a new URL
RESULT=$(curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://traced-example.com/demo"}')
CODE=$(echo $RESULT | jq -r '.short_code')
echo "Created short code: $CODE"

# Access it multiple times
echo "Generating redirect traces..."
for i in {1..5}; do
  curl -s -o /dev/null "http://localhost:8000/$CODE"
  echo "  Redirect $i complete"
done

echo ""
echo "View traces at: http://localhost:16686"
echo "Select service 'url-shortener' and click Find Traces"
```

---

## Part 6: Observe Cache Behavior

### Show cache statistics

```bash {"name": "cache-stats"}
curl -s http://localhost:8000/admin/cache-stats | jq
```

### Compare cache miss vs hit latency

```bash {"name": "cache-comparison"}
# Create a new URL
RESULT=$(curl -s -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://cache-demo.com/fresh-url"}')
CODE=$(echo $RESULT | jq -r '.short_code')

# Clear cache for this code
curl -s -X DELETE "http://localhost:8000/admin/cache/$CODE" > /dev/null

echo "Testing code: $CODE"
echo ""
echo "First access (cache MISS - hits database):"
curl -s -o /dev/null -w "  Time: %{time_total}s\n" "http://localhost:8000/$CODE"

echo ""
echo "Second access (cache HIT - from Redis):"
curl -s -o /dev/null -w "  Time: %{time_total}s\n" "http://localhost:8000/$CODE"

echo ""
echo "Multiple cached accesses:"
for i in {3..7}; do
  curl -s -o /dev/null -w "  Access $i: %{time_total}s\n" "http://localhost:8000/$CODE"
done
```

### View updated cache stats

```bash {"name": "cache-stats-after"}
curl -s http://localhost:8000/admin/cache-stats | jq
```

---

## Part 7: Read vs Write Patterns

### Show current metrics

```bash {"name": "show-metrics"}
echo "Current request counts:"
curl -s http://localhost:8000/metrics | grep -E "^http_requests_total" | head -10
```

### Simulate realistic 100:1 read/write ratio

```bash {"name": "simulate-ratio"}
echo "Simulating 100:1 read/write ratio..."
echo ""

# Get existing codes
CODES=$(curl -s http://localhost:8000/admin/recent?limit=10 | jq -r '.[].short_code')

for batch in {1..3}; do
  # 1 write
  curl -s -X POST http://localhost:8000/shorten \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"https://write-test.com/batch$batch\"}" > /dev/null

  # 100 reads
  for i in {1..100}; do
    CODE=$(echo "$CODES" | shuf -n 1)
    curl -s -o /dev/null "http://localhost:8000/$CODE"
  done

  echo "Batch $batch: 1 write, 100 reads"
done

echo ""
echo "Request distribution:"
curl -s http://localhost:8000/metrics | grep -E "^http_requests_total" | grep -E "(shorten|redirect)"
```

---

## Part 8: Load Testing

### Run the full load test

```bash {"name": "load-test", "background": true}
docker compose run --rm k6 run /scripts/url-shortener.js
```

### Quick inline load test

```bash {"name": "quick-load"}
echo "Running 20-second mixed workload..."

# Get some codes first
CODES=$(curl -s http://localhost:8000/admin/recent?limit=20 | jq -r '.[].short_code')

writes=0
reads=0
end=$((SECONDS+20))

while [ $SECONDS -lt $end ]; do
  # 1 write
  curl -s -X POST http://localhost:8000/shorten \
    -H "Content-Type: application/json" \
    -d "{\"url\": \"https://load-test.com/url$RANDOM\"}" > /dev/null &
  writes=$((writes+1))

  # 10 reads
  for i in {1..10}; do
    CODE=$(echo "$CODES" | shuf -n 1)
    curl -s -o /dev/null "http://localhost:8000/$CODE" &
    reads=$((reads+1))
  done

  wait
done

echo "Completed: $writes writes, $reads reads"
echo "Ratio: $((reads/writes)):1"
```

---

## Part 9: View All URLs

### List recent URLs

```bash {"name": "list-recent"}
curl -s http://localhost:8000/admin/recent?limit=10 | jq
```

### Count total URLs

```bash {"name": "count-urls"}
curl -s http://localhost:8000/admin/count | jq
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

### Check shortener service logs

```bash {"name": "logs-shortener"}
docker compose logs shortener --tail=100
```

### Check database content

```bash {"name": "check-db"}
docker compose exec postgres psql -U shortener -c "SELECT short_code, original_url, created_at, access_count FROM urls LIMIT 10;"
```

### Check Redis content

```bash {"name": "check-redis"}
docker compose exec redis redis-cli KEYS "url:*" | head -10
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart services

```bash {"name": "restart-all"}
docker compose restart
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Shorten URL | `curl -X POST localhost:8000/shorten -d '{"url":"..."}' -H "Content-Type: application/json"` |
| Redirect | `curl -L localhost:8000/{code}` |
| Change strategy | `curl -X POST localhost:8000/admin/strategy -d '{"strategy":"hash"}' -H "Content-Type: application/json"` |
| Cache stats | `curl localhost:8000/admin/cache-stats` |
| View traces | http://localhost:16686 |
| View metrics | http://localhost:3001 |
