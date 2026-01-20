# Demo Runbook: Saga Pattern for Distributed Transactions

This runbook contains all commands for demonstrating the Saga pattern. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

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
echo "Order Service:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Saga Orchestrator:"
curl -s http://localhost:8001/health | jq
echo ""
echo "Inventory Service:"
curl -s http://localhost:8002/health | jq
echo ""
echo "Payment Service:"
curl -s http://localhost:8003/health | jq
echo ""
echo "Shipping Service:"
curl -s http://localhost:8004/health | jq
```

---

## Part 2: Show Observability URLs

### Display all URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Jaeger (Traces):  http://localhost:16686"
echo "  Grafana:          http://localhost:3001 (admin/admin)"
echo "  Prometheus:       http://localhost:9090"
echo "  Order Service:    http://localhost:8000"
```

---

## Part 3: Successful Saga Execution

### Place a demo order (success)

```bash {"name": "demo-order-success"}
echo "Placing a demo order..."
curl -s http://localhost:8000/demo/order | jq
```

### Place a custom order

```bash {"name": "custom-order"}
curl -s -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "CUST-DEMO-001",
    "items": [
      {"product_id": "PROD-001", "name": "Widget Pro", "quantity": 2, "price": 29.99},
      {"product_id": "PROD-002", "name": "Gadget Plus", "quantity": 1, "price": 49.99}
    ]
  }' | jq
```

### View saga state in orchestrator

```bash {"name": "view-sagas"}
curl -s http://localhost:8001/sagas | jq
```

---

## Part 4: Payment Failure Demonstration

### Inject 100% payment failure

```bash {"name": "inject-payment-failure"}
curl -s -X POST http://localhost:8003/admin/fail-rate \
  -H "Content-Type: application/json" \
  -d '{"rate": 1.0}' | jq
```

### Verify payment failure is set

```bash {"name": "verify-payment-failure"}
curl -s http://localhost:8003/admin/fail-rate | jq
```

### Place an order (will fail at payment)

```bash {"name": "order-payment-fail"}
curl -s -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "CUST-PAYMENT-FAIL",
    "items": [
      {"product_id": "PROD-001", "name": "Widget Pro", "quantity": 1, "price": 29.99}
    ]
  }' | jq
```

### Check inventory was released (compensated)

```bash {"name": "check-inventory-compensation"}
echo "Inventory reservations (should show released status):"
curl -s http://localhost:8002/reservations | jq '.reservations[-1]'
```

### Reset payment failure

```bash {"name": "reset-payment-failure"}
curl -s -X POST http://localhost:8003/admin/fail-rate \
  -H "Content-Type: application/json" \
  -d '{"rate": 0.0}' | jq
```

---

## Part 5: Shipping Failure Demonstration

### Inject 100% shipping failure

```bash {"name": "inject-shipping-failure"}
curl -s -X POST http://localhost:8004/admin/fail-rate \
  -H "Content-Type: application/json" \
  -d '{"rate": 1.0}' | jq
```

### Place an order (will fail at shipping)

```bash {"name": "order-shipping-fail"}
curl -s -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "CUST-SHIPPING-FAIL",
    "items": [
      {"product_id": "PROD-002", "name": "Gadget Plus", "quantity": 2, "price": 49.99}
    ]
  }' | jq
```

### Check payment was refunded

```bash {"name": "check-payment-refund"}
echo "Payment transactions (should show refunded status):"
curl -s http://localhost:8003/transactions | jq '.transactions[-1]'
```

### Check full compensation chain

```bash {"name": "check-full-compensation"}
echo "=== Latest Saga State ==="
curl -s http://localhost:8001/sagas | jq '.sagas[-1]'
echo ""
echo "=== Latest Inventory State ==="
curl -s http://localhost:8002/reservations | jq '.reservations[-1]'
echo ""
echo "=== Latest Payment State ==="
curl -s http://localhost:8003/transactions | jq '.transactions[-1]'
```

### Reset shipping failure

```bash {"name": "reset-shipping-failure"}
curl -s -X POST http://localhost:8004/admin/fail-rate \
  -H "Content-Type: application/json" \
  -d '{"rate": 0.0}' | jq
```

---

## Part 6: Mixed Failure Rate Testing

### Set 30% failure rate on payment

```bash {"name": "set-partial-failure"}
curl -s -X POST http://localhost:8003/admin/fail-rate \
  -H "Content-Type: application/json" \
  -d '{"rate": 0.3}' | jq
```

### Generate 10 orders and observe mix

```bash {"name": "generate-mixed-orders"}
echo "Generating 10 orders with 30% payment failure rate..."
echo ""
for i in {1..10}; do
  result=$(curl -s http://localhost:8000/demo/order)
  state=$(echo $result | jq -r '.saga_state')
  failed=$(echo $result | jq -r '.failed_step // "none"')
  echo "Order $i: state=$state, failed_step=$failed"
  sleep 0.5
done
```

### Check saga statistics

```bash {"name": "saga-statistics"}
echo "=== Saga Statistics ==="
total=$(curl -s http://localhost:8001/sagas | jq '.total')
completed=$(curl -s http://localhost:8001/sagas | jq '[.sagas[] | select(.state == "completed")] | length')
compensated=$(curl -s http://localhost:8001/sagas | jq '[.sagas[] | select(.state == "compensated")] | length')
echo "Total sagas: $total"
echo "Completed: $completed"
echo "Compensated: $compensated"
```

### Reset all failure rates

```bash {"name": "reset-all-failures"}
curl -s -X POST http://localhost:8002/admin/fail-rate -H "Content-Type: application/json" -d '{"rate": 0}'
curl -s -X POST http://localhost:8003/admin/fail-rate -H "Content-Type: application/json" -d '{"rate": 0}'
curl -s -X POST http://localhost:8004/admin/fail-rate -H "Content-Type: application/json" -d '{"rate": 0}'
echo "All failure rates reset to 0"
```

---

## Part 7: Load Testing

### Run k6 load test

```bash {"name": "load-test", "background": true}
docker compose run --rm lab29-k6 run /scripts/saga-load.js
```

### Run failure scenarios test

```bash {"name": "failure-scenarios-test"}
docker compose run --rm lab29-k6 run /scripts/failure-scenarios.js
```

### Quick load generation (inline)

```bash {"name": "quick-load"}
echo "Generating 20 orders in parallel..."
for i in {1..20}; do
  curl -s http://localhost:8000/demo/order > /dev/null &
done
wait
echo "Done. Check Grafana dashboard for metrics."
```

---

## Part 8: Metrics Exploration

### Query Prometheus for saga metrics

```bash {"name": "prometheus-metrics"}
echo "=== Saga Metrics from Prometheus ==="
echo ""
echo "Sagas Started:"
curl -s "http://localhost:9090/api/v1/query?query=sagas_started_total" | jq '.data.result[0].value[1]'
echo ""
echo "Sagas Completed:"
curl -s "http://localhost:9090/api/v1/query?query=sagas_completed_total" | jq '.data.result[0].value[1]'
echo ""
echo "Sagas Compensated:"
curl -s "http://localhost:9090/api/v1/query?query=sum(sagas_compensated_total)" | jq '.data.result[0].value[1]'
```

### Check step failure counts

```bash {"name": "step-failures"}
echo "=== Step Failure Counts ==="
curl -s "http://localhost:9090/api/v1/query?query=saga_step_failures_total" | jq '.data.result[] | {step: .metric.step, count: .value[1]}'
```

### Check compensation action counts

```bash {"name": "compensation-counts"}
echo "=== Compensation Action Counts ==="
curl -s "http://localhost:9090/api/v1/query?query=compensation_actions_total" | jq '.data.result[] | {step: .metric.step, count: .value[1]}'
```

---

## Part 9: State Inspection

### View all service states

```bash {"name": "all-states"}
echo "=== Saga Orchestrator State ==="
curl -s http://localhost:8001/sagas | jq '.total'
echo "sagas total"
echo ""
echo "=== Inventory Reservations ==="
curl -s http://localhost:8002/reservations | jq '.total'
echo "reservations total"
echo ""
echo "=== Payment Transactions ==="
curl -s http://localhost:8003/transactions | jq '.total'
echo "transactions total"
echo ""
echo "=== Shipments ==="
curl -s http://localhost:8004/shipments | jq '.total'
echo "shipments total"
```

### Clear all state (reset for demo)

```bash {"name": "clear-all-state"}
curl -s -X DELETE http://localhost:8001/sagas | jq
curl -s -X DELETE http://localhost:8002/reservations | jq
curl -s -X DELETE http://localhost:8003/transactions | jq
curl -s -X DELETE http://localhost:8004/shipments | jq
echo "All state cleared"
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

### Check orchestrator logs

```bash {"name": "logs-orchestrator"}
docker compose logs lab29-orchestrator --tail=50
```

### Check for failed containers

```bash {"name": "check-containers"}
docker compose ps -a
```

### Restart all services

```bash {"name": "restart-all"}
docker compose restart
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
| Place order | `curl -s http://localhost:8000/demo/order \| jq` |
| Inject payment failure | `curl -X POST localhost:8003/admin/fail-rate -d '{"rate":1.0}'` |
| Inject shipping failure | `curl -X POST localhost:8004/admin/fail-rate -d '{"rate":1.0}'` |
| Reset all failures | `for p in 8002 8003 8004; do curl -X POST localhost:$p/admin/fail-rate -d '{"rate":0}'; done` |
| View sagas | `curl -s localhost:8001/sagas \| jq` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
