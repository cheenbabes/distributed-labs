# Lab 21: Reproducing Race Conditions

Race conditions are notoriously difficult to debug because they are non-deterministic - they may occur once in a million requests, or only under specific timing conditions. In this lab, you will learn techniques to reliably reproduce race conditions, making them deterministic and debuggable.

## What You Will Learn

- Why race conditions are hard to reproduce (timing-dependent behavior)
- The "check-then-act" pattern and why it is vulnerable
- How to use debug delays to widen the race window
- Barrier synchronization for guaranteed reproduction
- How to detect race conditions through metrics and logs
- The proper fix using database transactions/locks
- Comparing traces of buggy vs. fixed code

## The Scenario: Double-Spend in a Bank

We have a bank API that handles money transfers between accounts. The code contains a classic **check-then-act** race condition:

```python
# VULNERABLE CODE (simplified)
def transfer(from_account, to_account, amount):
    balance = read_balance(from_account)      # Step 1: Check
    if balance >= amount:                      # Step 2: Decide
        deduct(from_account, amount)           # Step 3: Act
        credit(to_account, amount)
```

The bug: Between reading the balance and deducting, another concurrent transfer can also read the same balance. Both transfers see sufficient funds and proceed, resulting in a **double-spend**.

```
Timeline (Race Condition):

Thread A                          Thread B
--------                          --------
read balance ($1000)
                                  read balance ($1000)  <- Same value!
check: $1000 >= $800? YES
                                  check: $1000 >= $800? YES
deduct $800 -> balance = $200
                                  deduct $800 -> balance = -$600  <- NEGATIVE!
```

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │                                         │
     Concurrent     │              Bank API                   │
     Requests ─────►│  ┌──────────────────────────────────┐  │
        │           │  │  Check-then-Act Vulnerability    │  │
        │           │  │                                  │  │
        ▼           │  │  1. Read balance                 │  │
   ┌─────────┐      │  │  2. Check if sufficient          │  │
   │ Request │      │  │  3. Deduct amount       ← RACE!  │  │
   │    A    │─────►│  │  4. Credit destination           │  │
   └─────────┘      │  └──────────────────────────────────┘  │
                    │                                         │
   ┌─────────┐      │           In-Memory DB                  │
   │ Request │─────►│     ┌──────────────────────┐           │
   │    B    │      │     │ alice: $1000         │           │
   └─────────┘      │     │ bob:   $500          │           │
                    │     │ charlie: $200        │           │
                    │     └──────────────────────┘           │
                    │                                         │
                    └─────────────────────────────────────────┘
```

## Prerequisites

- Docker and Docker Compose
- curl (for API testing)
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

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Bank API | http://localhost:8000 | API and health |
| Bank API Docs | http://localhost:8000/docs | Swagger UI |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

---

## Lab Exercises

### Exercise 1: Normal Operation - Race Rarely Visible

First, let's observe normal behavior where the race condition is unlikely to manifest.

```bash
# Check initial balances
curl -s http://localhost:8000/accounts | jq

# Make a single transfer
curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "bob", "amount": 100}' | jq

# Check balances after transfer
curl -s http://localhost:8000/accounts | jq
```

Now try concurrent requests manually:

```bash
# Two transfers in parallel (race unlikely to occur)
curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "bob", "amount": 100}' &
curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "charlie", "amount": 100}' &
wait
```

**Observation:** The race condition rarely triggers because the operations complete too quickly for the timing to overlap.

---

### Exercise 2: Enable Debug Delays - Widen the Race Window

The race window is the time between "check" and "act". By adding artificial delays, we widen this window and increase the probability of the race occurring.

```bash
# Reset accounts to initial state
curl -s -X POST http://localhost:8000/reset | jq

# Enable 200ms debug delay
curl -s -X POST "http://localhost:8000/admin/delay?enabled=true&ms=200" | jq

# Verify settings
curl -s http://localhost:8000/admin/settings | jq
```

Now try concurrent large transfers:

```bash
# Both try to transfer $800 from Alice's $1000 account
# With the delay, both are likely to read balance before either deducts

curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "bob", "amount": 800}' &

curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "charlie", "amount": 800}' &

wait

# Check Alice's balance - it should be negative!
curl -s http://localhost:8000/accounts/alice | jq
```

**Expected:** Alice's balance goes negative ($1000 - $800 - $800 = -$600), indicating a double-spend.

---

### Exercise 3: Barrier Synchronization - Guarantee the Race

Debug delays increase probability but do not guarantee reproduction. For deterministic testing, use barrier synchronization - a mechanism that forces concurrent requests to execute at exactly the same moment.

```bash
# Reset accounts
curl -s -X POST http://localhost:8000/reset | jq

# Enable barrier with count=2 (waits for 2 requests before releasing)
curl -s -X POST "http://localhost:8000/admin/barrier?enabled=true&count=2" | jq

# Also enable delay for maximum effect
curl -s -X POST "http://localhost:8000/admin/delay?enabled=true&ms=200" | jq
```

Now the barrier will hold requests until 2 arrive, then release them simultaneously:

```bash
# Send two transfer requests - they will wait at the barrier
echo "Sending two requests (they will wait at barrier)..."

curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "bob", "amount": 800}' &

curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "charlie", "amount": 800}' &

wait
echo "Both transfers complete!"

# Check result
curl -s http://localhost:8000/accounts | jq
```

**Expected:** Race condition is guaranteed - Alice's balance will be negative.

Watch the logs to see the barrier in action:

```bash
docker compose logs lab21-bank-api --tail=50
```

---

### Exercise 4: Observe the Double-Spend in Metrics

Check Prometheus metrics that track race conditions:

```bash
# Get metrics
curl -s http://localhost:8000/metrics | grep -E "race_condition|double_spend|transfers"
```

Key metrics:
- `race_condition_hits_total` - Number of times negative balance was detected
- `double_spend_detected_total` - Double-spend events
- `transfers_total` - Transfer attempts by status

Open Grafana (http://localhost:3001, admin/admin) and look at the "Race Conditions Lab" dashboard to visualize:
- Account balances going negative
- Race condition counter increasing
- Transfer rate and latency

---

### Exercise 5: Apply the Fix - Use Transactions/Locks

Enable safe mode, which uses proper locking:

```bash
# Reset accounts
curl -s -X POST http://localhost:8000/reset | jq

# Keep barrier and delay enabled (to prove they don't matter now)
curl -s -X POST "http://localhost:8000/admin/barrier?enabled=true&count=2" | jq
curl -s -X POST "http://localhost:8000/admin/delay?enabled=true&ms=200" | jq

# ENABLE SAFE MODE (uses locks)
curl -s -X POST "http://localhost:8000/admin/transactions?enabled=true" | jq

# Verify settings
curl -s http://localhost:8000/admin/settings | jq
```

Now repeat the concurrent transfers:

```bash
# Same test, but with locks enabled
curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "bob", "amount": 800}' &

curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "charlie", "amount": 800}' &

wait

# Check Alice's balance - should NOT be negative
curl -s http://localhost:8000/accounts | jq
```

**Expected:** One transfer succeeds ($800), one fails (insufficient funds). Alice's balance is $200 (not negative).

---

### Exercise 6: Compare Traces - Buggy vs Fixed

Generate traces for both scenarios and compare in Jaeger.

```bash
# Disable safe mode (buggy)
curl -s -X POST "http://localhost:8000/admin/transactions?enabled=false" | jq
curl -s -X POST http://localhost:8000/reset | jq

# Generate buggy traces
for i in {1..3}; do
  curl -s -X POST http://localhost:8000/transfer \
    -H "Content-Type: application/json" \
    -d '{"from_account": "alice", "to_account": "bob", "amount": 800}' &
  curl -s -X POST http://localhost:8000/transfer \
    -H "Content-Type: application/json" \
    -d '{"from_account": "alice", "to_account": "charlie", "amount": 800}' &
  wait
  curl -s -X POST http://localhost:8000/reset > /dev/null
done

# Enable safe mode (fixed)
curl -s -X POST "http://localhost:8000/admin/transactions?enabled=true" | jq
curl -s -X POST http://localhost:8000/reset | jq

# Generate fixed traces
for i in {1..3}; do
  curl -s -X POST http://localhost:8000/transfer \
    -H "Content-Type: application/json" \
    -d '{"from_account": "alice", "to_account": "bob", "amount": 800}' &
  curl -s -X POST http://localhost:8000/transfer \
    -H "Content-Type: application/json" \
    -d '{"from_account": "alice", "to_account": "charlie", "amount": 800}' &
  wait
  curl -s -X POST http://localhost:8000/reset > /dev/null
done
```

Open Jaeger (http://localhost:16686) and:
1. Select "bank-api" service
2. Search for "transfer" operation
3. Look for spans with `race_condition.detected=true` attribute
4. Compare timing of concurrent requests
5. Notice how the fixed version serializes access

---

### Exercise 7: Load Test to Quantify

Run k6 load tests to see race condition frequency:

```bash
# Reset and configure
curl -s -X POST http://localhost:8000/reset | jq
curl -s -X POST "http://localhost:8000/admin/transactions?enabled=false" | jq
curl -s -X POST "http://localhost:8000/admin/delay?enabled=true&ms=50" | jq
curl -s -X POST "http://localhost:8000/admin/barrier?enabled=false" | jq

# Run load test
docker compose run --rm lab21-k6 run /scripts/basic.js
```

For the guaranteed reproduction test:

```bash
curl -s -X POST http://localhost:8000/reset | jq
docker compose run --rm lab21-k6 run /scripts/barrier-test.js
```

---

## Key Takeaways

1. **Race conditions are timing-dependent** - They may occur 1 in a million times, making them hard to reproduce.

2. **Debug delays widen the race window** - Artificial delays increase the probability of triggering the race.

3. **Barrier synchronization guarantees reproduction** - By forcing concurrent operations to execute simultaneously, we can deterministically trigger race conditions.

4. **Metrics are essential** - Counters for race condition hits and double-spends help detect issues in production.

5. **The fix is serialization** - Locks, transactions, or other synchronization primitives ensure atomic check-then-act.

6. **Traces show the story** - Distributed tracing reveals the interleaving that caused the race.

## The Fix Explained

The vulnerable code reads the balance, checks it, then updates it - without holding a lock:

```python
# VULNERABLE
balance = read_balance(account)  # Others can read same balance
if balance >= amount:
    deduct(account, amount)       # Race! Balance may have changed
```

The fix acquires a lock before the entire operation:

```python
# SAFE
with account.lock:
    balance = read_balance(account)  # Exclusive access
    if balance >= amount:
        deduct(account, amount)       # Still exclusive
```

In real databases, this is achieved through:
- `SELECT ... FOR UPDATE` (pessimistic locking)
- Database transactions with proper isolation levels
- Optimistic locking with version checks

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Race condition not triggering
- Enable debug delays: `curl -X POST "localhost:8000/admin/delay?enabled=true&ms=200"`
- Enable barrier: `curl -X POST "localhost:8000/admin/barrier?enabled=true&count=2"`
- Ensure both requests use the same source account

### Barrier timing out
- Make sure you send exactly `barrier_count` concurrent requests
- Check logs: `docker compose logs lab21-bank-api`

### Metrics not updating
- Wait a few seconds for Prometheus to scrape
- Check target status in Prometheus: http://localhost:9090/targets

## Next Steps

- Try implementing optimistic locking (version field that fails if changed)
- Explore database isolation levels (READ COMMITTED vs SERIALIZABLE)
- Add retry logic for failed transfers due to lock contention
