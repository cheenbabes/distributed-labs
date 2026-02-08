# Demo Runbook: Retraining Pipeline

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
```

---

## Part 1: Start the Lab

### Build and start all services

```bash {"name": "start-lab"}
docker compose up --build -d
```

### Wait for services to be healthy

```bash {"name": "wait-healthy"}
echo "Waiting for services to start..."
sleep 30
docker compose ps
```

### Verify the API is running

```bash {"name": "verify-api"}
curl -s http://localhost:8000/health | python3 -m json.tool
```

### Check the initial model info

```bash {"name": "model-info"}
curl -s http://localhost:8000/model/info | python3 -m json.tool
```

---

## Part 2: Make Predictions

### Send a baseball prediction

```bash {"name": "predict-baseball"}
curl -s -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"text": "The pitcher threw a fastball and struck out the batter in the ninth inning"}' \
  | python3 -m json.tool
```

### Send a space prediction

```bash {"name": "predict-space"}
curl -s -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"text": "NASA launched a new telescope to study distant galaxies and black holes"}' \
  | python3 -m json.tool
```

---

## Part 3: Send Feedback

### Send correct feedback

```bash {"name": "feedback-correct"}
curl -s -X POST http://localhost:8000/feedback \
  -H "Content-Type: application/json" \
  -d '{"text": "The home run sealed the championship game", "predicted_class": "rec.sport.baseball", "correct_class": "rec.sport.baseball"}' \
  | python3 -m json.tool
```

### Send incorrect feedback (model got it wrong)

```bash {"name": "feedback-incorrect"}
curl -s -X POST http://localhost:8000/feedback \
  -H "Content-Type: application/json" \
  -d '{"text": "The satellite orbited the planet collecting atmospheric data", "predicted_class": "rec.sport.baseball", "correct_class": "sci.space"}' \
  | python3 -m json.tool
```

### Bulk-send feedback to trigger retraining (need 20+ samples)

```bash {"name": "bulk-feedback"}
echo "Sending 25 feedback samples..."

# Baseball samples
for i in $(seq 1 13); do
  curl -s -X POST http://localhost:8000/feedback \
    -H "Content-Type: application/json" \
    -d "{\"text\": \"The team scored $i runs in the baseball game this season\", \"predicted_class\": \"rec.sport.baseball\", \"correct_class\": \"rec.sport.baseball\"}" > /dev/null
done

# Space samples
for i in $(seq 1 12); do
  curl -s -X POST http://localhost:8000/feedback \
    -H "Content-Type: application/json" \
    -d "{\"text\": \"The spacecraft traveled $i million miles to reach the distant planet\", \"predicted_class\": \"sci.space\", \"correct_class\": \"sci.space\"}" > /dev/null
done

echo "Done! Sent 25 feedback samples."
curl -s http://localhost:8000/model/info | python3 -m json.tool
```

---

## Part 4: Watch the Retrain Cycle

### Check retrainer logs

```bash {"name": "retrainer-logs"}
docker compose logs retrainer --tail=30
```

### Wait for retraining and check new model version

```bash {"name": "wait-retrain"}
echo "Waiting 90 seconds for retrain cycle..."
sleep 90
echo "Checking model info after retrain:"
curl -s http://localhost:8000/model/info | python3 -m json.tool
```

### Check model API logs for hot-reload

```bash {"name": "api-reload-logs"}
docker compose logs model-api --tail=20 | grep -i "reload\|new model\|version"
```

---

## Part 5: Verify the Hot-Swap

### Confirm predictions still work after reload

```bash {"name": "post-reload-predict"}
curl -s -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"text": "The rover explored the surface of Mars looking for signs of water"}' \
  | python3 -m json.tool
```

### Check model version changed

```bash {"name": "check-version"}
curl -s http://localhost:8000/model/info | python3 -m json.tool
```

---

## Part 6: Monitor in Grafana

### Open Grafana dashboard

```bash {"name": "open-grafana"}
echo "Open Grafana in your browser:"
echo ""
echo "  http://localhost:3000"
echo ""
echo "Dashboard: ML Lab 06 - Retraining Pipeline"
echo "Default login: admin / admin"
```

### Check Prometheus targets

```bash {"name": "check-prometheus"}
curl -s http://localhost:9090/api/v1/targets | python3 -m json.tool | head -30
```

### Check raw metrics

```bash {"name": "raw-metrics"}
curl -s http://localhost:8000/metrics | head -30
```

---

## Part 7: Test the Validation Gate

### Send bad feedback to simulate data poisoning

```bash {"name": "bad-feedback"}
echo "Sending 25 deliberately wrong feedback samples..."

for i in $(seq 1 25); do
  curl -s -X POST http://localhost:8000/feedback \
    -H "Content-Type: application/json" \
    -d "{\"text\": \"The pitcher threw a curveball strike $i\", \"predicted_class\": \"rec.sport.baseball\", \"correct_class\": \"sci.space\"}" > /dev/null
done

echo "Done! Check retrainer logs after next cycle to see if validation catches it."
```

### Watch retrainer handle bad data

```bash {"name": "watch-validation"}
echo "Waiting 90 seconds for retrain cycle..."
sleep 90
docker compose logs retrainer --tail=20
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

### Check all container logs

```bash {"name": "all-logs"}
docker compose logs --tail=30
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart specific service

```bash {"name": "restart-api"}
docker compose restart model-api
```

### Inspect shared volumes

```bash {"name": "inspect-volumes"}
docker compose exec model-api ls -la /app/model/
docker compose exec model-api cat /app/model/model_metadata.json 2>/dev/null | python3 -m json.tool
docker compose exec model-api wc -l /app/data/feedback.jsonl 2>/dev/null
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| View all logs | `docker compose logs` |
| Model API docs | http://localhost:8000/docs |
| Model info | `curl http://localhost:8000/model/info` |
| Send prediction | `curl -X POST http://localhost:8000/predict -H "Content-Type: application/json" -d '{"text": "..."}'` |
| Send feedback | `curl -X POST http://localhost:8000/feedback -H "Content-Type: application/json" -d '{"text": "...", "predicted_class": "...", "correct_class": "..."}'` |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |
| Retrainer logs | `docker compose logs retrainer` |
