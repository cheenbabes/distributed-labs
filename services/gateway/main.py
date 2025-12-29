import logging
import os
import time

import httpx
from fastapi import FastAPI, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from pythonjsonlogger import jsonlogger

SERVICE_NAME = "gateway"
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
SERVICE_A_URL = os.getenv("SERVICE_A_URL", "http://service-a:8001")

# Configure structured JSON logging
logger = logging.getLogger()
handler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter(
    fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
    rename_fields={"asctime": "timestamp", "levelname": "level"}
)
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Configure OpenTelemetry
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
    ["method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0]
)

app = FastAPI(title=SERVICE_NAME)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/process")
async def api_process(request: Request):
    start_time = time.time()

    current_span = trace.get_current_span()

    # Log the incoming request
    logger.info(
        "Received request at gateway",
        extra={
            "service": SERVICE_NAME,
            "trace_id": format(current_span.get_span_context().trace_id, "032x"),
            "path": "/api/process"
        }
    )

    # Call Service A
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{SERVICE_A_URL}/process")
        service_a_result = response.json()

    duration = time.time() - start_time
    REQUEST_COUNT.labels(method="GET", endpoint="/api/process", status="200").inc()
    REQUEST_LATENCY.labels(method="GET", endpoint="/api/process").observe(duration)

    # Log completion
    logger.info(
        "Request completed",
        extra={
            "service": SERVICE_NAME,
            "trace_id": format(current_span.get_span_context().trace_id, "032x"),
            "duration_ms": round(duration * 1000, 2)
        }
    )

    return {
        "service": SERVICE_NAME,
        "total_duration_ms": round(duration * 1000, 2),
        "chain": service_a_result
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
