# Demo Runbook: Canary Releases

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
echo "Nginx (Load Balancer):"
curl -s http://localhost:8000/health | jq
echo ""
echo "Stable (v1):"
curl -s http://localhost:8081/health | jq
echo ""
echo "Canary (v2):"
curl -s http://localhost:8082/health | jq
echo ""
echo "Canary Controller:"
curl -s http://localhost:8090/health | jq
```

---

## Part 2: Show URLs

### Print all UI URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Gateway (Nginx):    http://localhost:8000"
echo "  Stable (v1):        http://localhost:8081"
echo "  Canary (v2):        http://localhost:8082"
echo "  Canary Controller:  http://localhost:8090"
echo "  Jaeger:             http://localhost:16686"
echo "  Grafana:            http://localhost:3001 (admin/admin)"
echo "  Prometheus:         http://localhost:9090"
```

---

## Part 3: Baseline - All Traffic to Stable

### Check initial deployment status

```bash {"name": "check-status"}
curl -s http://localhost:8090/status | jq
```

### Make requests - all should go to v1

```bash {"name": "baseline-requests"}
echo "Making 10 requests - all should be v1:"
echo ""
for i in {1..10}; do
  version=$(curl -s http://localhost:8000/api/process | jq -r '.version')
  echo "Request $i: $version"
done
```

### Show full response details

```bash {"name": "single-request"}
curl -s http://localhost:8000/api/process | jq
```

---

## Part 4: Deploy Canary at 1%

### Promote to first stage (1%)

```bash {"name": "promote-1-percent"}
curl -s -X POST http://localhost:8090/promote | jq
```

### Verify the weight change

```bash {"name": "verify-1-percent"}
curl -s http://localhost:8090/status | jq
```

### Test traffic distribution (100 requests)

```bash {"name": "test-1-percent"}
echo "Distribution over 100 requests:"
echo ""
for i in {1..100}; do
  curl -s http://localhost:8000/api/process | jq -r '.version'
done | sort | uniq -c
```

---

## Part 5: Gradually Increase Traffic

### Promote to 5%

```bash {"name": "promote-5-percent"}
curl -s -X POST http://localhost:8090/promote | jq
```

### Promote to 10%

```bash {"name": "promote-10-percent"}
curl -s -X POST http://localhost:8090/promote | jq
```

### Promote to 25%

```bash {"name": "promote-25-percent"}
curl -s -X POST http://localhost:8090/promote | jq
```

### Test traffic distribution at 25%

```bash {"name": "test-25-percent"}
echo "Distribution over 50 requests (expect ~75% v1, ~25% v2):"
echo ""
for i in {1..50}; do
  curl -s http://localhost:8000/api/process | jq -r '.version'
done | sort | uniq -c
```

---

## Part 6: Start Background Load

### Generate continuous traffic

```bash {"name": "background-load", "background": true}
echo "Starting background load..."
while true; do
  curl -s http://localhost:8000/api/process > /dev/null
  sleep 0.1
done
```

---

## Part 7: Inject Errors into Canary

### Inject 30% error rate

```bash {"name": "inject-errors"}
curl -s -X POST http://localhost:8082/admin/config \
  -H "Content-Type: application/json" \
  -d '{"error_rate": 0.3, "extra_latency_ms": 0}' | jq
```

### Verify error injection is active

```bash {"name": "verify-errors"}
curl -s http://localhost:8082/admin/config | jq
```

### Make requests to see errors (some will fail)

```bash {"name": "test-errors"}
echo "Making 20 requests - expect ~25% to fail (from canary with 30% error rate at 25% traffic):"
echo ""
for i in {1..20}; do
  status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/process)
  echo "Request $i: HTTP $status"
done
```

---

## Part 8: Observe Automatic Rollback

### Check current error rate

```bash {"name": "check-error-rate"}
echo "Current status (watch canary_error_rate):"
curl -s http://localhost:8090/status | jq
```

### Increase canary traffic to trigger rollback

```bash {"name": "trigger-rollback"}
echo "Setting canary to 50% to exceed error threshold..."
curl -s -X POST http://localhost:8090/weight \
  -H "Content-Type: application/json" \
  -d '{"canary_weight": 50}' | jq
```

### Wait for auto-rollback

```bash {"name": "wait-rollback"}
echo "Waiting for auto-rollback (monitoring interval is 10s)..."
sleep 15
echo ""
echo "Status after monitoring:"
curl -s http://localhost:8090/status | jq
```

### Verify rollback occurred

```bash {"name": "verify-rollback"}
echo "Traffic distribution after rollback:"
for i in {1..10}; do
  curl -s http://localhost:8000/api/process | jq -r '.version'
done | sort | uniq -c
```

---

## Part 9: Successful Promotion

### Remove error injection

```bash {"name": "remove-errors"}
curl -s -X POST http://localhost:8082/admin/config \
  -H "Content-Type: application/json" \
  -d '{"error_rate": 0, "extra_latency_ms": 0}' | jq
```

### Promote through all stages

```bash {"name": "full-promotion"}
echo "Promoting through all stages..."
echo ""

echo "Stage 1 (1%):"
curl -s -X POST http://localhost:8090/promote | jq -r '.canary_weight + "% canary"'
sleep 2

echo "Stage 2 (5%):"
curl -s -X POST http://localhost:8090/promote | jq -r '.canary_weight + "% canary"'
sleep 2

echo "Stage 3 (10%):"
curl -s -X POST http://localhost:8090/promote | jq -r '.canary_weight + "% canary"'
sleep 2

echo "Stage 4 (25%):"
curl -s -X POST http://localhost:8090/promote | jq -r '.canary_weight + "% canary"'
sleep 2

echo "Stage 5 (50%):"
curl -s -X POST http://localhost:8090/promote | jq -r '.canary_weight + "% canary"'
sleep 2

echo "Stage 6 (75%):"
curl -s -X POST http://localhost:8090/promote | jq -r '.canary_weight + "% canary"'
sleep 2

echo "Stage 7 (100%) - FULLY PROMOTED:"
curl -s -X POST http://localhost:8090/promote | jq
```

### Verify full promotion

```bash {"name": "verify-promotion"}
echo "All traffic should now be v2:"
for i in {1..10}; do
  curl -s http://localhost:8000/api/process | jq -r '.version'
done | sort | uniq -c
```

---

## Part 10: Manual Rollback Demo

### Manually rollback to stable

```bash {"name": "manual-rollback"}
curl -s -X POST http://localhost:8090/rollback | jq
```

### Verify rollback

```bash {"name": "verify-manual-rollback"}
curl -s http://localhost:8090/status | jq
```

---

## Part 11: Load Testing (Optional)

### Run k6 canary traffic test

```bash {"name": "k6-load-test", "background": true}
docker compose run --rm k6 run /scripts/canary-traffic.js
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

### Check canary controller logs

```bash {"name": "logs-controller"}
docker compose logs canary-controller --tail=50
```

### Check nginx logs

```bash {"name": "logs-nginx"}
docker compose logs nginx --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart services

```bash {"name": "restart-services"}
docker compose restart nginx canary-controller
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Check status | `curl http://localhost:8090/status \| jq` |
| Promote canary | `curl -X POST http://localhost:8090/promote` |
| Rollback | `curl -X POST http://localhost:8090/rollback` |
| Set weight | `curl -X POST http://localhost:8090/weight -d '{"canary_weight": 10}'` |
| Inject errors | `curl -X POST http://localhost:8082/admin/config -d '{"error_rate": 0.3}'` |
| Remove errors | `curl -X POST http://localhost:8082/admin/config -d '{"error_rate": 0}'` |
| View dashboard | http://localhost:3001 |
| View traces | http://localhost:16686 |
