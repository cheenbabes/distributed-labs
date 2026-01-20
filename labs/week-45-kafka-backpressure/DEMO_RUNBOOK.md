# Demo Runbook: Kafka Backpressure Lab

This runbook contains all commands for demonstrating Kafka consumer lag and backpressure handling. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

---

## Pre-Demo Setup

### Check Docker is running

```bash {"name": "check-docker"}
docker info > /dev/null 2>&1 && echo "Docker is running" || echo "Docker is not running"
```

### Clean any previous lab state

```bash {"name": "clean-previous"}
docker compose --profile scaled down -v 2>/dev/null || true
docker system prune -f
```

---

## Part 1: Start the Lab

### Build and start all services

```bash {"name": "start-lab"}
docker compose up --build -d
```

### Wait for Kafka to be healthy

```bash {"name": "wait-kafka"}
echo "Waiting for Kafka to be healthy..."
until docker compose exec kafka kafka-topics --bootstrap-server localhost:29092 --list 2>/dev/null; do
  echo "Waiting for Kafka..."
  sleep 5
done
echo "Kafka is ready!"
```

### Wait for all services

```bash {"name": "wait-services"}
echo "Waiting for services to be healthy..."
sleep 15
docker compose ps
```

### Verify all services are responding

```bash {"name": "verify-services"}
echo "Producer:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Consumer:"
curl -s http://localhost:8001/health | jq
echo ""
echo "Lag Monitor:"
curl -s http://localhost:8010/health | jq
```

---

## Part 2: Show URLs

### Display all service URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Grafana:      http://localhost:3001 (admin/admin)"
echo "  Kafka UI:     http://localhost:8080"
echo "  Prometheus:   http://localhost:9090"
echo "  Jaeger:       http://localhost:16686"
echo ""
echo "  Producer:     http://localhost:8000"
echo "  Consumer:     http://localhost:8001"
echo "  Lag Monitor:  http://localhost:8010"
```

---

## Part 3: Establish Baseline

### Check initial lag (should be 0)

```bash {"name": "check-initial-lag"}
curl -s http://localhost:8010/lag | jq '{total_lag, status}'
```

### Check consumer configuration

```bash {"name": "check-consumer-config"}
curl -s http://localhost:8001/config | jq
echo ""
echo "With 500ms processing time, max throughput = 2 msg/sec"
```

### Produce a small batch to test

```bash {"name": "produce-small-batch"}
curl -X POST http://localhost:8000/produce/batch?count=10 | jq
```

### Watch lag clear (should take ~5 seconds)

```bash {"name": "watch-lag-clear"}
for i in {1..10}; do
  lag=$(curl -s http://localhost:8010/lag | jq '.total_lag')
  echo "Lag: $lag"
  sleep 1
done
```

---

## Part 4: Build Up Lag

### Start fast production (overwhelm consumer)

```bash {"name": "start-fast-producer"}
curl -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"rate_ms": 100, "batch_size": 10, "running": true}' | jq
echo ""
echo "Producing 100 msg/sec vs consuming 2 msg/sec"
```

### Watch lag grow

```bash {"name": "watch-lag-grow"}
echo "Press Ctrl+C to stop watching"
for i in {1..20}; do
  data=$(curl -s http://localhost:8010/lag)
  lag=$(echo $data | jq '.total_lag')
  status=$(echo $data | jq -r '.status')
  rate=$(echo $data | jq '.rate_of_change // 0')
  echo "Lag: $lag | Status: $status | Rate: $rate msg/s"
  sleep 2
done
```

### Stop producer after building lag

```bash {"name": "stop-producer"}
curl -X POST http://localhost:8000/stop | jq
```

### Check accumulated lag

```bash {"name": "check-lag-accumulated"}
curl -s http://localhost:8010/lag | jq
```

---

## Part 5: Detect and Diagnose

### Check lag status

```bash {"name": "check-lag-status"}
curl -s http://localhost:8010/status | jq
```

### View alerts

```bash {"name": "view-alerts"}
curl -s http://localhost:8010/alerts | jq
```

### Get recommendations

```bash {"name": "get-recommendations"}
curl -s http://localhost:8010/recommendations | jq
```

### Check Prometheus metrics

```bash {"name": "check-prometheus-metrics"}
echo "Key lag metrics:"
curl -s http://localhost:8010/metrics | grep -E "kafka_consumer_group_lag|kafka_lag_" | head -20
```

---

## Part 6: Scale Consumers

### Start additional consumers

```bash {"name": "scale-consumers"}
docker compose --profile scaled up -d consumer-2 consumer-3
sleep 5
docker compose ps
```

### Watch lag decrease with 3x throughput

```bash {"name": "watch-lag-decrease"}
echo "3 consumers = 6 msg/sec throughput"
for i in {1..15}; do
  data=$(curl -s http://localhost:8010/lag)
  lag=$(echo $data | jq '.total_lag')
  rate=$(echo $data | jq '.rate_of_change // 0')
  echo "Lag: $lag | Rate: $rate msg/s"
  sleep 2
done
```

### Check all consumer stats

```bash {"name": "check-all-consumers"}
echo "Consumer 1:"
curl -s http://localhost:8001/stats | jq '{instance_id, messages_processed}'
echo ""
echo "Consumer 2:"
curl -s http://localhost:8002/stats | jq '{instance_id, messages_processed}'
echo ""
echo "Consumer 3:"
curl -s http://localhost:8003/stats | jq '{instance_id, messages_processed}'
```

---

## Part 7: Pause/Resume Pattern

### Pause consumer-1 (simulate downstream issues)

```bash {"name": "pause-consumer"}
curl -X POST http://localhost:8001/pause | jq
```

### Check consumer status

```bash {"name": "check-paused-status"}
curl -s http://localhost:8001/health | jq
```

### Produce while paused

```bash {"name": "produce-while-paused"}
curl -X POST http://localhost:8000/burst \
  -H "Content-Type: application/json" \
  -d '{"count": 50, "delay_ms": 10}' | jq
```

### Check lag (consumer-1 not helping)

```bash {"name": "check-lag-paused"}
sleep 2
curl -s http://localhost:8010/lag | jq '{total_lag, status}'
```

### Resume consumer-1

```bash {"name": "resume-consumer"}
curl -X POST http://localhost:8001/resume | jq
```

---

## Part 8: Adjust Processing Time

### Set consumer to fast mode

```bash {"name": "set-fast-mode"}
curl -X POST http://localhost:8001/fast?time_ms=50 | jq
echo ""
echo "Now consumer-1 can handle 20 msg/sec"
```

### Set consumer to slow mode

```bash {"name": "set-slow-mode"}
curl -X POST http://localhost:8001/slow?time_ms=1000 | jq
echo ""
echo "Now consumer-1 only handles 1 msg/sec"
```

### Reset to default

```bash {"name": "reset-processing-time"}
curl -X POST http://localhost:8001/config \
  -H "Content-Type: application/json" \
  -d '{"processing_time_ms": 500}' | jq
```

---

## Part 9: Burst Load Test

### Trigger a large burst

```bash {"name": "trigger-burst"}
curl -X POST http://localhost:8000/burst \
  -H "Content-Type: application/json" \
  -d '{"count": 500, "delay_ms": 5}' | jq
```

### Check immediate impact

```bash {"name": "check-burst-impact"}
curl -s http://localhost:8010/lag | jq
curl -s http://localhost:8010/recommendations | jq
```

### Watch recovery

```bash {"name": "watch-burst-recovery"}
for i in {1..20}; do
  lag=$(curl -s http://localhost:8010/lag | jq '.total_lag')
  echo "Lag: $lag"
  sleep 3
done
```

---

## Part 10: k6 Load Testing

### Run steady-state load test

```bash {"name": "k6-steady"}
docker compose run --rm k6 run /scripts/steady-producer.js
```

### Run burst load test

```bash {"name": "k6-burst"}
docker compose run --rm k6 run /scripts/burst-producer.js
```

---

## Cleanup

### Stop additional consumers

```bash {"name": "stop-scaled"}
docker compose stop consumer-2 consumer-3
```

### Full cleanup

```bash {"name": "cleanup"}
docker compose --profile scaled down -v
echo "Lab cleaned up"
```

---

## Troubleshooting Commands

### Check all logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check Kafka logs

```bash {"name": "logs-kafka"}
docker compose logs kafka --tail=50
```

### Check consumer logs

```bash {"name": "logs-consumer"}
docker compose logs consumer --tail=50
```

### Check lag monitor logs

```bash {"name": "logs-lag-monitor"}
docker compose logs lag-monitor --tail=50
```

### List Kafka topics

```bash {"name": "list-topics"}
docker compose exec kafka kafka-topics --bootstrap-server localhost:29092 --list
```

### Describe orders topic

```bash {"name": "describe-topic"}
docker compose exec kafka kafka-topics --bootstrap-server localhost:29092 --describe --topic orders
```

### Check consumer group

```bash {"name": "describe-consumer-group"}
docker compose exec kafka kafka-consumer-groups --bootstrap-server localhost:29092 --describe --group order-processors
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Start scaled | `docker compose --profile scaled up -d` |
| Stop lab | `docker compose --profile scaled down -v` |
| Check lag | `curl -s http://localhost:8010/lag \| jq` |
| Start producer | `curl -X POST http://localhost:8000/start` |
| Stop producer | `curl -X POST http://localhost:8000/stop` |
| Pause consumer | `curl -X POST http://localhost:8001/pause` |
| Resume consumer | `curl -X POST http://localhost:8001/resume` |
| Burst produce | `curl -X POST http://localhost:8000/burst -d '{"count":100}'` |
| Get recommendations | `curl -s http://localhost:8010/recommendations \| jq` |
