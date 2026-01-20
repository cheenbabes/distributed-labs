"""
API Service - Query interface for processed messages and system statistics.

Provides:
- Statistics on message processing
- Query interface for orders
- Comparison tools for delivery semantics
"""
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

import psycopg2
import psycopg2.extras
from confluent_kafka.admin import AdminClient, NewTopic
from confluent_kafka import Consumer, TopicPartition, OFFSET_END
from fastapi import FastAPI, HTTPException, Query
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "api")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://lab:lab123@postgres:5432/exactly_once")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")

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


def get_db_connection():
    """Create database connection."""
    return psycopg2.connect(DATABASE_URL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    yield
    logger.info(f"{SERVICE_NAME} shutting down")


app = FastAPI(title=SERVICE_NAME, lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/stats")
async def get_stats():
    """Get comprehensive processing statistics."""
    with tracer.start_as_current_span("get_stats"):
        try:
            conn = get_db_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Total processed messages
                cur.execute("SELECT COUNT(*) as total FROM processed_messages")
                total_processed = cur.fetchone()["total"]

                # Total unique orders
                cur.execute("SELECT COUNT(*) as total FROM orders")
                total_orders = cur.fetchone()["total"]

                # Messages by consumer
                cur.execute("""
                    SELECT consumer_id, COUNT(*) as count
                    FROM processed_messages
                    GROUP BY consumer_id
                    ORDER BY count DESC
                """)
                by_consumer = cur.fetchall()

                # Processing rate (last minute)
                cur.execute("""
                    SELECT COUNT(*) as count
                    FROM processed_messages
                    WHERE processed_at > NOW() - INTERVAL '1 minute'
                """)
                rate_per_minute = cur.fetchone()["count"]

                # First and last processed
                cur.execute("""
                    SELECT
                        MIN(processed_at) as first_processed,
                        MAX(processed_at) as last_processed
                    FROM processed_messages
                """)
                time_range = cur.fetchone()

            conn.close()

            return {
                "total_processed_messages": total_processed,
                "total_unique_orders": total_orders,
                "messages_by_consumer": by_consumer,
                "processing_rate_per_minute": rate_per_minute,
                "first_processed": time_range["first_processed"].isoformat() if time_range["first_processed"] else None,
                "last_processed": time_range["last_processed"].isoformat() if time_range["last_processed"] else None
            }

        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats/duplicates")
async def get_duplicate_stats():
    """Get statistics about duplicate message handling."""
    with tracer.start_as_current_span("get_duplicate_stats"):
        try:
            conn = get_db_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Find messages with multiple processing attempts
                # (would show up in logs but not in processed_messages due to idempotency)
                cur.execute("""
                    SELECT message_id, COUNT(*) as attempt_count
                    FROM processed_messages
                    GROUP BY message_id
                    HAVING COUNT(*) > 1
                """)
                multi_attempts = cur.fetchall()

                # Total unique messages
                cur.execute("SELECT COUNT(DISTINCT message_id) as unique_count FROM processed_messages")
                unique_count = cur.fetchone()["unique_count"]

                # Total rows (should equal unique if idempotency working)
                cur.execute("SELECT COUNT(*) as total FROM processed_messages")
                total_rows = cur.fetchone()["total"]

            conn.close()

            return {
                "unique_message_count": unique_count,
                "total_database_rows": total_rows,
                "idempotency_working": unique_count == total_rows,
                "duplicate_entries": multi_attempts
            }

        except Exception as e:
            logger.error(f"Error getting duplicate stats: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/orders")
async def get_orders(
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0),
    customer_id: str = Query(default=None),
    status: str = Query(default=None)
):
    """Get processed orders with filtering."""
    with tracer.start_as_current_span("get_orders"):
        try:
            conn = get_db_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                query = "SELECT * FROM orders WHERE 1=1"
                params = []

                if customer_id:
                    query += " AND customer_id = %s"
                    params.append(customer_id)

                if status:
                    query += " AND status = %s"
                    params.append(status)

                query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
                params.extend([limit, offset])

                cur.execute(query, params)
                orders = cur.fetchall()

                # Get total count
                count_query = "SELECT COUNT(*) FROM orders WHERE 1=1"
                count_params = []
                if customer_id:
                    count_query += " AND customer_id = %s"
                    count_params.append(customer_id)
                if status:
                    count_query += " AND status = %s"
                    count_params.append(status)

                cur.execute(count_query, count_params)
                total = cur.fetchone()["count"]

            conn.close()

            return {
                "orders": orders,
                "total": total,
                "limit": limit,
                "offset": offset
            }

        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    """Get a specific order by ID."""
    with tracer.start_as_current_span("get_order") as span:
        span.set_attribute("order_id", order_id)

        try:
            conn = get_db_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Get order
                cur.execute("SELECT * FROM orders WHERE order_id = %s", (order_id,))
                order = cur.fetchone()

                # Get processing record
                cur.execute(
                    "SELECT * FROM processed_messages WHERE message_id = %s",
                    (order_id,)
                )
                processing_record = cur.fetchone()

            conn.close()

            if not order and not processing_record:
                raise HTTPException(status_code=404, detail="Order not found")

            return {
                "order": order,
                "processing_record": processing_record
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting order: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/kafka/topics")
async def get_kafka_topics():
    """Get Kafka topic information."""
    with tracer.start_as_current_span("get_kafka_topics"):
        try:
            admin = AdminClient({"bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS})
            metadata = admin.list_topics(timeout=10)

            topics = {}
            for topic_name, topic_metadata in metadata.topics.items():
                if not topic_name.startswith("__"):  # Skip internal topics
                    topics[topic_name] = {
                        "partitions": len(topic_metadata.partitions),
                        "partition_info": [
                            {
                                "id": p.id,
                                "leader": p.leader,
                                "replicas": list(p.replicas),
                                "isrs": list(p.isrs)
                            }
                            for p in topic_metadata.partitions.values()
                        ]
                    }

            return {"topics": topics}

        except Exception as e:
            logger.error(f"Error getting Kafka topics: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/kafka/consumer-groups")
async def get_consumer_groups():
    """Get Kafka consumer group information."""
    with tracer.start_as_current_span("get_consumer_groups"):
        try:
            admin = AdminClient({"bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS})

            # List consumer groups
            groups = admin.list_groups(timeout=10)

            group_info = []
            for g in groups:
                group_info.append({
                    "id": g.id,
                    "broker_id": g.broker.id if g.broker else None,
                    "protocol": g.protocol,
                    "protocol_type": g.protocol_type,
                    "state": g.state
                })

            return {"consumer_groups": group_info}

        except Exception as e:
            logger.error(f"Error getting consumer groups: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/kafka/lag")
async def get_consumer_lag():
    """Get consumer group lag for the orders topic."""
    with tracer.start_as_current_span("get_consumer_lag"):
        try:
            # Create a temporary consumer to get offsets
            consumer = Consumer({
                "bootstrap.servers": KAFKA_BOOTSTRAP_SERVERS,
                "group.id": "lag-checker-temp",
                "auto.offset.reset": "latest"
            })

            # Get topic partitions
            metadata = consumer.list_topics(topic="orders", timeout=10)
            partitions = metadata.topics["orders"].partitions

            lag_info = []
            total_lag = 0

            for partition_id in partitions:
                tp = TopicPartition("orders", partition_id)

                # Get end offset (latest)
                consumer.assign([tp])
                _, high_watermark = consumer.get_watermark_offsets(tp, timeout=10)

                # Get committed offset for the consumer group
                committed = consumer.committed([tp], timeout=10)
                committed_offset = committed[0].offset if committed[0].offset >= 0 else 0

                partition_lag = high_watermark - committed_offset
                total_lag += partition_lag

                lag_info.append({
                    "partition": partition_id,
                    "high_watermark": high_watermark,
                    "committed_offset": committed_offset,
                    "lag": partition_lag
                })

            consumer.close()

            return {
                "topic": "orders",
                "consumer_group": "order-processors",
                "total_lag": total_lag,
                "partitions": lag_info
            }

        except Exception as e:
            logger.error(f"Error getting consumer lag: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/reset")
async def reset_all():
    """Reset all data - clear database and optionally Kafka offsets."""
    with tracer.start_as_current_span("reset_all"):
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE processed_messages CASCADE")
                cur.execute("TRUNCATE TABLE orders CASCADE")
            conn.commit()
            conn.close()

            return {
                "status": "reset",
                "message": "All database tables cleared. Note: Kafka offsets are preserved."
            }

        except Exception as e:
            logger.error(f"Error resetting: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.get("/compare/semantics")
async def compare_semantics():
    """Get comparison data for different delivery semantics experiments."""
    return {
        "at_most_once": {
            "description": "Fire and forget - fastest but may lose messages",
            "producer_config": {
                "acks": 0,
                "retries": 0
            },
            "consumer_config": {
                "enable_auto_commit": True,
                "auto_commit_interval_ms": 100
            },
            "guarantees": {
                "message_loss": "Possible",
                "duplicates": "None",
                "ordering": "Best effort"
            },
            "use_cases": [
                "Metrics/telemetry where occasional loss is acceptable",
                "High-throughput logging",
                "Real-time game state (superseded by newer data)"
            ]
        },
        "at_least_once": {
            "description": "Retries until acknowledged - no loss but may duplicate",
            "producer_config": {
                "acks": "all",
                "retries": 3
            },
            "consumer_config": {
                "enable_auto_commit": False,
                "commit_after_processing": True
            },
            "guarantees": {
                "message_loss": "None",
                "duplicates": "Possible on failure",
                "ordering": "Per partition"
            },
            "use_cases": [
                "Notifications (duplicate is better than missing)",
                "Event logging (can dedupe downstream)",
                "Cache invalidation"
            ]
        },
        "exactly_once": {
            "description": "Transactional + idempotent - strongest guarantees",
            "producer_config": {
                "acks": "all",
                "enable_idempotence": True,
                "transactional_id": "required for transactions"
            },
            "consumer_config": {
                "enable_auto_commit": False,
                "isolation_level": "read_committed",
                "idempotent_processing": "Required in consumer"
            },
            "guarantees": {
                "message_loss": "None",
                "duplicates": "None (with idempotent consumer)",
                "ordering": "Per partition"
            },
            "use_cases": [
                "Financial transactions",
                "Order processing",
                "Inventory management",
                "Any state that must be accurate"
            ]
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
