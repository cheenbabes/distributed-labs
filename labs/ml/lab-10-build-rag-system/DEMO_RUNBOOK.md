# Demo Runbook: Build a RAG System

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

## Part 1: Start the RAG Stack

### Build and start all services

```bash {"name": "start-lab"}
docker compose up --build -d
```

### Wait for services to be healthy

```bash {"name": "wait-healthy"}
echo "Waiting for services..."
sleep 15
docker compose ps
```

### Pull the LLM model

```bash {"name": "pull-model"}
echo "Pulling tinyllama model (this takes a minute on first run)..."
docker exec ml-lab-10-ollama ollama pull tinyllama
echo "✓ Model ready"
```

### Show service URLs

```bash {"name": "show-urls"}
echo "Services are ready:"
echo ""
echo "  RAG API:    http://localhost:8000/docs"
echo "  ChromaDB:   http://localhost:8001"
echo "  Grafana:    http://localhost:3001  (admin/admin)"
echo "  Prometheus: http://localhost:9090"
echo "  Ollama:     http://localhost:11434"
```

---

## Part 2: Verify Document Ingestion

### Check that documents were ingested

```bash {"name": "check-ingestion"}
echo "=== Ingestion Logs ==="
docker compose logs ingestion

echo ""
echo "=== Collection Stats ==="
curl -s http://localhost:8000/collection/stats | python3 -m json.tool
```

---

## Part 3: Query the RAG System

### Ask about the CAP theorem

```bash {"name": "query-cap"}
echo "=== Query: What is the CAP theorem? ==="
curl -s http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the CAP theorem?", "n_results": 3}' | python3 -m json.tool
```

### Ask about gradient descent

```bash {"name": "query-gradient"}
echo "=== Query: How does gradient descent work? ==="
curl -s http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "How does gradient descent work?", "n_results": 3}' | python3 -m json.tool
```

### Ask about Python decorators

```bash {"name": "query-python"}
echo "=== Query: What are Python decorators? ==="
curl -s http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What are Python decorators?", "n_results": 3}' | python3 -m json.tool
```

### Ask something NOT in the documents

```bash {"name": "query-unknown"}
echo "=== Query: What is the weather like today? ==="
curl -s http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the weather like today?", "n_results": 3}' | python3 -m json.tool
```

---

## Part 4: Explore Chunking Impact

### Check current collection size

```bash {"name": "collection-stats"}
curl -s http://localhost:8000/collection/stats | python3 -m json.tool
```

### Ingest a document with small chunks (100 words)

```bash {"name": "ingest-small-chunks"}
TEXT=$(cat data/documents/distributed_systems.md)
curl -s http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d "{\"text\": $(echo "$TEXT" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))'), \"metadata\": {\"source\": \"distributed_systems_small\"}, \"chunk_size\": 100, \"chunk_overlap\": 15}" | python3 -m json.tool
```

### Ingest with large chunks (1000 words)

```bash {"name": "ingest-large-chunks"}
TEXT=$(cat data/documents/distributed_systems.md)
curl -s http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d "{\"text\": $(echo "$TEXT" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))'), \"metadata\": {\"source\": \"distributed_systems_large\"}, \"chunk_size\": 1000, \"chunk_overlap\": 100}" | python3 -m json.tool
```

---

## Part 5: Check Metrics

### View RAG metrics

```bash {"name": "view-metrics"}
curl -s http://localhost:8000/metrics | grep -E "^rag_" | head -30
```

### Open Grafana dashboard

```bash {"name": "open-grafana"}
echo "Open Grafana in your browser:"
echo ""
echo "  http://localhost:3001/d/ml-lab-10-rag"
echo ""
echo "Login: admin / admin"
```

---

## Part 6: Run the Notebook

### Open the exploration notebook

```bash {"name": "open-notebook"}
echo "Open the notebook in VS Code or Jupyter:"
echo ""
echo "  notebooks/rag_exploration.ipynb"
echo ""
echo "The notebook covers:"
echo "  1. Querying the RAG system"
echo "  2. Chunking strategy experiments"
echo "  3. Retrieval quality evaluation"
echo "  4. Breaking the system (adversarial tests)"
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

### Check all container logs

```bash {"name": "logs-all"}
docker compose logs --tail=30
```

### Check RAG API logs

```bash {"name": "logs-rag"}
docker compose logs rag-api --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart RAG API

```bash {"name": "restart-rag"}
docker compose restart rag-api
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Pull model | `docker exec ml-lab-10-ollama ollama pull tinyllama` |
| Query RAG | `curl -s http://localhost:8000/query -H "Content-Type: application/json" -d '{"question": "..."}' \| python3 -m json.tool` |
| View logs | `docker compose logs rag-api` |
| RAG API docs | http://localhost:8000/docs |
| Grafana | http://localhost:3001 |
| Collection stats | `curl -s http://localhost:8000/collection/stats` |
| Notebook | `notebooks/rag_exploration.ipynb` |
