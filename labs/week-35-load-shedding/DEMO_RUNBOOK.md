# Demo Runbook: Load Shedding

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
echo "API Service:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Worker Service:"
curl -s http://localhost:8001/health | jq
```

---

## Part 2: Open Observability UIs

### Show URLs for browser

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Grafana:    http://localhost:3001 (admin/admin)"
echo "  Jaeger:     http://localhost:16686"
echo "  Prometheus: http://localhost:9090"
echo "  API:        http://localhost:8000"
echo ""
echo "Use the 'Load Shedding Dashboard' in Grafana"
```

---

## Part 3: Baseline Without Shedding

### Check current configuration

```bash {"name": "check-config"}
curl -s http://localhost:8000/admin/config | jq
```

### Make a single request

```bash {"name": "single-request"}
curl -s http://localhost:8000/api/process | jq
```

### Baseline timing (5 requests)

```bash {"name": "baseline-timing"}
echo "Baseline timing (5 requests):"
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/process)
  echo "Request $i: ${time}s"
done
```

### Generate light load

```bash {"name": "light-load"}
echo "Generating light load (20 requests)..."
for i in {1..20}; do
  curl -s -o /dev/null -w "." http://localhost:8000/api/process
done
echo ""
echo "Done. Check Grafana for metrics."
```

---

## Part 4: Demonstrate System Collapse

### Generate overload WITHOUT shedding

```bash {"name": "overload-no-shedding"}
echo "Generating overload without shedding..."
echo "Watch Grafana for latency spikes!"
echo ""

# Run concurrent requests
for i in {1..50}; do
  curl -s -m 30 http://localhost:8000/api/process > /dev/null 2>&1 &
done

# Wait and show status
sleep 5
echo "Current status:"
curl -s http://localhost:8000/admin/config | jq '{concurrent: .current_concurrent, utilization: .utilization_percent}'
wait
echo ""
echo "Overload complete. Check Grafana for impact."
```

### Check latency after overload

```bash {"name": "post-overload-latency"}
echo "Latency after overload:"
for i in {1..3}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/process)
  echo "Request $i: ${time}s"
done
```

---

## Part 5: Enable Load Shedding

### Enable random shedding strategy

```bash {"name": "enable-random-shedding"}
curl -s -X POST "http://localhost:8000/admin/config?enabled=true&strategy=random" | jq
```

### Verify shedding is enabled

```bash {"name": "verify-shedding-enabled"}
curl -s http://localhost:8000/admin/config | jq
```

### Generate overload WITH shedding

```bash {"name": "overload-with-shedding"}
echo "Generating overload WITH shedding enabled..."
echo "Watch the shed rate in Grafana!"
echo ""

success=0
shed=0

for i in {1..50}; do
  status=$(curl -s -o /dev/null -w "%{http_code}" -m 10 http://localhost:8000/api/process)
  if [ "$status" = "200" ]; then
    ((success++))
    echo -n "."
  elif [ "$status" = "503" ]; then
    ((shed++))
    echo -n "X"
  else
    echo -n "?"
  fi
done

echo ""
echo ""
echo "Results: Success=$success, Shed=$shed"
```

### Compare: Request still succeeding requests are fast

```bash {"name": "fast-despite-load"}
echo "Making requests during shedding - successful ones are fast:"
for i in {1..5}; do
  result=$(curl -s -o /dev/null -w "status=%{http_code} time=%{time_total}s" http://localhost:8000/api/process)
  echo "Request $i: $result"
done
```

---

## Part 6: Priority-Based Shedding

### Switch to priority strategy

```bash {"name": "enable-priority-shedding"}
curl -s -X POST "http://localhost:8000/admin/config?strategy=priority" | jq
```

### Test VIP request (should succeed)

```bash {"name": "test-vip"}
echo "VIP request:"
curl -s -H "X-Priority: vip" http://localhost:8000/api/process | jq '{priority: .priority, status: "success"}'
```

### Test LOW priority request (may be shed)

```bash {"name": "test-low"}
echo "LOW priority request:"
status=$(curl -s -o /dev/null -w "%{http_code}" -H "X-Priority: low" http://localhost:8000/api/process)
echo "Status: $status"
```

### Generate mixed priority load

```bash {"name": "mixed-priority-load"}
echo "Generating mixed priority load..."
echo "VIP=v, HIGH=h, NORMAL=n, LOW=l, Shed=X"
echo ""

for i in {1..40}; do
  # Cycle through priorities
  case $((i % 4)) in
    0) priority="vip"; char="v" ;;
    1) priority="high"; char="h" ;;
    2) priority="normal"; char="n" ;;
    3) priority="low"; char="l" ;;
  esac

  status=$(curl -s -o /dev/null -w "%{http_code}" -H "X-Priority: $priority" http://localhost:8000/api/process)
  if [ "$status" = "503" ]; then
    echo -n "X"
  else
    echo -n "$char"
  fi
done

echo ""
echo ""
echo "Notice: VIP (v) and HIGH (h) rarely show as X"
```

### Run k6 priority test

```bash {"name": "k6-priority-test"}
docker compose run --rm lab35-k6 run /scripts/priority-test.js 2>&1 | tail -20
```

---

## Part 7: LIFO Shedding

### Switch to LIFO strategy

```bash {"name": "enable-lifo-shedding"}
curl -s -X POST "http://localhost:8000/admin/config?strategy=lifo" | jq
```

### Explain LIFO concept

```bash {"name": "explain-lifo"}
echo "LIFO (Last In, First Out) Shedding:"
echo ""
echo "  - Newest requests are served first"
echo "  - Old waiting requests are shed"
echo "  - No request waits forever"
echo "  - Better user experience under load"
echo ""
echo "Watch the queue_depth metric in Grafana"
```

### Generate LIFO test load

```bash {"name": "lifo-test-load"}
echo "Generating load with LIFO shedding..."
for i in {1..30}; do
  curl -s -m 10 http://localhost:8000/api/process > /dev/null 2>&1 &
done

sleep 3
echo "Current queue depth:"
curl -s http://localhost:8000/admin/config | jq '{queue_depth: .current_queue_depth, concurrent: .current_concurrent}'
wait
```

---

## Part 8: Tune Thresholds

### Lower threshold (aggressive shedding)

```bash {"name": "lower-threshold"}
curl -s -X POST "http://localhost:8000/admin/config?shed_threshold_percent=60" | jq '{threshold: .shed_threshold_percent, strategy: .strategy}'
```

### Test with lower threshold

```bash {"name": "test-lower-threshold"}
echo "Testing with 60% threshold (more aggressive):"
for i in {1..20}; do
  status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/process)
  echo -n "$status "
done
echo ""
```

### Higher threshold (less aggressive)

```bash {"name": "higher-threshold"}
curl -s -X POST "http://localhost:8000/admin/config?shed_threshold_percent=90" | jq '{threshold: .shed_threshold_percent, strategy: .strategy}'
```

### Test with higher threshold

```bash {"name": "test-higher-threshold"}
echo "Testing with 90% threshold (less aggressive):"
for i in {1..20}; do
  status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/process)
  echo -n "$status "
done
echo ""
```

### Reset to optimal threshold

```bash {"name": "reset-threshold"}
curl -s -X POST "http://localhost:8000/admin/config?shed_threshold_percent=80" | jq '{threshold: .shed_threshold_percent}'
```

---

## Part 9: Full Load Tests

### Run basic load test

```bash {"name": "run-basic-loadtest"}
echo "Running basic load test (3 minutes)..."
docker compose run --rm lab35-k6 run /scripts/basic.js 2>&1 | tail -30
```

### Run overload test

```bash {"name": "run-overload-test"}
echo "Running extreme overload test..."
docker compose run --rm lab35-k6 run /scripts/overload-test.js 2>&1 | tail -30
```

---

## Part 10: Demonstrate Recovery

### Disable shedding to show difference

```bash {"name": "disable-shedding"}
curl -s -X POST "http://localhost:8000/admin/config?enabled=false" | jq
```

### Generate overload and watch collapse

```bash {"name": "collapse-demo"}
echo "Without shedding, the system struggles..."
for i in {1..30}; do
  curl -s -m 30 http://localhost:8000/api/process > /dev/null 2>&1 &
done
sleep 5
echo "Check Grafana for latency explosion!"
wait
```

### Re-enable shedding

```bash {"name": "reenable-shedding"}
curl -s -X POST "http://localhost:8000/admin/config?enabled=true&strategy=random" | jq
```

### Show immediate recovery

```bash {"name": "show-recovery"}
echo "With shedding re-enabled, system recovers:"
for i in {1..5}; do
  result=$(curl -s -o /dev/null -w "status=%{http_code} time=%{time_total}s" http://localhost:8000/api/process)
  echo "Request $i: $result"
done
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

### Check service logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check API service logs

```bash {"name": "logs-api"}
docker compose logs lab35-api-service --tail=50
```

### Check worker service logs

```bash {"name": "logs-worker"}
docker compose logs lab35-worker-service --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart services

```bash {"name": "restart-services"}
docker compose restart lab35-api-service lab35-worker-service
```

### Reset configuration

```bash {"name": "reset-config"}
curl -s -X POST "http://localhost:8000/admin/config?enabled=false&strategy=random&max_concurrent=50&shed_threshold_percent=80" | jq
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Enable shedding | `curl -X POST "localhost:8000/admin/config?enabled=true"` |
| Disable shedding | `curl -X POST "localhost:8000/admin/config?enabled=false"` |
| Set strategy | `curl -X POST "localhost:8000/admin/config?strategy=random"` |
| Check config | `curl localhost:8000/admin/config` |
| View dashboard | http://localhost:3001 |
| View traces | http://localhost:16686 |
