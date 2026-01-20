"""
Job Scheduler API - Manages job scheduling, status, and coordination.
"""
import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import asyncpg
import redis.asyncio as redis
from croniter import croniter
from fastapi import FastAPI, HTTPException, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel, Field
from starlette.responses import Response

# Configuration
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "scheduler")
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://scheduler:scheduler@postgres:5432/jobscheduler")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

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
JOBS_SCHEDULED = Counter(
    "jobs_scheduled_total",
    "Total jobs scheduled",
    ["job_type", "schedule_type"]
)
JOBS_COMPLETED = Counter(
    "jobs_completed_total",
    "Total jobs completed",
    ["job_type", "status"]
)
JOBS_FAILED = Counter(
    "jobs_failed_total",
    "Total jobs failed",
    ["job_type", "failure_reason"]
)
JOB_DURATION = Histogram(
    "job_duration_seconds",
    "Job execution duration",
    ["job_type"],
    buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0]
)
JOBS_PENDING = Gauge(
    "jobs_pending_total",
    "Current pending jobs",
    ["job_type"]
)
JOBS_RUNNING = Gauge(
    "jobs_running_total",
    "Currently running jobs",
    ["worker_id"]
)
ACTIVE_WORKERS = Gauge(
    "active_workers_total",
    "Number of active workers"
)
JOB_RETRIES = Counter(
    "job_retries_total",
    "Total job retry attempts",
    ["job_type"]
)
JOB_QUEUE_WAIT = Histogram(
    "job_queue_wait_seconds",
    "Time jobs spend waiting in queue",
    ["job_type"],
    buckets=[0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 300.0]
)


class JobType(str, Enum):
    IMMEDIATE = "immediate"
    DELAYED = "delayed"
    RECURRING = "recurring"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class CreateJobRequest(BaseModel):
    name: str = Field(..., description="Job name/identifier")
    job_type: JobType = Field(default=JobType.IMMEDIATE, description="Type of job scheduling")
    payload: dict = Field(default_factory=dict, description="Job payload data")
    delay_seconds: Optional[int] = Field(default=None, description="Delay in seconds for delayed jobs")
    cron_expression: Optional[str] = Field(default=None, description="Cron expression for recurring jobs")
    max_retries: int = Field(default=3, description="Maximum retry attempts")
    timeout_seconds: int = Field(default=60, description="Job timeout in seconds")
    fail_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="Simulated failure rate (0.0-1.0)")
    execution_time_ms: int = Field(default=1000, ge=100, description="Simulated execution time in ms")


class JobResponse(BaseModel):
    id: str
    name: str
    job_type: str
    status: str
    payload: dict
    created_at: str
    scheduled_at: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]
    worker_id: Optional[str]
    attempt: int
    max_retries: int
    error_message: Optional[str]


class WorkerHeartbeat(BaseModel):
    worker_id: str
    current_job_id: Optional[str] = None


# Database connection pool
db_pool: Optional[asyncpg.Pool] = None
redis_client: Optional[redis.Redis] = None


async def init_db():
    """Initialize database schema."""
    global db_pool, redis_client

    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=5, max_size=20)
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)

    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id UUID PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                job_type VARCHAR(50) NOT NULL,
                status VARCHAR(50) NOT NULL DEFAULT 'pending',
                payload JSONB NOT NULL DEFAULT '{}',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                scheduled_at TIMESTAMP WITH TIME ZONE,
                started_at TIMESTAMP WITH TIME ZONE,
                completed_at TIMESTAMP WITH TIME ZONE,
                worker_id VARCHAR(100),
                attempt INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3,
                timeout_seconds INTEGER DEFAULT 60,
                fail_rate FLOAT DEFAULT 0.0,
                execution_time_ms INTEGER DEFAULT 1000,
                cron_expression VARCHAR(100),
                error_message TEXT,
                next_run_at TIMESTAMP WITH TIME ZONE
            )
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_scheduled_at ON jobs(scheduled_at)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_jobs_next_run_at ON jobs(next_run_at)
        """)

        # Workers table for tracking active workers
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS workers (
                worker_id VARCHAR(100) PRIMARY KEY,
                last_heartbeat TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                current_job_id UUID,
                status VARCHAR(50) DEFAULT 'active'
            )
        """)


async def close_db():
    """Close database connections."""
    global db_pool, redis_client
    if db_pool:
        await db_pool.close()
    if redis_client:
        await redis_client.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"{SERVICE_NAME} starting up")
    await init_db()
    # Start recurring job scheduler
    asyncio.create_task(recurring_job_scheduler())
    yield
    logger.info(f"{SERVICE_NAME} shutting down")
    await close_db()


app = FastAPI(title="Job Scheduler API", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


async def recurring_job_scheduler():
    """Background task to schedule recurring jobs."""
    while True:
        try:
            async with db_pool.acquire() as conn:
                # Find recurring jobs that need to be scheduled
                now = datetime.utcnow()
                recurring_jobs = await conn.fetch("""
                    SELECT id, name, cron_expression, payload, max_retries,
                           timeout_seconds, fail_rate, execution_time_ms
                    FROM jobs
                    WHERE job_type = 'recurring'
                    AND status = 'completed'
                    AND next_run_at <= $1
                """, now)

                for job in recurring_jobs:
                    # Calculate next run time
                    cron = croniter(job['cron_expression'], now)
                    next_run = cron.get_next(datetime)

                    # Create new job instance
                    new_job_id = str(uuid.uuid4())
                    await conn.execute("""
                        INSERT INTO jobs (id, name, job_type, status, payload,
                                         scheduled_at, max_retries, timeout_seconds,
                                         fail_rate, execution_time_ms, cron_expression, next_run_at)
                        VALUES ($1, $2, 'recurring', 'pending', $3, $4, $5, $6, $7, $8, $9, $10)
                    """, uuid.UUID(new_job_id), job['name'], json.dumps(dict(job['payload'])),
                        now, job['max_retries'], job['timeout_seconds'],
                        job['fail_rate'], job['execution_time_ms'],
                        job['cron_expression'], next_run)

                    # Push to Redis queue
                    await redis_client.lpush("job_queue", new_job_id)

                    # Update original job's next_run_at
                    await conn.execute("""
                        UPDATE jobs SET next_run_at = $1 WHERE id = $2
                    """, next_run, job['id'])

                    JOBS_SCHEDULED.labels(job_type="recurring", schedule_type="cron").inc()
                    logger.info(f"Scheduled recurring job {new_job_id} from template {job['id']}")

        except Exception as e:
            logger.error(f"Error in recurring job scheduler: {e}")

        await asyncio.sleep(1)  # Check every second


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    # Update pending jobs gauge
    async with db_pool.acquire() as conn:
        pending_counts = await conn.fetch("""
            SELECT job_type, COUNT(*) as count
            FROM jobs WHERE status = 'pending'
            GROUP BY job_type
        """)
        for row in pending_counts:
            JOBS_PENDING.labels(job_type=row['job_type']).set(row['count'])

        # Update active workers gauge
        active_count = await conn.fetchval("""
            SELECT COUNT(*) FROM workers
            WHERE last_heartbeat > NOW() - INTERVAL '30 seconds'
        """)
        ACTIVE_WORKERS.set(active_count or 0)

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/jobs", response_model=JobResponse)
async def create_job(request: CreateJobRequest):
    """Create a new job."""
    with tracer.start_as_current_span("create_job") as span:
        job_id = str(uuid.uuid4())
        now = datetime.utcnow()

        span.set_attribute("job.id", job_id)
        span.set_attribute("job.name", request.name)
        span.set_attribute("job.type", request.job_type.value)

        # Calculate scheduled time
        if request.job_type == JobType.DELAYED:
            if not request.delay_seconds:
                raise HTTPException(400, "delay_seconds required for delayed jobs")
            scheduled_at = now + timedelta(seconds=request.delay_seconds)
        elif request.job_type == JobType.RECURRING:
            if not request.cron_expression:
                raise HTTPException(400, "cron_expression required for recurring jobs")
            try:
                cron = croniter(request.cron_expression, now)
                scheduled_at = cron.get_next(datetime)
            except Exception as e:
                raise HTTPException(400, f"Invalid cron expression: {e}")
        else:
            scheduled_at = now

        # Calculate next_run_at for recurring jobs
        next_run_at = None
        if request.job_type == JobType.RECURRING:
            cron = croniter(request.cron_expression, scheduled_at)
            next_run_at = cron.get_next(datetime)

        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO jobs (id, name, job_type, status, payload, scheduled_at,
                                 max_retries, timeout_seconds, fail_rate, execution_time_ms,
                                 cron_expression, next_run_at)
                VALUES ($1, $2, $3, 'pending', $4, $5, $6, $7, $8, $9, $10, $11)
            """, uuid.UUID(job_id), request.name, request.job_type.value,
                json.dumps(request.payload), scheduled_at, request.max_retries,
                request.timeout_seconds, request.fail_rate, request.execution_time_ms,
                request.cron_expression, next_run_at)

        # Push to Redis queue (workers will pick up based on scheduled_at)
        job_data = {
            "id": job_id,
            "scheduled_at": scheduled_at.isoformat()
        }
        await redis_client.lpush("job_queue", job_id)

        JOBS_SCHEDULED.labels(
            job_type=request.job_type.value,
            schedule_type="cron" if request.job_type == JobType.RECURRING else "one-time"
        ).inc()

        logger.info(f"Created job {job_id} type={request.job_type.value} scheduled_at={scheduled_at}")

        return JobResponse(
            id=job_id,
            name=request.name,
            job_type=request.job_type.value,
            status="pending",
            payload=request.payload,
            created_at=now.isoformat(),
            scheduled_at=scheduled_at.isoformat(),
            started_at=None,
            completed_at=None,
            worker_id=None,
            attempt=0,
            max_retries=request.max_retries,
            error_message=None
        )


@app.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    """Get job status."""
    async with db_pool.acquire() as conn:
        job = await conn.fetchrow("""
            SELECT * FROM jobs WHERE id = $1
        """, uuid.UUID(job_id))

        if not job:
            raise HTTPException(404, "Job not found")

        return JobResponse(
            id=str(job['id']),
            name=job['name'],
            job_type=job['job_type'],
            status=job['status'],
            payload=dict(job['payload']) if job['payload'] else {},
            created_at=job['created_at'].isoformat() if job['created_at'] else None,
            scheduled_at=job['scheduled_at'].isoformat() if job['scheduled_at'] else None,
            started_at=job['started_at'].isoformat() if job['started_at'] else None,
            completed_at=job['completed_at'].isoformat() if job['completed_at'] else None,
            worker_id=job['worker_id'],
            attempt=job['attempt'],
            max_retries=job['max_retries'],
            error_message=job['error_message']
        )


@app.get("/jobs")
async def list_jobs(
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    limit: int = 50
):
    """List jobs with optional filtering."""
    async with db_pool.acquire() as conn:
        query = "SELECT * FROM jobs WHERE 1=1"
        params = []
        param_idx = 1

        if status:
            query += f" AND status = ${param_idx}"
            params.append(status)
            param_idx += 1

        if job_type:
            query += f" AND job_type = ${param_idx}"
            params.append(job_type)
            param_idx += 1

        query += f" ORDER BY created_at DESC LIMIT ${param_idx}"
        params.append(limit)

        jobs = await conn.fetch(query, *params)

        return [
            JobResponse(
                id=str(job['id']),
                name=job['name'],
                job_type=job['job_type'],
                status=job['status'],
                payload=dict(job['payload']) if job['payload'] else {},
                created_at=job['created_at'].isoformat() if job['created_at'] else None,
                scheduled_at=job['scheduled_at'].isoformat() if job['scheduled_at'] else None,
                started_at=job['started_at'].isoformat() if job['started_at'] else None,
                completed_at=job['completed_at'].isoformat() if job['completed_at'] else None,
                worker_id=job['worker_id'],
                attempt=job['attempt'],
                max_retries=job['max_retries'],
                error_message=job['error_message']
            )
            for job in jobs
        ]


@app.delete("/jobs/{job_id}")
async def cancel_job(job_id: str):
    """Cancel a pending job."""
    async with db_pool.acquire() as conn:
        result = await conn.execute("""
            UPDATE jobs SET status = 'cancelled'
            WHERE id = $1 AND status = 'pending'
        """, uuid.UUID(job_id))

        if result == "UPDATE 0":
            raise HTTPException(400, "Job not found or not in pending status")

        # Remove from Redis queue
        await redis_client.lrem("job_queue", 0, job_id)

        logger.info(f"Cancelled job {job_id}")
        return {"message": "Job cancelled", "job_id": job_id}


@app.post("/jobs/{job_id}/claim")
async def claim_job(job_id: str, worker_id: str):
    """Worker claims a job for execution (internal API)."""
    with tracer.start_as_current_span("claim_job") as span:
        span.set_attribute("job.id", job_id)
        span.set_attribute("worker.id", worker_id)

        async with db_pool.acquire() as conn:
            # Use a lock to prevent race conditions
            lock_key = f"job_lock:{job_id}"
            lock = await redis_client.set(lock_key, worker_id, nx=True, ex=60)

            if not lock:
                raise HTTPException(409, "Job already claimed by another worker")

            try:
                job = await conn.fetchrow("""
                    SELECT * FROM jobs WHERE id = $1
                """, uuid.UUID(job_id))

                if not job:
                    raise HTTPException(404, "Job not found")

                if job['status'] not in ('pending', 'retrying'):
                    raise HTTPException(400, f"Job not claimable, status: {job['status']}")

                # Check if scheduled time has passed
                now = datetime.utcnow()
                if job['scheduled_at'] and job['scheduled_at'].replace(tzinfo=None) > now:
                    raise HTTPException(400, "Job not yet due for execution")

                # Record queue wait time
                if job['created_at']:
                    wait_time = (now - job['created_at'].replace(tzinfo=None)).total_seconds()
                    JOB_QUEUE_WAIT.labels(job_type=job['job_type']).observe(wait_time)

                # Claim the job
                await conn.execute("""
                    UPDATE jobs SET
                        status = 'running',
                        started_at = $1,
                        worker_id = $2,
                        attempt = attempt + 1
                    WHERE id = $3
                """, now, worker_id, uuid.UUID(job_id))

                JOBS_RUNNING.labels(worker_id=worker_id).inc()

                return {
                    "id": str(job['id']),
                    "name": job['name'],
                    "payload": dict(job['payload']) if job['payload'] else {},
                    "timeout_seconds": job['timeout_seconds'],
                    "fail_rate": job['fail_rate'],
                    "execution_time_ms": job['execution_time_ms'],
                    "attempt": job['attempt'] + 1,
                    "max_retries": job['max_retries']
                }
            except HTTPException:
                await redis_client.delete(lock_key)
                raise


@app.post("/jobs/{job_id}/complete")
async def complete_job(job_id: str, worker_id: str, success: bool, error_message: Optional[str] = None):
    """Worker reports job completion (internal API)."""
    with tracer.start_as_current_span("complete_job") as span:
        span.set_attribute("job.id", job_id)
        span.set_attribute("worker.id", worker_id)
        span.set_attribute("job.success", success)

        async with db_pool.acquire() as conn:
            job = await conn.fetchrow("""
                SELECT * FROM jobs WHERE id = $1 AND worker_id = $2
            """, uuid.UUID(job_id), worker_id)

            if not job:
                raise HTTPException(404, "Job not found or not owned by this worker")

            now = datetime.utcnow()

            # Calculate duration
            if job['started_at']:
                duration = (now - job['started_at'].replace(tzinfo=None)).total_seconds()
                JOB_DURATION.labels(job_type=job['job_type']).observe(duration)

            JOBS_RUNNING.labels(worker_id=worker_id).dec()

            if success:
                await conn.execute("""
                    UPDATE jobs SET
                        status = 'completed',
                        completed_at = $1
                    WHERE id = $2
                """, now, uuid.UUID(job_id))

                JOBS_COMPLETED.labels(job_type=job['job_type'], status="success").inc()
                logger.info(f"Job {job_id} completed successfully by {worker_id}")
            else:
                # Check if we should retry
                if job['attempt'] < job['max_retries']:
                    # Exponential backoff: 2^attempt seconds
                    backoff = 2 ** job['attempt']
                    retry_at = now + timedelta(seconds=backoff)

                    await conn.execute("""
                        UPDATE jobs SET
                            status = 'retrying',
                            scheduled_at = $1,
                            error_message = $2
                        WHERE id = $3
                    """, retry_at, error_message, uuid.UUID(job_id))

                    # Re-queue for retry
                    await redis_client.lpush("job_queue", job_id)

                    JOB_RETRIES.labels(job_type=job['job_type']).inc()
                    logger.info(f"Job {job_id} scheduled for retry at {retry_at} (attempt {job['attempt'] + 1}/{job['max_retries']})")
                else:
                    await conn.execute("""
                        UPDATE jobs SET
                            status = 'failed',
                            completed_at = $1,
                            error_message = $2
                        WHERE id = $3
                    """, now, error_message, uuid.UUID(job_id))

                    JOBS_COMPLETED.labels(job_type=job['job_type'], status="failed").inc()
                    JOBS_FAILED.labels(job_type=job['job_type'], failure_reason="max_retries").inc()
                    logger.warning(f"Job {job_id} failed permanently after {job['attempt']} attempts")

            # Release lock
            await redis_client.delete(f"job_lock:{job_id}")

            return {"message": "Job status updated", "job_id": job_id}


@app.post("/workers/heartbeat")
async def worker_heartbeat(heartbeat: WorkerHeartbeat):
    """Worker sends heartbeat to indicate it's alive."""
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO workers (worker_id, last_heartbeat, current_job_id)
            VALUES ($1, NOW(), $2)
            ON CONFLICT (worker_id) DO UPDATE SET
                last_heartbeat = NOW(),
                current_job_id = $2
        """, heartbeat.worker_id, uuid.UUID(heartbeat.current_job_id) if heartbeat.current_job_id else None)

    return {"status": "ok"}


@app.get("/workers")
async def list_workers():
    """List all workers and their status."""
    async with db_pool.acquire() as conn:
        workers = await conn.fetch("""
            SELECT worker_id, last_heartbeat, current_job_id, status,
                   CASE WHEN last_heartbeat > NOW() - INTERVAL '30 seconds'
                        THEN 'active' ELSE 'stale' END as health
            FROM workers
            ORDER BY last_heartbeat DESC
        """)

        return [
            {
                "worker_id": w['worker_id'],
                "last_heartbeat": w['last_heartbeat'].isoformat() if w['last_heartbeat'] else None,
                "current_job_id": str(w['current_job_id']) if w['current_job_id'] else None,
                "health": w['health']
            }
            for w in workers
        ]


@app.get("/queue/fetch")
async def fetch_job_from_queue(worker_id: str):
    """Worker fetches next available job from queue."""
    # Pop from Redis queue
    job_id = await redis_client.rpop("job_queue")

    if not job_id:
        return {"job": None}

    # Check if job is ready to run
    async with db_pool.acquire() as conn:
        job = await conn.fetchrow("""
            SELECT * FROM jobs WHERE id = $1 AND status IN ('pending', 'retrying')
        """, uuid.UUID(job_id))

        if not job:
            # Job was cancelled or already claimed, try next
            return {"job": None}

        now = datetime.utcnow()

        # Check if scheduled time has passed
        if job['scheduled_at'] and job['scheduled_at'].replace(tzinfo=None) > now:
            # Not ready yet, push back to queue
            await redis_client.lpush("job_queue", job_id)
            return {"job": None}

        return {"job": {"id": job_id}}


@app.get("/stats")
async def get_stats():
    """Get scheduler statistics."""
    async with db_pool.acquire() as conn:
        stats = {}

        # Job counts by status
        status_counts = await conn.fetch("""
            SELECT status, COUNT(*) as count FROM jobs GROUP BY status
        """)
        stats['jobs_by_status'] = {row['status']: row['count'] for row in status_counts}

        # Job counts by type
        type_counts = await conn.fetch("""
            SELECT job_type, COUNT(*) as count FROM jobs GROUP BY job_type
        """)
        stats['jobs_by_type'] = {row['job_type']: row['count'] for row in type_counts}

        # Active workers
        active_workers = await conn.fetchval("""
            SELECT COUNT(*) FROM workers
            WHERE last_heartbeat > NOW() - INTERVAL '30 seconds'
        """)
        stats['active_workers'] = active_workers

        # Queue length
        queue_length = await redis_client.llen("job_queue")
        stats['queue_length'] = queue_length

        # Average job duration (last hour)
        avg_duration = await conn.fetchval("""
            SELECT AVG(EXTRACT(EPOCH FROM (completed_at - started_at)))
            FROM jobs
            WHERE status = 'completed'
            AND completed_at > NOW() - INTERVAL '1 hour'
        """)
        stats['avg_job_duration_seconds'] = round(avg_duration, 2) if avg_duration else 0

        return stats


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
