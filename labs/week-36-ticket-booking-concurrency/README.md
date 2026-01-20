# Lab 36: Ticket Booking Concurrency

A classic distributed systems interview problem: how do you prevent double-booking of seats? In this lab, you'll explore race conditions in ticket booking and compare different locking strategies.

## What You'll Learn

- Why concurrent access causes double-booking bugs
- How pessimistic locking (SELECT FOR UPDATE) prevents race conditions
- How optimistic locking (version columns) works with retry logic
- How distributed locks (Redis) coordinate across service instances
- Trade-offs between different locking strategies

## Architecture

```
┌──────────────┐     ┌─────────────────────┐     ┌────────────┐
│   Clients    │────▶│   Booking Service   │────▶│  PostgreSQL │
│  (k6 load)   │     │                     │     │   (seats)   │
└──────────────┘     │  Locking Strategies:│     └────────────┘
                     │  - none             │           │
                     │  - pessimistic      │     ┌────────────┐
                     │  - optimistic       │────▶│   Redis    │
                     │  - distributed      │     │  (locks)   │
                     └─────────────────────┘     └────────────┘
```

### The Problem: Race Condition in Booking

Without locking, two requests can both see a seat as "available" and both succeed in booking it:

```
Time    Thread 1                    Thread 2                    Database
────────────────────────────────────────────────────────────────────────
T1      SELECT seat (available)                                 seat = available
T2                                  SELECT seat (available)     seat = available
T3      UPDATE seat = booked                                    seat = booked by T1
T4                                  UPDATE seat = booked        seat = booked by T2 (DOUBLE BOOKING!)
```

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- k6 (optional, for load testing)

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
| Booking API | http://localhost:8000 | Ticket booking service |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Observe Double-Booking Bug (No Locking)

First, let's see the problem. With no locking, concurrent requests cause double bookings.

```bash
# Check current strategy (should be "none")
curl -s http://localhost:8000/admin/strategy | jq

# Reset the event to start fresh
curl -s -X POST "http://localhost:8000/admin/reset?event_id=1" | jq

# Check available seats
curl -s "http://localhost:8000/events/1" | jq '.seats.available'
```

Now simulate concurrent booking attempts for the same seat:

```bash
# Fire 10 concurrent requests for seat A1
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/book \
    -H "Content-Type: application/json" \
    -d "{\"event_id\": 1, \"seat_number\": \"A1\", \"customer_id\": \"customer_$i\"}" &
done
wait

# Check for double bookings
curl -s http://localhost:8000/admin/double-bookings | jq
```

**Expected:** Multiple customers booked the same seat!

---

### Exercise 2: Fix with Pessimistic Locking

Pessimistic locking uses `SELECT ... FOR UPDATE` to lock the row during the transaction.

```bash
# Switch to pessimistic locking
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "pessimistic"}' | jq

# Reset the event
curl -s -X POST "http://localhost:8000/admin/reset?event_id=1" | jq

# Try the same concurrent booking test
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/book \
    -H "Content-Type: application/json" \
    -d "{\"event_id\": 1, \"seat_number\": \"A1\", \"customer_id\": \"customer_$i\"}" &
done
wait

# Check for double bookings
curl -s http://localhost:8000/admin/double-bookings | jq
```

**Expected:** Zero double bookings! Only one customer got the seat.

**How it works:**
```sql
-- First request acquires lock
SELECT * FROM seats WHERE seat_number = 'A1' FOR UPDATE;
-- Other requests WAIT here until lock is released
UPDATE seats SET is_booked = TRUE WHERE id = ...;
-- Lock released, next request proceeds
```

---

### Exercise 3: Try Optimistic Locking

Optimistic locking uses a version column and retries on conflict.

```bash
# Switch to optimistic locking
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "optimistic"}' | jq

# Reset the event
curl -s -X POST "http://localhost:8000/admin/reset?event_id=1" | jq

# Try concurrent bookings
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/book \
    -H "Content-Type: application/json" \
    -d "{\"event_id\": 1, \"seat_number\": \"A1\", \"customer_id\": \"customer_$i\"}" &
done
wait

# Check results
curl -s http://localhost:8000/admin/double-bookings | jq
curl -s http://localhost:8000/admin/stats | jq
```

**Expected:** Zero double bookings, but you may see some "failed" bookings due to retries exhausted.

**How it works:**
```sql
-- Read with version
SELECT id, version FROM seats WHERE seat_number = 'A1';
-- version = 1

-- Update with version check (atomic compare-and-set)
UPDATE seats SET is_booked = TRUE, version = version + 1
WHERE id = ... AND version = 1;
-- If version changed, 0 rows updated -> retry!
```

---

### Exercise 4: Distributed Locking with Redis

For horizontally scaled services, use a distributed lock.

```bash
# Switch to distributed locking
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "distributed"}' | jq

# Reset the event
curl -s -X POST "http://localhost:8000/admin/reset?event_id=1" | jq

# Try concurrent bookings
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/book \
    -H "Content-Type: application/json" \
    -d "{\"event_id\": 1, \"seat_number\": \"A1\", \"customer_id\": \"customer_$i\"}" &
done
wait

# Check results
curl -s http://localhost:8000/admin/double-bookings | jq
```

**Expected:** Zero double bookings!

**How it works:**
```
1. Try to SET lock_key NX EX 5  (set if not exists, expire in 5s)
2. If acquired: proceed with booking
3. If not acquired: return "could not acquire lock"
4. After booking: DELETE lock_key (only if we own it)
```

---

### Exercise 5: High Concurrency Load Test

Run the k6 load test to stress-test each strategy:

```bash
# Test with no locking (expect double bookings)
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "none"}' | jq
curl -s -X POST "http://localhost:8000/admin/reset?event_id=1"

docker compose run --rm lab36-k6 run /scripts/concurrent-booking.js

# Check damage
curl -s http://localhost:8000/admin/double-bookings | jq
```

Now test with pessimistic locking:

```bash
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "pessimistic"}' | jq
curl -s -X POST "http://localhost:8000/admin/reset?event_id=1"

docker compose run --rm lab36-k6 run /scripts/concurrent-booking.js

curl -s http://localhost:8000/admin/double-bookings | jq
```

---

### Exercise 6: Compare Latency and Throughput

Watch Grafana (http://localhost:3001) while testing different strategies:

1. **No Locking**: Fast but broken - low latency, high double bookings
2. **Pessimistic**: Correct but blocking - higher latency under contention
3. **Optimistic**: Correct with retries - variable latency, retry overhead
4. **Distributed**: Correct across instances - network overhead for Redis

Questions to investigate:
- Which strategy has the lowest p50 latency?
- Which strategy has the highest p99 latency?
- What happens to lock contentions under high load?

---

## Strategy Comparison

| Strategy | Double Bookings | Latency | Throughput | Use Case |
|----------|-----------------|---------|------------|----------|
| None | Yes | Low | High | Never use for critical resources |
| Pessimistic | No | Medium-High | Medium | High contention, short transactions |
| Optimistic | No | Variable | High | Low contention, read-heavy workloads |
| Distributed | No | Medium | Medium | Horizontally scaled services |

## Key Takeaways

1. **Race conditions are real** - Without proper locking, concurrent writes corrupt data

2. **Pessimistic locking is simple** - `SELECT FOR UPDATE` just works, but blocks other transactions

3. **Optimistic locking scales better** - No locks held during reads, but requires retry logic

4. **Distributed locks are necessary for scaling** - When you have multiple service instances, database locks aren't enough

5. **Choose based on your workload**:
   - High contention + short transactions = Pessimistic
   - Low contention + long transactions = Optimistic
   - Multiple service instances = Distributed

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Database connection issues
```bash
docker compose logs lab36-postgres
docker compose exec lab36-postgres psql -U booking -d tickets -c "SELECT * FROM seats LIMIT 5;"
```

### Redis connection issues
```bash
docker compose exec lab36-redis redis-cli ping
```

### Check booking service logs
```bash
docker compose logs lab36-booking-service --tail=50
```

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/events/{id}` | GET | Get event with seat availability |
| `/events/{id}/seats` | GET | Get all seats (add `?available_only=true`) |
| `/book` | POST | Book a seat `{"event_id": 1, "seat_number": "A1", "customer_id": "..."}` |
| `/admin/strategy` | GET/POST | Get or set locking strategy |
| `/admin/reset` | POST | Reset event (clear all bookings) |
| `/admin/double-bookings` | GET | Check for double bookings |
| `/admin/stats` | GET | Get booking statistics |
