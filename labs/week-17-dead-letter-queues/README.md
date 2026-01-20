# Lab 17: Dead Letter Queues

When messages fail processing in a distributed system, where do they go? In this lab, you'll explore dead letter queues (DLQs) - a critical pattern for handling failed messages gracefully without losing data.

## What You'll Learn

- How dead letter queues capture failed messages
- Retry policies and when to give up
- How to inspect and diagnose DLQ contents
- Strategies for replaying failed messages
- Observability patterns for message failure analysis

## Architecture

```
┌──────────────┐     ┌───────────────┐     ┌────────────┐
│   Producer   │────▶│  Main Queue   │────▶│   Worker   │
│   (HTTP)     │     │   (tasks)     │     │            │
└──────────────┘     └───────────────┘     └─────┬──────┘
                                                  │
                            ┌─────────────────────┘
                            │ Retry up to 3x
                            │ then route to DLQ
                            ▼
                     ┌───────────────┐     ┌──────────────┐
                     │     DLQ       │────▶│ DLQ Consumer │
                     │ (tasks.dlq)   │     │  (Inspect/   │
                     └───────────────┘     │   Replay)    │
                                           └──────────────┘
```

**Message Flow:**
1. Producer publishes tasks to the main queue
2. Worker consumes and processes tasks
3. If processing fails, message is retried (up to max_retries)
4. After all retries exhausted, message is routed to DLQ
5. DLQ Consumer allows inspection, analysis, and replay

## Prerequisites

- Docker and Docker Compose
- curl (for API calls)
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

All services should show as "healthy".

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Producer API | http://localhost:8000 | Publish messages |
| Worker API | http://localhost:8001 | Worker config/stats |
| DLQ Consumer API | http://localhost:8002 | DLQ inspection/replay |
| RabbitMQ Management | http://localhost:15672 | Queue browser (guest/guest) |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Normal Message Processing

First, observe normal message flow without failures.

```bash
# Publish a single task
curl -s -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{"task_type": "email", "payload": {"to": "user@example.com"}}' | jq

# Check queue status
curl -s http://localhost:8000/queues/status | jq

# Check DLQ (should be empty)
curl -s http://localhost:8002/dlq | jq
```

**Expected:** Messages are processed successfully, DLQ remains empty.

Watch the flow in Jaeger:
1. Open http://localhost:16686
2. Select "producer" from the Service dropdown
3. Find a trace showing the full publish -> process flow

---

### Exercise 2: Enable Failure Injection

Now let's make the worker fail some messages.

```bash
# Set 50% failure rate on the worker
curl -s -X POST http://localhost:8001/admin/config \
  -H "Content-Type: application/json" \
  -d '{"failure_rate": 0.5}' | jq

# Verify the configuration
curl -s http://localhost:8001/admin/config | jq
```

Send some messages and watch what happens:

```bash
# Send 20 messages
curl -s -X POST http://localhost:8000/tasks/bulk \
  -H "Content-Type: application/json" \
  -d '{"count": 20, "task_type": "test"}' | jq

# Wait a few seconds for processing
sleep 5

# Check DLQ contents
curl -s http://localhost:8002/dlq | jq
```

**Questions to explore:**
- How many messages ended up in the DLQ?
- How many retries did each failed message have?
- What error information is captured?

---

### Exercise 3: Inspect DLQ Contents

Examine the failed messages in detail.

```bash
# Get DLQ statistics
curl -s http://localhost:8002/dlq/stats | jq

# View full DLQ contents
curl -s http://localhost:8002/dlq?limit=10 | jq

# Check queue depths in RabbitMQ
curl -s http://localhost:8000/queues/status | jq
```

Look at a specific failed message:

```bash
# Get a task_id from the DLQ listing
TASK_ID=$(curl -s http://localhost:8002/dlq?limit=1 | jq -r '.messages[0].task_id')
echo "Examining task: $TASK_ID"

# Get detailed info about this message
curl -s "http://localhost:8002/dlq/message/$TASK_ID" | jq
```

**What to look for:**
- `attempt` - How many times was processing attempted?
- `dlq_reason` - Why was it sent to DLQ?
- `final_error` - The last error message
- `trace_context` - Can you follow the trace in Jaeger?

---

### Exercise 4: Replay Failed Messages

After fixing the root cause (reducing failure rate), replay messages from the DLQ.

```bash
# First, reduce failure rate to 0 (fix the "bug")
curl -s -X POST http://localhost:8001/admin/config \
  -H "Content-Type: application/json" \
  -d '{"failure_rate": 0.0}' | jq

# Check how many messages are in DLQ
curl -s http://localhost:8002/dlq/stats | jq

# Replay 5 messages back to the main queue
curl -s -X POST http://localhost:8002/dlq/replay \
  -H "Content-Type: application/json" \
  -d '{"count": 5, "reset_attempts": true}' | jq

# Verify DLQ depth decreased
curl -s http://localhost:8002/dlq/stats | jq
```

**Observe:**
- Replayed messages get `replayed_from_dlq: true` flag
- Attempt counter is reset to 0
- Messages process successfully this time

---

### Exercise 5: Configure Retry Policies

Experiment with different retry configurations.

```bash
# Set aggressive failure rate with minimal retries
curl -s -X POST http://localhost:8001/admin/config \
  -H "Content-Type: application/json" \
  -d '{
    "failure_rate": 0.8,
    "max_retries": 1,
    "retry_delay_ms": 500
  }' | jq

# Send messages and observe quick DLQ accumulation
curl -s -X POST http://localhost:8000/tasks/bulk \
  -H "Content-Type: application/json" \
  -d '{"count": 10, "task_type": "retry_test"}' | jq

sleep 3

# Check DLQ - should fill quickly with only 1 retry
curl -s http://localhost:8002/dlq/stats | jq
```

Now try with more retries:

```bash
# More retries, longer delay
curl -s -X POST http://localhost:8001/admin/config \
  -H "Content-Type: application/json" \
  -d '{
    "failure_rate": 0.5,
    "max_retries": 5,
    "retry_delay_ms": 2000
  }' | jq

# Send messages - these will retry more times
curl -s -X POST http://localhost:8000/tasks/bulk \
  -H "Content-Type: application/json" \
  -d '{"count": 10, "task_type": "retry_test_v2"}' | jq
```

Watch the Grafana dashboard to see retry patterns in real-time.

---

### Exercise 6: Load Test with Failures

Generate sustained load while injecting failures to see patterns emerge.

```bash
# Set moderate failure rate
curl -s -X POST http://localhost:8001/admin/config \
  -H "Content-Type: application/json" \
  -d '{"failure_rate": 0.3, "max_retries": 3}' | jq

# Run k6 load test
docker compose run --rm lab17-k6 run /scripts/basic.js
```

While the test runs:
1. Open Grafana at http://localhost:3001
2. View the "Dead Letter Queue Dashboard"
3. Observe:
   - Message throughput vs DLQ accumulation
   - Retry rate patterns
   - Processing latency during retries

---

## Key Metrics to Watch

| Metric | Description |
|--------|-------------|
| `messages_published_total` | Total messages sent to queue |
| `messages_processed_total` | Successfully processed messages |
| `messages_failed_total` | Failed processing attempts |
| `messages_retried_total` | Retry attempts |
| `dlq_messages_total` | Messages sent to DLQ |
| `queue_depth` | Current messages in each queue |
| `worker_failure_rate` | Configured failure rate |

## Key Takeaways

1. **DLQs prevent message loss** - Failed messages aren't dropped, they're preserved for analysis

2. **Retry policies matter** - Too few retries waste the DLQ; too many cause delays

3. **Observability is critical** - Without metrics, you won't know messages are failing

4. **Replay enables recovery** - Once the root cause is fixed, messages can be reprocessed

5. **Trace context preservation** - Failed message traces help debug the original failure

## Common DLQ Patterns

### Pattern 1: Poison Messages
Messages that will never succeed (malformed data, invalid references). These should be discarded after investigation.

### Pattern 2: Transient Failures
Temporary issues (network timeout, service unavailable). These often succeed on replay.

### Pattern 3: Rate Limiting
Downstream service rate limits. Use exponential backoff before replay.

### Pattern 4: Schema Mismatch
Message format doesn't match consumer expectations. Requires code fix before replay.

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Messages not appearing in DLQ
```bash
# Check worker logs
docker compose logs lab17-worker

# Verify RabbitMQ bindings
curl -s -u guest:guest http://localhost:15672/api/bindings | jq
```

### Worker not processing messages
```bash
# Check if worker is paused
curl -s http://localhost:8001/admin/config | jq

# Check RabbitMQ connection
docker compose logs lab17-worker | grep -i rabbit
```

### Traces not appearing in Jaeger
- Wait 10-15 seconds after making requests
- Check OTEL collector: `docker compose logs lab17-otel-collector`

## API Reference

### Producer (port 8000)
- `POST /tasks` - Publish single task
- `POST /tasks/bulk` - Publish multiple tasks
- `GET /queues/status` - Get queue depths
- `GET /health` - Health check
- `GET /metrics` - Prometheus metrics

### Worker (port 8001)
- `GET /admin/config` - Get current config
- `POST /admin/config` - Update config (failure_rate, max_retries, etc.)
- `GET /stats` - Get processing stats
- `GET /health` - Health check
- `GET /metrics` - Prometheus metrics

### DLQ Consumer (port 8002)
- `GET /dlq` - List DLQ messages
- `GET /dlq/stats` - Get DLQ statistics
- `GET /dlq/message/{task_id}` - Get specific message
- `POST /dlq/replay` - Replay messages to main queue
- `POST /dlq/replay/{task_id}` - Replay specific message
- `DELETE /dlq/purge` - Purge all DLQ messages
- `DELETE /dlq/message/{task_id}` - Discard specific message
- `GET /health` - Health check
- `GET /metrics` - Prometheus metrics

## Next Lab

[Lab 18: Event Sourcing Basics](../week-18-event-sourcing/) - Build an event-sourced system and explore how events can be replayed to rebuild state.
