# Demo Runbook: Unique ID Generator (Snowflake IDs)

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
sleep 20
docker compose ps
```

### Verify all services are responding

```bash {"name": "verify-services"}
echo "=== Coordinator ==="
curl -s http://localhost:8100/health | jq

echo -e "\n=== Generator 1 ==="
curl -s http://localhost:8001/health | jq

echo -e "\n=== Generator 2 ==="
curl -s http://localhost:8002/health | jq

echo -e "\n=== Generator 3 ==="
curl -s http://localhost:8003/health | jq

echo -e "\n=== Client ==="
curl -s http://localhost:8000/health | jq
```

---

## Part 2: Understanding Snowflake IDs

### Show URLs for UIs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Client API:  http://localhost:8000"
echo "  Jaeger:      http://localhost:16686"
echo "  Grafana:     http://localhost:3001 (admin/admin)"
echo "  Prometheus:  http://localhost:9090"
echo "  Coordinator: http://localhost:8100"
```

### Generate your first Snowflake ID

```bash {"name": "first-id"}
echo "Generating a Snowflake ID..."
curl -s http://localhost:8000/id | jq
```

### Parse the ID to see its components

```bash {"name": "parse-id"}
ID=$(curl -s http://localhost:8000/id | jq -r '.id')
echo "Generated ID: $ID"
echo ""
echo "Parsing the ID..."
curl -s "http://localhost:8000/parse/$ID" | jq
```

### Show ID structure information

```bash {"name": "id-structure"}
curl -s http://localhost:8001/info | jq
```

---

## Part 3: Multiple Workers in Action

### Check registered workers

```bash {"name": "check-workers"}
curl -s http://localhost:8100/workers | jq
```

### Generate IDs from different workers

```bash {"name": "worker-distribution"}
echo "Generating 12 IDs and showing worker distribution..."
for i in {1..12}; do
  result=$(curl -s http://localhost:8000/id)
  worker=$(echo $result | jq -r '.worker_id')
  id=$(echo $result | jq -r '.id')
  echo "ID: $id -> Worker: $worker"
done
```

### Check generator health status

```bash {"name": "generators-status"}
curl -s http://localhost:8000/generators | jq
```

---

## Part 4: Time-Sortability Demo

### Generate IDs with delays to show time ordering

```bash {"name": "time-ordering"}
echo "Generating IDs with 1-second delays..."
echo ""

ID1=$(curl -s http://localhost:8000/id | jq -r '.id')
echo "ID1: $ID1"
sleep 1

ID2=$(curl -s http://localhost:8000/id | jq -r '.id')
echo "ID2: $ID2"
sleep 1

ID3=$(curl -s http://localhost:8000/id | jq -r '.id')
echo "ID3: $ID3"

echo ""
echo "Verifying order: ID1 < ID2 < ID3"
if [ "$ID1" -lt "$ID2" ] && [ "$ID2" -lt "$ID3" ]; then
  echo "PASS: IDs are correctly time-ordered!"
else
  echo "FAIL: IDs are not in order"
fi

echo ""
echo "Timestamps extracted from IDs:"
echo "ID1: $(curl -s "http://localhost:8000/parse/$ID1" | jq -r '.components.timestamp_utc')"
echo "ID2: $(curl -s "http://localhost:8000/parse/$ID2" | jq -r '.components.timestamp_utc')"
echo "ID3: $(curl -s "http://localhost:8000/parse/$ID3" | jq -r '.components.timestamp_utc')"
```

---

## Part 5: High Throughput Demo

### Generate batch of IDs

```bash {"name": "batch-generation"}
echo "Generating 50 IDs in a batch..."
curl -s "http://localhost:8001/generate/batch?count=50" | jq '{
  count: .count,
  generation_time_ms: .generation_time_ms,
  avg_time_per_id_us: .avg_time_per_id_us,
  worker_id: .worker_id,
  first_5_ids: .ids[:5]
}'
```

### Parallel generation from all generators

```bash {"name": "parallel-generation"}
echo "Generating 100 IDs from each generator in parallel..."
curl -s "http://localhost:8000/ids/parallel?count=100" | jq '{
  total_ids: .total_ids,
  collisions: .collisions_detected,
  generators: .generators_used,
  total_time_ms: .total_time_ms
}'
```

---

## Part 6: Clock Skew Simulation

### Check current state

```bash {"name": "check-before-skew"}
echo "Generator 1 info before clock skew:"
curl -s http://localhost:8001/info | jq
```

### Simulate small clock skew (generator handles it)

```bash {"name": "small-clock-skew"}
echo "Simulating clock moving backwards by 5ms..."
curl -s -X POST "http://localhost:8001/admin/clock-offset?offset_ms=-5" | jq

echo ""
echo "Trying to generate an ID (should succeed after waiting)..."
curl -s http://localhost:8001/generate | jq
```

### Check clock skew metrics

```bash {"name": "check-skew-metrics"}
echo "Clock skew events:"
curl -s http://localhost:8001/metrics | grep clock_skew
```

### Simulate large clock skew (generator rejects it)

```bash {"name": "large-clock-skew"}
echo "Simulating clock moving backwards by 100ms..."
curl -s -X POST "http://localhost:8001/admin/clock-offset?offset_ms=-100" | jq

echo ""
echo "Trying to generate an ID (should fail)..."
curl -s http://localhost:8001/generate
```

### Reset clock offset

```bash {"name": "reset-clock"}
echo "Resetting clock offset..."
curl -s -X POST "http://localhost:8001/admin/clock-offset?offset_ms=0" | jq

echo ""
echo "Verifying generation works again..."
curl -s http://localhost:8001/generate | jq
```

---

## Part 7: Collision Detection

### Check collision stats

```bash {"name": "collision-stats"}
curl -s http://localhost:8000/collision-stats | jq
```

### Generate many IDs and verify no collisions

```bash {"name": "no-collisions"}
echo "Generating 500 IDs and checking for collisions..."
result=$(curl -s "http://localhost:8000/ids/parallel?count=500")
echo "$result" | jq '{
  total_ids: .total_ids,
  collisions_detected: .collisions_detected
}'

if [ "$(echo $result | jq '.collisions_detected')" = "0" ]; then
  echo ""
  echo "SUCCESS: No collisions detected!"
else
  echo ""
  echo "WARNING: Collisions detected - check worker IDs!"
fi
```

### Reset collision tracking

```bash {"name": "reset-collisions"}
curl -s -X POST http://localhost:8000/collision-stats/reset | jq
```

---

## Part 8: Load Testing

### Run quick load test (50 seconds)

```bash {"name": "quick-load-test"}
echo "Running quick load test..."
docker compose run --rm lab48-k6 run /scripts/quick.js
```

### Run full load test (about 6 minutes)

```bash {"name": "full-load-test", "background": true}
echo "Running full load test (this takes about 6 minutes)..."
docker compose run --rm lab48-k6 run /scripts/basic.js
```

---

## Part 9: Worker Failover

### Stop one generator

```bash {"name": "stop-generator"}
echo "Stopping generator 2..."
docker compose stop lab48-id-generator-2

echo ""
echo "Checking workers (should show 2 active)..."
sleep 2
curl -s http://localhost:8100/workers | jq
```

### Generate IDs with one less worker

```bash {"name": "generate-with-fewer"}
echo "Generating IDs (using remaining workers)..."
for i in {1..6}; do
  curl -s http://localhost:8000/id | jq -r '"ID from worker \(.worker_id)"'
done
```

### Restart the stopped generator

```bash {"name": "restart-generator"}
echo "Restarting generator 2..."
docker compose start lab48-id-generator-2

echo ""
echo "Waiting for registration..."
sleep 10

echo "Checking workers (should show 3 active again)..."
curl -s http://localhost:8100/workers | jq
```

---

## Part 10: Binary Representation

### Show the binary structure of an ID

```bash {"name": "binary-structure"}
ID=$(curl -s http://localhost:8000/id | jq -r '.id')
echo "ID: $ID"
echo ""

parsed=$(curl -s "http://localhost:8000/parse/$ID")

echo "Binary representation:"
echo "$parsed" | jq -r '.binary'

echo ""
echo "Components:"
echo "$parsed" | jq '.components'

echo ""
echo "Bit layout:"
echo "  [0]     - Unused (always 0)"
echo "  [1-41]  - Timestamp (ms since epoch)"
echo "  [42-46] - Datacenter ID"
echo "  [47-51] - Worker ID"
echo "  [52-63] - Sequence number"
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

### Check coordinator logs

```bash {"name": "logs-coordinator"}
docker compose logs lab48-coordinator --tail=50
```

### Check generator logs

```bash {"name": "logs-generators"}
docker compose logs lab48-id-generator-1 lab48-id-generator-2 lab48-id-generator-3 --tail=30
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart all generators

```bash {"name": "restart-generators"}
docker compose restart lab48-id-generator-1 lab48-id-generator-2 lab48-id-generator-3
sleep 10
curl -s http://localhost:8100/workers | jq
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Generate single ID | `curl localhost:8000/id` |
| Generate batch | `curl "localhost:8000/ids/batch?count=10"` |
| Parse ID | `curl localhost:8000/parse/{id}` |
| Check workers | `curl localhost:8100/workers` |
| Simulate clock skew | `curl -X POST "localhost:8001/admin/clock-offset?offset_ms=-10"` |
| Reset clock | `curl -X POST "localhost:8001/admin/clock-offset?offset_ms=0"` |
| Collision stats | `curl localhost:8000/collision-stats` |
| View traces | http://localhost:16686 |
| View metrics | http://localhost:3001 |
