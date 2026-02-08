# ML Lab 04: Model Serving Under Load

Your model works great with one request at a time. But what happens when a hundred users hit it simultaneously? In this lab, you'll load-test your model API, watch it buckle under pressure, then fix it with batch prediction -- the same technique production ML systems use to serve millions of predictions per second.

## What You'll Learn

- How model APIs behave under concurrent load (spoiler: latency spikes)
- Why batch prediction is dramatically faster than individual requests
- How to instrument a model API with Prometheus metrics
- How to use k6 to load-test ML endpoints
- How to read Grafana dashboards to diagnose serving bottlenecks

## Architecture

```
                              ┌──────────────────────────────────────────────┐
                              │             Grafana (:3001)                  │
                              │   ┌────────────────────────────────────┐    │
                              │   │  Throughput │ Latency │ Batch Size │    │
                              │   └────────────────────────────────────┘    │
                              └──────────────────┬───────────────────────────┘
                                                 │ queries
                              ┌──────────────────▼───────────────────────────┐
                              │           Prometheus (:9090)                  │
                              │         scrapes /metrics every 5s            │
                              └──────────────────┬───────────────────────────┘
                                                 │ scrapes
┌──────────────┐  /predict   ┌───────────────────▼───────────────────────────┐
│     k6       │────────────▶│           Model API (:8000)                   │
│  Load Tester │             │  ┌─────────┐  ┌─────────────┐  ┌──────────┐  │
│              │  /predict/  │  │ /predict│  │/predict/batch│  │ /metrics │  │
│              │────batch───▶│  │ (single)│  │ (vectorized) │  │ (prom)   │  │
└──────────────┘             │  └─────────┘  └─────────────┘  └──────────┘  │
                             └───────────────────────────────────────────────┘
```

## Prerequisites

- Docker and Docker Compose

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

### 2. Open Grafana

Open [http://localhost:3001](http://localhost:3001) in your browser (admin/admin).

### 3. Run Load Tests

```bash
# Single-request load test
docker compose run --rm k6 run /scripts/single-requests.js

# Batch-request load test
docker compose run --rm k6 run /scripts/batch-requests.js
```

| Service | URL | Purpose |
|---------|-----|---------|
| Model API | http://localhost:8000 | Prediction endpoints |
| Model API Docs | http://localhost:8000/docs | Swagger UI |
| Prometheus | http://localhost:9090 | Metrics storage |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Explore the Model API

Start the services and test both endpoints manually. Send a single prediction request and a batch request. Compare the response formats and notice the per-item latency difference.

**You'll answer:** What is the per-item latency for a single request vs. a batch of 10?

### Exercise 2: Load Test Single Predictions

Run the k6 single-request load test and watch the Grafana dashboard. Observe how latency increases as concurrent users ramp up. Note the p95 and p99 latencies.

**You'll answer:** What happens to p95 latency when 10 virtual users hit /predict simultaneously?

### Exercise 3: Load Test Batch Predictions

Run the k6 batch load test. Each virtual user sends 10 texts per request. Compare the total throughput (predictions per second) to the single-request test.

**You'll answer:** How many more predictions per second does batch mode achieve?

### Exercise 4: Compare Single vs. Batch in Grafana

With both load test results visible in Grafana, compare the throughput and latency panels side by side. The batch endpoint processes the same TF-IDF vectorization once for N inputs instead of N times.

**You'll answer:** Why is batch prediction faster? What specific computation is saved?

### Exercise 5: Push the Limits

Modify the k6 scripts to increase virtual users or batch sizes. Find the point where the API starts returning errors or latency becomes unacceptable.

**You'll answer:** At what load does the API start failing? What is the bottleneck?

## Key Takeaways

1. **Individual predictions don't scale** -- each request pays the full overhead of vectorization and inference
2. **Batch prediction amortizes overhead** -- vectorize once, predict many, dramatically improving throughput
3. **Metrics are essential** -- without Prometheus counters and histograms, you're flying blind under load
4. **Load testing reveals surprises** -- your model may be fast in isolation but slow under concurrency

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Model API not starting

```bash
docker compose logs model-api
```

The model trains on startup -- give it 30-60 seconds.

### k6 connection refused

Make sure the model API is healthy before running load tests:

```bash
docker compose ps
curl http://localhost:8000/ready
```

### Grafana shows no data

Check that Prometheus is scraping the model API:

```bash
curl http://localhost:9090/api/v1/targets
```

### Port conflicts

```bash
lsof -i :8000 -i :9090 -i :3001
```

Kill conflicting processes or change ports in `docker-compose.yml`.

## What's Next

[ML Lab 05: Model Drift Detection](../lab-05-model-drift-detection/) -- Deploy your model and watch what happens when the real world stops looking like your training data.
