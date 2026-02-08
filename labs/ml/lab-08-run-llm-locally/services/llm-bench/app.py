"""LLM Benchmark Service — Proxies to Ollama with timing metrics."""
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from prometheus_client import Histogram, Counter, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
import requests
import time
import json
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="LLM Benchmark Service", version="1.0.0")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

# -------------------------------------------------------
# Prometheus Metrics
# -------------------------------------------------------
TTFT = Histogram(
    "llm_ttft_seconds",
    "Time to first token",
    ["model"],
    buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10, 30],
)
TPS = Histogram(
    "llm_tokens_per_second",
    "Tokens per second",
    ["model"],
    buckets=[1, 5, 10, 20, 50, 100, 200],
)
TOTAL_TIME = Histogram(
    "llm_generation_seconds",
    "Total generation time",
    ["model"],
    buckets=[1, 2, 5, 10, 30, 60, 120],
)
REQUEST_COUNT = Counter(
    "llm_requests_total",
    "Total LLM requests",
    ["model", "endpoint"],
)


# -------------------------------------------------------
# Request / Response Models
# -------------------------------------------------------
class GenerateRequest(BaseModel):
    prompt: str
    model: str = "tinyllama"
    max_tokens: int = 100


class GenerateResponse(BaseModel):
    response: str
    model: str
    ttft_ms: float
    tokens_per_second: float
    total_time_ms: float
    token_count: int


class PullRequest(BaseModel):
    model: str = "tinyllama"


# -------------------------------------------------------
# Endpoints
# -------------------------------------------------------
@app.get("/health")
def health():
    """Health check."""
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        ollama_ok = resp.status_code == 200
    except Exception:
        ollama_ok = False
    return {"status": "healthy", "ollama_reachable": ollama_ok}


@app.post("/pull")
def pull_model(req: PullRequest):
    """Pull a model from the Ollama registry."""
    logger.info(f"Pulling model: {req.model}")
    resp = requests.post(
        f"{OLLAMA_URL}/api/pull",
        json={"name": req.model},
        stream=True,
        timeout=600,
    )
    statuses = []
    for line in resp.iter_lines():
        if line:
            data = json.loads(line)
            status = data.get("status", "")
            if status and status not in statuses:
                statuses.append(status)
                logger.info(f"  pull {req.model}: {status}")
    return {"status": "complete", "model": req.model, "steps": statuses}


@app.get("/models")
def list_models():
    """List models available in Ollama."""
    resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=10)
    return resp.json()


@app.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest):
    """Generate text and return timing metrics."""
    REQUEST_COUNT.labels(model=req.model, endpoint="generate").inc()

    start = time.time()
    first_token_time = None
    full_response = ""
    token_count = 0

    resp = requests.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": req.model,
            "prompt": req.prompt,
            "stream": True,
            "options": {"num_predict": req.max_tokens},
        },
        stream=True,
        timeout=120,
    )

    for line in resp.iter_lines():
        if line:
            data = json.loads(line)
            if "response" in data and data["response"]:
                if first_token_time is None:
                    first_token_time = time.time()
                full_response += data["response"]
                token_count += 1

    end = time.time()
    total_time = end - start
    ttft = (first_token_time - start) if first_token_time else total_time
    generation_window = (end - first_token_time) if first_token_time else 0.001
    tps = token_count / generation_window if generation_window > 0 else 0

    # Record metrics
    TTFT.labels(model=req.model).observe(ttft)
    TPS.labels(model=req.model).observe(tps)
    TOTAL_TIME.labels(model=req.model).observe(total_time)

    logger.info(
        f"generate model={req.model} tokens={token_count} "
        f"ttft={ttft:.2f}s tps={tps:.1f} total={total_time:.2f}s"
    )

    return GenerateResponse(
        response=full_response,
        model=req.model,
        ttft_ms=round(ttft * 1000, 1),
        tokens_per_second=round(tps, 1),
        total_time_ms=round(total_time * 1000, 1),
        token_count=token_count,
    )


@app.post("/generate/stream")
def generate_stream(req: GenerateRequest):
    """Stream tokens back as Server-Sent Events with timing info."""
    REQUEST_COUNT.labels(model=req.model, endpoint="generate_stream").inc()

    def token_stream():
        start = time.time()
        first_token_time = None
        token_count = 0

        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": req.model,
                "prompt": req.prompt,
                "stream": True,
                "options": {"num_predict": req.max_tokens},
            },
            stream=True,
            timeout=120,
        )

        for line in resp.iter_lines():
            if line:
                data = json.loads(line)
                if "response" in data and data["response"]:
                    now = time.time()
                    if first_token_time is None:
                        first_token_time = now
                    token_count += 1
                    elapsed = now - start
                    event = {
                        "token": data["response"],
                        "token_num": token_count,
                        "elapsed_ms": round(elapsed * 1000, 1),
                    }
                    yield f"data: {json.dumps(event)}\n\n"

                if data.get("done"):
                    end = time.time()
                    total_time = end - start
                    ttft = (first_token_time - start) if first_token_time else total_time
                    generation_window = (end - first_token_time) if first_token_time else 0.001
                    tps = token_count / generation_window if generation_window > 0 else 0

                    TTFT.labels(model=req.model).observe(ttft)
                    TPS.labels(model=req.model).observe(tps)
                    TOTAL_TIME.labels(model=req.model).observe(total_time)

                    summary = {
                        "done": True,
                        "model": req.model,
                        "token_count": token_count,
                        "ttft_ms": round(ttft * 1000, 1),
                        "tokens_per_second": round(tps, 1),
                        "total_time_ms": round(total_time * 1000, 1),
                    }
                    yield f"data: {json.dumps(summary)}\n\n"

    return StreamingResponse(token_stream(), media_type="text/event-stream")


@app.post("/benchmark")
def run_benchmark(model: str = "tinyllama", runs: int = 3):
    """Run a standard benchmark suite and return aggregated results."""
    REQUEST_COUNT.labels(model=model, endpoint="benchmark").inc()

    prompts = [
        {"name": "short", "prompt": "What is 2+2?", "max_tokens": 20},
        {
            "name": "medium",
            "prompt": "Explain what a neural network is in 3 sentences.",
            "max_tokens": 100,
        },
        {
            "name": "long",
            "prompt": "Write a Python function that implements binary search. Include docstring and comments.",
            "max_tokens": 200,
        },
    ]

    results = []
    for prompt_cfg in prompts:
        ttfts, tpss, totals, tokens = [], [], [], []
        for _ in range(runs):
            resp = generate(
                GenerateRequest(
                    prompt=prompt_cfg["prompt"],
                    model=model,
                    max_tokens=prompt_cfg["max_tokens"],
                )
            )
            ttfts.append(resp.ttft_ms)
            tpss.append(resp.tokens_per_second)
            totals.append(resp.total_time_ms)
            tokens.append(resp.token_count)

        results.append(
            {
                "prompt_name": prompt_cfg["name"],
                "runs": runs,
                "avg_ttft_ms": round(sum(ttfts) / len(ttfts), 1),
                "avg_tps": round(sum(tpss) / len(tpss), 1),
                "avg_total_ms": round(sum(totals) / len(totals), 1),
                "avg_tokens": round(sum(tokens) / len(tokens), 1),
            }
        )

    return {"model": model, "runs_per_prompt": runs, "results": results}


@app.get("/metrics")
def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
