"""
Search Service - Maintains an in-memory search index built from CDC events.
Demonstrates the "read model" pattern in event-sourced/CQRS architectures.
"""
import asyncio
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "search-service")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(SERVICE_NAME)

# OpenTelemetry setup
resource = Resource.create({"service.name": SERVICE_NAME})
provider = TracerProvider(resource=resource)
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# Prometheus metrics
INDEX_SIZE = Gauge(
    "search_index_size",
    "Number of documents in the search index",
    ["service"]
)
INDEX_UPDATES = Counter(
    "search_index_updates_total",
    "Total index update operations",
    ["service", "operation"]
)
SEARCH_REQUESTS = Counter(
    "search_requests_total",
    "Total search requests",
    ["service"]
)
SEARCH_LATENCY = Histogram(
    "search_request_duration_seconds",
    "Search request latency",
    ["service"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5]
)
LAST_UPDATE_TIME = Gauge(
    "search_last_update_timestamp",
    "Timestamp of last index update",
    ["service"]
)

# In-memory search index
# In production, you would use Elasticsearch, Meilisearch, or similar
search_index: Dict[int, Dict] = {}
update_log: List[Dict] = []
MAX_UPDATE_LOG = 1000


class ProductIndex(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    price: float
    category: Optional[str] = None
    stock_quantity: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info("Search index initialized (empty - will be populated via CDC)")

    # Start metrics updater
    asyncio.create_task(update_metrics_loop())

    yield

    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


async def update_metrics_loop():
    """Periodically update gauge metrics."""
    while True:
        INDEX_SIZE.labels(service=SERVICE_NAME).set(len(search_index))
        await asyncio.sleep(5)


def tokenize(text: str) -> List[str]:
    """Simple tokenization for full-text search."""
    if not text:
        return []
    # Lowercase and split on non-alphanumeric
    return re.findall(r'\w+', text.lower())


def score_match(doc: Dict, query_tokens: List[str]) -> float:
    """Calculate a simple relevance score."""
    score = 0.0

    # Tokenize document fields
    name_tokens = tokenize(doc.get("name", ""))
    desc_tokens = tokenize(doc.get("description", ""))
    category_tokens = tokenize(doc.get("category", ""))

    for token in query_tokens:
        # Name matches score higher
        if token in name_tokens:
            score += 3.0
        # Description matches
        if token in desc_tokens:
            score += 1.0
        # Category matches
        if token in category_tokens:
            score += 2.0

    return score


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "index_size": len(search_index)
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ===========================================
# Index Management Endpoints (called by CDC consumer)
# ===========================================

@app.post("/index")
async def index_document(doc: dict):
    """Add or update a document in the search index."""
    global update_log

    with tracer.start_as_current_span("index_document") as span:
        doc_id = doc.get("id")
        if doc_id is None:
            raise HTTPException(status_code=400, detail="Document must have an 'id' field")

        span.set_attribute("document.id", doc_id)

        # Determine if this is create or update
        operation = "update" if doc_id in search_index else "create"
        span.set_attribute("operation", operation)

        # Store in index
        search_index[doc_id] = {
            "id": doc_id,
            "name": doc.get("name", ""),
            "description": doc.get("description"),
            "price": float(doc.get("price", 0)) if doc.get("price") else 0,
            "category": doc.get("category"),
            "stock_quantity": doc.get("stock_quantity", 0),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
            "indexed_at": datetime.now().isoformat()
        }

        # Log the update
        update_log.append({
            "operation": operation,
            "doc_id": doc_id,
            "timestamp": datetime.now().isoformat()
        })
        if len(update_log) > MAX_UPDATE_LOG:
            update_log = update_log[-MAX_UPDATE_LOG:]

        # Update metrics
        INDEX_UPDATES.labels(service=SERVICE_NAME, operation=operation).inc()
        LAST_UPDATE_TIME.labels(service=SERVICE_NAME).set(time.time())

        logger.info(f"Indexed document id={doc_id} operation={operation}")

        return {
            "status": "indexed",
            "id": doc_id,
            "operation": operation
        }


@app.delete("/index/{doc_id}")
async def delete_document(doc_id: int):
    """Remove a document from the search index."""
    global update_log

    with tracer.start_as_current_span("delete_document") as span:
        span.set_attribute("document.id", doc_id)

        if doc_id not in search_index:
            raise HTTPException(status_code=404, detail="Document not found")

        del search_index[doc_id]

        # Log the delete
        update_log.append({
            "operation": "delete",
            "doc_id": doc_id,
            "timestamp": datetime.now().isoformat()
        })
        if len(update_log) > MAX_UPDATE_LOG:
            update_log = update_log[-MAX_UPDATE_LOG:]

        INDEX_UPDATES.labels(service=SERVICE_NAME, operation="delete").inc()
        LAST_UPDATE_TIME.labels(service=SERVICE_NAME).set(time.time())

        logger.info(f"Deleted document id={doc_id}")

        return {"status": "deleted", "id": doc_id}


# ===========================================
# Search Endpoints
# ===========================================

@app.get("/search")
async def search(
    q: str = Query(..., description="Search query"),
    category: Optional[str] = Query(None, description="Filter by category"),
    min_price: Optional[float] = Query(None, description="Minimum price"),
    max_price: Optional[float] = Query(None, description="Maximum price"),
    in_stock: Optional[bool] = Query(None, description="Only show in-stock items"),
    limit: int = Query(20, description="Maximum results to return")
):
    """Full-text search across the product index."""
    start_time = time.time()

    with tracer.start_as_current_span("search") as span:
        span.set_attribute("search.query", q)
        span.set_attribute("search.limit", limit)

        query_tokens = tokenize(q)
        results = []

        for doc_id, doc in search_index.items():
            # Apply filters
            if category and doc.get("category", "").lower() != category.lower():
                continue
            if min_price is not None and doc.get("price", 0) < min_price:
                continue
            if max_price is not None and doc.get("price", 0) > max_price:
                continue
            if in_stock and doc.get("stock_quantity", 0) <= 0:
                continue

            # Calculate score
            score = score_match(doc, query_tokens)
            if score > 0:
                results.append({
                    **doc,
                    "_score": score
                })

        # Sort by score descending
        results.sort(key=lambda x: x["_score"], reverse=True)
        results = results[:limit]

        duration = time.time() - start_time
        SEARCH_REQUESTS.labels(service=SERVICE_NAME).inc()
        SEARCH_LATENCY.labels(service=SERVICE_NAME).observe(duration)

        span.set_attribute("search.results_count", len(results))
        span.set_attribute("search.duration_ms", duration * 1000)

        return {
            "query": q,
            "results": results,
            "total": len(results),
            "took_ms": round(duration * 1000, 2)
        }


@app.get("/products")
async def list_all_products(
    category: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
):
    """List all products in the search index."""
    with tracer.start_as_current_span("list_products") as span:
        products = list(search_index.values())

        # Filter by category if specified
        if category:
            products = [p for p in products if p.get("category", "").lower() == category.lower()]

        total = len(products)
        products = products[offset:offset + limit]

        span.set_attribute("products.total", total)
        span.set_attribute("products.returned", len(products))

        return {
            "products": products,
            "total": total,
            "limit": limit,
            "offset": offset
        }


@app.get("/products/{product_id}")
async def get_product(product_id: int):
    """Get a single product from the search index."""
    if product_id not in search_index:
        raise HTTPException(status_code=404, detail="Product not found in search index")

    return search_index[product_id]


@app.get("/categories")
async def list_categories():
    """List all unique categories in the index."""
    categories = {}
    for doc in search_index.values():
        cat = doc.get("category")
        if cat:
            categories[cat] = categories.get(cat, 0) + 1

    return {
        "categories": [
            {"name": k, "count": v}
            for k, v in sorted(categories.items(), key=lambda x: x[1], reverse=True)
        ]
    }


# ===========================================
# Index Status Endpoints
# ===========================================

@app.get("/index/status")
async def get_index_status():
    """Get the current status of the search index."""
    return {
        "size": len(search_index),
        "recent_updates": update_log[-20:],
        "last_update": update_log[-1] if update_log else None
    }


@app.post("/index/clear")
async def clear_index():
    """Clear the entire search index (for testing)."""
    global search_index, update_log

    count = len(search_index)
    search_index = {}
    update_log = []

    INDEX_UPDATES.labels(service=SERVICE_NAME, operation="clear").inc()

    logger.warning(f"Cleared search index ({count} documents removed)")

    return {"status": "cleared", "documents_removed": count}


@app.get("/index/compare")
async def compare_with_source():
    """
    Compare search index with source database.
    This endpoint is for debugging - shows what's in the index.
    """
    return {
        "index_size": len(search_index),
        "document_ids": list(search_index.keys()),
        "note": "Compare these IDs with the products in PostgreSQL to verify CDC sync"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
