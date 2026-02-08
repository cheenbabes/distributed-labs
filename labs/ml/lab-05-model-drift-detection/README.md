# ML Lab 05: Model Drift Detection

Your model scored 96% accuracy in testing. Six months later, it's silently making garbage predictions and nobody noticed. This is model drift -- the real world changes but your model doesn't. In this lab, you'll deploy a model, simulate drift with gradually shifting data, build detection metrics, and learn to catch drift before your users do.

## What You'll Learn

- What data drift and concept drift are and why they silently kill model performance
- How to instrument a model API to detect distribution shifts in real time
- How prediction confidence and entropy reveal out-of-distribution inputs
- How to build Grafana dashboards that alert on drift signals
- How to calculate Population Stability Index (PSI) for drift quantification

## Architecture

```
┌──────────────────────┐         ┌──────────────────────────────────────────┐
│   Drift Simulator    │         │          Model API (:8000)               │
│                      │         │                                          │
│  Phase 1: Normal     │ /predict│  ┌──────────┐  ┌───────────────────┐    │
│  Phase 2: Slight     │────────▶│  │ Predict  │  │  Drift Metrics    │    │
│  Phase 3: Moderate   │         │  │          │  │  - text length    │    │
│  Phase 4: Heavy      │         │  │          │  │  - confidence     │    │
│                      │         │  │          │  │  - entropy        │    │
└──────────────────────┘         │  └──────────┘  └───────────────────┘    │
                                 └──────────────────┬──────────────────────┘
                                                    │ /metrics
                                 ┌──────────────────▼──────────────────────┐
                                 │         Prometheus (:9090)               │
                                 │       scrapes every 5 seconds            │
                                 └──────────────────┬──────────────────────┘
                                                    │ queries
                                 ┌──────────────────▼──────────────────────┐
                                 │          Grafana (:3001)                 │
                                 │  ┌──────────────────────────────────┐   │
                                 │  │ Confidence │ Entropy │ Rate     │   │
                                 │  └──────────────────────────────────┘   │
                                 └─────────────────────────────────────────┘
```

## Prerequisites

- Docker and Docker Compose

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

### 2. Open Grafana

Open [http://localhost:3001](http://localhost:3001) in your browser (admin/admin). Navigate to the "ML Lab 05 - Drift Detection" dashboard.

### 3. Start the Drift Simulator

```bash
docker compose up drift-simulator
```

Watch the Grafana dashboard as the simulator progresses through phases.

| Service | URL | Purpose |
|---------|-----|---------|
| Model API | http://localhost:8000 | Prediction endpoint with drift metrics |
| Model API Docs | http://localhost:8000/docs | Swagger UI |
| Prometheus | http://localhost:9090 | Metrics storage |
| Grafana | http://localhost:3001 | Drift dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Understand the Baseline

Before starting the drift simulator, send some in-distribution requests manually and observe the metrics. Note the typical confidence level and text length for baseball and space articles.

**You'll answer:** What is the baseline confidence range for in-distribution data?

### Exercise 2: Watch Drift Happen in Real Time

Start the drift simulator and watch the Grafana dashboard. The simulator runs four phases, each with increasing amounts of out-of-distribution data.

**You'll answer:** At which phase does the confidence metric first noticeably drop?

### Exercise 3: Analyze Prediction Entropy

High prediction entropy means the model is uncertain -- probabilities are spread across classes rather than concentrated on one. During drift, entropy increases because the model receives inputs unlike its training data.

**You'll answer:** How does average entropy change between Phase 1 and Phase 4?

### Exercise 4: Explore the Analysis Notebook

Open `notebooks/drift_analysis.ipynb` in Jupyter (or run locally). This notebook queries Prometheus metrics and calculates PSI (Population Stability Index) to quantify drift.

**You'll answer:** What PSI value indicates significant drift? Where does your simulation cross that threshold?

### Exercise 5: Design a Drift Alert

Using what you learned, define a Grafana alert rule. What metric would you alert on? What threshold? How quickly should it fire?

**You'll answer:** Write the PromQL expression and threshold for a drift alert.

## Key Takeaways

1. **Models degrade silently** -- without monitoring, you won't know until users complain
2. **Confidence drops signal trouble** -- a model unsure about its predictions is seeing unfamiliar data
3. **Entropy is a universal drift signal** -- it works regardless of the specific drift type
4. **PSI quantifies distribution shift** -- it gives you a single number to threshold and alert on
5. **Detection is only half the battle** -- you also need a plan for retraining or fallback

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

### Drift simulator exits immediately

The simulator waits for the model API to be ready. Check that the API is healthy:

```bash
curl http://localhost:8000/ready
```

### Grafana shows no data

Check Prometheus targets:

```bash
curl http://localhost:9090/api/v1/targets
```

### Simulator finishes too quickly

The default simulation runs for about 4 minutes (4 phases x 60 seconds). Adjust phase durations in `services/drift-simulator/simulator.py`.

### Port conflicts

```bash
lsof -i :8000 -i :9090 -i :3001
```

Kill conflicting processes or change ports in `docker-compose.yml`.

## What's Next

[ML Lab 06: A/B Testing Models](../lab-06-ab-testing-models/) -- Deploy two model versions side by side and use statistical testing to decide which one is better.
