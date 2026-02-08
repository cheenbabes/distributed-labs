# Demo Runbook: Model Serving Under Load

This runbook contains all commands for the video demo. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

---

## Pre-Demo Setup

### Check Docker is running

```bash {"name": "check-docker"}
docker info > /dev/null 2>&1 && echo "✓ Docker is running" || echo "✗ Docker is not running"
```

### Clean any previous lab state

```bash {"name": "clean-previous"}
docker compose down -v 2>/dev/null || true
```

---

## Part 1: Start the Services

### Build and start all services

```bash {"name": "start-lab"}
docker compose up --build -d
```

### Wait for model API to be ready

```bash {"name": "wait-healthy"}
echo "Waiting for model to train and API to start (may take 30-60s)..."
for i in $(seq 1 30); do
    if curl -s http://localhost:8000/ready > /dev/null 2>&1; then
        echo "✓ Model API is ready!"
        break
    fi
    echo "  waiting... ($i)"
    sleep 5
done
docker compose ps
```

### Show service URLs

```bash {"name": "show-urls"}
echo "Service URLs:"
echo ""
echo "  Model API:     http://localhost:8000"
echo "  API Docs:      http://localhost:8000/docs"
echo "  Prometheus:    http://localhost:9090"
echo "  Grafana:       http://localhost:3001  (admin/admin)"
echo ""
echo "Open Grafana and navigate to the 'ML Lab 04 - Model Serving Under Load' dashboard."
```

---

## Part 2: Test Single Predictions

### Send a single prediction request

```bash {"name": "single-predict"}
curl -s -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"text": "The pitcher threw a perfect curveball for strike three"}' | python3 -m json.tool
```

### Send another prediction

```bash {"name": "single-predict-2"}
curl -s -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"text": "NASA launched the James Webb telescope to observe distant galaxies"}' | python3 -m json.tool
```

---

## Part 3: Test Batch Predictions

### Send a batch of 5 predictions

```bash {"name": "batch-predict"}
curl -s -X POST http://localhost:8000/predict/batch \
  -H "Content-Type: application/json" \
  -d '{
    "texts": [
      "The home run sailed over the center field wall",
      "SpaceX landed the Falcon 9 first stage successfully",
      "The shortstop turned an incredible double play",
      "The Hubble telescope captured a new nebula image",
      "The batting average leader has been on fire this month"
    ]
  }' | python3 -m json.tool
```

### Compare single vs batch latency

```bash {"name": "compare-latency"}
echo "=== Single request (1 prediction) ==="
time curl -s -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"text": "The pitcher struck out the side"}' > /dev/null

echo ""
echo "=== Batch request (10 predictions) ==="
time curl -s -X POST http://localhost:8000/predict/batch \
  -H "Content-Type: application/json" \
  -d '{
    "texts": [
      "The pitcher threw a fastball",
      "NASA launched a satellite",
      "The home run sealed the game",
      "The telescope captured images",
      "The shortstop made a diving catch",
      "SpaceX landed the rocket",
      "The batting average is remarkable",
      "The space station orbits Earth",
      "The catcher called for a curveball",
      "Astronomers found a new exoplanet"
    ]
  }' > /dev/null
```

---

## Part 4: Load Test — Single Requests

### Run k6 single-request load test

```bash {"name": "loadtest-single"}
docker compose run --rm k6 run /scripts/single-requests.js
```

---

## Part 5: Load Test — Batch Requests

### Run k6 batch-request load test

```bash {"name": "loadtest-batch"}
docker compose run --rm k6 run /scripts/batch-requests.js
```

---

## Part 6: Check Metrics

### View raw Prometheus metrics

```bash {"name": "raw-metrics"}
curl -s http://localhost:8000/metrics | grep -E "^model_" | head -30
```

### Query Prometheus for throughput

```bash {"name": "prom-throughput"}
curl -s "http://localhost:9090/api/v1/query?query=rate(model_requests_total[1m])" | python3 -m json.tool
```

### Query Prometheus for latency percentiles

```bash {"name": "prom-latency"}
curl -s "http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,rate(model_prediction_duration_seconds_bucket[1m]))" | python3 -m json.tool
```

---

## Part 7: Explore in Grafana

Open Grafana and observe:

1. **Throughput panel** — how many requests/sec each endpoint handles
2. **Latency panel** — p50/p95/p99 for single vs batch
3. **Batch size panel** — distribution of batch sizes
4. **Predictions by class** — whether both classes are balanced

```bash {"name": "open-grafana"}
echo "Open Grafana: http://localhost:3001"
echo "Login: admin / admin"
echo "Dashboard: ML Lab 04 - Model Serving Under Load"
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo "✓ Lab cleaned up"
```

---

## Troubleshooting Commands

### Check model API logs

```bash {"name": "logs-api"}
docker compose logs model-api --tail=50
```

### Check all service status

```bash {"name": "service-status"}
docker compose ps
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart model API

```bash {"name": "restart-api"}
docker compose restart model-api
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Single predict | `curl -X POST http://localhost:8000/predict -H 'Content-Type: application/json' -d '{"text": "..."}'` |
| Batch predict | `curl -X POST http://localhost:8000/predict/batch -H 'Content-Type: application/json' -d '{"texts": [...]}'` |
| Load test (single) | `docker compose run --rm k6 run /scripts/single-requests.js` |
| Load test (batch) | `docker compose run --rm k6 run /scripts/batch-requests.js` |
| View metrics | `curl http://localhost:8000/metrics` |
| API Docs | http://localhost:8000/docs |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3001 (admin/admin) |
