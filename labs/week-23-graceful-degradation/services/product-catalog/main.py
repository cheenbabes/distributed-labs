"""
Product Catalog Service - Main entry point demonstrating graceful degradation patterns.

This service aggregates data from multiple dependencies:
- Recommendation service (optional - fallback to popular items)
- Pricing service (optional - fallback to cached prices)
- Inventory service (required - but can degrade to "check in store")

Feature flags control graceful degradation behavior.
"""
import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Query
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "product-catalog")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab23-otel-collector:4317")
RECOMMENDATION_URL = os.getenv("RECOMMENDATION_URL", "http://lab23-recommendation:8001")
PRICING_URL = os.getenv("PRICING_URL", "http://lab23-pricing:8002")
INVENTORY_URL = os.getenv("INVENTORY_URL", "http://lab23-inventory:8003")
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "2.0"))

# Feature flags for graceful degradation
degradation_config = {
    "graceful_degradation_enabled": os.getenv("GRACEFUL_DEGRADATION_ENABLED", "true").lower() == "true",
    "recommendation_fallback_enabled": os.getenv("RECOMMENDATION_FALLBACK_ENABLED", "true").lower() == "true",
    "pricing_fallback_enabled": os.getenv("PRICING_FALLBACK_ENABLED", "true").lower() == "true",
    "inventory_fallback_enabled": os.getenv("INVENTORY_FALLBACK_ENABLED", "true").lower() == "true",
}

# Simulated cached/fallback data
POPULAR_ITEMS = ["SKU-001", "SKU-002", "SKU-003", "SKU-005", "SKU-010"]
CACHED_PRICES = {
    "SKU-001": {"price": 29.99, "currency": "USD", "cached_at": "2024-01-01T00:00:00Z"},
    "SKU-002": {"price": 49.99, "currency": "USD", "cached_at": "2024-01-01T00:00:00Z"},
    "SKU-003": {"price": 19.99, "currency": "USD", "cached_at": "2024-01-01T00:00:00Z"},
    "SKU-005": {"price": 99.99, "currency": "USD", "cached_at": "2024-01-01T00:00:00Z"},
    "SKU-010": {"price": 149.99, "currency": "USD", "cached_at": "2024-01-01T00:00:00Z"},
}

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

# Instrument httpx for trace propagation
HTTPXClientInstrumentor().instrument()

# Prometheus metrics
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["service", "method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

# Graceful degradation specific metrics
FALLBACK_USED = Counter(
    "fallback_used_total",
    "Total times a fallback was used",
    ["service", "dependency", "fallback_type"]
)
DEGRADED_RESPONSES = Counter(
    "degraded_responses_total",
    "Total degraded responses served",
    ["service", "degradation_level"]
)
FULL_RESPONSES = Counter(
    "full_responses_total",
    "Total full (non-degraded) responses served",
    ["service"]
)
DEPENDENCY_STATUS = Gauge(
    "dependency_status",
    "Current status of dependencies (1=healthy, 0=unhealthy)",
    ["service", "dependency"]
)
RESPONSE_QUALITY_SCORE = Histogram(
    "response_quality_score",
    "Quality score of responses (1.0=full, 0.0=all fallbacks)",
    ["service"],
    buckets=[0.0, 0.25, 0.5, 0.75, 1.0]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    logger.info(f"Graceful degradation: {'enabled' if degradation_config['graceful_degradation_enabled'] else 'disabled'}")
    logger.info(f"Dependencies: recommendation={RECOMMENDATION_URL}, pricing={PRICING_URL}, inventory={INVENTORY_URL}")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


class DegradationConfig(BaseModel):
    graceful_degradation_enabled: bool
    recommendation_fallback_enabled: bool = True
    pricing_fallback_enabled: bool = True
    inventory_fallback_enabled: bool = True


class ProductRequest(BaseModel):
    product_id: str = "SKU-001"
    user_id: str = "user-123"


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/admin/config")
async def get_config():
    """Get current degradation configuration."""
    return degradation_config


@app.post("/admin/config")
async def set_config(config: DegradationConfig):
    """Set degradation configuration at runtime."""
    degradation_config["graceful_degradation_enabled"] = config.graceful_degradation_enabled
    degradation_config["recommendation_fallback_enabled"] = config.recommendation_fallback_enabled
    degradation_config["pricing_fallback_enabled"] = config.pricing_fallback_enabled
    degradation_config["inventory_fallback_enabled"] = config.inventory_fallback_enabled
    logger.info(f"Degradation config updated: {degradation_config}")
    return degradation_config


async def fetch_recommendations(client: httpx.AsyncClient, user_id: str, product_id: str) -> dict:
    """Fetch recommendations with fallback to popular items."""
    current_span = trace.get_current_span()

    try:
        response = await client.get(
            f"{RECOMMENDATION_URL}/recommendations",
            params={"user_id": user_id, "product_id": product_id},
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        DEPENDENCY_STATUS.labels(service=SERVICE_NAME, dependency="recommendation").set(1)
        current_span.set_attribute("recommendation.source", "live")
        return {
            "recommendations": data.get("items", []),
            "source": "live",
            "degraded": False
        }
    except Exception as e:
        logger.warning(f"Recommendation service failed: {e}")
        DEPENDENCY_STATUS.labels(service=SERVICE_NAME, dependency="recommendation").set(0)
        current_span.set_attribute("recommendation.source", "fallback")
        current_span.set_attribute("recommendation.error", str(e))

        if degradation_config["graceful_degradation_enabled"] and degradation_config["recommendation_fallback_enabled"]:
            FALLBACK_USED.labels(service=SERVICE_NAME, dependency="recommendation", fallback_type="popular_items").inc()
            return {
                "recommendations": POPULAR_ITEMS[:3],
                "source": "fallback_popular_items",
                "degraded": True,
                "fallback_reason": str(e)
            }
        else:
            raise


async def fetch_pricing(client: httpx.AsyncClient, product_id: str) -> dict:
    """Fetch pricing with fallback to cached prices."""
    current_span = trace.get_current_span()

    try:
        response = await client.get(
            f"{PRICING_URL}/price/{product_id}",
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        DEPENDENCY_STATUS.labels(service=SERVICE_NAME, dependency="pricing").set(1)
        current_span.set_attribute("pricing.source", "live")
        return {
            "price": data.get("price"),
            "currency": data.get("currency", "USD"),
            "source": "live",
            "degraded": False
        }
    except Exception as e:
        logger.warning(f"Pricing service failed: {e}")
        DEPENDENCY_STATUS.labels(service=SERVICE_NAME, dependency="pricing").set(0)
        current_span.set_attribute("pricing.source", "fallback")
        current_span.set_attribute("pricing.error", str(e))

        if degradation_config["graceful_degradation_enabled"] and degradation_config["pricing_fallback_enabled"]:
            cached = CACHED_PRICES.get(product_id, {"price": 0, "currency": "USD", "cached_at": "unknown"})
            FALLBACK_USED.labels(service=SERVICE_NAME, dependency="pricing", fallback_type="cached_price").inc()
            return {
                "price": cached["price"],
                "currency": cached["currency"],
                "source": "fallback_cached",
                "cached_at": cached.get("cached_at"),
                "degraded": True,
                "fallback_reason": str(e)
            }
        else:
            raise


async def fetch_inventory(client: httpx.AsyncClient, product_id: str) -> dict:
    """Fetch inventory with fallback to 'check in store' message."""
    current_span = trace.get_current_span()

    try:
        response = await client.get(
            f"{INVENTORY_URL}/stock/{product_id}",
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        DEPENDENCY_STATUS.labels(service=SERVICE_NAME, dependency="inventory").set(1)
        current_span.set_attribute("inventory.source", "live")
        return {
            "in_stock": data.get("in_stock", False),
            "quantity": data.get("quantity", 0),
            "source": "live",
            "degraded": False
        }
    except Exception as e:
        logger.warning(f"Inventory service failed: {e}")
        DEPENDENCY_STATUS.labels(service=SERVICE_NAME, dependency="inventory").set(0)
        current_span.set_attribute("inventory.source", "fallback")
        current_span.set_attribute("inventory.error", str(e))

        if degradation_config["graceful_degradation_enabled"] and degradation_config["inventory_fallback_enabled"]:
            FALLBACK_USED.labels(service=SERVICE_NAME, dependency="inventory", fallback_type="check_in_store").inc()
            return {
                "in_stock": None,  # Unknown
                "quantity": None,
                "availability_message": "Check availability in store",
                "source": "fallback_check_store",
                "degraded": True,
                "fallback_reason": str(e)
            }
        else:
            raise


@app.get("/product/{product_id}")
async def get_product(
    request: Request,
    product_id: str,
    user_id: str = Query(default="user-123")
):
    """
    Get product details with graceful degradation.

    Aggregates data from recommendation, pricing, and inventory services.
    When dependencies fail, gracefully degrades to fallback data if enabled.
    """
    start_time = time.time()

    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "032x")

    current_span.set_attribute("product_id", product_id)
    current_span.set_attribute("user_id", user_id)
    current_span.set_attribute("graceful_degradation_enabled", degradation_config["graceful_degradation_enabled"])

    # Simulate some base processing time
    await asyncio.sleep(random.uniform(0.005, 0.015))

    async with httpx.AsyncClient() as client:
        # Fetch from all dependencies concurrently
        results = await asyncio.gather(
            fetch_recommendations(client, user_id, product_id),
            fetch_pricing(client, product_id),
            fetch_inventory(client, product_id),
            return_exceptions=True
        )

    recommendation_result, pricing_result, inventory_result = results

    # Handle any exceptions that weren't caught by fallbacks
    errors = []
    if isinstance(recommendation_result, Exception):
        errors.append(f"recommendations: {recommendation_result}")
        recommendation_result = None
    if isinstance(pricing_result, Exception):
        errors.append(f"pricing: {pricing_result}")
        pricing_result = None
    if isinstance(inventory_result, Exception):
        errors.append(f"inventory: {inventory_result}")
        inventory_result = None

    # Calculate degradation level and quality score
    degraded_components = []
    if recommendation_result and recommendation_result.get("degraded"):
        degraded_components.append("recommendations")
    if pricing_result and pricing_result.get("degraded"):
        degraded_components.append("pricing")
    if inventory_result and inventory_result.get("degraded"):
        degraded_components.append("inventory")

    total_components = 3
    live_components = total_components - len(degraded_components) - len(errors)
    quality_score = live_components / total_components

    # Record metrics
    RESPONSE_QUALITY_SCORE.labels(service=SERVICE_NAME).observe(quality_score)

    if len(degraded_components) == 0 and len(errors) == 0:
        FULL_RESPONSES.labels(service=SERVICE_NAME).inc()
        degradation_level = "none"
    elif len(degraded_components) + len(errors) == total_components:
        degradation_level = "full"
    else:
        degradation_level = "partial"

    if degradation_level != "none":
        DEGRADED_RESPONSES.labels(service=SERVICE_NAME, degradation_level=degradation_level).inc()

    current_span.set_attribute("degradation_level", degradation_level)
    current_span.set_attribute("quality_score", quality_score)
    current_span.set_attribute("degraded_components", ",".join(degraded_components))

    duration = time.time() - start_time

    # Record general metrics
    REQUEST_COUNT.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/product",
        status="200"
    ).inc()
    REQUEST_LATENCY.labels(
        service=SERVICE_NAME,
        method="GET",
        endpoint="/product"
    ).observe(duration)

    logger.info(f"Product request completed trace_id={trace_id} product_id={product_id} quality={quality_score:.2f} degraded={degraded_components}")

    return {
        "service": SERVICE_NAME,
        "product_id": product_id,
        "user_id": user_id,
        "trace_id": trace_id,
        "duration_ms": round(duration * 1000, 2),
        "quality": {
            "score": quality_score,
            "degradation_level": degradation_level,
            "degraded_components": degraded_components,
            "errors": errors
        },
        "data": {
            "recommendations": recommendation_result,
            "pricing": pricing_result,
            "inventory": inventory_result
        },
        "config": {
            "graceful_degradation_enabled": degradation_config["graceful_degradation_enabled"]
        }
    }


@app.get("/api/catalog")
async def get_catalog(request: Request):
    """Simplified endpoint for load testing - returns multiple products."""
    products = ["SKU-001", "SKU-002", "SKU-003"]
    results = []

    for product_id in products:
        result = await get_product(request, product_id, "user-loadtest")
        results.append(result)

    return {
        "service": SERVICE_NAME,
        "products": results
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
