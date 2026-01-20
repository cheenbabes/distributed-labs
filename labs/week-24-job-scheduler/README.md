# Lab 24: Distributed Job Scheduler

Build and observe a distributed job scheduler system with multiple workers competing for jobs, handling failures with retries, and processing immediate, delayed, and recurring jobs.

## What You'll Learn

- How distributed job scheduling works with competing workers
- Job queue management with Redis
- Persistent job storage with PostgreSQL
- Failure handling with exponential backoff retries
- Observability of background job systems
- Worker scaling and its effect on throughput

## Architecture

```
                                    ┌─────────────┐
                                    │   Client    │
                                    └──────┬──────┘
                                           │
                                           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                           Scheduler API                               │
│  - Create/cancel jobs                                                │
│  - Job status queries                                                │
│  - Worker coordination                                               │
└──────────────────────────────────────────────────────────────────────┘
         │                    │                    │
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────┐      ┌─────────────────┐    ┌─────────────┐
│  PostgreSQL │      │      Redis      │    │   Workers   │
│  (jobs DB)  │◄────►│   (job queue)   │◄───│  (1, 2, 3)  │
└─────────────┘      └─────────────────┘    └─────────────┘
                                                   │
                                                   ▼
                                            ┌─────────────┐
                                            │   Jaeger    │
                                            │  (traces)   │
                                            └─────────────┘
```

### Components

- **Scheduler API**: FastAPI service managing job lifecycle
- **Workers**: Compete for jobs from Redis queue, execute with configurable behavior
- **PostgreSQL**: Persistent job storage with status tracking
- **Redis**: Job queue and distributed locking
- **Prometheus/Grafana**: Metrics and dashboards
- **Jaeger**: Distributed tracing

### Job Types

| Type | Description | Example |
|------|-------------|---------|
| `immediate` | Execute as soon as a worker is available | Send email, process upload |
| `delayed` | Execute after a specified delay | Schedule reminder, delayed notification |
| `recurring` | Execute on a cron schedule | Daily report, hourly cleanup |

## Prerequisites

- Docker and Docker Compose
- curl (for API testing)
- jq (for JSON formatting)

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

### 2. Verify Services Are Running

```bash
docker compose ps
```

Expected: All services healthy (postgres, redis, scheduler, worker-1, worker-2).

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Scheduler API | http://localhost:8000 | Job management API |
| Swagger Docs | http://localhost:8000/docs | API documentation |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Schedule Immediate Jobs

Schedule jobs that execute immediately and observe them being processed by workers.

```bash
# Create a simple immediate job
curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-first-job",
    "job_type": "immediate",
    "payload": {"action": "compute", "value": 42},
    "execution_time_ms": 2000
  }' | jq

# Create 5 jobs and watch workers process them
for i in {1..5}; do
  curl -s -X POST http://localhost:8000/jobs \
    -H "Content-Type: application/json" \
    -d "{
      \"name\": \"batch-job-$i\",
      \"job_type\": \"immediate\",
      \"execution_time_ms\": 1000
    }" | jq -r '.id'
done

# Check scheduler stats
curl -s http://localhost:8000/stats | jq
```

**What to observe:**
- Jobs are created in `pending` status
- Workers pick them up and move to `running`
- After execution, jobs become `completed`
- Watch worker logs: `docker compose logs -f worker-1 worker-2`

**Questions:**
1. How are jobs distributed between workers?
2. What happens if you create more jobs than workers can process?

---

### Exercise 2: Schedule Delayed Jobs

Create jobs that execute after a specified delay.

```bash
# Create a job that runs in 10 seconds
curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "delayed-notification",
    "job_type": "delayed",
    "delay_seconds": 10,
    "payload": {"message": "This runs after 10 seconds"}
  }' | jq

# Save the job ID
JOB_ID=$(curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "timed-job",
    "job_type": "delayed",
    "delay_seconds": 15
  }' | jq -r '.id')

echo "Created job: $JOB_ID"

# Poll status every 3 seconds
for i in {1..10}; do
  echo "=== Check $i ==="
  curl -s "http://localhost:8000/jobs/$JOB_ID" | jq '{status, scheduled_at, started_at}'
  sleep 3
done
```

**What to observe:**
- `scheduled_at` is set to current time + delay
- Job stays `pending` until scheduled time
- Worker picks it up after the delay passes

---

### Exercise 3: Kill a Worker Mid-Job

Simulate worker failure during job execution and observe job recovery.

```bash
# Create a long-running job
JOB_ID=$(curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "long-running-job",
    "job_type": "immediate",
    "execution_time_ms": 30000,
    "max_retries": 3
  }' | jq -r '.id')

echo "Created job: $JOB_ID"

# Wait for a worker to pick it up
sleep 3

# Check which worker has the job
curl -s "http://localhost:8000/jobs/$JOB_ID" | jq '{status, worker_id, attempt}'

# Kill that worker (e.g., worker-1)
docker compose kill worker-1

# Watch the job status
for i in {1..10}; do
  echo "=== Check $i ==="
  curl -s "http://localhost:8000/jobs/$JOB_ID" | jq '{status, worker_id, attempt, error_message}'
  sleep 5
done

# Restart the worker
docker compose up -d worker-1
```

**What to observe:**
- Job stays in `running` status with dead worker
- In a production system, you'd have a cleanup process for stale jobs
- The retry mechanism handles explicit failures, not worker crashes

**Questions:**
1. How would you detect and recover from worker crashes?
2. What timeout mechanisms could help?

---

### Exercise 4: Configure Jobs with Failures

Create jobs with a high failure rate and observe the retry pattern.

```bash
# Create a job that fails 70% of the time
JOB_ID=$(curl -s -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "name": "flaky-job",
    "job_type": "immediate",
    "execution_time_ms": 500,
    "fail_rate": 0.7,
    "max_retries": 5
  }' | jq -r '.id')

echo "Created job: $JOB_ID"

# Watch the job retry
for i in {1..20}; do
  STATUS=$(curl -s "http://localhost:8000/jobs/$JOB_ID")
  echo "Attempt $(echo $STATUS | jq -r '.attempt'), Status: $(echo $STATUS | jq -r '.status')"

  # Check if job is done
  if [[ "$(echo $STATUS | jq -r '.status')" == "completed" ]] || \
     [[ "$(echo $STATUS | jq -r '.status')" == "failed" ]]; then
    echo "Final status: $(echo $STATUS | jq -r '.status')"
    break
  fi
  sleep 2
done

# Check metrics for retries
curl -s http://localhost:8000/metrics | grep job_retries
```

**What to observe:**
- Job retries with exponential backoff (2^attempt seconds)
- Each retry increments the attempt counter
- After max_retries, job becomes `failed`
- Metrics track retry counts

---

### Exercise 5: Scale Workers and See Throughput Changes

Start with 2 workers, create load, then scale to 3 workers.

```bash
# Check current workers
curl -s http://localhost:8000/workers | jq

# Create 20 jobs
for i in {1..20}; do
  curl -s -X POST http://localhost:8000/jobs \
    -H "Content-Type: application/json" \
    -d "{
      \"name\": \"scale-test-$i\",
      \"job_type\": \"immediate\",
      \"execution_time_ms\": 2000
    }" > /dev/null
done

echo "Created 20 jobs"

# Watch queue length
for i in {1..5}; do
  curl -s http://localhost:8000/stats | jq '{queue_length, active_workers, jobs_by_status}'
  sleep 3
done

# Scale up to 3 workers
docker compose --profile scale up -d worker-3

echo "Scaled to 3 workers"

# Watch queue drain faster
for i in {1..10}; do
  curl -s http://localhost:8000/stats | jq '{queue_length, active_workers}'
  sleep 2
done
```

**What to observe:**
- Queue length decreases as workers process jobs
- Adding workers increases throughput
- Check Grafana for "Worker Throughput" graph

---

## Key Metrics

| Metric | Description |
|--------|-------------|
| `jobs_scheduled_total` | Total jobs created |
| `jobs_completed_total` | Jobs finished (success/failed) |
| `jobs_failed_total` | Jobs that failed permanently |
| `job_duration_seconds` | How long jobs take to execute |
| `jobs_pending_total` | Current pending jobs by type |
| `active_workers_total` | Number of healthy workers |
| `job_retries_total` | Number of retry attempts |
| `job_queue_wait_seconds` | Time jobs spend waiting |

## Key Takeaways

1. **Competing consumers**: Multiple workers pulling from the same queue scales horizontally

2. **Idempotency matters**: Jobs may run more than once due to retries or failures

3. **Exponential backoff**: Prevents thundering herd during transient failures

4. **Observability is critical**: You need metrics to understand job system health

5. **Graceful degradation**: Handle worker failures without losing jobs

## Cleanup

```bash
docker compose --profile scale down -v
```

## Troubleshooting

### Jobs stuck in pending
```bash
# Check if workers are running
docker compose ps worker-1 worker-2

# Check worker logs
docker compose logs -f worker-1
```

### Database connection issues
```bash
# Check postgres is healthy
docker compose logs postgres

# Verify connection
docker compose exec postgres psql -U scheduler -d jobscheduler -c "SELECT 1"
```

### Redis queue issues
```bash
# Check Redis
docker compose exec redis redis-cli LLEN job_queue
```

## Next Lab

[Lab 25: Circuit Breakers](../week-25-circuit-breaker/) - Implement circuit breaker patterns to handle cascading failures.
