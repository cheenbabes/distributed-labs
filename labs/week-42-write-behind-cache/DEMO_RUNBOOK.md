# Demo Runbook: Write-Behind Cache (Write-Back)

This runbook contains all commands for demonstrating write-behind caching. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

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
echo "API Health:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Configuration:"
curl -s http://localhost:8000/admin/config | jq
```

---

## Part 2: Show Observability URLs

### Display URLs for all UIs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  API:        http://localhost:8000"
echo "  Jaeger:     http://localhost:16686"
echo "  Grafana:    http://localhost:3001 (admin/admin)"
echo "  Prometheus: http://localhost:9090"
echo ""
echo "Grafana Dashboard: Write-Behind Cache Dashboard"
```

---

## Part 3: Write-Behind Basics

### Create a product with write-behind

```bash {"name": "create-product"}
echo "Creating a product (write-behind mode)..."
curl -s -X POST http://localhost:8000/products \
  -H "Content-Type: application/json" \
  -d '{"name": "Demo Widget", "price": 29.99, "quantity": 100}' | jq
echo ""
echo "Note: 'source: cache' means the write was acknowledged from cache immediately"
```

### Check the write queue

```bash {"name": "check-queue"}
echo "Write queue status:"
curl -s http://localhost:8000/admin/queue | jq
```

### Verify product is persisted

```bash {"name": "verify-persistence"}
echo "Waiting for worker to persist..."
sleep 2
echo ""
echo "Read from database (cache miss after TTL or restart):"
docker compose exec lab42-postgres psql -U labuser -d labdb -c "SELECT id, name, price, quantity FROM products ORDER BY created_at DESC LIMIT 5;"
```

---

## Part 4: Latency Comparison

### Measure write-behind latency

```bash {"name": "write-behind-latency"}
echo "Write-Behind Latency (5 requests):"
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" \
    -X POST http://localhost:8000/products \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"WB-Test-$i\", \"price\": 19.99, \"quantity\": 50}")
  echo "  Request $i: ${time}s"
done
```

### Calculate average write-behind latency

```bash {"name": "wb-average"}
total=0
for i in {1..10}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" \
    -X POST http://localhost:8000/products \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"WB-Avg-$i\", \"price\": 19.99, \"quantity\": 50}")
  total=$(echo "$total + $time" | bc)
done
avg=$(echo "scale=4; $total / 10" | bc)
echo "Average write-behind latency: ${avg}s"
```

---

## Part 5: Write Coalescing Demo

### Rapidly update the same product

```bash {"name": "rapid-updates"}
echo "Sending 20 rapid updates to the SAME product..."
for i in {1..20}; do
  curl -s -X PUT http://localhost:8000/products/seed-001 \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"Coalesced-Update-$i\", \"price\": $((RANDOM % 100)).99, \"quantity\": $i}" &
done
wait
echo "Done. All 20 updates sent."
```

### Check queue size after coalescing

```bash {"name": "check-coalescing"}
echo "Queue size (should be much less than 20 if coalescing worked):"
curl -s http://localhost:8000/admin/queue | jq '.queue_size'
echo ""
echo "Check Grafana for 'Writes Coalesced' metric"
```

### Read final state

```bash {"name": "read-final-state"}
echo "Final state of product seed-001:"
curl -s http://localhost:8000/products/seed-001 | jq
```

---

## Part 6: Data Loss Risk Demo

### Step 1 - Create products with pending writes

```bash {"name": "create-at-risk"}
echo "Creating 10 products that will be at risk..."
for i in {1..10}; do
  result=$(curl -s -X POST http://localhost:8000/products \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"AtRisk-$i\", \"price\": 99.99, \"quantity\": $i}")
  id=$(echo $result | jq -r '.id')
  echo "Created: $id"
done
echo ""
echo "Queue size:"
curl -s http://localhost:8000/admin/queue | jq '.queue_size'
```

### Step 2 - Stop worker to accumulate writes

```bash {"name": "stop-worker"}
echo "Stopping worker to prevent persistence..."
docker compose stop lab42-worker
echo "Worker stopped."
```

### Step 3 - Create more products that will be lost

```bash {"name": "create-doomed"}
echo "Creating 5 more products (these WILL be lost)..."
DOOMED_IDS=""
for i in {1..5}; do
  result=$(curl -s -X POST http://localhost:8000/products \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"WillBeLost-$i\", \"price\": 99.99, \"quantity\": $i}")
  id=$(echo $result | jq -r '.id')
  DOOMED_IDS="$DOOMED_IDS $id"
  echo "Created (doomed): $id"
done
echo ""
echo "These IDs will be lost: $DOOMED_IDS"
echo ""
echo "Queue size before crash:"
curl -s http://localhost:8000/admin/queue | jq '.queue_size'
```

### Step 4 - Simulate cache crash

```bash {"name": "crash-cache"}
echo "SIMULATING CACHE CRASH - Flushing Redis..."
docker compose exec lab42-redis redis-cli FLUSHALL
echo "Cache flushed!"
echo ""
echo "Queue size after crash:"
curl -s http://localhost:8000/admin/queue | jq '.queue_size'
```

### Step 5 - Restart worker

```bash {"name": "restart-worker"}
echo "Restarting worker..."
docker compose start lab42-worker
sleep 2
echo "Worker restarted."
```

### Step 6 - Verify data loss

```bash {"name": "verify-loss"}
echo "Trying to read the 'doomed' products..."
echo "These products were acknowledged to clients but are now GONE:"
echo ""

# Check if products exist
for i in {1..5}; do
  # Try to find any product with WillBeLost in the name
  result=$(docker compose exec lab42-postgres psql -U labuser -d labdb -t -c "SELECT COUNT(*) FROM products WHERE name LIKE 'WillBeLost-%';" 2>/dev/null)
  break
done

lost_count=$(echo $result | tr -d ' ')
echo "Products with 'WillBeLost' prefix in database: $lost_count"
echo ""
if [ "$lost_count" = "0" ]; then
  echo "*** DATA LOSS CONFIRMED ***"
  echo "All 5 'WillBeLost' products were acknowledged but never persisted!"
fi
```

---

## Part 7: Retry Logic Demo

### Enable failure simulation

```bash {"name": "enable-failures"}
echo "Restarting worker with failure simulation..."
docker compose stop lab42-worker
docker compose up -d --no-deps lab42-worker
# Note: Set SIMULATE_DB_FAILURE=true in docker-compose.yml to test this
echo ""
echo "Check docker-compose.yml to enable SIMULATE_DB_FAILURE=true"
```

### Watch worker logs for retries

```bash {"name": "watch-retries"}
echo "Watching worker logs (Ctrl+C to stop)..."
docker compose logs -f lab42-worker --tail=20
```

### Check dead letter queue

```bash {"name": "check-dlq"}
echo "Dead Letter Queue contents:"
docker compose exec lab42-redis redis-cli LRANGE write_behind:dlq 0 -1
```

---

## Part 8: Load Testing

### Run basic load test

```bash {"name": "load-test-basic"}
echo "Running basic load test (2 minutes)..."
docker compose run --rm lab42-k6 run /scripts/basic.js
```

### Run coalescing test

```bash {"name": "load-test-coalescing"}
echo "Running coalescing test (30 seconds)..."
docker compose run --rm lab42-k6 run /scripts/coalescing-test.js
```

### Quick manual load

```bash {"name": "quick-load"}
echo "Generating 50 rapid writes..."
for i in {1..50}; do
  curl -s -X POST http://localhost:8000/products \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"Load-$i\", \"price\": $((RANDOM % 100)).99, \"quantity\": $i}" &
done
wait
echo "Done. Check Grafana for queue depth and throughput."
```

---

## Part 9: Verify Database State

### Count products in database

```bash {"name": "count-products"}
echo "Products in database:"
docker compose exec lab42-postgres psql -U labuser -d labdb -c "SELECT COUNT(*) as total_products FROM products;"
```

### Show recent products

```bash {"name": "recent-products"}
echo "Recent products:"
docker compose exec lab42-postgres psql -U labuser -d labdb -c "SELECT id, name, price, quantity, created_at FROM products ORDER BY created_at DESC LIMIT 10;"
```

### Show product stats

```bash {"name": "product-stats"}
echo "Product statistics:"
docker compose exec lab42-postgres psql -U labuser -d labdb -c "SELECT * FROM product_stats;"
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

### Check API logs

```bash {"name": "logs-api"}
docker compose logs lab42-api --tail=50
```

### Check worker logs

```bash {"name": "logs-worker"}
docker compose logs lab42-worker --tail=50
```

### Check Redis

```bash {"name": "check-redis"}
echo "Redis ping:"
docker compose exec lab42-redis redis-cli PING
echo ""
echo "Queue size:"
docker compose exec lab42-redis redis-cli LLEN write_behind:queue
echo ""
echo "Keys in cache:"
docker compose exec lab42-redis redis-cli KEYS "cache:*" | head -10
```

### Check PostgreSQL

```bash {"name": "check-postgres"}
echo "PostgreSQL status:"
docker compose exec lab42-postgres pg_isready -U labuser -d labdb
```

### Resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Check queue | `curl -s localhost:8000/admin/queue \| jq` |
| Create product | `curl -X POST localhost:8000/products -H "Content-Type: application/json" -d '{"name":"X","price":9.99,"quantity":1}'` |
| Read product | `curl localhost:8000/products/<id>` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
