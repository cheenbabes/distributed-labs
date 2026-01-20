# Demo Runbook: Dead Letter Queues

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

```bash {"name": "start-lab", "background": true}
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
echo "Producer:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Worker:"
curl -s http://localhost:8001/health | jq
echo ""
echo "DLQ Consumer:"
curl -s http://localhost:8002/health | jq
```

---

## Part 2: Normal Message Flow

### Show current queue status (should be empty)

```bash {"name": "initial-queue-status"}
curl -s http://localhost:8000/queues/status | jq
```

### Publish a single task

```bash {"name": "publish-single"}
curl -s -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{"task_type": "email", "payload": {"to": "user@example.com"}}' | jq
```

### Wait for processing and check DLQ (should be empty)

```bash {"name": "check-dlq-empty"}
sleep 2
echo "DLQ Status (should be empty):"
curl -s http://localhost:8002/dlq | jq
```

### Publish multiple tasks

```bash {"name": "publish-bulk"}
curl -s -X POST http://localhost:8000/tasks/bulk \
  -H "Content-Type: application/json" \
  -d '{"count": 10, "task_type": "normal_test"}' | jq
```

### Verify all processed successfully

```bash {"name": "verify-normal-processing"}
sleep 3
echo "Queue Status:"
curl -s http://localhost:8000/queues/status | jq
echo ""
echo "DLQ Status:"
curl -s http://localhost:8002/dlq/stats | jq
```

---

## Part 3: Open Observability UIs

### Print URLs for browser access

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  RabbitMQ:   http://localhost:15672 (guest/guest)"
echo "  Jaeger:     http://localhost:16686"
echo "  Grafana:    http://localhost:3001 (admin/admin)"
echo "  Prometheus: http://localhost:9090"
echo ""
echo "APIs:"
echo "  Producer:   http://localhost:8000"
echo "  Worker:     http://localhost:8001"
echo "  DLQ:        http://localhost:8002"
```

---

## Part 4: Inject Failures

### Check current worker configuration

```bash {"name": "check-worker-config"}
curl -s http://localhost:8001/admin/config | jq
```

### Enable 50% failure rate

```bash {"name": "enable-failures"}
curl -s -X POST http://localhost:8001/admin/config \
  -H "Content-Type: application/json" \
  -d '{"failure_rate": 0.5}' | jq
```

### Send messages that will partially fail

```bash {"name": "send-failing-messages"}
curl -s -X POST http://localhost:8000/tasks/bulk \
  -H "Content-Type: application/json" \
  -d '{"count": 20, "task_type": "failure_test"}' | jq
```

### Wait and check DLQ accumulation

```bash {"name": "wait-for-failures"}
echo "Waiting for retries and DLQ routing..."
sleep 10
echo ""
echo "DLQ Statistics:"
curl -s http://localhost:8002/dlq/stats | jq
```

---

## Part 5: Inspect DLQ Contents

### View messages in DLQ

```bash {"name": "view-dlq-contents"}
curl -s http://localhost:8002/dlq?limit=5 | jq
```

### Get a specific failed message

```bash {"name": "inspect-single-message"}
TASK_ID=$(curl -s http://localhost:8002/dlq?limit=1 | jq -r '.messages[0].task_id')
echo "Examining task: $TASK_ID"
echo ""
curl -s "http://localhost:8002/dlq/message/$TASK_ID" | jq
```

### Check queue depths

```bash {"name": "check-queue-depths"}
curl -s http://localhost:8000/queues/status | jq
```

---

## Part 6: Replay Failed Messages

### First, fix the "bug" by disabling failures

```bash {"name": "disable-failures"}
curl -s -X POST http://localhost:8001/admin/config \
  -H "Content-Type: application/json" \
  -d '{"failure_rate": 0.0}' | jq
```

### Check DLQ depth before replay

```bash {"name": "dlq-before-replay"}
curl -s http://localhost:8002/dlq/stats | jq
```

### Replay 5 messages from DLQ

```bash {"name": "replay-messages"}
curl -s -X POST http://localhost:8002/dlq/replay \
  -H "Content-Type: application/json" \
  -d '{"count": 5, "reset_attempts": true}' | jq
```

### Check DLQ depth after replay

```bash {"name": "dlq-after-replay"}
sleep 2
curl -s http://localhost:8002/dlq/stats | jq
```

### Replay all remaining messages

```bash {"name": "replay-all"}
DLQ_COUNT=$(curl -s http://localhost:8002/dlq/stats | jq -r '.total_messages')
echo "Replaying $DLQ_COUNT messages..."
curl -s -X POST http://localhost:8002/dlq/replay \
  -H "Content-Type: application/json" \
  -d "{\"count\": $DLQ_COUNT, \"reset_attempts\": true}" | jq
```

### Verify DLQ is empty

```bash {"name": "verify-empty-dlq"}
sleep 3
curl -s http://localhost:8002/dlq/stats | jq
```

---

## Part 7: Retry Policy Experiments

### Set aggressive failure with minimal retries

```bash {"name": "minimal-retries"}
curl -s -X POST http://localhost:8001/admin/config \
  -H "Content-Type: application/json" \
  -d '{
    "failure_rate": 0.8,
    "max_retries": 1,
    "retry_delay_ms": 100
  }' | jq
```

### Send messages - quick DLQ accumulation

```bash {"name": "send-with-minimal-retries"}
curl -s -X POST http://localhost:8000/tasks/bulk \
  -H "Content-Type: application/json" \
  -d '{"count": 10, "task_type": "min_retry_test"}' | jq

sleep 3
curl -s http://localhost:8002/dlq/stats | jq
```

### Set more aggressive retry policy

```bash {"name": "aggressive-retries"}
curl -s -X POST http://localhost:8001/admin/config \
  -H "Content-Type: application/json" \
  -d '{
    "failure_rate": 0.5,
    "max_retries": 5,
    "retry_delay_ms": 500
  }' | jq
```

### Send messages - observe retry behavior in logs

```bash {"name": "send-with-more-retries"}
curl -s -X POST http://localhost:8000/tasks/bulk \
  -H "Content-Type: application/json" \
  -d '{"count": 5, "task_type": "max_retry_test"}' | jq

echo ""
echo "Watch the worker logs for retry activity:"
echo "docker compose logs -f lab17-worker"
```

---

## Part 8: Purge DLQ

### Check current DLQ state

```bash {"name": "check-before-purge"}
curl -s http://localhost:8002/dlq/stats | jq
```

### Purge all messages from DLQ

```bash {"name": "purge-dlq"}
curl -s -X DELETE http://localhost:8002/dlq/purge | jq
```

### Verify DLQ is purged

```bash {"name": "verify-purge"}
curl -s http://localhost:8002/dlq/stats | jq
```

---

## Part 9: Load Testing

### Reset to moderate failure rate

```bash {"name": "reset-for-loadtest"}
curl -s -X POST http://localhost:8001/admin/config \
  -H "Content-Type: application/json" \
  -d '{"failure_rate": 0.3, "max_retries": 3, "retry_delay_ms": 1000}' | jq
```

### Run k6 load test

```bash {"name": "load-test", "background": true}
docker compose run --rm lab17-k6 run /scripts/basic.js
```

### Quick inline load test

```bash {"name": "quick-load"}
echo "Running 30-second load test..."
end=$((SECONDS+30))
count=0
while [ $SECONDS -lt $end ]; do
  curl -s -X POST http://localhost:8000/tasks \
    -H "Content-Type: application/json" \
    -d '{"task_type": "load_test"}' > /dev/null &
  count=$((count+1))
  sleep 0.1
done
wait
echo "Completed $count requests in 30 seconds"
```

### Check final state after load test

```bash {"name": "post-load-state"}
echo "Queue Status:"
curl -s http://localhost:8000/queues/status | jq
echo ""
echo "DLQ Stats:"
curl -s http://localhost:8002/dlq/stats | jq
```

---

## Part 10: Burst Test

### Run burst test (sends many messages quickly)

```bash {"name": "burst-test"}
docker compose run --rm lab17-k6 run /scripts/burst.js
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

### Check all service logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check worker logs

```bash {"name": "logs-worker"}
docker compose logs lab17-worker --tail=100
```

### Check RabbitMQ logs

```bash {"name": "logs-rabbitmq"}
docker compose logs lab17-rabbitmq --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart worker

```bash {"name": "restart-worker"}
docker compose restart lab17-worker
```

### Restart all services

```bash {"name": "restart-all"}
docker compose restart
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Publish task | `curl -X POST localhost:8000/tasks -d '{"task_type":"test"}'` |
| Publish bulk | `curl -X POST localhost:8000/tasks/bulk -d '{"count":10}'` |
| Set failure rate | `curl -X POST localhost:8001/admin/config -d '{"failure_rate":0.5}'` |
| View DLQ | `curl localhost:8002/dlq` |
| Replay from DLQ | `curl -X POST localhost:8002/dlq/replay -d '{"count":5}'` |
| Purge DLQ | `curl -X DELETE localhost:8002/dlq/purge` |
| Queue depths | `curl localhost:8000/queues/status` |
| Worker config | `curl localhost:8001/admin/config` |
