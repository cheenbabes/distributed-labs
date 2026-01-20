# Demo Runbook: Circuit Breakers with Dashboard

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
echo "Client service:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Backend service:"
curl -s http://localhost:8001/health | jq
```

---

## Part 2: Understand the Circuit Breaker

### Check circuit breaker status

```bash {"name": "circuit-status"}
curl -s http://localhost:8000/circuit/status | jq
```

### Show URLs to open

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Grafana:      http://localhost:3001 (admin/admin)"
echo "  Jaeger:       http://localhost:16686"
echo "  Prometheus:   http://localhost:9090"
echo "  Client API:   http://localhost:8000"
echo "  Backend API:  http://localhost:8001"
echo ""
echo "In Grafana, find the 'Circuit Breaker Dashboard' in the 'Lab 07' folder"
```

---

## Part 3: Normal Operation (CLOSED State)

### Make a single request

```bash {"name": "single-request"}
curl -s http://localhost:8000/api/data | jq
```

### Make multiple requests

```bash {"name": "multiple-requests"}
echo "Making 5 requests in CLOSED state:"
for i in {1..5}; do
  state=$(curl -s http://localhost:8000/api/data | jq -r '.circuit_state')
  echo "Request $i: Circuit state = $state"
done
```

### Check backend is healthy

```bash {"name": "check-backend"}
curl -s http://localhost:8001/admin/failure | jq
```

---

## Part 4: Trigger Circuit Opening

### Put backend in failure mode

```bash {"name": "enable-failures"}
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "failing"}' | jq
```

### Verify backend fails

```bash {"name": "verify-backend-fails"}
echo "Backend should return 503:"
curl -s -w "\nHTTP Status: %{http_code}\n" http://localhost:8001/api/data
```

### Send requests to trigger circuit opening

```bash {"name": "trigger-circuit-open"}
echo "Sending requests to trigger circuit opening..."
echo "(Circuit opens after 5 failures)"
echo ""
for i in {1..8}; do
  echo "=== Request $i ==="
  result=$(curl -s http://localhost:8000/api/data)

  # Check if it's a success, failure, or rejection
  if echo "$result" | jq -e '.circuit_state' > /dev/null 2>&1; then
    echo "$result" | jq -c '{state: .circuit_state}'
  else
    echo "$result" | jq -c '{error: .detail.error, state: .detail.circuit_state}'
  fi
  sleep 0.3
done
```

### Check circuit is now OPEN

```bash {"name": "verify-open"}
curl -s http://localhost:8000/circuit/status | jq
```

---

## Part 5: Observe Fast-Fail Behavior

### Time a rejected request

```bash {"name": "time-rejected"}
echo "Timing a REJECTED request (circuit is OPEN):"
time curl -s http://localhost:8000/api/data > /dev/null
echo ""
echo "Notice how fast the rejection is - no waiting for backend timeout!"
```

### Show the fast rejection response

```bash {"name": "show-rejection"}
curl -s http://localhost:8000/api/data | jq
```

---

## Part 6: Recovery Process (HALF_OPEN State)

### Fix the backend

```bash {"name": "fix-backend"}
echo "Fixing the backend..."
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "healthy"}' | jq
```

### Wait for timeout

```bash {"name": "wait-timeout"}
echo "Waiting 30 seconds for circuit to enter HALF_OPEN state..."
echo "(Watch the Grafana dashboard!)"
for i in {30..1}; do
  echo -ne "\r$i seconds remaining..."
  sleep 1
done
echo ""
echo "Circuit should now be HALF_OPEN"
```

### Check state is HALF_OPEN

```bash {"name": "check-half-open"}
curl -s http://localhost:8000/circuit/status | jq
```

### Make requests to trigger recovery

```bash {"name": "trigger-recovery"}
echo "Making requests in HALF_OPEN state..."
echo "(Need 3 successes to close circuit)"
echo ""
for i in {1..5}; do
  echo "=== Request $i ==="
  result=$(curl -s http://localhost:8000/api/data)
  echo "$result" | jq -c '{state: .circuit_state, success_count: .backend_response.status}'

  # Check current status
  status=$(curl -s http://localhost:8000/circuit/status | jq -r '.state')
  if [ "$status" == "CLOSED" ]; then
    echo "Circuit is now CLOSED!"
    break
  fi
  sleep 0.5
done
```

### Verify circuit is CLOSED

```bash {"name": "verify-closed"}
curl -s http://localhost:8000/circuit/status | jq
```

---

## Part 7: Failure During HALF_OPEN

### Reset and open circuit again

```bash {"name": "reset-and-open"}
# Reset circuit
curl -s -X POST http://localhost:8000/circuit/reset | jq

# Enable failures
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "failing"}' > /dev/null

# Open circuit
echo "Opening circuit with failures..."
for i in {1..6}; do
  curl -s http://localhost:8000/api/data > /dev/null
done

curl -s http://localhost:8000/circuit/status | jq
```

### Wait for HALF_OPEN (shorter wait for demo)

```bash {"name": "wait-half-open"}
echo "Waiting 30 seconds for HALF_OPEN..."
sleep 30
curl -s http://localhost:8000/circuit/status | jq
```

### Show failure in HALF_OPEN reopens circuit

```bash {"name": "half-open-failure"}
echo "Making a request while backend is still failing..."
echo "This should reopen the circuit immediately."
echo ""

echo "Before request:"
curl -s http://localhost:8000/circuit/status | jq -c '{state}'

echo ""
echo "Making request..."
curl -s http://localhost:8000/api/data | jq -c

echo ""
echo "After request (should be OPEN again):"
curl -s http://localhost:8000/circuit/status | jq -c '{state}'
```

---

## Part 8: Batch Request Demo

### Reset everything

```bash {"name": "reset-all"}
curl -s -X POST http://localhost:8000/circuit/reset | jq
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "healthy"}' | jq
```

### Use batch endpoint to show state changes

```bash {"name": "batch-demo-healthy"}
echo "Making 15 requests with healthy backend:"
curl -s "http://localhost:8000/api/data/batch?count=15" | jq '.results[] | {request, status, circuit_state}'
```

### Batch with failures

```bash {"name": "batch-demo-failures"}
# Enable failures
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "failing"}' > /dev/null

# Reset circuit
curl -s -X POST http://localhost:8000/circuit/reset > /dev/null

echo "Making 15 requests with failing backend:"
echo "(Watch the circuit state change!)"
curl -s "http://localhost:8000/api/data/batch?count=15" | jq '.results[] | {request, status, circuit_state}'
```

---

## Part 9: Load Test

### Prepare for load test

```bash {"name": "prepare-loadtest"}
# Reset everything
curl -s -X POST http://localhost:8000/circuit/reset | jq
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "healthy"}' | jq

echo ""
echo "Ready for load test. During the test, you can inject failures with:"
echo "  curl -X POST localhost:8001/admin/failure -H 'Content-Type: application/json' -d '{\"mode\": \"failing\"}'"
```

### Run load test

```bash {"name": "run-loadtest", "background": true}
docker compose run --rm k6 run /scripts/circuit-breaker.js
```

### Inject failures during load test (run in another terminal)

```bash {"name": "inject-during-load"}
echo "Injecting failures in 10 seconds..."
sleep 10
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "failing"}' | jq

echo "Failures injected. Wait 30 seconds then fix..."
sleep 30

curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "healthy"}' | jq
echo "Backend fixed!"
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

### Check client logs

```bash {"name": "logs-client"}
docker compose logs client --tail=50
```

### Check backend logs

```bash {"name": "logs-backend"}
docker compose logs backend --tail=50
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
| Check circuit status | `curl localhost:8000/circuit/status` |
| Reset circuit | `curl -X POST localhost:8000/circuit/reset` |
| Enable failures | `curl -X POST localhost:8001/admin/failure -d '{"mode":"failing"}'` |
| Disable failures | `curl -X POST localhost:8001/admin/failure -d '{"mode":"healthy"}'` |
| Partial failures | `curl -X POST localhost:8001/admin/failure -d '{"mode":"partial","error_rate":0.5}'` |
| View dashboard | http://localhost:3001 (admin/admin) |
