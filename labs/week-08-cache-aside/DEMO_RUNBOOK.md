# Demo Runbook: Cache-Aside Pattern

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
echo "Product Service:"
curl -s http://localhost:8000/health | jq
```

---

## Part 2: Show URLs

### Display all service URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Product API:  http://localhost:8000"
echo "  Jaeger:       http://localhost:16686"
echo "  Grafana:      http://localhost:3001 (admin/admin)"
echo "  Prometheus:   http://localhost:9090"
```

---

## Part 3: Demonstrate Cache Miss vs Hit

### Clear cache to start fresh

```bash {"name": "clear-cache"}
curl -s -X POST http://localhost:8000/admin/cache/clear | jq
```

### First read - Cache MISS

```bash {"name": "first-read-miss"}
echo "=== First Read (Cache Miss) ==="
curl -s http://localhost:8000/products/prod-001 | jq '{id, name, price, cache_status}'
```

### Second read - Cache HIT

```bash {"name": "second-read-hit"}
echo "=== Second Read (Cache Hit) ==="
curl -s http://localhost:8000/products/prod-001 | jq '{id, name, price, cache_status}'
```

### Show timing difference

```bash {"name": "timing-comparison"}
echo "=== Timing Comparison ==="
echo ""
curl -s -X POST http://localhost:8000/admin/cache/clear > /dev/null

echo "Cache MISS timing:"
time curl -s http://localhost:8000/products/prod-001 > /dev/null

echo ""
echo "Cache HIT timing:"
time curl -s http://localhost:8000/products/prod-001 > /dev/null
```

---

## Part 4: Demonstrate Cache-Aside Write Behavior

### Create a new product (no cache write in cache-aside)

```bash {"name": "create-product"}
echo "=== Creating New Product ==="
RESPONSE=$(curl -s -X POST http://localhost:8000/products \
  -H "Content-Type: application/json" \
  -d '{"name": "Demo Widget", "description": "Created for demo", "price": 49.99}')
echo $RESPONSE | jq
echo ""
PRODUCT_ID=$(echo $RESPONSE | jq -r '.id')
echo "Product ID: $PRODUCT_ID"
```

### Check cache - product NOT cached yet

```bash {"name": "check-cache-after-create"}
echo "=== Check Cache (should be empty for new product) ==="
# Get the product ID from the previous command or use a known one
curl -s http://localhost:8000/admin/cache-stats | jq '.cache_state.cached_items'
```

### Read product - populates cache

```bash {"name": "read-to-cache"}
echo "=== Read Product (populates cache) ==="
curl -s http://localhost:8000/products/prod-001 | jq '{cache_status}'
curl -s http://localhost:8000/admin/cache/key/prod-001 | jq '{exists, ttl_seconds}'
```

---

## Part 5: Demonstrate Cache Invalidation

### Cache a product first

```bash {"name": "cache-product"}
echo "=== Step 1: Cache the product ==="
curl -s http://localhost:8000/products/prod-002 | jq '{name, price, cache_status}'
```

### Verify it's in cache

```bash {"name": "verify-cached"}
echo "=== Step 2: Verify in cache ==="
curl -s http://localhost:8000/admin/cache/key/prod-002 | jq
```

### Update product - invalidates cache

```bash {"name": "update-product"}
echo "=== Step 3: Update product (invalidates cache) ==="
curl -s -X PUT http://localhost:8000/products/prod-002 \
  -H "Content-Type: application/json" \
  -d '{"name": "Mechanical Keyboard Pro", "description": "Updated!", "price": 199.99}' | jq '{name, price, cache_status}'
```

### Verify cache invalidated

```bash {"name": "verify-invalidated"}
echo "=== Step 4: Verify cache invalidated ==="
curl -s http://localhost:8000/admin/cache/key/prod-002 | jq
```

### Read again - repopulates cache

```bash {"name": "read-repopulate"}
echo "=== Step 5: Read again (repopulates cache) ==="
curl -s http://localhost:8000/products/prod-002 | jq '{name, price, cache_status}'
```

---

## Part 6: Demonstrate Stale Read Scenario

### Cache a product

```bash {"name": "cache-for-stale"}
curl -s -X POST http://localhost:8000/admin/cache/clear > /dev/null
echo "=== Step 1: Cache the product ==="
curl -s http://localhost:8000/products/prod-003 | jq '{name, price, cache_status}'
```

### Simulate external database update

```bash {"name": "simulate-external-update"}
echo "=== Step 2: Simulate external DB update ==="
curl -s -X POST "http://localhost:8000/admin/simulate-stale-read/prod-003?new_price=999.99" | jq
```

### Show stale read

```bash {"name": "show-stale-read"}
echo "=== Step 3: Read shows STALE data ==="
echo "Database has 999.99, but cache returns old price:"
curl -s http://localhost:8000/products/prod-003 | jq '{name, price, cache_status}'
```

### Fix with manual invalidation

```bash {"name": "fix-stale-read"}
echo "=== Step 4: Fix with manual invalidation ==="
curl -s -X POST http://localhost:8000/admin/invalidate/prod-003 | jq
echo ""
echo "Now reading fresh data:"
curl -s http://localhost:8000/products/prod-003 | jq '{name, price, cache_status}'
```

---

## Part 7: Show Cache Statistics

### View cache stats

```bash {"name": "cache-stats"}
echo "=== Cache Statistics ==="
curl -s http://localhost:8000/admin/cache-stats | jq
```

### Generate traffic and show hit rate

```bash {"name": "generate-traffic"}
echo "=== Generating Traffic ==="
curl -s -X POST http://localhost:8000/admin/cache/clear > /dev/null

echo "Making 25 reads across 5 products..."
for i in {1..25}; do
  PROD_ID="prod-00$((($i % 5) + 1))"
  curl -s http://localhost:8000/products/$PROD_ID > /dev/null
  echo -n "."
done
echo ""
echo ""
echo "Final stats:"
curl -s http://localhost:8000/admin/cache-stats | jq '.service_stats'
```

---

## Part 8: Load Testing

### Run cache warmup test

```bash {"name": "load-test-warmup"}
docker compose run --rm lab08-k6 run /scripts/cache-warmup.js
```

### Run mixed workload test

```bash {"name": "load-test-mixed"}
docker compose run --rm lab08-k6 run /scripts/basic.js
```

### Run stale read test

```bash {"name": "load-test-stale"}
docker compose run --rm lab08-k6 run /scripts/stale-read-test.js
```

---

## Part 9: Compare with Write-Through

### Show the key difference

```bash {"name": "compare-patterns"}
echo "=== Cache-Aside vs Write-Through ==="
echo ""
echo "Cache-Aside (this lab):"
echo "  - Write: DB only, then invalidate cache"
echo "  - Read: Check cache, miss goes to DB, then populates cache"
echo "  - Lower write latency"
echo "  - Potential for stale reads"
echo ""
echo "Write-Through (Lab 03):"
echo "  - Write: Both DB AND cache"
echo "  - Read: Cache first, then DB on miss"
echo "  - Higher write latency"
echo "  - Strong consistency"
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

### Check all logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check product service logs

```bash {"name": "logs-product-service"}
docker compose logs lab08-product-service --tail=50
```

### Check Redis logs

```bash {"name": "logs-redis"}
docker compose logs lab08-redis --tail=20
```

### Check Postgres logs

```bash {"name": "logs-postgres"}
docker compose logs lab08-postgres --tail=20
```

### Direct Redis CLI access

```bash {"name": "redis-cli"}
docker compose exec lab08-redis redis-cli
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart product service

```bash {"name": "restart-service"}
docker compose restart lab08-product-service
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| View cache stats | `curl localhost:8000/admin/cache-stats` |
| Clear cache | `curl -X POST localhost:8000/admin/cache/clear` |
| Invalidate key | `curl -X POST localhost:8000/admin/invalidate/prod-001` |
| Inspect key | `curl localhost:8000/admin/cache/key/prod-001` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
