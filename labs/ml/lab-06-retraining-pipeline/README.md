# ML Lab 06: Retraining Pipeline

Your model is live, users are sending predictions, and some of them are wrong. Now what? You can't just retrain once and forget about it -- real-world data shifts, user behavior changes, and your model decays. In this lab, you'll build a closed-loop retraining pipeline: collect feedback, accumulate data, retrain the model, validate it against a golden set, and hot-swap it into production without downtime.

## What You'll Learn

- How to collect user feedback and store it for retraining
- How to combine original training data with feedback data for retraining
- How to validate a new model against a golden evaluation set before deploying
- How to hot-swap a model in a running service without restart
- How to monitor the full retrain cycle with Prometheus and Grafana

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Client     │────▶│  Model API   │────▶│  Prediction  │
│  (curl)      │     │  (FastAPI)   │     │  Response    │
└──────┬───────┘     └──────┬───────┘     └──────────────┘
       │                    │
       │  /feedback         │  watches /app/model/
       ▼                    ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Feedback    │────▶│  Retrainer   │────▶│  New Model   │
│  (JSONL)     │     │  (periodic)  │     │  (if better) │
└──────────────┘     └──────┬───────┘     └──────┬───────┘
                            │                     │
                            ▼                     │
                     ┌──────────────┐             │
                     │  Golden Set  │    hot-reload│
                     │  (validate)  │◀────────────┘
                     └──────────────┘

┌──────────────┐     ┌──────────────┐
│  Prometheus  │────▶│   Grafana    │
│  (scrape)    │     │ (dashboards) │
└──────────────┘     └──────────────┘
```

## Prerequisites

- Docker and Docker Compose

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

### 2. Open the Services

Open [http://localhost:8000/docs](http://localhost:8000/docs) for the Model API (Swagger UI).

Open [http://localhost:3000](http://localhost:3000) for Grafana dashboards.

| Service | URL | Purpose |
|---------|-----|---------|
| Model API | http://localhost:8000 | Prediction + feedback endpoints |
| Swagger UI | http://localhost:8000/docs | Interactive API docs |
| Prometheus | http://localhost:9090 | Metrics collection |
| Grafana | http://localhost:3000 | Monitoring dashboards |

## Lab Exercises

### Exercise 1: Make Predictions and Send Feedback

Hit the prediction endpoint with some text, then send feedback telling the model whether it was correct or incorrect. Watch the feedback accumulate in the JSONL file.

**You'll answer:** How does the feedback file grow? What data is stored per entry?

### Exercise 2: Trigger a Retrain Cycle

Send at least 20 feedback samples (the minimum threshold), then watch the retrainer container logs. It will combine your feedback with the original training data, train a new model, and validate it.

**You'll answer:** Did the new model improve? What does the retrainer log when it decides to save or skip?

### Exercise 3: Watch the Hot-Swap

After the retrainer saves a new model, watch the model API logs. It polls the model file every 10 seconds and reloads automatically when it detects a change. Verify the version changed via `/model/info`.

**You'll answer:** How long does hot-reload take? Is there any downtime during the swap?

### Exercise 4: Intentionally Degrade the Model

Send feedback with deliberately wrong corrections to simulate data poisoning. After a retrain cycle, check whether the golden-set validation catches the degradation.

**You'll answer:** Does the validation gate prevent a worse model from deploying?

### Exercise 5: Monitor the Pipeline in Grafana

Open the Grafana dashboard and watch predictions, feedback rate, model reloads, and latency in real time. Generate some traffic and retrain cycles to populate the graphs.

**You'll answer:** Which metrics would you alert on in production? How would you detect a failed retrain?

## Key Takeaways

1. **Feedback is fuel** -- collecting corrections from users creates a continuous improvement loop
2. **Golden-set validation is your safety net** -- never deploy a model that performs worse than the current one
3. **Hot-reload avoids downtime** -- the API watches for file changes and swaps models without restarting
4. **Retraining needs a trigger threshold** -- don't retrain on 5 samples; wait for enough signal
5. **Monitoring closes the loop** -- without metrics on model version, accuracy, and feedback rate, you're flying blind

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Model API not starting

```bash
docker compose logs model-api
```

The initial model training downloads data from the internet. If it fails, check your network connection.

### Retrainer not triggering

Check the retrainer logs:

```bash
docker compose logs retrainer
```

The retrainer waits 30 seconds before its first check, then checks every 60 seconds. It also requires at least 20 feedback samples before retraining.

### Port conflicts

```bash
lsof -i :8000
lsof -i :3000
lsof -i :9090
```

Kill conflicting processes or change ports in `docker-compose.yml`.

### Grafana dashboard not showing data

Wait 30 seconds after starting for Prometheus to begin scraping. Verify the data source works at http://localhost:3000/connections/datasources.

## What's Next

[ML Lab 07: Train a Neural Network](../lab-07-train-neural-network/) -- Move beyond scikit-learn and build your first neural network with PyTorch. Learn why deep learning matters for images and other complex data.
