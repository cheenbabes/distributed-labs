# ML Lab 08: Run an LLM Locally

Everyone uses ChatGPT, but what happens when you run an LLM on your own machine? In this lab, you'll pull a language model, run it locally with Ollama, measure its performance down to the millisecond, and discover the metrics that actually matter: time-to-first-token, tokens-per-second, and total generation time. By the end, you'll know exactly what "running an LLM" means at the systems level.

## What You'll Learn

- How to run open-source LLMs locally using Ollama
- What time-to-first-token (TTFT) and tokens-per-second (TPS) mean and why they matter
- How streaming generation works vs batch generation
- How prompt length and max tokens affect latency
- How to monitor LLM inference with Prometheus and Grafana

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Benchmark  │────▶│  LLM Bench   │────▶│   Ollama     │
│   Script     │     │  (FastAPI)   │     │  (LLM Runtime│
│              │     │  :8000       │     │   :11434)    │
└──────────────┘     └──────┬───────┘     └──────────────┘
                            │
                     ┌──────▼───────┐     ┌──────────────┐
                     │  Prometheus  │────▶│   Grafana    │
                     │  :9090       │     │   :3001      │
                     └──────────────┘     └──────────────┘
```

## Prerequisites

- Docker and Docker Compose
- At least 4 GB of free disk space (for the tinyllama model)
- At least 4 GB of free RAM

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

### 2. Pull a Model

The first time, you need to pull a model into Ollama. TinyLlama (~637 MB) is the smallest option:

```bash
curl -X POST http://localhost:8000/pull \
  -H "Content-Type: application/json" \
  -d '{"model": "tinyllama"}'
```

This takes 1-3 minutes depending on your internet speed. Subsequent runs are instant.

### 3. Generate Text

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What is 2+2?", "model": "tinyllama", "max_tokens": 50}'
```

### 4. Open Grafana

Open [http://localhost:3001](http://localhost:3001) in your browser (admin/admin).

| Service | URL | Purpose |
|---------|-----|---------|
| LLM Bench API | http://localhost:8000 | Benchmark service with metrics |
| Ollama | http://localhost:11434 | LLM runtime |
| Prometheus | http://localhost:9090 | Metrics collection |
| Grafana | http://localhost:3001 | Metrics dashboards |

## Lab Exercises

### Exercise 1: Pull and Run Your First Local LLM

Pull the tinyllama model and send your first prompt. Examine the response object to understand what TTFT, TPS, and total generation time mean.

**You'll answer:** How long did the first token take? How many tokens per second did you get?

### Exercise 2: Compare Different Prompts

Use the benchmark script to test short, medium, and long prompts. See how prompt complexity affects TTFT and generation time.

```bash
docker compose exec llm-bench python benchmark.py tinyllama 3
```

**You'll answer:** Which prompt type has the highest TTFT? Which has the most tokens per second?

### Exercise 3: Streaming vs Non-Streaming

Try the streaming endpoint and compare the user experience to the batch endpoint. Watch tokens arrive one by one.

```bash
curl -N -X POST http://localhost:8000/generate/stream \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Write a short poem about code.", "model": "tinyllama", "max_tokens": 100}'
```

**You'll answer:** Why does streaming feel faster even though total time is the same?

### Exercise 4: Run the API Benchmark Suite

Hit the built-in benchmark endpoint that runs all prompt types with multiple iterations and returns aggregated statistics.

```bash
curl -X POST "http://localhost:8000/benchmark?model=tinyllama&runs=3"
```

**You'll answer:** What is the average TPS across all prompt types? Is it consistent?

### Exercise 5: Observe Metrics in Grafana

Open Grafana, find the "LLM Benchmark Dashboard", and run several more requests. Watch the TTFT, TPS, and generation time panels update in real time.

**You'll answer:** What do the p50 vs p95 latency lines tell you about consistency?

## Key Takeaways

1. **TTFT is the user-perceived latency** -- how long before the first token appears on screen
2. **TPS determines throughput** -- how fast the model generates once it starts
3. **Streaming hides latency** -- same total time, but the user sees progress immediately
4. **Model size is the biggest factor** -- smaller models are faster but less capable
5. **Local inference gives you control** -- no API rate limits, no data leaving your machine

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Ollama healthcheck failing

Ollama can take 30-60 seconds to start. Wait and check:

```bash
docker compose logs ollama --tail=20
```

### Model pull is slow

The first pull downloads the model weights. TinyLlama is ~637 MB. Check progress:

```bash
curl http://localhost:11434/api/tags
```

### Out of memory

TinyLlama needs ~2 GB RAM for inference. Check resource usage:

```bash
docker stats --no-stream
```

If you are low on memory, make sure to close other applications.

### Port conflicts

```bash
lsof -i :8000
lsof -i :11434
```

Kill conflicting processes or change ports in `docker-compose.yml`.

### Generation is very slow

On CPU-only machines, expect 5-20 tokens/sec for TinyLlama. This is normal. GPU acceleration (if available) can provide 10-100x speedup.

## What's Next

[ML Lab 09: Fine-Tune a Model](../lab-09-fine-tune-model/) -- Take a pre-trained model and teach it your specific task with fine-tuning.
