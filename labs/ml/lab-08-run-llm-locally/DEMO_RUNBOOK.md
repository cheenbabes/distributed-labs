# Demo Runbook: Run an LLM Locally

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
echo "Waiting for Ollama and LLM Bench to start..."
sleep 15
docker compose ps
```

### Verify Ollama is running

```bash {"name": "check-ollama"}
curl -s http://localhost:11434/api/tags | python3 -m json.tool
```

### Verify LLM Bench API is running

```bash {"name": "check-bench"}
curl -s http://localhost:8000/health | python3 -m json.tool
```

---

## Part 2: Pull Your First Model

### Pull tinyllama (smallest model, ~637 MB)

```bash {"name": "pull-tinyllama"}
echo "Pulling tinyllama... this may take 1-3 minutes on first run."
curl -X POST http://localhost:8000/pull \
  -H "Content-Type: application/json" \
  -d '{"model": "tinyllama"}'
echo ""
```

### List available models

```bash {"name": "list-models"}
curl -s http://localhost:8000/models | python3 -m json.tool
```

---

## Part 3: Generate Text and Measure Latency

### Simple generation with timing

```bash {"name": "generate-simple"}
echo "--- Short prompt ---"
curl -s -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What is 2+2?", "model": "tinyllama", "max_tokens": 20}' | python3 -m json.tool
```

### Medium prompt

```bash {"name": "generate-medium"}
echo "--- Medium prompt ---"
curl -s -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Explain what a neural network is in 3 sentences.", "model": "tinyllama", "max_tokens": 100}' | python3 -m json.tool
```

### Long prompt

```bash {"name": "generate-long"}
echo "--- Long prompt ---"
curl -s -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Write a Python function that implements binary search. Include docstring and comments.", "model": "tinyllama", "max_tokens": 200}' | python3 -m json.tool
```

---

## Part 4: Streaming Generation

### Watch tokens arrive in real time

```bash {"name": "stream-generate"}
echo "--- Streaming tokens ---"
curl -N -X POST http://localhost:8000/generate/stream \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Write a haiku about distributed systems.", "model": "tinyllama", "max_tokens": 50}'
echo ""
```

---

## Part 5: Run the Benchmark Suite

### Run the built-in benchmark endpoint

```bash {"name": "benchmark-api"}
echo "--- Running benchmark via API ---"
curl -s -X POST "http://localhost:8000/benchmark?model=tinyllama&runs=3" | python3 -m json.tool
```

### Run the benchmark script directly

```bash {"name": "benchmark-script"}
docker compose exec llm-bench python benchmark.py tinyllama 3
```

---

## Part 6: Observe Metrics in Grafana

### Show Grafana URL

```bash {"name": "show-grafana"}
echo "Open Grafana in your browser:"
echo ""
echo "  http://localhost:3001"
echo ""
echo "Login: admin / admin"
echo "Dashboard: LLM Benchmark Dashboard"
```

### Generate load for the dashboard

```bash {"name": "generate-load"}
echo "Generating traffic for Grafana..."
for i in $(seq 1 10); do
  curl -s -X POST http://localhost:8000/generate \
    -H "Content-Type: application/json" \
    -d '{"prompt": "Count to five.", "model": "tinyllama", "max_tokens": 30}' > /dev/null
  echo "  Request $i complete"
done
echo "Done. Check the Grafana dashboard."
```

### Check raw Prometheus metrics

```bash {"name": "check-metrics"}
curl -s http://localhost:8000/metrics | grep "^llm_"
```

---

## Cleanup

### Stop all services and remove volumes

```bash {"name": "cleanup"}
docker compose down -v
echo "Lab cleaned up"
```

---

## Troubleshooting Commands

### Check Ollama logs

```bash {"name": "logs-ollama"}
docker compose logs ollama --tail=30
```

### Check LLM Bench logs

```bash {"name": "logs-bench"}
docker compose logs llm-bench --tail=30
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart services

```bash {"name": "restart"}
docker compose restart
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Pull model | `curl -X POST http://localhost:8000/pull -H "Content-Type: application/json" -d '{"model": "tinyllama"}'` |
| Generate text | `curl -X POST http://localhost:8000/generate -H "Content-Type: application/json" -d '{"prompt": "...", "model": "tinyllama"}'` |
| Stream text | `curl -N -X POST http://localhost:8000/generate/stream -H "Content-Type: application/json" -d '{"prompt": "..."}'` |
| Run benchmark | `docker compose exec llm-bench python benchmark.py tinyllama 3` |
| View logs | `docker compose logs ollama` |
| LLM Bench API | http://localhost:8000 |
| Ollama | http://localhost:11434 |
| Grafana | http://localhost:3001 (admin/admin) |
| Prometheus | http://localhost:9090 |
