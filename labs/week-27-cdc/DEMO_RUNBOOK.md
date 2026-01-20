# Demo Runbook: Change Data Capture (CDC)

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
echo "This may take 1-2 minutes for Kafka and Debezium..."
sleep 30

echo ""
echo "Checking service status:"
docker compose ps

echo ""
echo "Checking Debezium health:"
curl -s http://localhost:8083/ | jq
```

### Verify all services are responding

```bash {"name": "verify-services"}
echo "Writer Service:"
curl -s http://localhost:8000/health | jq

echo ""
echo "CDC Consumer:"
curl -s http://localhost:8001/health | jq

echo ""
echo "Search Service:"
curl -s http://localhost:8002/health | jq

echo ""
echo "Polling Service:"
curl -s http://localhost:8003/health | jq
```

---

## Part 2: Register Debezium Connector

### Register the PostgreSQL CDC connector

```bash {"name": "register-connector"}
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d @infra/debezium/register-connector.json | jq
```

### Verify connector is running

```bash {"name": "verify-connector"}
sleep 5
curl -s http://localhost:8083/connectors/products-connector/status | jq
```

### List Kafka topics created by Debezium

```bash {"name": "list-topics"}
docker compose exec kafka kafka-topics --bootstrap-server localhost:29092 --list
```

---

## Part 3: Show CDC in Action

### Show existing products in database

```bash {"name": "show-existing-products"}
curl -s http://localhost:8000/products | jq
```

### Create a new product

```bash {"name": "create-product"}
curl -X POST http://localhost:8000/products \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Demo Product",
    "description": "Created during demo to show CDC",
    "price": 149.99,
    "category": "Electronics",
    "stock_quantity": 100
  }' | jq
```

### View the CDC event captured

```bash {"name": "view-cdc-event"}
echo "Last CDC event received:"
curl -s http://localhost:8001/events | jq '.events[-1]'
```

### Verify product appears in search index

```bash {"name": "search-new-product"}
echo "Searching for 'Demo':"
curl -s "http://localhost:8002/search?q=Demo" | jq
```

---

## Part 4: Open UIs

### Print all UI URLs

```bash {"name": "show-urls"}
echo "=========================================="
echo "Open these URLs in your browser:"
echo "=========================================="
echo ""
echo "  Writer API:      http://localhost:8000/docs"
echo "  CDC Consumer:    http://localhost:8001/events"
echo "  Search Service:  http://localhost:8002/docs"
echo "  Polling Service: http://localhost:8003/compare"
echo ""
echo "  Kafka UI:        http://localhost:8080"
echo "  Jaeger:          http://localhost:16686"
echo "  Grafana:         http://localhost:3001 (admin/admin)"
echo "  Prometheus:      http://localhost:9090"
echo ""
echo "=========================================="
```

---

## Part 5: Compare CDC vs Polling

### Check polling service status

```bash {"name": "polling-status"}
curl -s http://localhost:8003/compare | jq
```

### Create product and compare detection times

```bash {"name": "compare-detection"}
echo "Creating product and measuring detection..."
echo ""

# Create product
RESULT=$(curl -s -X POST http://localhost:8000/products \
  -H "Content-Type: application/json" \
  -d '{"name": "Latency Test", "price": 50, "category": "Test"}')
echo "Created: $(echo $RESULT | jq -r '.name')"

# Wait for detection
sleep 2

echo ""
echo "CDC Consumer detected:"
curl -s http://localhost:8001/events | jq '.events[-1] | {operation, table, lag_ms}'

echo ""
echo "Polling Service detected:"
curl -s http://localhost:8003/changes | jq '.changes[-1]'
```

### Show CDC consumer stats

```bash {"name": "cdc-stats"}
curl -s http://localhost:8001/events/stats | jq
```

---

## Part 6: Demonstrate All Operations

### Create multiple products

```bash {"name": "create-multiple"}
echo "Creating 5 products..."
for i in {1..5}; do
  curl -s -X POST http://localhost:8000/products \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"Batch Product $i\", \"price\": $((i * 25)).99, \"category\": \"Demo\"}" > /dev/null
  echo "Created product $i"
done

echo ""
echo "CDC events received:"
curl -s http://localhost:8001/events/stats | jq '.by_operation'
```

### Update a product

```bash {"name": "update-product"}
# Get first product ID
PRODUCT_ID=$(curl -s http://localhost:8000/products | jq '.products[0].id')
echo "Updating product ID: $PRODUCT_ID"

curl -X PUT "http://localhost:8000/products/$PRODUCT_ID" \
  -H "Content-Type: application/json" \
  -d '{"price": 299.99, "stock_quantity": 200}' | jq

echo ""
echo "CDC captured update event:"
curl -s http://localhost:8001/events | jq '.events[-1]'
```

### Delete a product

```bash {"name": "delete-product"}
# Get a product ID (not the first one)
PRODUCT_ID=$(curl -s http://localhost:8000/products | jq '.products[1].id')
echo "Deleting product ID: $PRODUCT_ID"

curl -X DELETE "http://localhost:8000/products/$PRODUCT_ID" | jq

echo ""
echo "CDC captured delete event:"
curl -s http://localhost:8001/events | jq '.events[-1]'
```

---

## Part 7: Search Index Demo

### Show search index status

```bash {"name": "index-status"}
curl -s http://localhost:8002/index/status | jq
```

### Search for products

```bash {"name": "search-products"}
echo "Search for 'Product':"
curl -s "http://localhost:8002/search?q=Product" | jq '.results[:3]'

echo ""
echo "Search by category 'Electronics':"
curl -s "http://localhost:8002/search?q=product&category=Electronics" | jq
```

### List categories

```bash {"name": "list-categories"}
curl -s http://localhost:8002/categories | jq
```

---

## Part 8: Burst Load Test

### Run burst test

```bash {"name": "burst-test"}
echo "Running burst load test (50 VUs, 30 seconds)..."
echo "Watch the CDC lag in Grafana: http://localhost:3001"
echo ""

docker compose run --rm k6 run /scripts/burst-test.js
```

### Check lag after burst

```bash {"name": "check-lag-after-burst"}
echo "CDC Consumer Stats after burst:"
curl -s http://localhost:8001/events/stats | jq

echo ""
echo "Search Index Size:"
curl -s http://localhost:8002/index/status | jq '.size'
```

---

## Part 9: Create an Order

### Create an order

```bash {"name": "create-order"}
# Get a product ID
PRODUCT_ID=$(curl -s http://localhost:8000/products | jq '.products[0].id')
echo "Creating order for product ID: $PRODUCT_ID"

curl -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -d "{
    \"product_id\": $PRODUCT_ID,
    \"customer_email\": \"demo@example.com\",
    \"quantity\": 2
  }" | jq
```

### View order CDC events

```bash {"name": "view-order-events"}
echo "Recent CDC events (including order and stock update):"
curl -s http://localhost:8001/events | jq '.events[-3:]'
```

---

## Part 10: Explore Kafka

### View messages in products topic

```bash {"name": "kafka-messages"}
echo "Last 3 messages from cdc.public.products topic:"
docker compose exec kafka kafka-console-consumer \
  --bootstrap-server localhost:29092 \
  --topic cdc.public.products \
  --from-beginning \
  --max-messages 3 \
  2>/dev/null
```

### Count messages per topic

```bash {"name": "kafka-counts"}
echo "Message counts per topic:"
echo ""
echo "Products topic:"
docker compose exec kafka kafka-run-class kafka.tools.GetOffsetShell \
  --broker-list localhost:29092 \
  --topic cdc.public.products 2>/dev/null | tail -1

echo ""
echo "Orders topic:"
docker compose exec kafka kafka-run-class kafka.tools.GetOffsetShell \
  --broker-list localhost:29092 \
  --topic cdc.public.orders 2>/dev/null | tail -1
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

### Check Debezium logs

```bash {"name": "logs-debezium"}
docker compose logs debezium --tail=50
```

### Check CDC consumer logs

```bash {"name": "logs-cdc-consumer"}
docker compose logs cdc-consumer --tail=50
```

### Restart Debezium connector

```bash {"name": "restart-connector"}
curl -X POST http://localhost:8083/connectors/products-connector/restart
echo ""
curl -s http://localhost:8083/connectors/products-connector/status | jq
```

### Delete and recreate connector

```bash {"name": "recreate-connector"}
# Delete existing connector
curl -X DELETE http://localhost:8083/connectors/products-connector

sleep 2

# Recreate it
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d @infra/debezium/register-connector.json | jq
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Register connector | `curl -X POST localhost:8083/connectors -d @infra/debezium/register-connector.json` |
| View CDC events | `curl localhost:8001/events \| jq` |
| Search products | `curl "localhost:8002/search?q=term" \| jq` |
| Compare approaches | `curl localhost:8003/compare \| jq` |
| CDC lag | `curl localhost:8001/events/stats \| jq '.avg_lag_ms'` |
| Kafka UI | http://localhost:8080 |
| Grafana | http://localhost:3001 (admin/admin) |
