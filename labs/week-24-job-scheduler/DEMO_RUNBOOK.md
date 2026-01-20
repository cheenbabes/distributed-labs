# Demo Runbook: Distributed Job Scheduler

This runbook contains all commands for the video demo. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

---

## Pre-Demo Setup

### Check Docker is running

```bash {"name": "check-docker"}
docker info > /dev/null 2>&1 && echo "Docker is running" || echo "Docker is not running"
```

### Clean any previous lab state

```bash {"name": "clean-previous"}
docker compose --profile scale down -v 2>/dev/null || true
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

### Verify scheduler is responding

```bash {"name": "verify-scheduler"}
echo "Scheduler Health:"
curl -s http://localhost:8000/health | jq

echo ""
echo "Scheduler Stats:"
curl -s http://localhost:8000/stats | jq
```

### Check workers are registered

```bash {"name": "verify-workers"}
curl -s http://localhost:8000/workers | jq
```

---

## Part 2: Show the URLs

### Print all service URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Scheduler API:  http://localhost:8000"
echo "  Swagger Docs:   http://localhost:8000/docs"
echo "  Jaeger:         http://localhost:16686"
echo "  Grafana:        http://localhost:3001 (admin/admin)"
echo "  Prometheus:     http://localhost:9090"
```

---

## Part 3: Immediate Jobs

### Create a single immediate job

```bash {"name": "create-immediate-job"}
curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "demo-job-1",
    "job_type": "immediate",
    "payload": {"action": "compute", "value": 42},
    "execution_time_ms": 2000
  }' | jq
```

### Create batch of immediate jobs

```bash {"name": "create-batch-jobs"}
echo "Creating 10 immediate jobs..."
for i in {1..10}; do
  JOB_ID=$(curl -s -X POST http://localhost:8000/jobs \
    -H "Content-Type: application/json" \
    -d "{
      \"name\": \"batch-job-$i\",
      \"job_type\": \"immediate\",
      \"execution_time_ms\": 1500
    }" | jq -r '.id')
  echo "Created job $i: $JOB_ID"
done
```

### Watch jobs being processed

```bash {"name": "watch-jobs"}
echo "Watching job processing..."
for i in {1..10}; do
  echo "=== Check $i ==="
  curl -s http://localhost:8000/stats | jq '{queue_length, active_workers, jobs_by_status}'
  sleep 2
done
```

### View worker logs

```bash {"name": "worker-logs"}
docker compose logs --tail=20 worker-1 worker-2
```

---

## Part 4: Delayed Jobs

### Create a delayed job (10 seconds)

```bash {"name": "create-delayed-job"}
JOB_ID=$(curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "delayed-demo",
    "job_type": "delayed",
    "delay_seconds": 10,
    "payload": {"message": "I waited 10 seconds!"},
    "execution_time_ms": 1000
  }' | jq -r '.id')

echo "Created delayed job: $JOB_ID"
echo "export JOB_ID=$JOB_ID"
```

### Track delayed job status

```bash {"name": "track-delayed-job"}
JOB_ID=${JOB_ID:-$(curl -s "http://localhost:8000/jobs?job_type=delayed&limit=1" | jq -r '.[0].id')}

echo "Tracking job: $JOB_ID"
for i in {1..15}; do
  STATUS=$(curl -s "http://localhost:8000/jobs/$JOB_ID")
  echo "[$i] Status: $(echo $STATUS | jq -r '.status'), Scheduled: $(echo $STATUS | jq -r '.scheduled_at')"

  if [[ "$(echo $STATUS | jq -r '.status')" == "completed" ]]; then
    echo "Job completed!"
    break
  fi
  sleep 1
done
```

---

## Part 5: Job Failures and Retries

### Create a job that fails 70% of the time

```bash {"name": "create-flaky-job"}
JOB_ID=$(curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "flaky-job-demo",
    "job_type": "immediate",
    "execution_time_ms": 500,
    "fail_rate": 0.7,
    "max_retries": 5
  }' | jq -r '.id')

echo "Created flaky job: $JOB_ID"
echo "export FLAKY_JOB_ID=$JOB_ID"
```

### Watch the retry pattern

```bash {"name": "watch-retries"}
FLAKY_JOB_ID=${FLAKY_JOB_ID:-$(curl -s "http://localhost:8000/jobs?limit=1" | jq -r '.[0].id')}

echo "Watching job: $FLAKY_JOB_ID"
for i in {1..25}; do
  STATUS=$(curl -s "http://localhost:8000/jobs/$FLAKY_JOB_ID")
  CURRENT_STATUS=$(echo $STATUS | jq -r '.status')
  ATTEMPT=$(echo $STATUS | jq -r '.attempt')
  ERROR=$(echo $STATUS | jq -r '.error_message // "none"')

  echo "[$i] Status: $CURRENT_STATUS, Attempt: $ATTEMPT, Error: $ERROR"

  if [[ "$CURRENT_STATUS" == "completed" ]] || [[ "$CURRENT_STATUS" == "failed" ]]; then
    echo ""
    echo "=== Final State ==="
    echo $STATUS | jq
    break
  fi
  sleep 2
done
```

### Check retry metrics

```bash {"name": "check-retry-metrics"}
echo "Retry metrics:"
curl -s http://localhost:8000/metrics | grep -E "job_retries|jobs_failed"
```

---

## Part 6: Worker Failure Recovery

### Create a long-running job

```bash {"name": "create-long-job"}
LONG_JOB_ID=$(curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "long-running-demo",
    "job_type": "immediate",
    "execution_time_ms": 60000,
    "max_retries": 3
  }' | jq -r '.id')

echo "Created long-running job: $LONG_JOB_ID"
echo "export LONG_JOB_ID=$LONG_JOB_ID"
sleep 3
curl -s "http://localhost:8000/jobs/$LONG_JOB_ID" | jq '{status, worker_id, attempt}'
```

### Kill the worker processing the job

```bash {"name": "kill-worker"}
echo "Current job status:"
LONG_JOB_ID=${LONG_JOB_ID:-$(curl -s "http://localhost:8000/jobs?status=running&limit=1" | jq -r '.[0].id')}
curl -s "http://localhost:8000/jobs/$LONG_JOB_ID" | jq '{status, worker_id}'

echo ""
echo "Killing worker-1..."
docker compose kill worker-1
```

### Check job status after worker death

```bash {"name": "check-after-kill"}
echo "Job status after worker death:"
for i in {1..5}; do
  curl -s "http://localhost:8000/jobs/$LONG_JOB_ID" | jq '{status, worker_id, attempt}'
  sleep 2
done
```

### Restart the worker

```bash {"name": "restart-worker"}
docker compose up -d worker-1
echo "Worker-1 restarted"
sleep 5
docker compose ps worker-1
```

---

## Part 7: Scale Workers

### Check current throughput with 2 workers

```bash {"name": "check-2-workers"}
echo "Current workers:"
curl -s http://localhost:8000/workers | jq

echo ""
echo "Creating 20 jobs..."
for i in {1..20}; do
  curl -s -X POST http://localhost:8000/jobs \
    -H "Content-Type: application/json" \
    -d "{
      \"name\": \"scale-test-$i\",
      \"job_type\": \"immediate\",
      \"execution_time_ms\": 3000
    }" > /dev/null
done
echo "Jobs created"
```

### Watch queue with 2 workers

```bash {"name": "watch-queue-2-workers"}
echo "Queue drain with 2 workers:"
for i in {1..5}; do
  STATS=$(curl -s http://localhost:8000/stats)
  echo "[$i] Queue: $(echo $STATS | jq -r '.queue_length'), Workers: $(echo $STATS | jq -r '.active_workers')"
  sleep 3
done
```

### Scale to 3 workers

```bash {"name": "scale-to-3"}
echo "Scaling to 3 workers..."
docker compose --profile scale up -d worker-3
sleep 5

echo "Workers now:"
curl -s http://localhost:8000/workers | jq
```

### Watch improved throughput

```bash {"name": "watch-queue-3-workers"}
echo "Queue drain with 3 workers:"
for i in {1..10}; do
  STATS=$(curl -s http://localhost:8000/stats)
  echo "[$i] Queue: $(echo $STATS | jq -r '.queue_length'), Workers: $(echo $STATS | jq -r '.active_workers')"

  if [[ "$(echo $STATS | jq -r '.queue_length')" == "0" ]]; then
    echo "Queue drained!"
    break
  fi
  sleep 2
done
```

---

## Part 8: Load Testing

### Run k6 load test

```bash {"name": "run-load-test", "background": true}
docker compose run --rm k6 run /scripts/job-scheduler.js
```

### Check metrics during load

```bash {"name": "check-load-metrics"}
echo "Job scheduler metrics:"
curl -s http://localhost:8000/metrics | grep -E "^jobs_|^job_duration|^active_workers"
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose --profile scale down -v
echo "Lab cleaned up"
```

---

## Troubleshooting Commands

### Check all service logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check scheduler logs

```bash {"name": "logs-scheduler"}
docker compose logs scheduler --tail=50
```

### Check worker logs

```bash {"name": "logs-workers"}
docker compose logs worker-1 worker-2 --tail=50
```

### Check Redis queue

```bash {"name": "check-redis"}
docker compose exec redis redis-cli LLEN job_queue
docker compose exec redis redis-cli LRANGE job_queue 0 10
```

### Check PostgreSQL jobs

```bash {"name": "check-postgres"}
docker compose exec postgres psql -U scheduler -d jobscheduler -c \
  "SELECT id, name, status, attempt, worker_id FROM jobs ORDER BY created_at DESC LIMIT 10;"
```

### Restart all services

```bash {"name": "restart-all"}
docker compose --profile scale restart
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose --profile scale down -v` |
| Create job | `curl -X POST localhost:8000/jobs -d '{"name":"test","job_type":"immediate"}'` |
| Get job | `curl localhost:8000/jobs/{id}` |
| List jobs | `curl localhost:8000/jobs` |
| Get stats | `curl localhost:8000/stats` |
| Scale up | `docker compose --profile scale up -d worker-3` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
