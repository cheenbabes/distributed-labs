# Demo Runbook: Quorum Reads/Writes

This runbook contains all commands for the lab demo. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

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

### Verify coordinator is responding

```bash {"name": "verify-coordinator"}
curl -s http://localhost:8000/health | jq
```

### Check replica status

```bash {"name": "check-replicas"}
curl -s http://localhost:8000/replicas/status | jq
```

---

## Part 2: Understanding Quorum Configuration

### Show current configuration

```bash {"name": "show-config"}
curl -s http://localhost:8000/config | jq
```

### Show URLs for observability

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Client UI:  http://localhost:8080"
echo "  Coordinator: http://localhost:8000"
echo "  Jaeger:     http://localhost:16686"
echo "  Grafana:    http://localhost:3001 (admin/admin)"
echo "  Prometheus: http://localhost:9090"
```

---

## Part 3: Strong Consistency (R=3, W=3)

### Ensure strong consistency configuration

```bash {"name": "set-strong-consistency"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"r": 3, "w": 3, "mode": "strict"}' | jq
```

### Write a test value

```bash {"name": "write-test-1"}
curl -s -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"key": "demo-key", "value": "hello-quorum"}' | jq
```

### Read the value back

```bash {"name": "read-test-1"}
curl -s http://localhost:8000/read/demo-key | jq
```

### Run consistency test (10 iterations)

```bash {"name": "consistency-test"}
curl -s -X POST http://localhost:8080/api/test/consistency \
  -H "Content-Type: application/json" \
  -d '{"iterations": 10}' | jq
```

---

## Part 4: Weak Consistency (R=1, W=1)

### Switch to weak consistency

```bash {"name": "set-weak-consistency"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"r": 1, "w": 1}' | jq
```

### Rapid writes

```bash {"name": "rapid-writes"}
echo "Writing 10 values rapidly..."
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/write \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"rapid-$i\", \"value\": \"value-$i\"}" &
done
wait
echo "Done!"
```

### Read back rapidly

```bash {"name": "rapid-reads"}
echo "Reading values..."
for i in {1..10}; do
  result=$(curl -s http://localhost:8000/read/rapid-$i | jq -r '.value // "NOT FOUND"')
  echo "rapid-$i: $result"
done
```

### Run consistency test with weak config

```bash {"name": "weak-consistency-test"}
curl -s -X POST http://localhost:8080/api/test/consistency \
  -H "Content-Type: application/json" \
  -d '{"iterations": 20}' | jq
```

---

## Part 5: Node Failures

### Restore strong consistency first

```bash {"name": "restore-strong"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"r": 3, "w": 3, "mode": "strict"}' | jq
```

### Kill replica 1

```bash {"name": "kill-replica-1"}
curl -s -X POST http://localhost:9001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "error"}' | jq
```

### Test write with 1 replica down

```bash {"name": "write-1-down"}
curl -s -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"key": "one-down", "value": "should-work"}' | jq
```

### Kill replica 2

```bash {"name": "kill-replica-2"}
curl -s -X POST http://localhost:9002/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "error"}' | jq
```

### Test write with 2 replicas down

```bash {"name": "write-2-down"}
curl -s -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"key": "two-down", "value": "barely-works"}' | jq
```

### Kill replica 3 (should break quorum)

```bash {"name": "kill-replica-3"}
curl -s -X POST http://localhost:9003/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "error"}' | jq
```

### Test write with 3 replicas down (should fail)

```bash {"name": "write-3-down"}
echo "This should fail with 503 error:"
curl -s -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"key": "three-down", "value": "fails"}'
echo ""
```

### Check replica status

```bash {"name": "check-failure-status"}
curl -s http://localhost:8000/replicas/status | jq
```

### Restore all replicas

```bash {"name": "restore-replicas"}
for port in 9001 9002 9003 9004 9005; do
  curl -s -X POST http://localhost:$port/admin/failure \
    -H "Content-Type: application/json" \
    -d '{"mode": "none"}' > /dev/null
done
echo "All replicas restored"
curl -s http://localhost:8000/replicas/status | jq '.healthy_replicas'
```

---

## Part 6: Sloppy vs Strict Quorum

### Set sloppy quorum mode

```bash {"name": "set-sloppy"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "sloppy", "w": 2}' | jq
```

### Kill 3 replicas

```bash {"name": "kill-3-for-sloppy"}
for port in 9001 9002 9003; do
  curl -s -X POST http://localhost:$port/admin/failure \
    -H "Content-Type: application/json" \
    -d '{"mode": "error"}' > /dev/null
done
echo "3 replicas killed"
```

### Test with sloppy quorum (may succeed with only 2 replicas)

```bash {"name": "sloppy-test"}
curl -s -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"key": "sloppy-test", "value": "available"}' | jq
```

### Restore and reset to strict

```bash {"name": "reset-to-strict"}
for port in 9001 9002 9003 9004 9005; do
  curl -s -X POST http://localhost:$port/admin/failure \
    -H "Content-Type: application/json" \
    -d '{"mode": "none"}' > /dev/null
done
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "strict", "r": 3, "w": 3}' | jq
```

---

## Part 7: Latency Trade-offs

### Balanced configuration (R=3, W=3)

```bash {"name": "latency-balanced"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"r": 3, "w": 3}' > /dev/null
echo "=== R=3, W=3 (Balanced) ==="
for i in {1..3}; do
  write_time=$(curl -s -o /dev/null -w "%{time_total}" -X POST http://localhost:8000/write \
    -H "Content-Type: application/json" -d "{\"key\": \"balanced-$i\", \"value\": \"test\"}")
  read_time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/read/balanced-$i)
  echo "Write: ${write_time}s, Read: ${read_time}s"
done
```

### Fast reads configuration (R=1, W=5)

```bash {"name": "latency-fast-reads"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"r": 1, "w": 5}' > /dev/null
echo "=== R=1, W=5 (Fast Reads) ==="
for i in {1..3}; do
  write_time=$(curl -s -o /dev/null -w "%{time_total}" -X POST http://localhost:8000/write \
    -H "Content-Type: application/json" -d "{\"key\": \"fast-read-$i\", \"value\": \"test\"}")
  read_time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/read/fast-read-$i)
  echo "Write: ${write_time}s, Read: ${read_time}s"
done
```

### Fast writes configuration (R=5, W=1)

```bash {"name": "latency-fast-writes"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"r": 5, "w": 1}' > /dev/null
echo "=== R=5, W=1 (Fast Writes) ==="
for i in {1..3}; do
  write_time=$(curl -s -o /dev/null -w "%{time_total}" -X POST http://localhost:8000/write \
    -H "Content-Type: application/json" -d "{\"key\": \"fast-write-$i\", \"value\": \"test\"}")
  read_time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/read/fast-write-$i)
  echo "Write: ${write_time}s, Read: ${read_time}s"
done
```

### Reset to balanced

```bash {"name": "reset-balanced"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"r": 3, "w": 3}' | jq
```

---

## Part 8: Load Testing

### Run k6 load test

```bash {"name": "load-test"}
docker compose run --rm lab46-k6 run /scripts/quorum.js
```

### Load test with slow replica

```bash {"name": "load-test-slow-replica"}
# Make replica 3 slow
curl -s -X POST http://localhost:9003/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "timeout", "timeout_ms": 1000}' > /dev/null
echo "Replica 3 is now slow (1s timeout)"
echo "Running load test..."
docker compose run --rm lab46-k6 run /scripts/quorum.js --duration 30s
```

### Restore slow replica

```bash {"name": "restore-slow-replica"}
curl -s -X POST http://localhost:9003/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "none"}' | jq
```

---

## Part 9: Observing Traces

### Generate traces

```bash {"name": "generate-traces"}
echo "Generating 20 traces..."
for i in {1..20}; do
  curl -s -X POST http://localhost:8000/write \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"trace-$i\", \"value\": \"value-$i\"}" > /dev/null
  curl -s http://localhost:8000/read/trace-$i > /dev/null
  echo -n "."
done
echo ""
echo "Check Jaeger: http://localhost:16686"
echo "Select 'coordinator' service to see quorum traces"
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
docker compose logs lab46-coordinator --tail=50
```

### Check replica logs

```bash {"name": "logs-replicas"}
for i in 1 2 3 4 5; do
  echo "=== Replica $i ==="
  docker compose logs lab46-replica-$i --tail=10
done
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart coordinator

```bash {"name": "restart-coordinator"}
docker compose restart lab46-coordinator
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Check config | `curl localhost:8000/config` |
| Set R=3, W=3 | `curl -X POST localhost:8000/config -d '{"r":3,"w":3}'` |
| Set R=1, W=1 | `curl -X POST localhost:8000/config -d '{"r":1,"w":1}'` |
| Check replicas | `curl localhost:8000/replicas/status` |
| Kill replica N | `curl -X POST localhost:900N/admin/failure -d '{"mode":"error"}'` |
| Restore replica N | `curl -X POST localhost:900N/admin/failure -d '{"mode":"none"}'` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
