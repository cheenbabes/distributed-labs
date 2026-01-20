# Demo Runbook: Netflix Distributed Counter

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
echo "Gateway:"
curl -s http://localhost:8080/health | jq
echo ""
echo "Coordinator:"
curl -s http://localhost:8000/health | jq
echo ""
echo "US-East Counter:"
curl -s http://localhost:8001/health | jq
echo ""
echo "US-West Counter:"
curl -s http://localhost:8002/health | jq
echo ""
echo "EU-West Counter:"
curl -s http://localhost:8003/health | jq
```

---

## Part 2: Show URLs

### Display all service URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Gateway API:     http://localhost:8080"
echo "  Coordinator:     http://localhost:8000"
echo "  US-East Counter: http://localhost:8001"
echo "  US-West Counter: http://localhost:8002"
echo "  EU-West Counter: http://localhost:8003"
echo "  Jaeger:          http://localhost:16686"
echo "  Grafana:         http://localhost:3001 (admin/admin)"
echo "  Prometheus:      http://localhost:9090"
```

---

## Part 3: Basic Counting Demo

### Increment counter via gateway

```bash {"name": "increment-single"}
curl -s -X POST http://localhost:8080/api/increment \
  -H "Content-Type: application/json" \
  -d '{"counter_name": "views", "amount": 1}' | jq
```

### Increment 10 times and show distribution

```bash {"name": "increment-multiple"}
echo "Incrementing 10 times..."
for i in {1..10}; do
  result=$(curl -s -X POST http://localhost:8080/api/increment \
    -H "Content-Type: application/json" \
    -d '{"counter_name": "views", "amount": 1}')
  region=$(echo $result | jq -r '.region')
  local=$(echo $result | jq -r '.local_count')
  echo "Request $i: routed to $region (local count: $local)"
done
```

### Get global counter view

```bash {"name": "get-global"}
curl -s http://localhost:8080/api/counter/views | jq
```

---

## Part 4: Demonstrate Eventual Consistency

### Increment directly on US-East

```bash {"name": "increment-us-east"}
curl -s -X POST http://localhost:8001/increment \
  -H "Content-Type: application/json" \
  -d '{"counter_name": "consistency_test", "amount": 100}' | jq
```

### Check US-West immediately (before sync)

```bash {"name": "check-us-west-before"}
echo "Checking US-West immediately after US-East increment..."
curl -s http://localhost:8002/counter/consistency_test | jq
```

### Wait for sync and check again

```bash {"name": "check-us-west-after"}
echo "Waiting 6 seconds for sync..."
sleep 6
echo "Checking US-West after sync..."
curl -s http://localhost:8002/counter/consistency_test | jq
```

### Show convergence

```bash {"name": "show-convergence"}
echo "=== All Regions After Sync ==="
echo ""
echo "US-East:"
curl -s http://localhost:8001/counter/consistency_test | jq '{total: .total_count, nodes: .nodes}'
echo ""
echo "US-West:"
curl -s http://localhost:8002/counter/consistency_test | jq '{total: .total_count, nodes: .nodes}'
echo ""
echo "EU-West:"
curl -s http://localhost:8003/counter/consistency_test | jq '{total: .total_count, nodes: .nodes}'
```

---

## Part 5: Network Partition Demo

### Check current status (all healthy)

```bash {"name": "check-status-before"}
curl -s http://localhost:8080/api/status | jq
```

### Enable partition on US-East

```bash {"name": "enable-partition"}
curl -s -X POST "http://localhost:8080/admin/partition/us-east?enabled=true" | jq
```

### Increment while partitioned

```bash {"name": "increment-partitioned"}
echo "Incrementing 20 times while US-East is partitioned..."
for i in {1..20}; do
  curl -s -X POST http://localhost:8080/api/increment \
    -H "Content-Type: application/json" \
    -d '{"counter_name": "partition_demo", "amount": 1}' > /dev/null
  echo -n "."
done
echo ""
echo "Done!"
```

### Check divergence

```bash {"name": "check-divergence"}
echo "Checking counter state (should show divergence)..."
curl -s http://localhost:8080/api/counter/partition_demo | jq '{
  global_count: .global_count,
  converged: .convergence.converged,
  lag: .convergence.lag,
  regions: .regions | to_entries | map({region: .key, total: .value.total_count})
}'
```

### Show US-East is still counting locally

```bash {"name": "show-local-counting"}
echo "US-East partition status:"
curl -s http://localhost:8001/admin/partition | jq

echo ""
echo "US-East can still increment locally:"
curl -s -X POST http://localhost:8001/increment \
  -H "Content-Type: application/json" \
  -d '{"counter_name": "partition_demo", "amount": 50}' | jq
```

### Heal partition

```bash {"name": "heal-partition"}
echo "Healing partition..."
curl -s -X POST "http://localhost:8080/admin/partition/us-east?enabled=false" | jq
```

### Force sync

```bash {"name": "force-sync"}
echo "Forcing immediate sync..."
curl -s -X POST http://localhost:8080/admin/force-sync | jq
```

### Verify convergence after heal

```bash {"name": "verify-convergence"}
sleep 2
echo "Checking convergence after partition healed..."
curl -s http://localhost:8080/api/counter/partition_demo | jq '{
  global_count: .global_count,
  converged: .convergence.converged,
  lag: .convergence.lag,
  regions: .regions | to_entries | map({region: .key, total: .value.total_count})
}'
```

---

## Part 6: Consistency Comparison

### Run built-in comparison

```bash {"name": "run-comparison"}
echo "Comparing Eventual vs Strong Consistency..."
curl -s -X POST http://localhost:8080/api/compare \
  -H "Content-Type: application/json" \
  -d '{"counter_name": "benchmark", "iterations": 30}' | jq
```

### Increment on strongly consistent counter

```bash {"name": "increment-consistent"}
echo "Incrementing on strongly consistent counter..."
time curl -s -X POST http://localhost:8004/increment \
  -H "Content-Type: application/json" \
  -d '{"counter_name": "strong_test", "amount": 1}' | jq
```

### Compare latencies side-by-side

```bash {"name": "compare-latencies"}
echo "=== Eventual Consistency (CRDT) ==="
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" -X POST http://localhost:8080/api/increment \
    -H "Content-Type: application/json" \
    -d '{"counter_name": "latency_test", "amount": 1}')
  echo "Request $i: ${time}s"
done

echo ""
echo "=== Strong Consistency ==="
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" -X POST http://localhost:8004/increment \
    -H "Content-Type: application/json" \
    -d '{"counter_name": "latency_test", "amount": 1}')
  echo "Request $i: ${time}s"
done
```

---

## Part 7: CRDT State Deep Dive

### Show G-Counter state from each region

```bash {"name": "show-crdt-state"}
echo "=== G-Counter State ==="
echo ""
echo "Each region maintains a vector of counts indexed by node ID."
echo "To get total: sum all values. To merge: take max of each."
echo ""
echo "US-East state:"
curl -s http://localhost:8001/counter/views | jq '.nodes'
echo ""
echo "US-West state:"
curl -s http://localhost:8002/counter/views | jq '.nodes'
echo ""
echo "EU-West state:"
curl -s http://localhost:8003/counter/views | jq '.nodes'
```

---

## Part 8: Load Testing (Optional)

### Run basic load test

```bash {"name": "load-test-basic"}
docker compose run --rm lab34-k6 run /scripts/basic.js
```

### Run comparison load test

```bash {"name": "load-test-comparison"}
docker compose run --rm lab34-k6 run /scripts/comparison.js
```

### Run partition test

```bash {"name": "load-test-partition"}
docker compose run --rm lab34-k6 run /scripts/partition-test.js
```

---

## Part 9: Generate Traffic for Grafana

### Generate sustained traffic

```bash {"name": "generate-traffic"}
echo "Generating traffic for 30 seconds..."
end=$((SECONDS+30))
count=0
while [ $SECONDS -lt $end ]; do
  curl -s -X POST http://localhost:8080/api/increment \
    -H "Content-Type: application/json" \
    -d '{"counter_name": "views", "amount": 1}' > /dev/null &
  count=$((count+1))
  sleep 0.1
done
wait
echo "Completed $count requests"
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo "Lab cleaned up"
```

---

## Troubleshooting

### Check all logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check specific service logs

```bash {"name": "logs-us-east"}
docker compose logs lab34-counter-us-east --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart a service

```bash {"name": "restart-counter"}
docker compose restart lab34-counter-us-east
```

### Reset all partitions

```bash {"name": "reset-partitions"}
curl -s -X POST "http://localhost:8080/admin/partition/all?enabled=false" | jq
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Increment counter | `curl -X POST localhost:8080/api/increment -d '{"counter_name":"views","amount":1}'` |
| Get global count | `curl localhost:8080/api/counter/views` |
| Enable partition | `curl -X POST localhost:8080/admin/partition/us-east?enabled=true` |
| Disable partition | `curl -X POST localhost:8080/admin/partition/us-east?enabled=false` |
| Force sync | `curl -X POST localhost:8080/admin/force-sync` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
