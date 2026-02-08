from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
import chromadb
import requests
import time
import os
import logging
import hashlib

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="RAG API", version="1.0.0")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
CHROMA_URL = os.getenv("CHROMA_URL", "http://chromadb:8000")
COLLECTION_NAME = "documents"

# -----------------------------------------------
# Prometheus Metrics
# -----------------------------------------------
QUERY_COUNT = Counter('rag_queries_total', 'Total RAG queries')
INGEST_COUNT = Counter('rag_ingestions_total', 'Total document ingestions')
RETRIEVAL_LATENCY = Histogram(
    'rag_retrieval_seconds', 'Retrieval latency',
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2]
)
GENERATION_LATENCY = Histogram(
    'rag_generation_seconds', 'LLM generation latency',
    buckets=[0.5, 1, 2, 5, 10, 30]
)
TOTAL_LATENCY = Histogram(
    'rag_total_seconds', 'Total query latency',
    buckets=[0.5, 1, 2, 5, 10, 30, 60]
)
RETRIEVAL_SCORE = Histogram(
    'rag_retrieval_score', 'Retrieval similarity scores',
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
)
CHUNKS_RETRIEVED = Histogram(
    'rag_chunks_retrieved', 'Number of chunks retrieved',
    buckets=[1, 2, 3, 5, 10]
)

# -----------------------------------------------
# ChromaDB Connection
# -----------------------------------------------
chroma_client = None
collection = None


def get_collection():
    global chroma_client, collection
    if chroma_client is None:
        chroma_client = chromadb.HttpClient(
            host=CHROMA_URL.replace("http://", "").split(":")[0],
            port=int(CHROMA_URL.split(":")[-1])
        )
    if collection is None:
        try:
            collection = chroma_client.get_or_create_collection(name=COLLECTION_NAME)
        except Exception as e:
            logger.error(f"Failed to get collection: {e}")
            raise
    return collection


# -----------------------------------------------
# Request / Response Models
# -----------------------------------------------
class IngestRequest(BaseModel):
    text: str
    metadata: dict = {}
    chunk_size: int = 500
    chunk_overlap: int = 50


class QueryRequest(BaseModel):
    question: str
    n_results: int = 3
    model: str = "tinyllama"


class QueryResponse(BaseModel):
    answer: str
    sources: list
    retrieval_time_ms: float
    generation_time_ms: float
    total_time_ms: float


# -----------------------------------------------
# Helpers
# -----------------------------------------------
def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks by word count."""
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        start = end - overlap
    return chunks


# -----------------------------------------------
# Endpoints
# -----------------------------------------------
@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/ingest")
def ingest(req: IngestRequest):
    """Ingest a document by chunking and storing in the vector DB."""
    INGEST_COUNT.inc()
    coll = get_collection()
    chunks = chunk_text(req.text, req.chunk_size, req.chunk_overlap)

    ids = []
    for i, chunk in enumerate(chunks):
        doc_id = hashlib.md5(f"{chunk[:100]}_{i}".encode()).hexdigest()
        ids.append(doc_id)

    coll.add(
        documents=chunks,
        ids=ids,
        metadatas=[{**req.metadata, "chunk_index": i} for i in range(len(chunks))]
    )

    logger.info(f"Ingested {len(chunks)} chunks from document (metadata={req.metadata})")
    return {"status": "ingested", "chunks": len(chunks)}


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    """Query the RAG system: retrieve relevant chunks then generate an answer."""
    QUERY_COUNT.inc()
    total_start = time.time()

    # --- Retrieve ---
    retrieval_start = time.time()
    coll = get_collection()
    results = coll.query(query_texts=[req.question], n_results=req.n_results)
    retrieval_time = time.time() - retrieval_start
    RETRIEVAL_LATENCY.observe(retrieval_time)
    CHUNKS_RETRIEVED.observe(len(results["documents"][0]) if results["documents"] else 0)

    # Log retrieval scores
    if results.get("distances"):
        for dist in results["distances"][0]:
            score = max(0.0, 1 - dist)  # Convert distance to similarity
            RETRIEVAL_SCORE.observe(score)

    # --- Build context ---
    context_chunks = results["documents"][0] if results["documents"] else []
    context = "\n\n---\n\n".join(context_chunks)

    # --- Generate ---
    generation_start = time.time()
    prompt = f"""Based on the following context, answer the question. If the context doesn't contain enough information, say so.

Context:
{context}

Question: {req.question}

Answer:"""

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": req.model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 200}
            },
            timeout=60
        )
        answer = resp.json().get("response", "Failed to generate response")
    except Exception as e:
        answer = f"Generation failed: {e}"

    generation_time = time.time() - generation_start
    GENERATION_LATENCY.observe(generation_time)

    total_time = time.time() - total_start
    TOTAL_LATENCY.observe(total_time)

    # --- Build sources list ---
    sources = []
    for i, (doc, meta) in enumerate(zip(
        results["documents"][0] if results["documents"] else [],
        results["metadatas"][0] if results["metadatas"] else []
    )):
        sources.append({"chunk": doc[:200] + "...", "metadata": meta})

    return QueryResponse(
        answer=answer,
        sources=sources,
        retrieval_time_ms=retrieval_time * 1000,
        generation_time_ms=generation_time * 1000,
        total_time_ms=total_time * 1000
    )


@app.get("/collection/stats")
def collection_stats():
    """Return stats about the document collection."""
    coll = get_collection()
    return {"count": coll.count(), "name": COLLECTION_NAME}


@app.get("/metrics")
def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
