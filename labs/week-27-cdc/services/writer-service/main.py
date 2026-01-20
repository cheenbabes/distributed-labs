"""
Writer Service - Writes products and orders to PostgreSQL.
Changes are captured by Debezium and streamed to Kafka.
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Optional

import asyncpg
from fastapi import FastAPI, HTTPException
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "writer-service")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://cdc_user:cdc_password@postgres:5432/products_db")

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
WRITE_COUNT = Counter(
    "db_writes_total",
    "Total database write operations",
    ["service", "operation", "table"]
)
WRITE_LATENCY = Histogram(
    "db_write_duration_seconds",
    "Database write latency",
    ["service", "operation", "table"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
PRODUCTS_GAUGE = Gauge(
    "products_total",
    "Total number of products in database",
    ["service"]
)
ORDERS_GAUGE = Gauge(
    "orders_total",
    "Total number of orders in database",
    ["service"]
)

# Database connection pool
db_pool: Optional[asyncpg.Pool] = None


class ProductCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: float
    category: Optional[str] = None
    stock_quantity: int = 0


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    category: Optional[str] = None
    stock_quantity: Optional[int] = None


class OrderCreate(BaseModel):
    product_id: int
    customer_email: str
    quantity: int


class OrderStatusUpdate(BaseModel):
    status: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    logger.info(f"{SERVICE_NAME} starting up")

    # Create database connection pool
    retry_count = 0
    max_retries = 30
    while retry_count < max_retries:
        try:
            db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=5, max_size=20)
            logger.info("Database connection pool created")
            break
        except Exception as e:
            retry_count += 1
            logger.warning(f"Failed to connect to database (attempt {retry_count}/{max_retries}): {e}")
            await asyncio.sleep(2)

    if db_pool is None:
        raise Exception("Failed to connect to database after maximum retries")

    # Start metrics updater
    asyncio.create_task(update_metrics_loop())

    yield

    # Cleanup
    if db_pool:
        await db_pool.close()
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


async def update_metrics_loop():
    """Periodically update gauge metrics."""
    while True:
        try:
            if db_pool:
                async with db_pool.acquire() as conn:
                    product_count = await conn.fetchval("SELECT COUNT(*) FROM products")
                    order_count = await conn.fetchval("SELECT COUNT(*) FROM orders")
                    PRODUCTS_GAUGE.labels(service=SERVICE_NAME).set(product_count)
                    ORDERS_GAUGE.labels(service=SERVICE_NAME).set(order_count)
        except Exception as e:
            logger.error(f"Error updating metrics: {e}")
        await asyncio.sleep(5)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ===========================================
# Product Endpoints
# ===========================================

@app.post("/products")
async def create_product(product: ProductCreate):
    """Create a new product - this change will be captured by CDC."""
    start_time = time.time()

    with tracer.start_as_current_span("create_product") as span:
        span.set_attribute("product.name", product.name)
        span.set_attribute("product.price", product.price)

        async with db_pool.acquire() as conn:
            result = await conn.fetchrow(
                """
                INSERT INTO products (name, description, price, category, stock_quantity)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id, name, description, price, category, stock_quantity, created_at, updated_at
                """,
                product.name,
                product.description,
                Decimal(str(product.price)),
                product.category,
                product.stock_quantity
            )

        duration = time.time() - start_time
        WRITE_COUNT.labels(service=SERVICE_NAME, operation="insert", table="products").inc()
        WRITE_LATENCY.labels(service=SERVICE_NAME, operation="insert", table="products").observe(duration)

        span.set_attribute("product.id", result["id"])
        logger.info(f"Created product id={result['id']} name={product.name}")

        return {
            "id": result["id"],
            "name": result["name"],
            "description": result["description"],
            "price": float(result["price"]),
            "category": result["category"],
            "stock_quantity": result["stock_quantity"],
            "created_at": result["created_at"].isoformat(),
            "updated_at": result["updated_at"].isoformat(),
            "cdc_note": "This change will appear in Kafka via Debezium CDC"
        }


@app.get("/products")
async def list_products(limit: int = 100, offset: int = 0):
    """List all products."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM products ORDER BY id LIMIT $1 OFFSET $2",
            limit, offset
        )
        total = await conn.fetchval("SELECT COUNT(*) FROM products")

    return {
        "products": [
            {
                "id": r["id"],
                "name": r["name"],
                "description": r["description"],
                "price": float(r["price"]),
                "category": r["category"],
                "stock_quantity": r["stock_quantity"],
                "created_at": r["created_at"].isoformat(),
                "updated_at": r["updated_at"].isoformat()
            }
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset
    }


@app.get("/products/{product_id}")
async def get_product(product_id: int):
    """Get a single product by ID."""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM products WHERE id = $1", product_id)

    if not row:
        raise HTTPException(status_code=404, detail="Product not found")

    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "price": float(row["price"]),
        "category": row["category"],
        "stock_quantity": row["stock_quantity"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat()
    }


@app.put("/products/{product_id}")
async def update_product(product_id: int, product: ProductUpdate):
    """Update a product - this change will be captured by CDC."""
    start_time = time.time()

    with tracer.start_as_current_span("update_product") as span:
        span.set_attribute("product.id", product_id)

        # Build dynamic update query
        updates = []
        values = []
        param_count = 1

        if product.name is not None:
            updates.append(f"name = ${param_count}")
            values.append(product.name)
            param_count += 1
        if product.description is not None:
            updates.append(f"description = ${param_count}")
            values.append(product.description)
            param_count += 1
        if product.price is not None:
            updates.append(f"price = ${param_count}")
            values.append(Decimal(str(product.price)))
            param_count += 1
        if product.category is not None:
            updates.append(f"category = ${param_count}")
            values.append(product.category)
            param_count += 1
        if product.stock_quantity is not None:
            updates.append(f"stock_quantity = ${param_count}")
            values.append(product.stock_quantity)
            param_count += 1

        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        values.append(product_id)
        query = f"""
            UPDATE products
            SET {', '.join(updates)}
            WHERE id = ${param_count}
            RETURNING id, name, description, price, category, stock_quantity, created_at, updated_at
        """

        async with db_pool.acquire() as conn:
            result = await conn.fetchrow(query, *values)

        if not result:
            raise HTTPException(status_code=404, detail="Product not found")

        duration = time.time() - start_time
        WRITE_COUNT.labels(service=SERVICE_NAME, operation="update", table="products").inc()
        WRITE_LATENCY.labels(service=SERVICE_NAME, operation="update", table="products").observe(duration)

        logger.info(f"Updated product id={product_id}")

        return {
            "id": result["id"],
            "name": result["name"],
            "description": result["description"],
            "price": float(result["price"]),
            "category": result["category"],
            "stock_quantity": result["stock_quantity"],
            "created_at": result["created_at"].isoformat(),
            "updated_at": result["updated_at"].isoformat(),
            "cdc_note": "This UPDATE will appear in Kafka via Debezium CDC"
        }


@app.delete("/products/{product_id}")
async def delete_product(product_id: int):
    """Delete a product - this change will be captured by CDC."""
    start_time = time.time()

    with tracer.start_as_current_span("delete_product") as span:
        span.set_attribute("product.id", product_id)

        async with db_pool.acquire() as conn:
            # Check for existing orders
            order_count = await conn.fetchval(
                "SELECT COUNT(*) FROM orders WHERE product_id = $1",
                product_id
            )
            if order_count > 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot delete product with {order_count} existing orders"
                )

            result = await conn.execute(
                "DELETE FROM products WHERE id = $1",
                product_id
            )

        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="Product not found")

        duration = time.time() - start_time
        WRITE_COUNT.labels(service=SERVICE_NAME, operation="delete", table="products").inc()
        WRITE_LATENCY.labels(service=SERVICE_NAME, operation="delete", table="products").observe(duration)

        logger.info(f"Deleted product id={product_id}")

        return {
            "deleted": True,
            "product_id": product_id,
            "cdc_note": "This DELETE will appear in Kafka via Debezium CDC"
        }


# ===========================================
# Order Endpoints
# ===========================================

@app.post("/orders")
async def create_order(order: OrderCreate):
    """Create a new order - this change will be captured by CDC."""
    start_time = time.time()

    with tracer.start_as_current_span("create_order") as span:
        span.set_attribute("order.product_id", order.product_id)
        span.set_attribute("order.quantity", order.quantity)

        async with db_pool.acquire() as conn:
            # Get product price
            product = await conn.fetchrow(
                "SELECT price, stock_quantity FROM products WHERE id = $1",
                order.product_id
            )

            if not product:
                raise HTTPException(status_code=404, detail="Product not found")

            if product["stock_quantity"] < order.quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient stock. Available: {product['stock_quantity']}"
                )

            total_amount = product["price"] * order.quantity

            # Create order and update stock in transaction
            async with conn.transaction():
                result = await conn.fetchrow(
                    """
                    INSERT INTO orders (product_id, customer_email, quantity, total_amount, status)
                    VALUES ($1, $2, $3, $4, 'pending')
                    RETURNING id, product_id, customer_email, quantity, total_amount, status, created_at, updated_at
                    """,
                    order.product_id,
                    order.customer_email,
                    order.quantity,
                    total_amount
                )

                # Update stock
                await conn.execute(
                    "UPDATE products SET stock_quantity = stock_quantity - $1 WHERE id = $2",
                    order.quantity,
                    order.product_id
                )

        duration = time.time() - start_time
        WRITE_COUNT.labels(service=SERVICE_NAME, operation="insert", table="orders").inc()
        WRITE_LATENCY.labels(service=SERVICE_NAME, operation="insert", table="orders").observe(duration)

        span.set_attribute("order.id", result["id"])
        logger.info(f"Created order id={result['id']} product_id={order.product_id}")

        return {
            "id": result["id"],
            "product_id": result["product_id"],
            "customer_email": result["customer_email"],
            "quantity": result["quantity"],
            "total_amount": float(result["total_amount"]),
            "status": result["status"],
            "created_at": result["created_at"].isoformat(),
            "updated_at": result["updated_at"].isoformat(),
            "cdc_note": "Both ORDER insert and PRODUCT stock update will appear in Kafka"
        }


@app.get("/orders")
async def list_orders(status: Optional[str] = None, limit: int = 100, offset: int = 0):
    """List orders with optional status filter."""
    async with db_pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                "SELECT * FROM orders WHERE status = $1 ORDER BY id DESC LIMIT $2 OFFSET $3",
                status, limit, offset
            )
            total = await conn.fetchval("SELECT COUNT(*) FROM orders WHERE status = $1", status)
        else:
            rows = await conn.fetch(
                "SELECT * FROM orders ORDER BY id DESC LIMIT $1 OFFSET $2",
                limit, offset
            )
            total = await conn.fetchval("SELECT COUNT(*) FROM orders")

    return {
        "orders": [
            {
                "id": r["id"],
                "product_id": r["product_id"],
                "customer_email": r["customer_email"],
                "quantity": r["quantity"],
                "total_amount": float(r["total_amount"]),
                "status": r["status"],
                "created_at": r["created_at"].isoformat(),
                "updated_at": r["updated_at"].isoformat()
            }
            for r in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset
    }


@app.put("/orders/{order_id}/status")
async def update_order_status(order_id: int, status_update: OrderStatusUpdate):
    """Update order status - this change will be captured by CDC."""
    start_time = time.time()
    valid_statuses = ["pending", "confirmed", "shipped", "delivered", "cancelled"]

    if status_update.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {valid_statuses}"
        )

    with tracer.start_as_current_span("update_order_status") as span:
        span.set_attribute("order.id", order_id)
        span.set_attribute("order.new_status", status_update.status)

        async with db_pool.acquire() as conn:
            result = await conn.fetchrow(
                """
                UPDATE orders SET status = $1
                WHERE id = $2
                RETURNING id, product_id, customer_email, quantity, total_amount, status, created_at, updated_at
                """,
                status_update.status,
                order_id
            )

        if not result:
            raise HTTPException(status_code=404, detail="Order not found")

        duration = time.time() - start_time
        WRITE_COUNT.labels(service=SERVICE_NAME, operation="update", table="orders").inc()
        WRITE_LATENCY.labels(service=SERVICE_NAME, operation="update", table="orders").observe(duration)

        logger.info(f"Updated order id={order_id} status={status_update.status}")

        return {
            "id": result["id"],
            "product_id": result["product_id"],
            "customer_email": result["customer_email"],
            "quantity": result["quantity"],
            "total_amount": float(result["total_amount"]),
            "status": result["status"],
            "created_at": result["created_at"].isoformat(),
            "updated_at": result["updated_at"].isoformat(),
            "cdc_note": "This status UPDATE will appear in Kafka via Debezium CDC"
        }


# ===========================================
# Bulk Operations for Load Testing
# ===========================================

@app.post("/products/bulk")
async def create_products_bulk(count: int = 10):
    """Create multiple products for load testing."""
    start_time = time.time()

    with tracer.start_as_current_span("create_products_bulk") as span:
        span.set_attribute("bulk.count", count)

        products = []
        async with db_pool.acquire() as conn:
            for i in range(count):
                result = await conn.fetchrow(
                    """
                    INSERT INTO products (name, description, price, category, stock_quantity)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING id, name, price
                    """,
                    f"Bulk Product {datetime.now().timestamp()}-{i}",
                    f"Auto-generated product for testing",
                    round(10 + (i * 5.5), 2),
                    ["Electronics", "Office", "Kitchen", "Accessories"][i % 4],
                    100
                )
                products.append({
                    "id": result["id"],
                    "name": result["name"],
                    "price": float(result["price"])
                })

        duration = time.time() - start_time
        for _ in range(count):
            WRITE_COUNT.labels(service=SERVICE_NAME, operation="insert", table="products").inc()
        WRITE_LATENCY.labels(service=SERVICE_NAME, operation="bulk_insert", table="products").observe(duration)

        logger.info(f"Created {count} products in bulk")

        return {
            "created": count,
            "products": products,
            "duration_ms": round(duration * 1000, 2),
            "cdc_note": f"All {count} INSERT events will stream through Kafka"
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
