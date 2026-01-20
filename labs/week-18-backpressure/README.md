# Lab 18: Backpressure

What happens when a producer generates work faster than a consumer can process it? Without backpressure, queues grow unbounded, memory explodes, and your service crashes. In this lab, you'll observe these failures firsthand and implement solutions.

## What You'll Learn

- Why unbounded queues are dangerous in production
- How memory grows when producer outpaces consumer
- Three backpressure strategies: bounded queues, rejection, and rate limiting
- How to monitor queue depth and memory usage
- Trade-offs between different backpressure approaches

## Architecture

```
┌──────────────┐         ┌─────────────┐         ┌──────────────┐
│              │         │             │         │              │
│   Producer   │────────▶│    Queue    │────────▶│   Consumer   │
│   (10/sec)   │         │             │         │   (5/sec)    │
│              │         │             │         │              │
└──────────────┘         └─────────────┘         └──────────────┘
      │                        │                        │
      │                        │                        │
      ▼                        ▼                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                         Prometheus                               │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
                        ┌─────────────┐
                        │   Grafana   │
                        │  Dashboard  │
                        └─────────────┘
```

**The Problem:**
- Producer generates 10 items/second
- Consumer can only process items at ~5-10/second (100ms each)
- Without backpressure, the queue grows indefinitely

**Queue Modes:**
1. **Unbounded** - Queue grows forever (leads to OOM)
2. **Bounded** - Queue has max size, rejects when full
3. **Rate Limited** - Producer slows down to match consumer capacity

## Prerequisites

- Docker and Docker Compose
- curl (for API calls)
- jq (for JSON formatting)

## Quick Start

### 1. Start the Lab

```bash
cd labs/week-18-backpressure
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
| Producer | http://localhost:8000 | Work item generator |
| Consumer | http://localhost:8001 | Work item processor |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboard (admin/admin) |

## Lab Exercises

### Exercise 1: Baseline - Balanced System

First, let's verify that when producer and consumer are balanced, the queue stays stable.

```bash
# Check initial status
curl -s http://localhost:8000/status | jq

# The queue should stay small because consumer can keep up
# Watch for 30 seconds
for i in {1..15}; do
  echo "=== Check $i ==="
  curl -s http://localhost:8000/status | jq '.queue'
  sleep 2
done
```

**Expected:** Queue depth stays relatively stable (under 20 items).

Open Grafana (http://localhost:3001, admin/admin) and view the "Backpressure Lab" dashboard. You should see:
- Queue Depth: Low and stable
- Throughput: Producer and consumer rates roughly matched

---

### Exercise 2: Create Backpressure - Slow Down the Consumer

Now let's create the problem. We'll slow down the consumer so it can't keep up.

```bash
# Slow down the consumer (5x slower: 500ms per item = 2 items/sec max)
curl -s -X POST http://localhost:8001/admin/slow-down \
  -H "Content-Type: application/json" \
  -d '{"factor": 5}' | jq
```

Watch the queue grow:

```bash
# Watch queue depth grow over 60 seconds
for i in {1..30}; do
  echo "=== Check $i ==="
  curl -s http://localhost:8000/status | jq '.queue | {size, memory_mb}'
  sleep 2
done
```

**Expected:** Queue depth grows continuously because producer (10/sec) >> consumer (2/sec).

**In Grafana, observe:**
- Queue Depth: Steadily increasing
- Memory Usage: Growing
- Throughput gap: Producer rate >> Consumer rate

---

### Exercise 3: Observe Memory Pressure

The producer container has a 256MB memory limit. Let's see what happens.

```bash
# Check producer memory
curl -s http://localhost:8000/status | jq '.process'

# Watch docker stats
docker stats lab18-producer --no-stream
```

**What to look for:**
- Process memory increasing
- Eventually the container may get OOM killed or become unresponsive

If you want to see an OOM faster, you can increase production rate:

```bash
# Increase production rate to 50/sec (WARNING: will cause OOM faster)
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"production_rate": 50}' | jq
```

---

### Exercise 4: Solution 1 - Bounded Queue

Let's implement the first solution: a bounded queue that rejects new items when full.

```bash
# First, reset the producer rate
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"production_rate": 10}' | jq

# Clear the existing queue
curl -s -X POST http://localhost:8000/admin/clear-queue | jq

# Switch to bounded queue mode with max 100 items
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"queue_mode": "bounded", "max_queue_size": 100}' | jq

# Watch what happens now
for i in {1..20}; do
  echo "=== Check $i ==="
  curl -s http://localhost:8000/status | jq '.queue | {mode, size, max_size}'
  sleep 2
done
```

**Expected:**
- Queue grows to 100, then stops
- New items are rejected
- Memory stays stable

**In Grafana, observe:**
- Queue Depth: Plateaus at 100
- Rejected Items: Starts incrementing
- Memory: Stable

**Trade-off:** Data loss! Rejected items are gone forever.

---

### Exercise 5: Solution 2 - Rate Limiting

Now let's try rate limiting, where the producer slows down to match consumer capacity.

```bash
# Clear queue again
curl -s -X POST http://localhost:8000/admin/clear-queue | jq

# Switch to rate-limited mode
# Set rate limit to 2/sec (matching slowed consumer)
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"queue_mode": "rate_limited", "rate_limit": 2}' | jq

# Watch the queue stay stable
for i in {1..15}; do
  echo "=== Check $i ==="
  curl -s http://localhost:8000/status | jq '.queue | {mode, size}'
  sleep 2
done
```

**Expected:**
- Queue stays small
- No rejections
- Producer is throttled to match consumer

**In Grafana, observe:**
- Queue Depth: Low and stable
- Rate Limit Delays: Shows producer is waiting
- Throughput: Producer rate matches consumer rate

**Trade-off:** Slower overall throughput, but no data loss.

---

### Exercise 6: Reset and Compare

Let's restore normal operation and compare the approaches.

```bash
# Speed up consumer back to normal
curl -s -X POST http://localhost:8001/admin/config \
  -H "Content-Type: application/json" \
  -d '{"processing_time_ms": 100}' | jq

# Clear queue
curl -s -X POST http://localhost:8000/admin/clear-queue | jq

# Return to unbounded mode
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"queue_mode": "unbounded", "production_rate": 10}' | jq

# Verify system is healthy again
curl -s http://localhost:8000/status | jq
curl -s http://localhost:8001/status | jq
```

---

### Exercise 7: Analyze Traces

Use Jaeger to understand the end-to-end flow:

1. Open http://localhost:16686
2. Select "producer" from the Service dropdown
3. Find traces for "send_to_consumer"
4. Look at the `queue.latency_seconds` attribute - this shows how long items waited

**Questions to explore:**
- How does queue latency change as the queue grows?
- What does a healthy trace look like vs. a backpressured one?

---

## Key Takeaways

1. **Unbounded queues are a time bomb** - They will eventually exhaust memory and crash your service.

2. **Know your throughput limits** - If producer can generate 10/sec but consumer can only handle 5/sec, you WILL have a problem.

3. **Choose your trade-off:**
   - **Bounded queue + rejection**: Protects memory, loses data
   - **Rate limiting**: Protects memory, no data loss, but slower
   - **Scaling consumers**: Best solution if possible

4. **Monitor queue depth religiously** - It's an early warning signal for backpressure.

5. **Latency isn't just processing time** - Queue wait time can dominate total latency.

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Producer OOM killed
```bash
# Check if container restarted
docker compose ps

# Check memory usage
docker stats lab18-producer --no-stream

# Restart with cleared state
docker compose restart lab18-producer
```

### Metrics not appearing in Grafana
- Wait 15-30 seconds for scrape interval
- Check Prometheus targets: http://localhost:9090/targets
- Verify services are healthy: `docker compose ps`

## Advanced Experiments

1. **Variable processing time**: Enable variable processing to simulate real-world jitter
   ```bash
   curl -s -X POST http://localhost:8001/admin/config \
     -H "Content-Type: application/json" \
     -d '{"variable_processing": true, "processing_min_ms": 50, "processing_max_ms": 500}'
   ```

2. **Burst traffic**: Use the manual produce endpoint to simulate traffic bursts
   ```bash
   curl -s -X POST http://localhost:8000/produce \
     -H "Content-Type: application/json" \
     -d '{"count": 1000}'
   ```

3. **Consumer failure**: Stop the consumer and watch the queue behavior
   ```bash
   docker compose stop lab18-consumer
   # Wait and observe
   docker compose start lab18-consumer
   ```
