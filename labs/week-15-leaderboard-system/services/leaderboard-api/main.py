"""
Leaderboard API - Real-time gaming leaderboard using Redis Sorted Sets.

Demonstrates O(log N) rank operations using Redis ZSET commands:
- ZINCRBY: Increment player score - O(log N)
- ZREVRANK: Get player rank - O(log N)
- ZREVRANGE: Get top N players - O(log N + M) where M is range size
- ZCARD: Get total players - O(1)
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import asyncpg
import redis.asyncio as redis
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
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "leaderboard-api")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://lab15-otel-collector:4317")
REDIS_HOST = os.getenv("REDIS_HOST", "lab15-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "lab15-postgres")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.getenv("POSTGRES_USER", "leaderboard")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "leaderboard")
POSTGRES_DB = os.getenv("POSTGRES_DB", "leaderboard")

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
SCORE_UPDATES = Counter(
    "leaderboard_score_updates_total",
    "Total score update operations",
    ["leaderboard_type"]
)
RANK_QUERIES = Counter(
    "leaderboard_rank_queries_total",
    "Total rank query operations",
    ["leaderboard_type"]
)
TOP_N_QUERIES = Counter(
    "leaderboard_top_n_queries_total",
    "Total top N query operations",
    ["leaderboard_type"]
)
OPERATION_LATENCY = Histogram(
    "leaderboard_operation_duration_seconds",
    "Leaderboard operation latency",
    ["operation", "leaderboard_type"],
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
)
TOTAL_PLAYERS = Gauge(
    "leaderboard_total_players",
    "Total players in leaderboard",
    ["leaderboard_type"]
)
REDIS_MEMORY_USAGE = Gauge(
    "leaderboard_redis_memory_bytes",
    "Redis memory usage in bytes"
)
OPERATIONS_PER_SECOND = Gauge(
    "leaderboard_operations_per_second",
    "Operations per second",
    ["operation"]
)

# Global connections
redis_client: Optional[redis.Redis] = None
pg_pool: Optional[asyncpg.Pool] = None

# Operation tracking for ops/sec
operation_counts = {"score_update": 0, "rank_query": 0, "top_n_query": 0}
last_ops_check = time.time()


class LeaderboardType(str, Enum):
    """Supported leaderboard types with different TTLs."""
    ALL_TIME = "all_time"
    WEEKLY = "weekly"
    DAILY = "daily"


def get_leaderboard_key(leaderboard_type: LeaderboardType) -> str:
    """Get Redis key for the leaderboard type with time-based suffix for rotating boards."""
    base = f"leaderboard:{leaderboard_type.value}"
    if leaderboard_type == LeaderboardType.DAILY:
        return f"{base}:{datetime.utcnow().strftime('%Y-%m-%d')}"
    elif leaderboard_type == LeaderboardType.WEEKLY:
        # ISO week number
        week = datetime.utcnow().isocalendar()
        return f"{base}:{week[0]}-W{week[1]:02d}"
    return base


class ScoreUpdate(BaseModel):
    """Request model for score updates."""
    increment: float = 1.0


class PlayerScore(BaseModel):
    """Response model for player score data."""
    player_id: str
    score: float
    rank: int


class LeaderboardEntry(BaseModel):
    """Response model for leaderboard entries."""
    rank: int
    player_id: str
    score: float


class LeaderboardStats(BaseModel):
    """Response model for leaderboard statistics."""
    total_players: int
    leaderboard_type: str
    top_score: Optional[float]
    min_score: Optional[float]
    avg_score: Optional[float]
    redis_memory_mb: float


async def update_ops_metrics():
    """Update operations per second metrics."""
    global last_ops_check, operation_counts
    now = time.time()
    elapsed = now - last_ops_check
    if elapsed >= 1.0:
        for op, count in operation_counts.items():
            OPERATIONS_PER_SECOND.labels(operation=op).set(count / elapsed)
            operation_counts[op] = 0
        last_ops_check = now


async def update_player_metadata(player_id: str, score: float):
    """Store player metadata in PostgreSQL for historical tracking."""
    if pg_pool is None:
        return
    try:
        async with pg_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO player_scores (player_id, score, recorded_at)
                VALUES ($1, $2, NOW())
            """, player_id, score)
    except Exception as e:
        logger.warning(f"Failed to store player metadata: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - setup and teardown connections."""
    global redis_client, pg_pool

    logger.info(f"{SERVICE_NAME} starting up")

    # Connect to Redis
    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        decode_responses=True
    )
    await redis_client.ping()
    logger.info(f"Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")

    # Connect to PostgreSQL
    try:
        pg_pool = await asyncpg.create_pool(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            database=POSTGRES_DB,
            min_size=5,
            max_size=20
        )
        # Create tables if they don't exist
        async with pg_pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS player_scores (
                    id SERIAL PRIMARY KEY,
                    player_id VARCHAR(255) NOT NULL,
                    score DOUBLE PRECISION NOT NULL,
                    recorded_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_player_scores_player_id ON player_scores(player_id)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_player_scores_recorded_at ON player_scores(recorded_at)
            """)
        logger.info(f"Connected to PostgreSQL at {POSTGRES_HOST}:{POSTGRES_PORT}")
    except Exception as e:
        logger.warning(f"PostgreSQL connection failed (optional): {e}")
        pg_pool = None

    # Start background metrics updater
    async def metrics_updater():
        while True:
            try:
                await update_ops_metrics()
                # Update Redis memory metric
                if redis_client:
                    info = await redis_client.info("memory")
                    REDIS_MEMORY_USAGE.set(info.get("used_memory", 0))
                # Update player counts
                for lb_type in LeaderboardType:
                    key = get_leaderboard_key(lb_type)
                    count = await redis_client.zcard(key)
                    TOTAL_PLAYERS.labels(leaderboard_type=lb_type.value).set(count)
            except Exception as e:
                logger.warning(f"Metrics update error: {e}")
            await asyncio.sleep(1)

    metrics_task = asyncio.create_task(metrics_updater())

    yield

    metrics_task.cancel()
    logger.info(f"{SERVICE_NAME} shutting down")
    if redis_client:
        await redis_client.close()
    if pg_pool:
        await pg_pool.close()


app = FastAPI(
    title="Leaderboard API",
    description="Real-time gaming leaderboard using Redis Sorted Sets",
    lifespan=lifespan
)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    """Health check endpoint."""
    redis_ok = False
    try:
        await redis_client.ping()
        redis_ok = True
    except Exception:
        pass

    return {
        "status": "ok" if redis_ok else "degraded",
        "service": SERVICE_NAME,
        "redis": "connected" if redis_ok else "disconnected",
        "postgres": "connected" if pg_pool else "disconnected"
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/players/{player_id}/score")
async def update_score(
    player_id: str,
    body: ScoreUpdate = ScoreUpdate(),
    leaderboard: LeaderboardType = Query(default=LeaderboardType.ALL_TIME)
):
    """
    Add to a player's score using ZINCRBY.

    Time Complexity: O(log N) where N is the number of players in the leaderboard.
    """
    with tracer.start_as_current_span("update_score") as span:
        span.set_attribute("player_id", player_id)
        span.set_attribute("increment", body.increment)
        span.set_attribute("leaderboard_type", leaderboard.value)

        start_time = time.perf_counter()

        key = get_leaderboard_key(leaderboard)

        # ZINCRBY: O(log N)
        new_score = await redis_client.zincrby(key, body.increment, player_id)

        duration = time.perf_counter() - start_time

        # Record metrics
        SCORE_UPDATES.labels(leaderboard_type=leaderboard.value).inc()
        OPERATION_LATENCY.labels(
            operation="score_update",
            leaderboard_type=leaderboard.value
        ).observe(duration)
        operation_counts["score_update"] += 1

        # Get rank (0-indexed, so add 1)
        rank = await redis_client.zrevrank(key, player_id)

        span.set_attribute("new_score", new_score)
        span.set_attribute("duration_ms", duration * 1000)
        span.set_attribute("rank", rank + 1 if rank is not None else -1)

        # Store in PostgreSQL for historical data (fire and forget)
        asyncio.create_task(update_player_metadata(player_id, new_score))

        return {
            "player_id": player_id,
            "score": new_score,
            "rank": rank + 1 if rank is not None else None,
            "leaderboard": leaderboard.value,
            "operation_time_ms": round(duration * 1000, 4)
        }


@app.get("/leaderboard/top")
async def get_top_players(
    n: int = Query(default=100, ge=1, le=1000),
    leaderboard: LeaderboardType = Query(default=LeaderboardType.ALL_TIME)
) -> list[LeaderboardEntry]:
    """
    Get top N players using ZREVRANGE with scores.

    Time Complexity: O(log N + M) where M is the number of results returned.
    """
    with tracer.start_as_current_span("get_top_players") as span:
        span.set_attribute("n", n)
        span.set_attribute("leaderboard_type", leaderboard.value)

        start_time = time.perf_counter()

        key = get_leaderboard_key(leaderboard)

        # ZREVRANGE with WITHSCORES: O(log N + M)
        results = await redis_client.zrevrange(key, 0, n - 1, withscores=True)

        duration = time.perf_counter() - start_time

        # Record metrics
        TOP_N_QUERIES.labels(leaderboard_type=leaderboard.value).inc()
        OPERATION_LATENCY.labels(
            operation="top_n_query",
            leaderboard_type=leaderboard.value
        ).observe(duration)
        operation_counts["top_n_query"] += 1

        span.set_attribute("result_count", len(results))
        span.set_attribute("duration_ms", duration * 1000)

        return [
            LeaderboardEntry(rank=i + 1, player_id=player_id, score=score)
            for i, (player_id, score) in enumerate(results)
        ]


@app.get("/players/{player_id}/rank")
async def get_player_rank(
    player_id: str,
    leaderboard: LeaderboardType = Query(default=LeaderboardType.ALL_TIME)
) -> PlayerScore:
    """
    Get a specific player's rank using ZREVRANK.

    Time Complexity: O(log N)
    """
    with tracer.start_as_current_span("get_player_rank") as span:
        span.set_attribute("player_id", player_id)
        span.set_attribute("leaderboard_type", leaderboard.value)

        start_time = time.perf_counter()

        key = get_leaderboard_key(leaderboard)

        # Use pipeline for atomic read of rank and score
        pipe = redis_client.pipeline()
        pipe.zrevrank(key, player_id)
        pipe.zscore(key, player_id)
        results = await pipe.execute()

        rank, score = results

        duration = time.perf_counter() - start_time

        # Record metrics
        RANK_QUERIES.labels(leaderboard_type=leaderboard.value).inc()
        OPERATION_LATENCY.labels(
            operation="rank_query",
            leaderboard_type=leaderboard.value
        ).observe(duration)
        operation_counts["rank_query"] += 1

        if rank is None:
            raise HTTPException(status_code=404, detail=f"Player {player_id} not found")

        span.set_attribute("rank", rank + 1)
        span.set_attribute("score", score)
        span.set_attribute("duration_ms", duration * 1000)

        return PlayerScore(
            player_id=player_id,
            score=score,
            rank=rank + 1  # 0-indexed to 1-indexed
        )


@app.get("/leaderboard/around/{player_id}")
async def get_players_around(
    player_id: str,
    range_size: int = Query(default=5, ge=1, le=50, alias="range"),
    leaderboard: LeaderboardType = Query(default=LeaderboardType.ALL_TIME)
) -> list[LeaderboardEntry]:
    """
    Get players around a specific player (for showing local context).

    Shows `range_size` players above and below the target player.

    Time Complexity: O(log N + M) for the range query.
    """
    with tracer.start_as_current_span("get_players_around") as span:
        span.set_attribute("player_id", player_id)
        span.set_attribute("range_size", range_size)
        span.set_attribute("leaderboard_type", leaderboard.value)

        start_time = time.perf_counter()

        key = get_leaderboard_key(leaderboard)

        # First get the player's rank
        rank = await redis_client.zrevrank(key, player_id)

        if rank is None:
            raise HTTPException(status_code=404, detail=f"Player {player_id} not found")

        # Calculate range (clamping to valid indices)
        start_idx = max(0, rank - range_size)
        end_idx = rank + range_size

        # Get the range
        results = await redis_client.zrevrange(key, start_idx, end_idx, withscores=True)

        duration = time.perf_counter() - start_time

        OPERATION_LATENCY.labels(
            operation="around_query",
            leaderboard_type=leaderboard.value
        ).observe(duration)

        span.set_attribute("result_count", len(results))
        span.set_attribute("duration_ms", duration * 1000)

        return [
            LeaderboardEntry(rank=start_idx + i + 1, player_id=pid, score=score)
            for i, (pid, score) in enumerate(results)
        ]


@app.get("/stats")
async def get_stats(
    leaderboard: LeaderboardType = Query(default=LeaderboardType.ALL_TIME)
) -> LeaderboardStats:
    """
    Get leaderboard statistics.

    Includes total players, score distribution, and Redis memory usage.
    """
    with tracer.start_as_current_span("get_stats") as span:
        span.set_attribute("leaderboard_type", leaderboard.value)

        key = get_leaderboard_key(leaderboard)

        # Get various stats using pipeline
        pipe = redis_client.pipeline()
        pipe.zcard(key)  # Total players
        pipe.zrevrange(key, 0, 0, withscores=True)  # Top score
        pipe.zrange(key, 0, 0, withscores=True)  # Min score
        pipe.info("memory")

        results = await pipe.execute()

        total_players = results[0]
        top_entry = results[1]
        min_entry = results[2]
        memory_info = results[3]

        # Calculate average score if there are players
        avg_score = None
        if total_players > 0:
            # Sample-based average for large leaderboards
            sample_size = min(1000, total_players)
            sample = await redis_client.zrandmember(key, sample_size, withscores=True)
            if sample:
                avg_score = sum(s for _, s in sample) / len(sample)

        redis_memory_mb = memory_info.get("used_memory", 0) / (1024 * 1024)

        return LeaderboardStats(
            total_players=total_players,
            leaderboard_type=leaderboard.value,
            top_score=top_entry[0][1] if top_entry else None,
            min_score=min_entry[0][1] if min_entry else None,
            avg_score=round(avg_score, 2) if avg_score else None,
            redis_memory_mb=round(redis_memory_mb, 2)
        )


@app.delete("/leaderboard")
async def clear_leaderboard(
    leaderboard: LeaderboardType = Query(default=LeaderboardType.ALL_TIME)
):
    """
    Clear a leaderboard (for testing/reset purposes).
    """
    with tracer.start_as_current_span("clear_leaderboard") as span:
        span.set_attribute("leaderboard_type", leaderboard.value)

        key = get_leaderboard_key(leaderboard)
        deleted = await redis_client.delete(key)

        return {
            "cleared": True,
            "leaderboard": leaderboard.value,
            "key": key
        }


@app.get("/leaderboard/types")
async def list_leaderboard_types():
    """List available leaderboard types and their current keys."""
    types = []
    for lb_type in LeaderboardType:
        key = get_leaderboard_key(lb_type)
        count = await redis_client.zcard(key)
        types.append({
            "type": lb_type.value,
            "key": key,
            "player_count": count
        })
    return types


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
