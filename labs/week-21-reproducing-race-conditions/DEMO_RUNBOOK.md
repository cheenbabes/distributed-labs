# Demo Runbook: Reproducing Race Conditions

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

### Verify Bank API is responding

```bash {"name": "verify-api"}
echo "Bank API Health:"
curl -s http://localhost:8000/health | jq

echo ""
echo "Initial Account Balances:"
curl -s http://localhost:8000/accounts | jq
```

---

## Part 2: Normal Operation (Race Unlikely)

### Show the vulnerable code pattern

```bash {"name": "show-concept"}
echo "=== THE VULNERABLE PATTERN ==="
echo ""
echo "  1. Read balance"
echo "  2. Check if sufficient"
echo "  3. Deduct amount"
echo ""
echo "The race: Between steps 1-2 and step 3, another"
echo "thread can also read the same balance!"
echo ""
echo "Alice starts with \$1000"
echo "Two transfers of \$800 each = \$1600 total"
echo "If both succeed, that is double-spending!"
```

### Make a single normal transfer

```bash {"name": "single-transfer"}
echo "Single transfer of \$100 from Alice to Bob:"
curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "bob", "amount": 100}' | jq

echo ""
echo "Account balances after transfer:"
curl -s http://localhost:8000/accounts | jq
```

### Try concurrent transfers without delays (race unlikely)

```bash {"name": "concurrent-no-delay"}
echo "Resetting accounts..."
curl -s -X POST http://localhost:8000/reset | jq

echo ""
echo "Sending two \$800 transfers concurrently (no delays)..."
curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "bob", "amount": 800}' &
PID1=$!

curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "charlie", "amount": 800}' &
PID2=$!

wait $PID1 $PID2

echo ""
echo "Account balances (race probably did NOT occur):"
curl -s http://localhost:8000/accounts | jq
```

---

## Part 3: Widen the Race Window with Debug Delays

### Enable debug delays

```bash {"name": "enable-delay"}
echo "Enabling 200ms debug delay to widen race window..."
curl -s -X POST "http://localhost:8000/admin/delay?enabled=true&ms=200" | jq

echo ""
echo "Current settings:"
curl -s http://localhost:8000/admin/settings | jq
```

### Trigger the race condition with delays

```bash {"name": "race-with-delay"}
echo "Resetting accounts..."
curl -s -X POST http://localhost:8000/reset > /dev/null

echo "Initial balance:"
curl -s http://localhost:8000/accounts/alice | jq

echo ""
echo "Sending two \$800 transfers with debug delays..."
curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "bob", "amount": 800}' &
PID1=$!

curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "charlie", "amount": 800}' &
PID2=$!

wait $PID1 $PID2

echo ""
echo "=== RESULT ==="
echo "Alice's balance (should be NEGATIVE if race occurred):"
curl -s http://localhost:8000/accounts/alice | jq

echo ""
echo "All balances:"
curl -s http://localhost:8000/accounts | jq
```

---

## Part 4: Guarantee the Race with Barrier Synchronization

### Configure barrier synchronization

```bash {"name": "enable-barrier"}
echo "Resetting accounts..."
curl -s -X POST http://localhost:8000/reset > /dev/null

echo "Enabling barrier (waits for 2 requests before releasing)..."
curl -s -X POST "http://localhost:8000/admin/barrier?enabled=true&count=2" | jq

echo ""
echo "Current settings:"
curl -s http://localhost:8000/admin/settings | jq
```

### Trigger guaranteed race with barrier

```bash {"name": "race-with-barrier"}
echo "Initial balance:"
curl -s http://localhost:8000/accounts/alice | jq

echo ""
echo "Sending two \$800 transfers (will wait at barrier)..."
echo "Watch the logs to see barrier synchronization!"
echo ""

curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "bob", "amount": 800}' &
PID1=$!

curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "charlie", "amount": 800}' &
PID2=$!

wait $PID1 $PID2

echo ""
echo "=== GUARANTEED RACE CONDITION ==="
echo "Alice's balance:"
curl -s http://localhost:8000/accounts/alice | jq

echo ""
echo "All balances:"
curl -s http://localhost:8000/accounts | jq
```

### View service logs showing barrier

```bash {"name": "view-logs"}
echo "Last 30 log lines (look for barrier messages):"
docker compose logs lab21-bank-api --tail=30
```

---

## Part 5: Check Metrics

### View race condition metrics

```bash {"name": "view-metrics"}
echo "=== RACE CONDITION METRICS ==="
curl -s http://localhost:8000/metrics | grep -E "race_condition|double_spend|transfers_total|account_balance"
```

### Print UI URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Grafana:    http://localhost:3001 (admin/admin)"
echo "              Dashboard: Race Conditions Lab"
echo ""
echo "  Jaeger:     http://localhost:16686"
echo "              Service: bank-api"
echo "              Look for: race_condition.detected=true"
echo ""
echo "  Prometheus: http://localhost:9090"
echo "              Query: race_condition_hits_total"
```

---

## Part 6: Apply the Fix (Use Transactions/Locks)

### Enable safe mode

```bash {"name": "enable-safe-mode"}
echo "Resetting accounts..."
curl -s -X POST http://localhost:8000/reset > /dev/null

echo "Enabling safe mode (uses locks)..."
curl -s -X POST "http://localhost:8000/admin/transactions?enabled=true" | jq

echo ""
echo "Settings (note: barrier and delay still enabled!):"
curl -s http://localhost:8000/admin/settings | jq
```

### Test with fix applied

```bash {"name": "test-with-fix"}
echo "Initial balance:"
curl -s http://localhost:8000/accounts/alice | jq

echo ""
echo "Sending two \$800 transfers with SAFE MODE..."
curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "bob", "amount": 800}' &
PID1=$!

curl -s -X POST http://localhost:8000/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "alice", "to_account": "charlie", "amount": 800}' &
PID2=$!

wait $PID1 $PID2

echo ""
echo "=== WITH FIX APPLIED ==="
echo "Alice's balance (should be \$200, NOT negative):"
curl -s http://localhost:8000/accounts/alice | jq

echo ""
echo "All balances (one transfer succeeded, one failed):"
curl -s http://localhost:8000/accounts | jq
```

### View transaction log

```bash {"name": "view-transactions"}
echo "Recent transactions:"
curl -s "http://localhost:8000/transactions?limit=5" | jq
```

---

## Part 7: Compare Traces

### Generate traces for comparison

```bash {"name": "generate-traces"}
echo "Generating buggy traces..."
curl -s -X POST "http://localhost:8000/admin/transactions?enabled=false" > /dev/null

for i in 1 2 3; do
  curl -s -X POST http://localhost:8000/reset > /dev/null
  curl -s -X POST http://localhost:8000/transfer \
    -H "Content-Type: application/json" \
    -d '{"from_account": "alice", "to_account": "bob", "amount": 800}' > /dev/null &
  curl -s -X POST http://localhost:8000/transfer \
    -H "Content-Type: application/json" \
    -d '{"from_account": "alice", "to_account": "charlie", "amount": 800}' > /dev/null &
  wait
done

echo "Generating fixed traces..."
curl -s -X POST "http://localhost:8000/admin/transactions?enabled=true" > /dev/null

for i in 1 2 3; do
  curl -s -X POST http://localhost:8000/reset > /dev/null
  curl -s -X POST http://localhost:8000/transfer \
    -H "Content-Type: application/json" \
    -d '{"from_account": "alice", "to_account": "bob", "amount": 800}' > /dev/null &
  curl -s -X POST http://localhost:8000/transfer \
    -H "Content-Type: application/json" \
    -d '{"from_account": "alice", "to_account": "charlie", "amount": 800}' > /dev/null &
  wait
done

echo "Done! Open Jaeger to compare traces:"
echo "http://localhost:16686"
echo ""
echo "Look for spans with attribute: race_condition.detected=true"
```

---

## Part 8: Load Testing

### Run basic load test

```bash {"name": "load-test-basic"}
curl -s -X POST http://localhost:8000/reset > /dev/null
curl -s -X POST "http://localhost:8000/admin/transactions?enabled=false" > /dev/null
curl -s -X POST "http://localhost:8000/admin/barrier?enabled=false" > /dev/null
curl -s -X POST "http://localhost:8000/admin/delay?enabled=true&ms=50" > /dev/null

echo "Running load test..."
docker compose run --rm lab21-k6 run /scripts/basic.js
```

### Run barrier test

```bash {"name": "load-test-barrier"}
echo "Running barrier synchronization test..."
docker compose run --rm lab21-k6 run /scripts/barrier-test.js
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

### Check Bank API logs

```bash {"name": "logs-bank-api"}
docker compose logs lab21-bank-api --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart Bank API

```bash {"name": "restart-bank-api"}
docker compose restart lab21-bank-api
sleep 5
docker compose ps
```

### Full reset

```bash {"name": "full-reset"}
docker compose down -v
docker compose up --build -d
sleep 15
curl -s http://localhost:8000/health | jq
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Reset accounts | `curl -X POST localhost:8000/reset` |
| Enable delay | `curl -X POST "localhost:8000/admin/delay?enabled=true&ms=200"` |
| Enable barrier | `curl -X POST "localhost:8000/admin/barrier?enabled=true&count=2"` |
| Enable fix | `curl -X POST "localhost:8000/admin/transactions?enabled=true"` |
| Check balances | `curl localhost:8000/accounts` |
| Check settings | `curl localhost:8000/admin/settings` |
| View metrics | `curl localhost:8000/metrics` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 (admin/admin) |
