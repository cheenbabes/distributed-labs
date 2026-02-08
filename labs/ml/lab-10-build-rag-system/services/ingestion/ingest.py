"""Ingest sample documents into the RAG system."""
import requests
import os
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RAG_API_URL = os.getenv("RAG_API_URL", "http://rag-api:8000")
DOCS_DIR = "/data/documents"


def wait_for_api():
    """Wait for the RAG API to become available."""
    for i in range(30):
        try:
            resp = requests.get(f"{RAG_API_URL}/health", timeout=2)
            if resp.status_code == 200:
                logger.info("RAG API is ready")
                return True
        except Exception:
            pass
        logger.info(f"Waiting for RAG API... ({i + 1}/30)")
        time.sleep(2)
    return False


def ingest_file(filepath):
    """Read a file and send it to the RAG API for ingestion."""
    with open(filepath) as f:
        text = f.read()

    filename = os.path.basename(filepath)
    resp = requests.post(f"{RAG_API_URL}/ingest", json={
        "text": text,
        "metadata": {"source": filename},
        "chunk_size": 200,
        "chunk_overlap": 30
    })
    return resp.json()


def main():
    logger.info("Starting document ingestion pipeline")

    logger.info("Waiting for RAG API...")
    if not wait_for_api():
        logger.error("RAG API not available after 60 seconds — aborting")
        return

    logger.info(f"Ingesting documents from {DOCS_DIR}")
    ingested = 0
    for filename in sorted(os.listdir(DOCS_DIR)):
        if filename.endswith('.md'):
            filepath = os.path.join(DOCS_DIR, filename)
            result = ingest_file(filepath)
            logger.info(f"  {filename}: {result}")
            ingested += 1

    # Check final stats
    stats = requests.get(f"{RAG_API_URL}/collection/stats").json()
    logger.info(f"Ingestion complete: {ingested} files processed")
    logger.info(f"Collection stats: {stats}")


if __name__ == "__main__":
    main()
