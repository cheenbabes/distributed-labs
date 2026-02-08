"""Agent API: FastAPI service for the AI agent."""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
import requests
import time
import os
import logging

from agent import run_agent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Agent API", version="1.0.0")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")

# -----------------------------------------------
# Prometheus Metrics
# -----------------------------------------------
AGENT_TASKS = Counter(
    'agent_tasks_total', 'Total agent tasks',
    ['status']  # completed, rejected, limit_reached, error
)
AGENT_STEPS = Histogram(
    'agent_steps_per_task', 'Steps per agent task',
    buckets=[1, 2, 3, 5, 7, 10, 15, 20]
)
AGENT_TOOL_CALLS = Counter(
    'agent_tool_calls_total', 'Total tool calls by tool name',
    ['tool']
)
AGENT_DURATION = Histogram(
    'agent_task_duration_seconds', 'Agent task duration',
    buckets=[1, 2, 5, 10, 20, 30, 60, 120]
)
AGENT_REJECTIONS = Counter(
    'agent_rejections_total', 'Total rejected requests',
    ['reason']
)


# -----------------------------------------------
# Request / Response Models
# -----------------------------------------------
class TaskRequest(BaseModel):
    task: str
    max_steps: int = 10
    model: str = "tinyllama"


class TaskResponse(BaseModel):
    answer: str
    steps: int
    tool_calls: list
    elapsed_seconds: float
    rejected: bool = False
    limit_reached: bool = False


# -----------------------------------------------
# LLM Function
# -----------------------------------------------
def call_ollama(prompt: str, model: str = "tinyllama") -> str:
    """Call Ollama to generate a response."""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 300}
            },
            timeout=60
        )
        return resp.json().get("response", "Failed to generate response")
    except Exception as e:
        return f"LLM generation failed: {e}"


# -----------------------------------------------
# Endpoints
# -----------------------------------------------
@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/task", response_model=TaskResponse)
def submit_task(req: TaskRequest):
    """Submit a task for the agent to complete."""
    start_time = time.time()
    logger.info(f"New task: {req.task[:100]}")

    # Create the LLM function with the requested model
    def llm_fn(prompt: str) -> str:
        return call_ollama(prompt, model=req.model)

    # Run the agent
    result = run_agent(task=req.task, llm_fn=llm_fn, max_steps=req.max_steps)

    elapsed = time.time() - start_time

    # Record metrics
    if result.get("rejected"):
        AGENT_TASKS.labels(status="rejected").inc()
        reason = result.get("answer", "unknown")[:50]
        AGENT_REJECTIONS.labels(reason=reason).inc()
    elif result.get("limit_reached"):
        AGENT_TASKS.labels(status="limit_reached").inc()
    else:
        AGENT_TASKS.labels(status="completed").inc()

    AGENT_STEPS.observe(result.get("steps", 0))
    AGENT_DURATION.observe(elapsed)

    for tc in result.get("tool_calls", []):
        AGENT_TOOL_CALLS.labels(tool=tc["tool"]).inc()

    logger.info(
        f"Task completed: steps={result.get('steps', 0)}, "
        f"tools={len(result.get('tool_calls', []))}, "
        f"elapsed={elapsed:.1f}s"
    )

    return TaskResponse(
        answer=result.get("answer", "No answer generated"),
        steps=result.get("steps", 0),
        tool_calls=result.get("tool_calls", []),
        elapsed_seconds=elapsed,
        rejected=result.get("rejected", False),
        limit_reached=result.get("limit_reached", False)
    )


@app.get("/tools")
def list_tools():
    """List available tools and their descriptions."""
    from tools import TOOLS
    return {"tools": {
        name: {"description": t["description"], "parameters": t["parameters"]}
        for name, t in TOOLS.items()
    }}


@app.get("/metrics")
def metrics():
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
