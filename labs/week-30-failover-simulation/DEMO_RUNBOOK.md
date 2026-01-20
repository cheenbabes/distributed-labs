# Demo Runbook: Failover Simulation

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
echo "=== Gateway ==="
curl -s http://localhost:8000/health | jq

echo ""
echo "=== Primary DB ==="
curl -s http://localhost:8001/health | jq

echo ""
echo "=== Replica 1 ==="
curl -s http://localhost:8002/health | jq

echo ""
echo "=== Replica 2 ==="
curl -s http://localhost:8003/health | jq

echo ""
echo "=== Failover Controller ==="
curl -s http://localhost:8010/health | jq
```

---

## Part 2: Normal Operations

### Show cluster topology

```bash {"name": "show-topology"}
curl -s http://localhost:8010/cluster/topology | jq
```

### Write some data to the primary

```bash {"name": "write-data"}
echo "Writing 5 records..."
for i in {1..5}; do
  curl -s -X POST http://localhost:8000/write \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"user:$i\", \"value\": {\"name\": \"User $i\", \"created\": \"$(date -Iseconds)\"}}" | jq -c
done
```

### Read data from replicas

```bash {"name": "read-from-replicas"}
echo "Reading from replicas (note which replica serves each request):"
for i in {1..5}; do
  echo "--- Read $i ---"
  curl -s http://localhost:8000/read/user:$i | jq -c '.{key, routed_to, result}'
done
```

### Check replication lag

```bash {"name": "check-replication-lag"}
echo "=== Primary Sequence ==="
curl -s http://localhost:8001/sequence | jq

echo ""
echo "=== Replica 1 Sequence ==="
curl -s http://localhost:8002/sequence | jq

echo ""
echo "=== Replica 2 Sequence ==="
curl -s http://localhost:8003/sequence | jq
```

---

## Part 3: Open Observability UIs

### Print all URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Gateway API:          http://localhost:8000"
echo "  Primary DB:           http://localhost:8001"
echo "  Replica 1:            http://localhost:8002"
echo "  Replica 2:            http://localhost:8003"
echo "  Failover Controller:  http://localhost:8010"
echo ""
echo "  Grafana:    http://localhost:3001 (admin/admin)"
echo "  Prometheus: http://localhost:9090"
echo "  Jaeger:     http://localhost:16686"
```

---

## Part 4: Simulate Primary Failure

### Check cluster status before failure

```bash {"name": "pre-failure-status"}
curl -s http://localhost:8010/status | jq
```

### Simulate primary failure

```bash {"name": "simulate-failure"}
echo "Simulating primary failure..."
curl -s -X POST http://localhost:8001/admin/simulate-failure \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' | jq
```

### Watch failover happen

```bash {"name": "watch-failover"}
echo "Watching failover (checking every second for 10 seconds)..."
for i in {1..10}; do
  echo "=== Check $i ==="
  STATUS=$(curl -s http://localhost:8010/status)
  echo "$STATUS" | jq '{primary_healthy, consecutive_failures, current_primary, failover_in_progress}'
  sleep 1
done
```

### Verify failover completed

```bash {"name": "verify-failover"}
echo "=== Cluster Status After Failover ==="
curl -s http://localhost:8010/status | jq

echo ""
echo "=== New Topology ==="
curl -s http://localhost:8010/cluster/topology | jq
```

### Test writes go to new primary

```bash {"name": "test-new-primary"}
echo "Writing to new primary..."
curl -s -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"key": "after-failover", "value": {"message": "This was written after failover!"}}' | jq
```

---

## Part 5: Measure RTO and RPO

### Check RTO (Recovery Time Objective)

```bash {"name": "check-rto"}
echo "RTO (time to recover from failure):"
curl -s http://localhost:9090/api/v1/query?query=rto_seconds | jq '.data.result[0].value[1]'
```

### Check RPO (Recovery Point Objective)

```bash {"name": "check-rpo"}
echo "RPO (sequences potentially lost):"
curl -s http://localhost:9090/api/v1/query?query=rpo_sequences_lost | jq '.data.result[0].value[1]'
```

### View key metrics in Prometheus

```bash {"name": "view-metrics"}
echo "=== Failover Count ==="
curl -s 'http://localhost:9090/api/v1/query?query=failover_count_total' | jq '.data.result'

echo ""
echo "=== Current Primary Health ==="
curl -s 'http://localhost:9090/api/v1/query?query=failover_primary_health' | jq '.data.result'

echo ""
echo "=== Replication Lag ==="
curl -s 'http://localhost:9090/api/v1/query?query=replication_lag_ms' | jq '.data.result'
```

---

## Part 6: Split-Brain Detection

### Check for split-brain

```bash {"name": "check-split-brain"}
curl -s http://localhost:8010/admin/split-brain-status | jq
```

### Simulate split-brain

```bash {"name": "simulate-split-brain"}
echo "Simulating split-brain scenario..."
curl -s -X POST http://localhost:8010/admin/simulate-split-brain \
  -H "Content-Type: application/json" \
  -d '{"simulate": true}' | jq
```

### Verify split-brain detected

```bash {"name": "verify-split-brain"}
echo "=== Split-Brain Status ==="
curl -s http://localhost:8010/admin/split-brain-status | jq

echo ""
echo "=== Split-Brain Metric ==="
curl -s 'http://localhost:9090/api/v1/query?query=split_brain_detected' | jq '.data.result'
```

### Clear split-brain simulation

```bash {"name": "clear-split-brain"}
curl -s -X POST http://localhost:8010/admin/simulate-split-brain \
  -H "Content-Type: application/json" \
  -d '{"simulate": false}' | jq
```

---

## Part 7: Reset and Restore

### Reset cluster to initial state

```bash {"name": "reset-cluster"}
echo "Resetting cluster to initial state..."
curl -s -X POST http://localhost:8010/admin/reset-cluster | jq
```

### Re-enable original primary

```bash {"name": "enable-primary"}
echo "Re-enabling original primary..."
curl -s -X POST http://localhost:8001/admin/simulate-failure \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' | jq
```

### Verify restored state

```bash {"name": "verify-restored"}
sleep 3
echo "=== Cluster Status After Reset ==="
curl -s http://localhost:8010/status | jq
```

---

## Part 8: Load Testing

### Run basic load test

```bash {"name": "basic-load-test"}
docker compose run --rm lab30-k6 run /scripts/basic.js
```

### Run failover load test

```bash {"name": "failover-load-test", "background": true}
docker compose run --rm lab30-k6 run /scripts/failover-test.js
```

### Quick inline load test

```bash {"name": "quick-load"}
echo "Running 20-second mixed read/write load..."
end=$((SECONDS+20))
writes=0
reads=0
errors=0

while [ $SECONDS -lt $end ]; do
  # 30% writes, 70% reads
  if [ $((RANDOM % 10)) -lt 3 ]; then
    result=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/write \
      -H "Content-Type: application/json" \
      -d "{\"key\": \"load-$RANDOM\", \"value\": {\"ts\": $SECONDS}}")
    if [ "$result" = "200" ]; then
      writes=$((writes + 1))
    else
      errors=$((errors + 1))
    fi
  else
    result=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/read/user:1)
    if [ "$result" = "200" ] || [ "$result" = "404" ]; then
      reads=$((reads + 1))
    else
      errors=$((errors + 1))
    fi
  fi
done

echo "Completed: $writes writes, $reads reads, $errors errors"
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

### View all logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### View specific service logs

```bash {"name": "logs-primary"}
docker compose logs lab30-primary-db --tail=50
```

```bash {"name": "logs-failover-controller"}
docker compose logs lab30-failover-controller --tail=50
```

```bash {"name": "logs-gateway"}
docker compose logs lab30-client-gateway --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart a specific service

```bash {"name": "restart-failover-controller"}
docker compose restart lab30-failover-controller
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Simulate primary failure | `curl -X POST localhost:8001/admin/simulate-failure -d '{"enabled":true}'` |
| Recover primary | `curl -X POST localhost:8001/admin/simulate-failure -d '{"enabled":false}'` |
| Check cluster status | `curl localhost:8010/status \| jq` |
| Write data | `curl -X POST localhost:8000/write -d '{"key":"x","value":"y"}'` |
| Read data | `curl localhost:8000/read/x` |
| Reset cluster | `curl -X POST localhost:8010/admin/reset-cluster` |
| View dashboard | http://localhost:3001 (admin/admin) |
| View traces | http://localhost:16686 |
