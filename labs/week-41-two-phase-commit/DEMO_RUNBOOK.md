# Demo Runbook: Two-Phase Commit (2PC)

This runbook contains all commands for demonstrating 2PC and its blocking problems. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

---

## Pre-Demo Setup

### Check Docker is running

```bash {"name": "check-docker"}
docker info > /dev/null 2>&1 && echo "Docker is running" || echo "Docker is not running"
```

### Clean any previous lab state

```bash {"name": "clean-previous"}
docker compose down -v 2>/dev/null || true
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
echo "Coordinator:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Participant A:"
curl -s http://localhost:8001/health | jq
echo ""
echo "Participant B:"
curl -s http://localhost:8002/health | jq
echo ""
echo "Participant C:"
curl -s http://localhost:8003/health | jq
echo ""
echo "Client:"
curl -s http://localhost:8080/health | jq
```

---

## Part 2: Open Observability UIs

### Show URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Jaeger:     http://localhost:16686"
echo "  Grafana:    http://localhost:3001 (admin/admin)"
echo "  Prometheus: http://localhost:9090"
echo ""
echo "  Coordinator: http://localhost:8000"
echo "  Client:      http://localhost:8080"
```

---

## Part 3: Happy Path - Successful 2PC Transaction

### Perform a transfer

```bash {"name": "happy-transfer"}
curl -s -X POST http://localhost:8080/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "account-A", "to_account": "account-B", "amount": 100}' | jq
```

### Check coordinator's transaction log

```bash {"name": "check-coordinator-txns"}
curl -s http://localhost:8000/transactions | jq
```

### Check participant states

```bash {"name": "check-participant-states"}
echo "=== Participant A ==="
curl -s http://localhost:8001/transactions | jq

echo ""
echo "=== Participant B ==="
curl -s http://localhost:8002/transactions | jq

echo ""
echo "=== Participant C ==="
curl -s http://localhost:8003/transactions | jq
```

### Run multiple transfers for baseline

```bash {"name": "baseline-timing"}
echo "Running 5 transfers to establish baseline timing..."
for i in {1..5}; do
  start=$(date +%s%N)
  result=$(curl -s -X POST http://localhost:8080/transfer \
    -H "Content-Type: application/json" \
    -d "{\"from_account\": \"account-A\", \"to_account\": \"account-B\", \"amount\": $((i * 10))}")
  end=$(date +%s%N)
  duration=$(( (end - start) / 1000000 ))
  status=$(echo $result | jq -r '.status')
  echo "Transfer $i: ${duration}ms - $status"
done
```

---

## Part 4: Participant Failure During Prepare

### Configure participant B to fail

```bash {"name": "inject-participant-failure"}
curl -s -X POST http://localhost:8002/admin/config \
  -H "Content-Type: application/json" \
  -d '{"fail_prepare_probability": 1.0}' | jq
```

### Try a transfer (should abort)

```bash {"name": "transfer-with-participant-failure"}
curl -s -X POST http://localhost:8080/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "account-A", "to_account": "account-B", "amount": 100}' | jq
```

### Check that no participants are blocked

```bash {"name": "check-no-blocked-after-abort"}
echo "=== Blocked transactions after participant failure ==="
echo "Participant A:"
curl -s http://localhost:8001/blocked | jq

echo ""
echo "Participant B:"
curl -s http://localhost:8002/blocked | jq

echo ""
echo "Participant C:"
curl -s http://localhost:8003/blocked | jq
```

### Reset participant B

```bash {"name": "reset-participant-b"}
curl -s -X POST http://localhost:8002/admin/reset | jq
```

---

## Part 5: THE BLOCKING SCENARIO - Coordinator Failure

This is the critical demo showing why 2PC is a trap.

### Configure coordinator to fail after prepare

```bash {"name": "inject-coordinator-failure"}
echo "Configuring coordinator to fail AFTER all participants vote YES..."
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"fail_after_prepare": true}' | jq
```

### Try a transfer (this will fail!)

```bash {"name": "transfer-with-coordinator-failure"}
echo "Attempting transfer with coordinator failure..."
curl -s -X POST http://localhost:8080/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "account-A", "to_account": "account-B", "amount": 100}' | jq
```

### CHECK: All participants are now BLOCKED!

```bash {"name": "show-blocked-transactions"}
echo ""
echo "=========================================="
echo "   PARTICIPANTS ARE NOW BLOCKED!"
echo "=========================================="
echo ""
echo "All participants voted YES and are holding locks."
echo "They cannot commit (coordinator might abort)."
echo "They cannot abort (coordinator might commit)."
echo "They are STUCK waiting for a coordinator that crashed."
echo ""

echo "=== Participant A blocked transactions ==="
curl -s http://localhost:8001/blocked | jq

echo ""
echo "=== Participant B blocked transactions ==="
curl -s http://localhost:8002/blocked | jq

echo ""
echo "=== Participant C blocked transactions ==="
curl -s http://localhost:8003/blocked | jq
```

### Show how long participants have been waiting

```bash {"name": "show-waiting-time"}
echo "Time participants have been waiting (seconds):"
echo ""
echo "Participant A:"
curl -s http://localhost:8001/blocked | jq '.blocked_transactions[].waiting_seconds'
echo ""
echo "Participant B:"
curl -s http://localhost:8002/blocked | jq '.blocked_transactions[].waiting_seconds'
echo ""
echo "Participant C:"
curl -s http://localhost:8003/blocked | jq '.blocked_transactions[].waiting_seconds'
```

### Disable coordinator failure mode

```bash {"name": "disable-coordinator-failure"}
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"fail_after_prepare": false}' | jq
```

### Show participants are STILL blocked

```bash {"name": "show-still-blocked"}
echo "Even after coordinator is 'fixed', participants are STILL blocked!"
echo "In real systems, this requires manual intervention or coordinator recovery."
echo ""
curl -s http://localhost:8001/blocked | jq '.blocked_count'
curl -s http://localhost:8002/blocked | jq '.blocked_count'
curl -s http://localhost:8003/blocked | jq '.blocked_count'
```

### Reset all state

```bash {"name": "reset-all-state"}
echo "Simulating coordinator recovery that resolves blocked transactions..."
curl -s -X POST http://localhost:8001/admin/reset | jq
curl -s -X POST http://localhost:8002/admin/reset | jq
curl -s -X POST http://localhost:8003/admin/reset | jq
curl -s -X POST http://localhost:8000/admin/reset | jq
echo "All state reset."
```

---

## Part 6: Blocking Delay Demo

Show how even slow coordinators cause lock holding.

### Configure blocking delay

```bash {"name": "inject-blocking-delay"}
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"block_duration_ms": 3000}' | jq
echo "Coordinator will now delay 3 seconds between prepare and commit."
```

### Run transfer with timing

```bash {"name": "transfer-with-delay"}
echo "Running transfer with 3-second blocking delay..."
echo "During this time, participants are holding locks!"
echo ""
time curl -s -X POST http://localhost:8080/transfer \
  -H "Content-Type: application/json" \
  -d '{"from_account": "account-A", "to_account": "account-B", "amount": 100}' | jq
```

### Reset blocking delay

```bash {"name": "reset-blocking-delay"}
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"block_duration_ms": 0}' | jq
```

---

## Part 7: Generate Load for Grafana

### Generate traces for analysis

```bash {"name": "generate-traces"}
echo "Generating 20 transactions for trace analysis..."
for i in {1..20}; do
  curl -s -X POST http://localhost:8080/transfer \
    -H "Content-Type: application/json" \
    -d "{\"from_account\": \"account-A\", \"to_account\": \"account-B\", \"amount\": $i}" > /dev/null
  echo -n "."
done
echo ""
echo "Done. Check Jaeger: http://localhost:16686"
```

### Run k6 load test

```bash {"name": "load-test"}
docker compose run --rm lab41-k6 run /scripts/basic.js
```

---

## Part 8: Summary Comparisons

### Compare 2PC vs Saga approaches

```bash {"name": "show-comparison"}
echo "============================================"
echo "         2PC vs Saga Comparison"
echo "============================================"
echo ""
echo "2PC (Two-Phase Commit):"
echo "  - Strong consistency (ACID)"
echo "  - BLOCKING on coordinator failure"
echo "  - Locks held for entire transaction"
echo "  - Single point of failure (coordinator)"
echo ""
echo "Saga:"
echo "  - Eventual consistency"
echo "  - NO BLOCKING - compensating transactions"
echo "  - No distributed locks"
echo "  - Each step is independent"
echo ""
echo "Use 2PC when:"
echo "  - Strong consistency is required"
echo "  - All participants are local"
echo "  - Transactions are fast"
echo ""
echo "Use Sagas when:"
echo "  - Availability > consistency"
echo "  - Cross-datacenter/external services"
echo "  - Long-running transactions"
echo "============================================"
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo "Lab cleaned up."
```

---

## Troubleshooting Commands

### Check all logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check coordinator logs

```bash {"name": "logs-coordinator"}
docker compose logs lab41-coordinator --tail=50
```

### Check participant logs

```bash {"name": "logs-participants"}
docker compose logs lab41-participant-a lab41-participant-b lab41-participant-c --tail=30
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart coordinator

```bash {"name": "restart-coordinator"}
docker compose restart lab41-coordinator
sleep 5
docker compose ps lab41-coordinator
```

### Full reset without stopping

```bash {"name": "full-reset"}
curl -s -X POST http://localhost:8001/admin/reset | jq
curl -s -X POST http://localhost:8002/admin/reset | jq
curl -s -X POST http://localhost:8003/admin/reset | jq
curl -s -X POST http://localhost:8000/admin/reset | jq
echo "All services reset to initial state."
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Do transfer | `curl -X POST localhost:8080/transfer -d '{"from_account":"A","to_account":"B","amount":100}'` |
| Inject coordinator failure | `curl -X POST localhost:8000/admin/config -d '{"fail_after_prepare":true}'` |
| Inject participant failure | `curl -X POST localhost:8002/admin/config -d '{"fail_prepare_probability":1.0}'` |
| Check blocked | `curl localhost:8001/blocked` |
| Reset all | `curl -X POST localhost:8000/admin/reset && curl -X POST localhost:8001/admin/reset && curl -X POST localhost:8002/admin/reset && curl -X POST localhost:8003/admin/reset` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
