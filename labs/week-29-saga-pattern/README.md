# Lab 29: Saga Pattern for Distributed Transactions

In distributed systems, you cannot use traditional ACID transactions across service boundaries. The Saga pattern provides a way to maintain data consistency across multiple services by breaking a transaction into a sequence of local transactions, each with a compensating transaction for rollback.

## What You Will Learn

- How the Saga pattern coordinates distributed transactions
- The difference between choreography and orchestration approaches
- How compensating transactions enable rollback across services
- How to trace and visualize saga execution
- How to handle partial failures in distributed systems

## Architecture

```
                                    ┌─────────────────────────────┐
                                    │      Saga Orchestrator      │
                                    │  (Coordinates transaction)  │
                                    └──────────────┬──────────────┘
                                                   │
                    ┌──────────────────────────────┼──────────────────────────────┐
                    │                              │                              │
                    ▼                              ▼                              ▼
        ┌───────────────────┐         ┌───────────────────┐         ┌───────────────────┐
        │ Inventory Service │         │  Payment Service  │         │ Shipping Service  │
        │  reserve/release  │         │  process/refund   │         │    ship/cancel    │
        └───────────────────┘         └───────────────────┘         └───────────────────┘
```

### Order Processing Saga Flow

**Happy Path (Success):**
```
1. Reserve Inventory  ──►  2. Process Payment  ──►  3. Ship Order  ──►  COMPLETED
```

**Failure at Payment (Compensation):**
```
1. Reserve Inventory  ──►  2. Process Payment ✗  ──►  COMPENSATE
                                                           │
                                    Release Inventory  ◄───┘
```

**Failure at Shipping (Full Rollback):**
```
1. Reserve Inventory  ──►  2. Process Payment  ──►  3. Ship Order ✗  ──►  COMPENSATE
                                                                              │
                           Refund Payment  ◄──  Release Inventory  ◄─────────┘
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| Order Service | 8000 | API gateway for placing orders |
| Saga Orchestrator | 8001 | Coordinates the saga execution |
| Inventory Service | 8002 | Manages inventory reservations |
| Payment Service | 8003 | Processes payments |
| Shipping Service | 8004 | Handles order shipping |

## Observability Stack

| Service | URL | Purpose |
|---------|-----|---------|
| Jaeger | http://localhost:16686 | Distributed traces - see saga flow |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |
| Prometheus | http://localhost:9090 | Metrics |

## Prerequisites

- Docker and Docker Compose
- curl (for API testing)
- jq (for JSON formatting)

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

### 3. Place a Test Order

```bash
curl -s http://localhost:8000/demo/order | jq
```

---

## Lab Exercises

### Exercise 1: Successful Saga Execution

First, let's observe a successful saga where all steps complete.

**Place an order:**
```bash
curl -s -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "CUST-001",
    "items": [
      {"product_id": "PROD-001", "name": "Widget Pro", "quantity": 2, "price": 29.99},
      {"product_id": "PROD-002", "name": "Gadget Plus", "quantity": 1, "price": 49.99}
    ]
  }' | jq
```

**Expected Response:**
```json
{
  "order_id": "...",
  "saga_state": "completed",
  "completed_steps": ["reserve_inventory", "process_payment", "ship_order"],
  "compensation_completed": []
}
```

**View the trace in Jaeger:**
1. Open http://localhost:16686
2. Select "order-service" from the Service dropdown
3. Click "Find Traces"
4. Click on a trace to see the saga flow

**Questions:**
- How many spans are in the trace?
- What is the order of operations?
- How long did each step take?

---

### Exercise 2: Payment Failure and Inventory Compensation

Now let's inject a failure in the payment step and observe how the saga compensates.

**Inject payment failure (100% fail rate):**
```bash
curl -s -X POST http://localhost:8003/admin/fail-rate \
  -H "Content-Type: application/json" \
  -d '{"rate": 1.0}' | jq
```

**Place an order:**
```bash
curl -s -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "CUST-002",
    "items": [
      {"product_id": "PROD-001", "name": "Widget Pro", "quantity": 1, "price": 29.99}
    ]
  }' | jq
```

**Expected Response:**
```json
{
  "saga_state": "compensated",
  "completed_steps": ["reserve_inventory"],
  "failed_step": "process_payment",
  "compensation_completed": ["reserve_inventory"]
}
```

**Observe in Jaeger:**
- Find the trace for this order
- Notice the `reserve_inventory` span succeeded
- Notice the `process_payment` span failed
- Notice the `release_inventory` compensation span

**Check inventory state:**
```bash
curl -s http://localhost:8002/reservations | jq
```

Notice the reservation status changed from "reserved" to "released".

**Reset payment failure:**
```bash
curl -s -X POST http://localhost:8003/admin/fail-rate \
  -H "Content-Type: application/json" \
  -d '{"rate": 0.0}' | jq
```

---

### Exercise 3: Shipping Failure and Full Rollback

Now let's fail at the shipping step to see compensation of both payment and inventory.

**Inject shipping failure:**
```bash
curl -s -X POST http://localhost:8004/admin/fail-rate \
  -H "Content-Type: application/json" \
  -d '{"rate": 1.0}' | jq
```

**Place an order:**
```bash
curl -s -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "CUST-003",
    "items": [
      {"product_id": "PROD-003", "name": "Premium Widget", "quantity": 3, "price": 99.99}
    ]
  }' | jq
```

**Expected Response:**
```json
{
  "saga_state": "compensated",
  "completed_steps": ["reserve_inventory", "process_payment"],
  "failed_step": "ship_order",
  "compensation_completed": ["process_payment", "reserve_inventory"]
}
```

**Observe the full rollback in Jaeger:**
- `reserve_inventory` - success
- `process_payment` - success
- `ship_order` - failed
- `refund_payment` - compensation
- `release_inventory` - compensation

**Check payment transactions:**
```bash
curl -s http://localhost:8003/transactions | jq
```

Notice the transaction status is "refunded".

**Reset shipping failure:**
```bash
curl -s -X POST http://localhost:8004/admin/fail-rate \
  -H "Content-Type: application/json" \
  -d '{"rate": 0.0}' | jq
```

---

### Exercise 4: View Saga State Transitions in Traces

Let's generate several orders and analyze the traces.

**Generate 10 orders with 30% payment failure rate:**
```bash
# Set partial failure rate
curl -s -X POST http://localhost:8003/admin/fail-rate \
  -H "Content-Type: application/json" \
  -d '{"rate": 0.3}' | jq

# Generate orders
for i in {1..10}; do
  echo "Order $i:"
  curl -s http://localhost:8000/demo/order | jq '{saga_state, failed_step, completed_steps}'
  sleep 0.5
done

# Reset failure rate
curl -s -X POST http://localhost:8003/admin/fail-rate \
  -H "Content-Type: application/json" \
  -d '{"rate": 0.0}' | jq
```

**In Jaeger, compare successful vs compensated sagas:**
1. Find a completed saga trace
2. Find a compensated saga trace
3. Compare the span count and structure

**Questions:**
- How does the trace structure differ between success and failure?
- What additional spans appear during compensation?
- Can you identify the exact failure point from the trace?

---

### Exercise 5: Metrics and Monitoring

Open Grafana at http://localhost:3001 (admin/admin) and explore the Saga Pattern Dashboard.

**Generate some load:**
```bash
for i in {1..20}; do
  curl -s http://localhost:8000/demo/order > /dev/null &
done
wait
echo "Done generating load"
```

**Observe in Grafana:**
- Sagas Started vs Completed vs Compensated
- Step duration histograms
- Failure rates by step
- Compensation actions

**Query Prometheus directly:**
```bash
# Total sagas started
curl -s "http://localhost:9090/api/v1/query?query=sagas_started_total" | jq '.data.result[0].value[1]'

# Total compensations
curl -s "http://localhost:9090/api/v1/query?query=sagas_compensated_total" | jq '.data.result[0].value[1]'

# Success rate
curl -s "http://localhost:9090/api/v1/query?query=sagas_completed_total/sagas_started_total" | jq '.data.result[0].value[1]'
```

---

## Key Concepts

### Orchestration vs Choreography

This lab demonstrates the **Orchestration** approach:

| Aspect | Orchestration (This Lab) | Choreography |
|--------|--------------------------|--------------|
| Control | Central orchestrator | Decentralized, event-driven |
| Coupling | Services depend on orchestrator | Services depend on events |
| Visibility | Easy to trace flow | Harder to trace |
| Complexity | Orchestrator can become complex | Event routing can be complex |
| Failure handling | Centralized | Distributed |

### Compensating Transactions

Each saga step has a compensating transaction:

| Step | Action | Compensation |
|------|--------|--------------|
| Inventory | Reserve items | Release items |
| Payment | Charge customer | Refund customer |
| Shipping | Create shipment | Cancel shipment |

### Saga States

```
PENDING -> INVENTORY_RESERVED -> PAYMENT_PROCESSED -> SHIPPED -> COMPLETED
                                         │                │
                                         │                ▼
                                         │         COMPENSATING
                                         │                │
                                         ▼                ▼
                                   COMPENSATING -> COMPENSATED
```

---

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Check individual service health
```bash
curl -s http://localhost:8000/health | jq
curl -s http://localhost:8001/health | jq
curl -s http://localhost:8002/health | jq
curl -s http://localhost:8003/health | jq
curl -s http://localhost:8004/health | jq
```

### View saga state
```bash
curl -s http://localhost:8001/sagas | jq
```

### Clear all state (reset)
```bash
curl -s -X DELETE http://localhost:8001/sagas | jq
curl -s -X DELETE http://localhost:8002/reservations | jq
curl -s -X DELETE http://localhost:8003/transactions | jq
curl -s -X DELETE http://localhost:8004/shipments | jq
```

### Reset all failure rates
```bash
curl -s -X POST http://localhost:8002/admin/fail-rate -H "Content-Type: application/json" -d '{"rate": 0}'
curl -s -X POST http://localhost:8003/admin/fail-rate -H "Content-Type: application/json" -d '{"rate": 0}'
curl -s -X POST http://localhost:8004/admin/fail-rate -H "Content-Type: application/json" -d '{"rate": 0}'
```

---

## Cleanup

```bash
docker compose down -v
```

---

## Key Takeaways

1. **Sagas enable distributed transactions** - When you cannot use ACID transactions across services, sagas provide eventual consistency through compensation.

2. **Compensation is not always simple** - Some actions cannot be fully undone (e.g., sending an email). Design compensations carefully.

3. **Observability is critical** - Without tracing, debugging saga failures is extremely difficult. Always instrument your saga steps.

4. **Orchestration simplifies debugging** - Having a central coordinator makes it easier to understand the saga state and flow.

5. **Idempotency matters** - Saga steps and compensations must be idempotent to handle retries safely.

6. **Partial failures are normal** - Design your system to handle and recover from partial failures gracefully.

---

## Further Reading

- [Microservices Patterns: Sagas](https://microservices.io/patterns/data/saga.html)
- [DDIA Chapter 9: Consistency and Consensus](https://dataintensive.net/)
- [Saga Pattern in Practice](https://docs.microsoft.com/en-us/azure/architecture/reference-architectures/saga/saga)
