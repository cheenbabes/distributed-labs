# Demo Runbook: Eventual Consistency in Action

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
echo "Primary:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Replica 1:"
curl -s http://localhost:8001/health | jq
echo ""
echo "Replica 2:"
curl -s http://localhost:8002/health | jq
echo ""
echo "Client:"
curl -s http://localhost:8003/health | jq
```

---

## Part 2: Open Observability UIs

### Show URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Primary API:  http://localhost:8000"
echo "  Replica 1:    http://localhost:8001"
echo "  Replica 2:    http://localhost:8002"
echo "  Client:       http://localhost:8003"
echo "  Jaeger:       http://localhost:16686"
echo "  Grafana:      http://localhost:3001 (admin/admin)"
echo "  Prometheus:   http://localhost:9090"
```

---

## Part 3: Demonstrate Eventual Consistency

### Show current replication configuration

```bash {"name": "show-config"}
echo "Current replication delays:"
curl -s http://localhost:8000/admin/replication-delays | jq
```

### Write data to primary

```bash {"name": "write-data"}
echo "Writing to primary..."
curl -s -X POST http://localhost:8000/data \
  -H "Content-Type: application/json" \
  -d '{"key": "greeting", "value": "Hello, Distributed World!"}' | jq
```

### Immediately check all sources (shows stale reads)

```bash {"name": "check-all-immediate"}
echo "=== Checking all sources IMMEDIATELY after write ==="
echo ""
echo "Primary (should have latest):"
curl -s http://localhost:8000/version | jq
echo ""
echo "Replica 1 (500ms delay - may be stale):"
curl -s http://localhost:8001/version | jq
echo ""
echo "Replica 2 (1500ms delay - likely stale):"
curl -s http://localhost:8002/version | jq
```

### Wait and check again (shows eventual consistency)

```bash {"name": "check-all-after-wait"}
echo "Waiting 2 seconds for replication..."
sleep 2
echo ""
echo "=== Checking all sources AFTER waiting ==="
echo ""
echo "Primary:"
curl -s http://localhost:8000/version | jq
echo ""
echo "Replica 1 (should be consistent now):"
curl -s http://localhost:8001/version | jq
echo ""
echo "Replica 2 (should be consistent now):"
curl -s http://localhost:8002/version | jq
```

---

## Part 4: Observe Stale Reads

### Write and immediately read (demonstrates stale reads)

```bash {"name": "write-and-read"}
echo "Writing and immediately reading from all sources..."
curl -s -X POST http://localhost:8003/write-and-read \
  -H "Content-Type: application/json" \
  -d '{"key": "stale-test", "value": "will-replicas-see-this?"}' | jq
```

### Use read-compare endpoint to see current state

```bash {"name": "read-compare"}
echo "Comparing current state across all sources..."
curl -s http://localhost:8003/read-compare | jq
```

### Get current consistency status

```bash {"name": "consistency-status"}
echo "Current consistency status:"
curl -s http://localhost:8003/status | jq
```

---

## Part 5: Measure Consistency Window

### Write and wait for consistency

```bash {"name": "write-and-wait"}
echo "Writing and waiting for all replicas to be consistent..."
curl -s -X POST "http://localhost:8003/write-and-wait?timeout_ms=5000" \
  -H "Content-Type: application/json" \
  -d '{"key": "measured-write", "value": "measuring-consistency-window"}' | jq
```

### Run multiple measurements

```bash {"name": "multiple-measurements"}
echo "Running 5 consistency window measurements..."
echo ""
for i in {1..5}; do
  result=$(curl -s -X POST "http://localhost:8003/write-and-wait?timeout_ms=5000" \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"measure-$i\", \"value\": \"test-$i\"}")
  time_ms=$(echo $result | jq '.consistency_time_ms')
  echo "Measurement $i: ${time_ms}ms"
done
```

---

## Part 6: Increase Replication Lag

### Increase Replica 2 delay to 3 seconds

```bash {"name": "increase-lag"}
echo "Increasing Replica 2 delay to 3000ms..."
curl -s -X POST http://localhost:8000/admin/replication-delay \
  -H "Content-Type: application/json" \
  -d '{"replica_url": "http://lab14-replica2:8002", "delay_ms": 3000}' | jq
```

### Test with increased lag

```bash {"name": "test-increased-lag"}
echo "Testing consistency window with 3s Replica 2 delay..."
curl -s -X POST "http://localhost:8003/write-and-wait?timeout_ms=10000" \
  -H "Content-Type: application/json" \
  -d '{"key": "slow-replica", "value": "testing-longer-delay"}' | jq
```

### Reset Replica 2 delay

```bash {"name": "reset-lag"}
echo "Resetting Replica 2 delay to 1500ms..."
curl -s -X POST http://localhost:8000/admin/replication-delay \
  -H "Content-Type: application/json" \
  -d '{"replica_url": "http://lab14-replica2:8002", "delay_ms": 1500}' | jq
```

---

## Part 7: Read-Your-Writes Strategies

### Strategy A: Read from Primary

```bash {"name": "ryw-primary"}
echo "=== Read-Your-Writes via Primary ==="
echo ""
echo "1. Write to primary:"
curl -s -X POST http://localhost:8000/data \
  -H "Content-Type: application/json" \
  -d '{"key": "ryw-test", "value": "read-your-writes-demo"}' | jq
echo ""
echo "2. Read from primary (guaranteed to see write):"
curl -s http://localhost:8000/data | jq '.version, .data'
echo ""
echo "3. Read from replicas (may be stale):"
echo "Replica 1: $(curl -s http://localhost:8001/version | jq '.version')"
echo "Replica 2: $(curl -s http://localhost:8002/version | jq '.version')"
```

### Strategy B: Wait for Consistency

```bash {"name": "ryw-wait"}
echo "=== Read-Your-Writes via Waiting ==="
echo ""
curl -s -X POST "http://localhost:8003/write-and-wait?timeout_ms=5000" \
  -H "Content-Type: application/json" \
  -d '{"key": "ryw-wait", "value": "waited-for-consistency"}' | jq
echo ""
echo "Now all replicas have the data:"
echo "Replica 1: $(curl -s http://localhost:8001/version | jq '.version')"
echo "Replica 2: $(curl -s http://localhost:8002/version | jq '.version')"
```

---

## Part 8: Load Testing

### Run k6 load test

```bash {"name": "load-test", "background": true}
docker compose run --rm lab14-k6 run /scripts/read-consistency.js
```

### Quick inline load test

```bash {"name": "quick-load"}
echo "Running 20-second load test..."
end=$((SECONDS+20))
count=0
stale=0

while [ $SECONDS -lt $end ]; do
  result=$(curl -s -X POST http://localhost:8003/write-and-read \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"load-$count\", \"value\": \"v-$count\"}")
  stale_count=$(echo $result | jq '.stale_read_count')
  if [ "$stale_count" -gt "0" ]; then
    stale=$((stale+1))
  fi
  count=$((count+1))
done

echo "Completed $count write+read operations"
echo "Stale reads detected: $stale times ($(( stale * 100 / count ))%)"
```

---

## Part 9: Visualize in Grafana

### Generate some data for visualization

```bash {"name": "generate-data"}
echo "Generating data for Grafana visualization..."
for i in {1..20}; do
  curl -s -X POST http://localhost:8000/data \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"viz-$i\", \"value\": \"data-$i\"}" > /dev/null
  echo -n "."
  sleep 0.3
done
echo ""
echo "Done! Check Grafana at http://localhost:3001"
echo "Look for the 'Eventual Consistency Dashboard'"
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo "Lab cleaned up"
```

### Reset data without stopping services

```bash {"name": "reset-data"}
curl -s -X POST http://localhost:8000/admin/reset | jq
echo "All data reset to initial state"
```

---

## Troubleshooting Commands

### Check all service logs

```bash {"name": "logs-all"}
docker compose logs --tail=30
```

### Check primary logs

```bash {"name": "logs-primary"}
docker compose logs lab14-primary --tail=50
```

### Check replica logs

```bash {"name": "logs-replicas"}
echo "=== Replica 1 ==="
docker compose logs lab14-replica1 --tail=20
echo ""
echo "=== Replica 2 ==="
docker compose logs lab14-replica2 --tail=20
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart a specific service

```bash {"name": "restart-primary"}
docker compose restart lab14-primary
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Write data | `curl -X POST localhost:8000/data -d '{"key":"k","value":"v"}'` |
| Check primary | `curl localhost:8000/version` |
| Check replica 1 | `curl localhost:8001/version` |
| Check replica 2 | `curl localhost:8002/version` |
| Compare all | `curl localhost:8003/read-compare` |
| Consistency status | `curl localhost:8003/status` |
| Set replication delay | `curl -X POST localhost:8000/admin/replication-delay -d '{"replica_url":"...","delay_ms":1000}'` |
| Reset data | `curl -X POST localhost:8000/admin/reset` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
