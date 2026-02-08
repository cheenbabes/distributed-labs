# ML Lab 03: Deploy a Model as an API

You've trained a model. You've cleaned the data. Now what? A model sitting in a Jupyter notebook is useless — it needs to serve predictions to real applications. In this lab, you'll wrap a trained model in a FastAPI service, add health checks and readiness probes, instrument it with Prometheus metrics, and watch it all come to life in a Grafana dashboard. By the end, you'll have a production-style ML API running locally.

## What You'll Learn

- How to serve an ML model behind a REST API with FastAPI
- How to implement health, readiness, and prediction endpoints
- How to instrument an ML service with Prometheus metrics (latency, throughput, confidence)
- How to visualize model performance in real-time with Grafana
- The difference between training a model and operating one

## Architecture

```
┌──────────────┐     ┌──────────────────────────────────────┐     ┌──────────────┐
│   Client     │────▶│          Model API (FastAPI)          │────▶│  Prometheus   │
│  (curl /     │     │                                      │     │  (scrapes     │
│   browser)   │     │  POST /predict  → classification     │     │   /metrics)   │
│              │     │  GET  /health   → status              │     │              │
│              │     │  GET  /ready    → model loaded?       │     └──────┬───────┘
│              │     │  GET  /metrics  → prometheus export   │            │
└──────────────┘     └──────────────────────────────────────┘     ┌──────▼───────┐
                                                                  │   Grafana     │
                                                                  │  (dashboards) │
                                                                  └──────────────┘
```

## Prerequisites

- Docker and Docker Compose

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

The model-api container will train the model at startup (takes ~30 seconds), then start serving predictions.

### 2. Wait for Services

```bash
docker compose ps
```

Wait until all services show "healthy" status.

### 3. Test the API

```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"text": "NASA launched a new satellite to study Mars"}'
```

### 4. Open the Dashboard

Open [http://localhost:3001](http://localhost:3001) in your browser (admin/admin).

| Service | URL | Purpose |
|---------|-----|---------|
| Model API | http://localhost:8000 | ML prediction service |
| API Docs | http://localhost:8000/docs | Interactive Swagger UI |
| Prometheus | http://localhost:9090 | Metrics collection |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Explore the API

Hit the health, readiness, and prediction endpoints. Open the Swagger UI at `/docs` and try different texts. Understand what each endpoint does and why you need all three.

**You'll answer:** What's the difference between `/health` and `/ready`? Why do you need both?

### Exercise 2: Make Predictions

Send different types of text to the `/predict` endpoint — baseball text, space text, ambiguous text. Look at the confidence scores and probability distributions returned.

**You'll answer:** What confidence does the model report for clearly in-domain vs ambiguous text?

### Exercise 3: Check the Metrics

Visit `http://localhost:8000/metrics` to see the raw Prometheus metrics. Identify the prediction counter, latency histogram, and confidence histogram. Understand how these get scraped into Prometheus.

**You'll answer:** What metrics are being exported? What do the histogram buckets tell you?

### Exercise 4: Watch the Dashboard

Open Grafana and find the "Model API Dashboard". Send a burst of predictions and watch the graphs update in real-time. Observe prediction rate, latency percentiles, and confidence distribution.

**You'll answer:** What's the p99 latency? Is it stable or does it spike?

### Exercise 5: Load Test

Send 100+ predictions rapidly and observe how the metrics change. Watch for latency increases under load.

**You'll answer:** How does the API perform under sustained load? Does latency degrade?

## Key Takeaways

1. **Models need APIs** — a trained model is useless until it can serve predictions to applications
2. **Health vs Ready** — health means the process is alive, ready means it can serve traffic (model loaded)
3. **Instrument everything** — prediction count, latency, and confidence are the minimum metrics for any ML service
4. **Dashboards make operations visible** — you can't manage what you can't see

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Model API not starting

The model takes ~30 seconds to train at startup. Check the logs:

```bash
docker compose logs model-api
```

### Port 8000 already in use

```bash
lsof -i :8000
```

Kill the process or change the port in `docker-compose.yml`.

### Grafana shows "No data"

Make sure you've sent at least a few predictions first:

```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"text": "The pitcher threw a fastball for strike three"}'
```

Then wait a few seconds for Prometheus to scrape the metrics.

### Prometheus target down

Check that the model-api is healthy:

```bash
docker compose ps
```

If it shows "starting", the model is still training. Wait for it to become "healthy".

## What's Next

[ML Lab 04: Model Serving Under Load](../lab-04-model-serving-under-load/) — Stress-test your model API with realistic traffic patterns and learn how to handle production load.
