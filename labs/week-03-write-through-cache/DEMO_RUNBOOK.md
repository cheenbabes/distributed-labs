# Demo Runbook: Write-Through Cache

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
echo "API Health:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Redis ping:"
docker compose exec redis redis-cli ping
echo ""
echo "Postgres ready:"
docker compose exec postgres pg_isready -U labuser -d items_db
```

---

## Part 2: Show UI URLs

### Print URLs for browser

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  API:        http://localhost:8000"
echo "  Jaeger:     http://localhost:16686"
echo "  Grafana:    http://localhost:3001 (admin/admin)"
echo "  Prometheus: http://localhost:9090"
```

---

## Part 3: Demonstrate Write-Through Pattern

### Show pre-loaded data

```bash {"name": "list-items"}
echo "Pre-loaded items from database:"
curl -s http://localhost:8000/items | jq
```

### Create a new item (write goes to BOTH Redis and Postgres)

```bash {"name": "create-item"}
echo "Creating new item..."
RESPONSE=$(curl -s -X POST http://localhost:8000/items \
  -H "Content-Type: application/json" \
  -d '{"name": "Demo Widget", "description": "Created in demo", "price": 42.99}')
echo $RESPONSE | jq
echo ""
echo "Item ID: $(echo $RESPONSE | jq -r '.id')"
```

### Read the item (should be a cache HIT)

```bash {"name": "read-item-hit"}
# Use the ID from the previous step
ITEM_ID=$(curl -s http://localhost:8000/items | jq -r '.items[0].id')
echo "Reading item $ITEM_ID (should be cache HIT)..."
curl -s http://localhost:8000/items/$ITEM_ID | jq
```

### Check cache statistics

```bash {"name": "cache-stats"}
echo "Cache statistics:"
curl -s http://localhost:8000/cache/stats | jq
```

---

## Part 4: Compare Write vs Read Latency

### Measure write latency (5 requests)

```bash {"name": "measure-writes"}
echo "Write latency (5 requests):"
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" -X POST http://localhost:8000/items \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"Test Item $i\", \"price\": $i.99}")
  echo "  Write $i: ${time}s"
done
```

### Measure read latency (5 requests - cache hits)

```bash {"name": "measure-reads"}
echo "Read latency (5 requests, cache hits):"
ITEM_ID=$(curl -s http://localhost:8000/items | jq -r '.items[0].id')
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/items/$ITEM_ID)
  echo "  Read $i: ${time}s"
done
```

### Compare read with cache miss

```bash {"name": "cache-miss-read"}
echo "Clearing cache..."
curl -s -X POST http://localhost:8000/cache/clear | jq

echo ""
echo "Reading item (cache MISS - goes to database):"
ITEM_ID=$(curl -s http://localhost:8000/items | jq -r '.items[0].id')
time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/items/$ITEM_ID)
echo "  Cache miss read: ${time}s"

echo ""
echo "Reading same item again (cache HIT):"
time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/items/$ITEM_ID)
echo "  Cache hit read: ${time}s"
```

---

## Part 5: Demonstrate Consistency

### Show that updates are immediately visible

```bash {"name": "consistency-demo"}
# Create an item
echo "1. Creating item..."
RESPONSE=$(curl -s -X POST http://localhost:8000/items \
  -H "Content-Type: application/json" \
  -d '{"name": "Original Name", "price": 10.00}')
ITEM_ID=$(echo $RESPONSE | jq -r '.id')
echo "Created: $(echo $RESPONSE | jq -c '{name, price}')"

echo ""
echo "2. Updating item..."
UPDATED=$(curl -s -X PUT http://localhost:8000/items/$ITEM_ID \
  -H "Content-Type: application/json" \
  -d '{"name": "Updated Name", "price": 99.99}')
echo "Updated: $(echo $UPDATED | jq -c '{name, price}')"

echo ""
echo "3. Reading item (should see update immediately)..."
READ=$(curl -s http://localhost:8000/items/$ITEM_ID)
echo "Read: $(echo $READ | jq -c '{name, price}')"

echo ""
echo "Consistency verified: Update visible immediately!"
```

---

## Part 6: Kill Redis (Graceful Degradation)

### Check health before

```bash {"name": "health-before-kill"}
echo "Health check (Redis running):"
curl -s http://localhost:8000/health | jq
```

### Stop Redis

```bash {"name": "stop-redis"}
echo "Stopping Redis..."
docker compose stop redis
sleep 2
echo "Redis stopped."
```

### Check health after

```bash {"name": "health-after-kill"}
echo "Health check (Redis down):"
curl -s http://localhost:8000/health | jq
```

### Show system still works

```bash {"name": "works-without-redis"}
echo "Reading item (falls back to database)..."
curl -s http://localhost:8000/items/550e8400-e29b-41d4-a716-446655440001 | jq

echo ""
echo "Creating item (writes to database, skips cache)..."
curl -s -X POST http://localhost:8000/items \
  -H "Content-Type: application/json" \
  -d '{"name": "Created without Redis", "price": 77.77}' | jq
```

### Restart Redis

```bash {"name": "restart-redis"}
echo "Restarting Redis..."
docker compose start redis
sleep 5
echo ""
echo "Health check (Redis back):"
curl -s http://localhost:8000/health | jq
```

---

## Part 7: Generate Traces for Jaeger

### Generate varied trace data

```bash {"name": "generate-traces"}
echo "Generating trace data..."

# Some writes
for i in {1..5}; do
  curl -s -X POST http://localhost:8000/items \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"Trace Item $i\", \"price\": $i.99}" > /dev/null
  echo -n "W"
done

# Some reads (cache hits)
ITEM_ID=$(curl -s http://localhost:8000/items | jq -r '.items[0].id')
for i in {1..10}; do
  curl -s http://localhost:8000/items/$ITEM_ID > /dev/null
  echo -n "R"
done

# Clear cache and do some cache misses
curl -s -X POST http://localhost:8000/cache/clear > /dev/null
for i in {1..5}; do
  curl -s http://localhost:8000/items/$ITEM_ID > /dev/null
  echo -n "M"
done

echo ""
echo "Done! Check Jaeger: http://localhost:16686"
echo "Select 'api' service and click 'Find Traces'"
```

---

## Part 8: Load Testing (Optional)

### Run k6 load test

```bash {"name": "load-test", "background": true}
docker compose run --rm k6 run /scripts/basic.js
```

### Quick inline load test

```bash {"name": "quick-load"}
echo "Running 20-second mixed load test..."

end=$((SECONDS+20))
writes=0
reads=0

while [ $SECONDS -lt $end ]; do
  # 70% reads, 30% writes
  if [ $((RANDOM % 10)) -lt 7 ]; then
    curl -s http://localhost:8000/items > /dev/null &
    reads=$((reads+1))
  else
    curl -s -X POST http://localhost:8000/items \
      -H "Content-Type: application/json" \
      -d '{"name": "Load test", "price": 1.99}' > /dev/null &
    writes=$((writes+1))
  fi
done
wait

echo "Completed: $reads reads, $writes writes"
echo ""
curl -s http://localhost:8000/cache/stats | jq
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
docker compose logs api --tail=50
```

### Check Redis directly

```bash {"name": "redis-inspect"}
echo "Redis keys:"
docker compose exec redis redis-cli keys "item:*"
echo ""
echo "Redis info:"
docker compose exec redis redis-cli info stats | grep -E "(keyspace|hits|misses)"
```

### Check Postgres directly

```bash {"name": "postgres-inspect"}
echo "Items in database:"
docker compose exec postgres psql -U labuser -d items_db -c "SELECT id, name, price FROM items LIMIT 10;"
```

### Restart everything

```bash {"name": "restart-all"}
docker compose restart
sleep 10
docker compose ps
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Create item | `curl -X POST localhost:8000/items -H "Content-Type: application/json" -d '{"name":"X","price":10}'` |
| Get item | `curl localhost:8000/items/{id}` |
| Update item | `curl -X PUT localhost:8000/items/{id} -H "Content-Type: application/json" -d '{"name":"Y","price":20}'` |
| Clear cache | `curl -X POST localhost:8000/cache/clear` |
| Cache stats | `curl localhost:8000/cache/stats` |
| Health check | `curl localhost:8000/health` |
| View traces | http://localhost:16686 |

---

## Demo Flow Summary

1. **Start** - Show architecture, start services
2. **Write-Through** - Create item, show trace with Redis+Postgres spans
3. **Latency** - Compare write (slower) vs read (faster)
4. **Consistency** - Update item, show immediate visibility
5. **Resilience** - Kill Redis, show graceful degradation
6. **Traces** - Explore in Jaeger
7. **Load Test** - (Optional) Show behavior under load
