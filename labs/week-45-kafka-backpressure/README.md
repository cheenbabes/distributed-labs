# Lab 45: Backpressure in Streams (Kafka Consumer Lag)

Your Kafka consumers are falling behind. Messages are piling up, and you need to detect, diagnose, and remediate the backpressure before it impacts your system. In this lab, you'll learn to manage consumer lag and implement backpressure strategies.

## What You'll Learn

- How consumer lag develops when producers outpace consumers
- How to monitor and detect lag using metrics and dedicated monitoring
- Strategies for handling backpressure (scaling, pausing, throttling)
- How to implement pause/resume patterns based on lag thresholds
- The relationship between processing time and throughput capacity

## Architecture

```
                                    ┌─────────────────┐
                                    │   Lag Monitor   │
                                    │  (metrics/alerts)│
                                    └────────┬────────┘
                                             │ monitors
                                             ▼
┌──────────────┐     ┌─────────┐     ┌───────────────┐     ┌──────────────┐
│   Producer   │────▶│  Kafka  │────▶│   Consumer    │────▶│   (Sink)     │
│ (fast, burst)│     │  Topic  │     │ (slow, 500ms) │     │              │
└──────────────┘     │ orders  │     └───────────────┘     └──────────────┘
                     └─────────┘              │
                          │                  │ can scale to...
                          │           ┌──────┴──────┐
                          │           ▼             ▼
                          │    ┌───────────┐ ┌───────────┐
                          └───▶│Consumer-2 │ │Consumer-3 │
                               └───────────┘ └───────────┘
```

**Key Components:**
- **Producer**: Fast message producer with configurable rate and burst capability
- **Consumer**: Slow message processor (default 500ms/message) - simulates real work
- **Lag Monitor**: Dedicated service that tracks consumer group lag and alerts
- **Kafka**: Single-node Kafka cluster with Zookeeper

## Prerequisites

- Docker and Docker Compose
- curl (for API calls)
- jq (for JSON parsing)
- k6 (optional, for load testing)

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

### 2. Wait for Services

```bash
# Wait for Kafka to be healthy
docker compose ps

# Verify all services
curl -s http://localhost:8000/health | jq  # Producer
curl -s http://localhost:8001/health | jq  # Consumer
curl -s http://localhost:8010/health | jq  # Lag Monitor
```

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Producer API | http://localhost:8000 | Produce messages |
| Consumer API | http://localhost:8001 | Consumer control |
| Lag Monitor | http://localhost:8010 | Lag metrics & alerts |
| Kafka UI | http://localhost:8080 | Kafka cluster view |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |
| Prometheus | http://localhost:9090 | Metrics |
| Jaeger | http://localhost:16686 | Traces |

## Lab Exercises

### Exercise 1: Understanding Baseline Throughput

First, let's understand what our consumer can handle.

**The Math:**
- Consumer processes 1 message in ~500ms
- Maximum throughput: 1000ms / 500ms = **2 messages/second**

```bash
# Check consumer configuration
curl -s http://localhost:8001/config | jq

# Check current lag (should be 0)
curl -s http://localhost:8010/lag | jq

# Produce 10 messages and watch consumption
curl -X POST http://localhost:8000/produce/batch?count=10

# Wait 10 seconds, check lag
sleep 10
curl -s http://localhost:8010/lag | jq '.total_lag'
```

**Question:** If we produce 10 messages and the consumer processes 2/second, how long until lag reaches 0?

---

### Exercise 2: Building Up Lag

Now let's overwhelm the consumer to see lag build up.

```bash
# Start the auto-producer at 10 msg/100ms = 100 msg/sec
curl -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"rate_ms": 100, "batch_size": 10, "running": true}'

# Watch lag grow in real-time
watch -n 2 'curl -s http://localhost:8010/lag | jq "{total_lag, status, rate_of_change}"'
```

**Open Grafana** (http://localhost:3001, admin/admin) and watch the "Kafka Backpressure Lab" dashboard.

**What to observe:**
- Total lag climbing rapidly
- Production rate >> Consumption rate
- Lag rate of change is positive

```bash
# Stop the producer after building lag
curl -X POST http://localhost:8000/stop

# Check final lag
curl -s http://localhost:8010/lag | jq
```

---

### Exercise 3: Detecting Lag via Metrics

The lag monitor exposes Prometheus metrics. Let's explore them.

```bash
# View lag metrics directly
curl -s http://localhost:8010/metrics | grep kafka_consumer

# Key metrics:
# - kafka_consumer_group_lag_total: Total messages behind
# - kafka_consumer_group_lag: Lag per partition
# - kafka_lag_status: 0=ok, 1=warning, 2=critical
# - kafka_lag_rate_of_change: Growing or shrinking

# Check recommendations
curl -s http://localhost:8010/recommendations | jq
```

In Prometheus (http://localhost:9090), try these queries:

```promql
# Total lag
kafka_consumer_group_lag_total

# Lag by partition
kafka_consumer_group_lag

# Is lag growing or shrinking?
kafka_lag_rate_of_change
```

---

### Exercise 4: Scaling Consumers

The classic solution to backpressure: add more consumers.

```bash
# Current lag
echo "Current lag: $(curl -s http://localhost:8010/lag | jq '.total_lag')"

# Start additional consumers
docker compose --profile scaled up -d consumer-2 consumer-3

# Watch lag decrease faster
watch -n 2 'curl -s http://localhost:8010/lag | jq "{total_lag, status, rate_of_change}"'
```

**Observe in Grafana:**
- Consumption rate triples (3 consumers)
- Lag decreases 3x faster
- Each consumer processes different partitions

```bash
# Check consumption rate per consumer
curl -s http://localhost:8001/stats | jq  # consumer-1
curl -s http://localhost:8002/stats | jq  # consumer-2
curl -s http://localhost:8003/stats | jq  # consumer-3
```

---

### Exercise 5: Pause/Resume Based on Lag

Another backpressure strategy: pause consumption when downstream systems are overwhelmed.

```bash
# Simulate downstream system overload by pausing consumer
curl -X POST http://localhost:8001/pause

# Check consumer status
curl -s http://localhost:8001/health | jq

# Produce more messages
curl -X POST http://localhost:8000/burst \
  -H "Content-Type: application/json" \
  -d '{"count": 100, "delay_ms": 10}'

# Watch lag grow while paused
curl -s http://localhost:8010/lag | jq

# Resume when "downstream" is ready
curl -X POST http://localhost:8001/resume

# Watch recovery
watch -n 2 'curl -s http://localhost:8010/lag | jq "{total_lag, status}"'
```

---

### Exercise 6: Adjusting Processing Time

In real systems, you might optimize processing to handle more throughput.

```bash
# Current processing time
curl -s http://localhost:8001/config | jq

# Set consumer to "fast mode" (50ms per message)
curl -X POST http://localhost:8001/fast?time_ms=50

# Now consumer can handle: 1000ms / 50ms = 20 msg/sec
# Compare to before: 2 msg/sec

# Generate load and watch lag behavior
curl -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"rate_ms": 100, "batch_size": 5, "running": true}'

# Now production (50/sec) vs consumption (20/sec) - still building lag, but slower

# After observing, stop producer
curl -X POST http://localhost:8000/stop

# Set consumer back to slow mode to see dramatic effect
curl -X POST http://localhost:8001/slow?time_ms=2000

# Now consumer handles only 0.5 msg/sec!
```

---

### Exercise 7: Burst Detection and Response

Production systems often see traffic bursts. Let's simulate and respond.

```bash
# Start with clean slate
curl -X POST http://localhost:8001/fast?time_ms=100  # 10 msg/sec capacity

# Trigger a burst
curl -X POST http://localhost:8000/burst \
  -H "Content-Type: application/json" \
  -d '{"count": 500, "delay_ms": 5}'

# Immediately check lag
curl -s http://localhost:8010/lag | jq

# Check alerts
curl -s http://localhost:8010/alerts | jq

# Get recommendations
curl -s http://localhost:8010/recommendations | jq

# Follow the recommendations to scale
docker compose --profile scaled up -d consumer-2 consumer-3

# Watch recovery
watch -n 2 'curl -s http://localhost:8010/lag | jq "{total_lag, status, rate_of_change}"'
```

---

### Exercise 8: Load Testing with k6

Use k6 to generate realistic production patterns.

```bash
# Steady-state load test
docker compose run --rm k6 run /scripts/steady-producer.js

# Burst load test (simulates traffic spike)
docker compose run --rm k6 run /scripts/burst-producer.js

# Monitor during test
watch -n 2 'curl -s http://localhost:8010/status | jq'
```

---

## Key Metrics to Monitor

| Metric | What It Means | Alert Threshold |
|--------|---------------|-----------------|
| `kafka_consumer_group_lag_total` | Messages behind | > 500 critical |
| `kafka_lag_rate_of_change` | Growing or shrinking | > 10/sec warning |
| `kafka_messages_consumed_total` | Consumer throughput | Decreasing |
| `kafka_consumer_paused` | Consumer paused | 1 = investigate |

## Backpressure Strategies

1. **Scale Horizontally** - Add more consumers
2. **Pause/Resume** - Stop consumption when downstream is overwhelmed
3. **Optimize Processing** - Reduce per-message processing time
4. **Rate Limit Producers** - Slow down at the source
5. **Dead Letter Queue** - Skip problematic messages
6. **Batch Processing** - Process multiple messages together

## Key Takeaways

1. **Lag is inevitable** - Production bursts will always happen
2. **Monitor proactively** - Know your lag before it becomes a problem
3. **Know your throughput** - `1000ms / processing_time_ms = max msg/sec`
4. **Scale before crisis** - Add consumers when lag trends upward
5. **Have a plan** - Know how to respond when lag spikes

## Cleanup

```bash
# Stop all services
docker compose --profile scaled down -v

# Or just stop without removing volumes
docker compose down
```

## Troubleshooting

### Consumer not receiving messages
```bash
docker compose logs consumer
```

### Kafka not healthy
```bash
docker compose logs kafka
docker compose exec kafka kafka-topics --bootstrap-server localhost:29092 --list
```

### Lag not updating
```bash
docker compose logs lag-monitor
```

## API Reference

### Producer (port 8000)
- `POST /produce` - Produce single message
- `POST /produce/batch?count=N` - Produce N messages
- `POST /burst` - Burst produce `{"count": N, "delay_ms": D}`
- `POST /start` - Start auto-producer
- `POST /stop` - Stop auto-producer
- `POST /config` - Configure `{"rate_ms": N, "batch_size": N, "running": bool}`

### Consumer (port 8001)
- `POST /pause` - Pause consumption
- `POST /resume` - Resume consumption
- `POST /slow?time_ms=N` - Set slow processing time
- `POST /fast?time_ms=N` - Set fast processing time
- `POST /config` - Configure `{"processing_time_ms": N, "paused": bool}`

### Lag Monitor (port 8010)
- `GET /lag` - Current lag details
- `GET /lag/history` - Lag history
- `GET /alerts` - Recent alerts
- `GET /status` - Quick status
- `GET /recommendations` - Actionable recommendations
