# Demo Runbook: Model Drift Detection

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

## Part 1: Start the Core Services

### Build and start model API + monitoring

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
echo "Open Grafana and navigate to the 'ML Lab 05 - Drift Detection' dashboard."
```

---

## Part 2: Establish the Baseline

### Send in-distribution requests (baseball)

```bash {"name": "baseline-baseball"}
echo "Sending baseball-related requests..."
for i in $(seq 1 5); do
    curl -s -X POST http://localhost:8000/predict \
      -H "Content-Type: application/json" \
      -d '{"text": "The pitcher threw a perfect curveball for strike three to end the inning"}' | python3 -m json.tool
    echo "---"
done
```

### Send in-distribution requests (space)

```bash {"name": "baseline-space"}
echo "Sending space-related requests..."
for i in $(seq 1 5); do
    curl -s -X POST http://localhost:8000/predict \
      -H "Content-Type: application/json" \
      -d '{"text": "NASA announced the discovery of water ice on the surface of Mars using satellite imagery"}' | python3 -m json.tool
    echo "---"
done
```

### Note the baseline confidence and entropy

```bash {"name": "baseline-metrics"}
echo "=== Current Drift Metrics ==="
echo ""
echo "Confidence (should be high ~0.9+):"
curl -s http://localhost:8000/metrics | grep "model_prediction_confidence_current"
echo ""
echo "Entropy (should be low ~0.2):"
curl -s http://localhost:8000/metrics | grep "model_prediction_entropy_current"
```

---

## Part 3: Send Out-of-Distribution Requests

### Send politics text (never seen in training)

```bash {"name": "ood-politics"}
curl -s -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"text": "The senator proposed a new bill to reform healthcare and reduce insurance costs for working families"}' | python3 -m json.tool
```

### Send computer graphics text (never seen in training)

```bash {"name": "ood-graphics"}
curl -s -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"text": "The ray tracing algorithm uses recursive reflection to generate photorealistic images in real time"}' | python3 -m json.tool
```

### Compare confidence: in-dist vs out-of-dist

```bash {"name": "ood-compare"}
echo "=== In-Distribution Request ==="
curl -s -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"text": "The home run sailed over the center field fence"}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Confidence: {d[\"confidence\"]:.4f}  Entropy: {d[\"entropy\"]:.4f}')"

echo ""
echo "=== Out-of-Distribution Request ==="
curl -s -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"text": "The parliament voted on the trade agreement after months of negotiation between member states"}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'  Confidence: {d[\"confidence\"]:.4f}  Entropy: {d[\"entropy\"]:.4f}')"
```

---

## Part 4: Run the Drift Simulator

### Start the drift simulator (runs ~4 minutes)

```bash {"name": "start-simulator"}
echo "Starting drift simulator..."
echo "Watch the Grafana dashboard as drift increases over 4 phases."
echo ""
docker compose --profile simulator up drift-simulator
```

---

## Part 5: Analyze Drift in Grafana

### Open Grafana to observe drift

```bash {"name": "open-grafana"}
echo "Open Grafana: http://localhost:3001"
echo "Login: admin / admin"
echo ""
echo "What to look for on the dashboard:"
echo "  1. Confidence panel — average drops from ~0.9 to ~0.6-0.7"
echo "  2. Entropy panel — average rises from ~0.3 to ~0.7-0.9"
echo "  3. Prediction Rate — class distribution may shift"
echo "  4. Text Length — distribution changes as different data comes in"
```

---

## Part 6: Query Prometheus Directly

### Average confidence over last 5 minutes

```bash {"name": "prom-confidence"}
curl -s "http://localhost:9090/api/v1/query?query=rate(model_prediction_confidence_sum[5m])/rate(model_prediction_confidence_count[5m])" | python3 -m json.tool
```

### Average entropy over last 5 minutes

```bash {"name": "prom-entropy"}
curl -s "http://localhost:9090/api/v1/query?query=rate(model_prediction_entropy_sum[5m])/rate(model_prediction_entropy_count[5m])" | python3 -m json.tool
```

### Prediction rate by class

```bash {"name": "prom-class-rate"}
curl -s "http://localhost:9090/api/v1/query?query=rate(model_predictions_total[5m])" | python3 -m json.tool
```

---

## Part 7: Design a Drift Alert

### Suggested PromQL for drift alert

```bash {"name": "drift-alert-query"}
echo "=== Drift Alert PromQL ==="
echo ""
echo "Alert on low confidence (below 0.75 average over 5 minutes):"
echo '  rate(model_prediction_confidence_sum[5m]) / rate(model_prediction_confidence_count[5m]) < 0.75'
echo ""
echo "Alert on high entropy (above 0.6 average over 5 minutes):"
echo '  rate(model_prediction_entropy_sum[5m]) / rate(model_prediction_entropy_count[5m]) > 0.6'
echo ""
echo "These can be configured as Grafana alert rules or Prometheus alerting rules."
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose --profile simulator down -v
echo "✓ Lab cleaned up"
```

---

## Troubleshooting Commands

### Check model API logs

```bash {"name": "logs-api"}
docker compose logs model-api --tail=50
```

### Check drift simulator logs

```bash {"name": "logs-simulator"}
docker compose --profile simulator logs drift-simulator --tail=50
```

### Check all service status

```bash {"name": "service-status"}
docker compose --profile simulator ps
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
| Start simulator | `docker compose --profile simulator up drift-simulator` |
| Stop lab | `docker compose --profile simulator down -v` |
| View API logs | `docker compose logs model-api` |
| View simulator logs | `docker compose --profile simulator logs drift-simulator` |
| Test predict | `curl -X POST http://localhost:8000/predict -H 'Content-Type: application/json' -d '{"text": "..."}'` |
| View metrics | `curl http://localhost:8000/metrics` |
| API Docs | http://localhost:8000/docs |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3001 (admin/admin) |
