# Demo Runbook: Exactly-Once Processing

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

### Wait for Kafka topics to be created

```bash {"name": "wait-kafka"}
echo "Waiting for Kafka setup..."
docker compose logs -f kafka-setup 2>&1 | head -20
echo ""
echo "Waiting for services to stabilize..."
sleep 15
docker compose ps
```

### Verify all services are healthy

```bash {"name": "verify-services"}
echo "Producer:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Consumer:"
curl -s http://localhost:8001/health | jq
echo ""
echo "API:"
curl -s http://localhost:8003/health | jq
```

---

## Part 2: Understanding Delivery Semantics

### View semantic comparison

```bash {"name": "show-semantics"}
curl -s http://localhost:8003/compare/semantics | jq
```

### Check current configuration

```bash {"name": "show-config"}
echo "=== Producer Configuration ==="
curl -s http://localhost:8000/config | jq
echo ""
echo "=== Consumer Configuration ==="
curl -s http://localhost:8001/config | jq
```

### Show URLs for observability

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Producer API:  http://localhost:8000"
echo "  Consumer API:  http://localhost:8001"
echo "  Query API:     http://localhost:8003"
echo "  Jaeger:        http://localhost:16686"
echo "  Grafana:       http://localhost:3001 (admin/admin)"
echo "  Prometheus:    http://localhost:9090"
```

---

## Part 3: Exactly-Once in Action

### Send a single order

```bash {"name": "send-single-order"}
curl -s -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "demo-customer-001",
    "items": [
      {"name": "Widget Pro", "quantity": 2, "price": 49.99}
    ],
    "total_amount": 99.98
  }' | jq
```

### Check processing statistics

```bash {"name": "check-stats"}
curl -s http://localhost:8003/stats | jq
```

### Simulate duplicate messages

```bash {"name": "simulate-duplicates"}
echo "Sending same order 3 times..."
curl -s -X POST http://localhost:8000/simulate/duplicate \
  -H "Content-Type: application/json" \
  -d '{
    "order_id": "DUPLICATE-TEST-001",
    "customer_id": "dup-customer",
    "items": [{"name": "Duplicate Item", "quantity": 1, "price": 25.00}],
    "total_amount": 25.00
  }' | jq
```

### Verify idempotent processing

```bash {"name": "verify-idempotency"}
echo "=== Duplicate Statistics ==="
curl -s http://localhost:8003/stats/duplicates | jq
echo ""
echo "=== Consumer Logs (looking for duplicates) ==="
docker compose logs consumer --tail=30 | grep -i duplicate || echo "No duplicate logs yet"
```

---

## Part 4: Transactional Batch Processing

### Send a batch of orders atomically

```bash {"name": "send-batch"}
curl -s -X POST http://localhost:8000/orders/batch \
  -H "Content-Type: application/json" \
  -d '{
    "orders": [
      {"customer_id": "batch-001", "items": [{"name": "Item A", "quantity": 1, "price": 15.00}], "total_amount": 15.00},
      {"customer_id": "batch-002", "items": [{"name": "Item B", "quantity": 2, "price": 20.00}], "total_amount": 40.00},
      {"customer_id": "batch-003", "items": [{"name": "Item C", "quantity": 3, "price": 10.00}], "total_amount": 30.00},
      {"customer_id": "batch-004", "items": [{"name": "Item D", "quantity": 1, "price": 50.00}], "total_amount": 50.00}
    ]
  }' | jq
```

### Check the orders

```bash {"name": "check-orders"}
curl -s "http://localhost:8003/orders?limit=10" | jq
```

---

## Part 5: Consumer Crash Recovery Demo

### Reset state for crash test

```bash {"name": "reset-for-crash-test"}
echo "Resetting all state..."
curl -s -X POST http://localhost:8003/reset | jq
curl -s -X POST http://localhost:8001/reset-stats | jq
```

### Configure consumer to crash after 5 messages

```bash {"name": "configure-crash"}
curl -s -X POST http://localhost:8001/config \
  -H "Content-Type: application/json" \
  -d '{"crash_after_messages": 5}' | jq
```

### Send 10 orders (consumer will crash mid-way)

```bash {"name": "send-crash-test-orders"}
echo "Sending 10 orders (consumer will crash after 5)..."
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/orders \
    -H "Content-Type: application/json" \
    -d "{\"customer_id\": \"crash-test-$i\", \"items\": [{\"name\": \"Item $i\", \"quantity\": 1, \"price\": 10.00}], \"total_amount\": 10.00}" > /dev/null
  echo "Sent order $i"
  sleep 0.3
done
echo "Done sending orders"
```

### Watch consumer crash in logs

```bash {"name": "watch-crash"}
docker compose logs consumer --tail=40 | grep -E "(crash|processed|duplicate)" || echo "Check logs manually"
```

### Restart consumer

```bash {"name": "restart-consumer"}
echo "Restarting consumer..."
docker compose up -d consumer
sleep 5
docker compose ps consumer
```

### Verify exactly-once processing after crash

```bash {"name": "verify-crash-recovery"}
echo "=== Statistics after crash recovery ==="
curl -s http://localhost:8003/stats | jq
echo ""
echo "=== Expected: exactly 10 unique orders (no duplicates, no loss) ==="
curl -s http://localhost:8003/stats/duplicates | jq
```

---

## Part 6: Compare At-Least-Once Behavior

### Reset state

```bash {"name": "reset-for-atleastonce"}
curl -s -X POST http://localhost:8003/reset | jq
```

### Disable idempotency to simulate at-least-once

```bash {"name": "disable-idempotency"}
curl -s -X POST http://localhost:8001/config \
  -H "Content-Type: application/json" \
  -d '{"enable_idempotency": false, "crash_after_messages": 5}' | jq
```

### Repeat crash test

```bash {"name": "atleastonce-crash-test"}
echo "Sending 10 orders without idempotency..."
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/orders \
    -H "Content-Type: application/json" \
    -d "{\"customer_id\": \"atleast-$i\", \"items\": [{\"name\": \"Item $i\", \"quantity\": 1, \"price\": 10.00}], \"total_amount\": 10.00}" > /dev/null
  echo "Sent order $i"
  sleep 0.3
done
```

### Restart and check for duplicates

```bash {"name": "check-atleastonce-duplicates"}
docker compose up -d consumer
sleep 5
echo "=== Statistics (expect duplicates!) ==="
curl -s http://localhost:8003/stats | jq
```

---

## Part 7: Performance Comparison

### Reset and enable exactly-once

```bash {"name": "setup-perf-test"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"delivery_mode": "exactly-once", "enable_transactions": true}' | jq

curl -s -X POST http://localhost:8001/config \
  -H "Content-Type: application/json" \
  -d '{"enable_idempotency": true, "crash_after_messages": 0}' | jq
```

### Measure exactly-once latency

```bash {"name": "measure-exactlyonce"}
echo "=== Exactly-Once Latency ==="
for i in {1..5}; do
  result=$(curl -s -X POST http://localhost:8000/orders \
    -H "Content-Type: application/json" \
    -d '{"customer_id": "perf", "items": [{"name": "Test", "quantity": 1, "price": 10}], "total_amount": 10}')
  latency=$(echo $result | jq -r '.latency_ms')
  echo "  Request $i: ${latency}ms"
done
```

### Switch to at-most-once and measure

```bash {"name": "measure-atmostonce"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"delivery_mode": "at-most-once"}' > /dev/null

echo "=== At-Most-Once Latency ==="
for i in {1..5}; do
  result=$(curl -s -X POST http://localhost:8000/orders \
    -H "Content-Type: application/json" \
    -d '{"customer_id": "perf", "items": [{"name": "Test", "quantity": 1, "price": 10}], "total_amount": 10}')
  latency=$(echo $result | jq -r '.latency_ms')
  echo "  Request $i: ${latency}ms"
done
```

---

## Part 8: Load Testing

### Reset to exactly-once for load test

```bash {"name": "setup-loadtest"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"delivery_mode": "exactly-once", "enable_transactions": true}' | jq

curl -s -X POST http://localhost:8001/config \
  -H "Content-Type: application/json" \
  -d '{"enable_idempotency": true, "crash_after_messages": 0, "failure_rate": 0}' | jq
```

### Run basic load test

```bash {"name": "run-loadtest"}
docker compose run --rm k6 run /scripts/basic.js
```

### Check final statistics

```bash {"name": "loadtest-stats"}
curl -s http://localhost:8003/stats | jq
curl -s http://localhost:8003/stats/duplicates | jq
```

---

## Cleanup

### Stop all services and remove volumes

```bash {"name": "cleanup"}
docker compose down -v
echo "Lab cleaned up successfully"
```

---

## Troubleshooting Commands

### Check all logs

```bash {"name": "logs-all"}
docker compose logs --tail=100
```

### Check Kafka logs

```bash {"name": "logs-kafka"}
docker compose logs kafka --tail=50
```

### Check consumer logs

```bash {"name": "logs-consumer"}
docker compose logs consumer --tail=50
```

### Check producer logs

```bash {"name": "logs-producer"}
docker compose logs producer --tail=50
```

### Check Kafka topics

```bash {"name": "check-topics"}
curl -s http://localhost:8003/kafka/topics | jq
```

### Check consumer lag

```bash {"name": "check-lag"}
curl -s http://localhost:8003/kafka/lag | jq
```

### Check database directly

```bash {"name": "check-db"}
docker compose exec postgres psql -U lab -d exactly_once -c "SELECT COUNT(*) as total_messages FROM processed_messages; SELECT COUNT(*) as total_orders FROM orders;"
```

### Restart all services

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
| Check stats | `curl localhost:8003/stats` |
| Send order | `curl -X POST localhost:8000/orders -H "Content-Type: application/json" -d '{"customer_id":"x","items":[{"name":"y","quantity":1,"price":10}],"total_amount":10}'` |
| Enable exactly-once | `curl -X POST localhost:8000/config -H "Content-Type: application/json" -d '{"delivery_mode":"exactly-once"}'` |
| Enable at-most-once | `curl -X POST localhost:8000/config -H "Content-Type: application/json" -d '{"delivery_mode":"at-most-once"}'` |
| Toggle idempotency | `curl -X POST localhost:8001/config -H "Content-Type: application/json" -d '{"enable_idempotency":true}'` |
| Reset data | `curl -X POST localhost:8003/reset` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
