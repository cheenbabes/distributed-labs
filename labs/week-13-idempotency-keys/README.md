# Lab 13: Stripe Idempotency Keys

You are building a payment system. A customer clicks "Pay" but their network is flaky. The request times out. They click again. Did they just get charged twice?

In this lab, you will implement the **idempotency key pattern** that Stripe uses to ensure exactly-once payment processing, even when clients retry requests.

## What You Will Learn

- Why network failures make "exactly-once" processing impossible without special patterns
- How idempotency keys work (client-generated ID, server-side deduplication)
- How to handle edge cases: concurrent requests, key conflicts, key expiration
- How to build idempotent APIs that are safe to retry

## The Problem

```
Client                           Server                          Database
  |                                |                                |
  |-- POST /payments ------------->|                                |
  |   (amount: $100)               |-- INSERT payment ------------->|
  |                                |                                |
  |        TIMEOUT!                |<-- OK ------------------------|
  |                                |                                |
  |   (no response received)       |                                |
  |                                |                                |
  |-- POST /payments ------------->|  (is this a retry or new?)     |
  |   (amount: $100)               |-- INSERT payment ------------->|
  |                                |                                |
  |<----- OK ---------------------|<-- OK ------------------------|
  |                                |                                |
  |   Customer charged $200!       |                                |
```

Without idempotency keys, the server cannot distinguish between:
- A retry of a timed-out request (should return cached response)
- A genuinely new request (should process normally)

## The Solution: Idempotency Keys

```
Client                           Server                          Database
  |                                |                                |
  |-- POST /payments ------------->|                                |
  |   Idempotency-Key: abc123      |                                |
  |   (amount: $100)               |-- Store key + request hash --->|
  |                                |-- INSERT payment ------------->|
  |                                |-- Store response with key ---->|
  |        TIMEOUT!                |<-- OK ------------------------|
  |                                |                                |
  |-- POST /payments ------------->|                                |
  |   Idempotency-Key: abc123      |-- Lookup key ----------------->|
  |   (amount: $100)               |<-- Found! Return cached -------|
  |                                |                                |
  |<----- OK (cached) ------------|                                |
  |                                |                                |
  |   Same response, no duplicate! |                                |
```

## Architecture

```
┌──────────────┐     ┌───────────────────────────────────────────┐
│              │     │              Payment API                   │
│   Client     │────▶│  ┌─────────────────────────────────────┐  │
│  (k6 tests)  │     │  │  Idempotency Key Handler            │  │
│              │     │  │  - Check if key exists              │  │
└──────────────┘     │  │  - Verify request hash matches      │  │
                     │  │  - Return cached or process new     │  │
                     │  └─────────────────────────────────────┘  │
                     │                    │                       │
                     │         ┌──────────┴──────────┐           │
                     │         ▼                     ▼           │
                     │  ┌─────────────┐    ┌────────────────┐   │
                     │  │   Redis     │    │   PostgreSQL   │   │
                     │  │  (locking)  │    │  (persistence) │   │
                     │  └─────────────┘    └────────────────┘   │
                     └───────────────────────────────────────────┘
```

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
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

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Payment API | http://localhost:8000 | Payment endpoint |
| API Docs | http://localhost:8000/docs | Swagger UI |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

---

## Lab Exercises

### Exercise 1: Make a Payment and Observe Idempotency Key Storage

First, let's make a payment with an idempotency key and see how it is stored.

```bash
# Generate a unique idempotency key
IDEM_KEY="payment-$(date +%s)-$(( RANDOM ))"
echo "Using idempotency key: $IDEM_KEY"

# Make a payment
curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEM_KEY" \
  -d '{"amount": 2500, "currency": "usd", "description": "Test payment"}' | jq
```

Now inspect the idempotency key record:

```bash
# Check the idempotency key state
curl -s http://localhost:8000/idempotency-keys/$IDEM_KEY | jq
```

**What to observe:**
- The key has a `status` of "complete"
- The `request_hash` is stored (used for conflict detection)
- The `expires_at` shows when the key can be reused

---

### Exercise 2: Retry the Same Payment (Cached Response)

Now let's simulate a retry - using the same idempotency key with the same request body.

```bash
# Use the SAME key and SAME body
curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEM_KEY" \
  -d '{"amount": 2500, "currency": "usd", "description": "Test payment"}' | jq
```

**What to observe:**
- The response has `"cached": true`
- The payment ID is the same as the first request
- The customer was NOT charged twice!
- The response is much faster (no processing time)

Compare the timing:

```bash
# First request (new payment)
NEW_KEY="payment-timing-$(date +%s)"
time curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $NEW_KEY" \
  -d '{"amount": 1000, "currency": "usd"}' > /dev/null

# Retry (cached)
time curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $NEW_KEY" \
  -d '{"amount": 1000, "currency": "usd"}' > /dev/null
```

---

### Exercise 3: Try to Reuse Key with Different Request Body (Conflict!)

What happens if someone tries to reuse an idempotency key with a different request?

```bash
# Try to use the SAME key with a DIFFERENT amount
curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEM_KEY" \
  -d '{"amount": 5000, "currency": "usd", "description": "Different amount!"}' | jq
```

**What to observe:**
- HTTP 409 Conflict response
- Error message explains the conflict
- The original payment is NOT affected

This protects against bugs where the wrong idempotency key is used.

---

### Exercise 4: Race Two Concurrent Requests with Same Key

What happens when two requests with the same idempotency key arrive simultaneously?

```bash
RACE_KEY="race-test-$(date +%s)"

# Send two concurrent requests
curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $RACE_KEY" \
  -d '{"amount": 3000, "currency": "usd"}' &

curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $RACE_KEY" \
  -d '{"amount": 3000, "currency": "usd"}' &

wait

echo "Done - check both responses for same payment ID"
```

**What to observe:**
- Only ONE payment is created
- One request processed normally, the other either waited or got cached response
- Both responses have the same payment ID

Check the metrics for concurrent waits:

```bash
curl -s http://localhost:8000/metrics | grep concurrent
```

---

### Exercise 5: Explore Key Expiration

Idempotency keys expire after a configurable time (default: 24 hours). Let's explore this.

```bash
# Check admin stats
curl -s http://localhost:8000/admin/stats | jq

# Inspect a specific key's expiration
curl -s http://localhost:8000/idempotency-keys/$IDEM_KEY | jq '.expires_at'
```

**Questions to consider:**
- Why do keys need to expire?
- What happens if a client retries after the key expires?
- How would you choose the right TTL for your use case?

---

### Exercise 6: The Danger of No Idempotency Key

Let's see what happens WITHOUT an idempotency key - this is the scary part!

```bash
# Make the SAME payment twice WITHOUT idempotency key
curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -d '{"amount": 1500, "currency": "usd", "description": "No key!"}' | jq '.id'

curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -d '{"amount": 1500, "currency": "usd", "description": "No key!"}' | jq '.id'
```

**What to observe:**
- Different payment IDs! The customer was charged TWICE!
- Check the server logs - there is a warning about missing idempotency key

---

### Exercise 7: Load Testing with k6

Run the comprehensive load test that simulates all scenarios:

```bash
docker compose run --rm lab13-k6 run /scripts/retry-test.js
```

This tests:
1. Normal payments with idempotency keys
2. Retry simulation (same key, same body)
3. Conflict simulation (same key, different body)
4. Concurrent requests with same key
5. Dangerous: payments without idempotency keys

Watch the dashboard: http://localhost:3001 (use the "Idempotency Keys Dashboard")

---

## Key Takeaways

### 1. Idempotency Keys Enable Safe Retries

Without them, clients cannot safely retry failed requests. With them, retrying is always safe.

### 2. The Key Must Be Client-Generated

The client knows whether this is a new request or a retry. Only the client can provide a meaningful idempotency key.

### 3. Request Body Hashing Prevents Misuse

Storing a hash of the request ensures the same key cannot be reused for different operations.

### 4. Distributed Locking Handles Concurrency

Redis (or similar) provides fast distributed locking to prevent race conditions when concurrent requests arrive.

### 5. Keys Must Expire

Old keys must be cleaned up, and clients should be able to reuse keys eventually. Stripe uses 24 hours.

### 6. Not Having a Key Should Be Logged/Alerted

In production, requests without idempotency keys for mutating operations should trigger warnings.

---

## Implementation Details

### How the Pattern Works

1. **Client sends request with `Idempotency-Key` header**
2. **Server checks if key exists:**
   - If key exists with same request hash: return cached response
   - If key exists with different hash: return 409 Conflict
   - If key exists but still processing: wait for completion
   - If key doesn't exist: acquire lock and process
3. **Server stores key with "processing" status**
4. **Server processes the request**
5. **Server updates key with response and "complete" status**
6. **Server releases lock**

### Database Schema

```sql
CREATE TABLE idempotency_keys (
    key VARCHAR(255) PRIMARY KEY,
    request_hash VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL,  -- 'processing', 'complete', 'failed'
    response_body JSONB,
    created_at TIMESTAMP,
    expires_at TIMESTAMP
);
```

### Best Practices

- Use UUIDs or similar for idempotency keys
- Include operation type in the key for clarity (e.g., `payment-{uuid}`)
- Set appropriate TTL based on your use case
- Always log when idempotency key is missing for mutating operations
- Consider making idempotency keys required for all mutating endpoints

---

## Cleanup

```bash
docker compose down -v
```

---

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Database connection issues
```bash
docker compose logs lab13-postgres
```

### Redis connection issues
```bash
docker compose logs lab13-redis
docker compose exec lab13-redis redis-cli ping
```

### Check payment-api logs
```bash
docker compose logs lab13-payment-api
```

---

## Further Reading

- [Stripe API Idempotency](https://stripe.com/docs/api/idempotent_requests)
- [Designing Data-Intensive Applications - Idempotence](https://dataintensive.net/)
- [Implementing Idempotent REST APIs](https://www.baeldung.com/java-idempotent-operations)

## Next Lab

[Lab 14: Saga Pattern](../week-14-saga-pattern/) - Coordinate distributed transactions across multiple services.
