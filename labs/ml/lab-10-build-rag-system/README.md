# ML Lab 10: Build a RAG System

Everyone is building RAG systems, but most people treat them as black boxes. In this lab, you'll build a complete Retrieval-Augmented Generation pipeline from scratch: ingest documents into a vector database, retrieve relevant chunks for a query, and feed them to an LLM for grounded answers. You'll see exactly where the magic happens and where it breaks down.

## What You'll Learn

- How document chunking and embedding power vector search
- How retrieval quality depends on chunk size, overlap, and embedding models
- How to connect a vector database (ChromaDB) to an LLM (Ollama) for grounded generation
- How to measure retrieval precision and identify failure modes
- Why chunk size is the single most impactful parameter in a RAG system

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Documents   │────▶│  Chunking &  │────▶│   ChromaDB   │     │   Ollama     │
│  (.md files) │     │  Ingestion   │     │  (Vectors)   │     │  (LLM)       │
└──────────────┘     └──────────────┘     └──────┬───────┘     └──────┬───────┘
                                                  │                    │
                                                  ▼                    ▼
                                           ┌──────────────────────────────────┐
                                           │          RAG API                 │
                                           │  /ingest  /query  /metrics      │
                                           └──────────────────────────────────┘
                                                  │
                                    ┌─────────────┼─────────────┐
                                    ▼             ▼             ▼
                              ┌──────────┐ ┌──────────┐ ┌──────────┐
                              │Prometheus│ │ Grafana  │ │ Notebook │
                              └──────────┘ └──────────┘ └──────────┘
```

## Prerequisites

- Docker and Docker Compose
- At least 4GB of free RAM (Ollama needs memory for the LLM)

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

This starts Ollama (LLM runtime), ChromaDB (vector DB), the RAG API, and monitoring. The ingestion service will automatically load sample documents.

### 2. Pull the LLM Model

```bash
docker exec ml-lab-10-ollama ollama pull tinyllama
```

### 3. Open the Services

| Service | URL | Purpose |
|---------|-----|---------|
| RAG API | http://localhost:8000/docs | Interactive API documentation |
| ChromaDB | http://localhost:8001 | Vector database |
| Grafana | http://localhost:3001 | Monitoring dashboards (admin/admin) |
| Prometheus | http://localhost:9090 | Metrics |
| Ollama | http://localhost:11434 | LLM runtime |

### 4. Try a Query

```bash
curl -s http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the CAP theorem?"}' | python3 -m json.tool
```

### 5. Open the Notebook

Open `notebooks/rag_exploration.ipynb` in Jupyter or VS Code to work through the exercises.

## Lab Exercises

### Exercise 1: Query the RAG System

Send various questions to the RAG API and examine the responses. Compare the retrieved chunks to the generated answers. Test questions that are well-covered by the documents versus questions the knowledge base cannot answer.

**You'll answer:** How does retrieval quality affect generation quality? What happens when the retrieved chunks are irrelevant?

### Exercise 2: Chunking Strategies

Re-ingest the same documents with different chunk sizes (100, 200, 500, 1000 words) and query the same questions each time. Compare how chunk size affects which text gets retrieved and how well the LLM can use it.

**You'll answer:** What chunk size gives the best retrieval? Why do very small and very large chunks both cause problems?

### Exercise 3: Evaluate Retrieval Quality

Create a set of golden question-answer pairs where you know exactly which document chunk contains the answer. For each question, check whether the correct chunk appears in the top-3 retrieved results. Calculate retrieval precision@3.

**You'll answer:** What is your system's retrieval precision? Which types of questions does it fail on?

### Exercise 4: Breaking the System

Stress-test the RAG system with adversarial inputs: questions not covered by the documents, ambiguous questions, questions where multiple chunks might contradict each other, and very long or very short queries. Document how the system handles each failure mode.

**You'll answer:** Does the system admit when it doesn't know? How does it handle conflicting information?

## Key Takeaways

1. **Chunk size is the most impactful RAG parameter** -- too small loses context, too large dilutes relevance
2. **Retrieval quality bounds generation quality** -- if the wrong chunks are retrieved, no LLM can fix it
3. **Vector search is approximate, not exact** -- similarity scores are not probabilities and need calibration
4. **RAG systems fail silently** -- they hallucinate confidently when retrieval misses, so always measure retrieval precision

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Ollama model not found

Make sure to pull the model after starting the services:

```bash
docker exec ml-lab-10-ollama ollama pull tinyllama
```

### ChromaDB connection refused

Check that ChromaDB is healthy:

```bash
docker compose logs chromadb
curl http://localhost:8001/api/v1/heartbeat
```

### RAG API not responding

```bash
docker compose logs rag-api
```

### Generation is slow

The first query after pulling a model is slow because Ollama loads the model into memory. Subsequent queries are faster. You can also use a smaller model if needed.

### Port conflicts

```bash
lsof -i :8000 -i :8001 -i :11434
```

Kill conflicting processes or change ports in `docker-compose.yml`.

## What's Next

[ML Lab 11: Design an AI Agent](../lab-11-design-ai-agent/) -- Build on this RAG system by creating an AI agent with tool use, memory, and guardrails.
