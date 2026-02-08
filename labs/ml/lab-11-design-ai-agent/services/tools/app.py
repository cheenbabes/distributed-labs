"""Tool Execution Service: Calculator, mock web search, and utilities.

This service provides external tools that the agent can call.
In a production system, these would connect to real APIs.
"""
from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime, timezone
import math

app = FastAPI(title="Tool Service", version="1.0.0")


# -----------------------------------------------
# Request / Response Models
# -----------------------------------------------
class CalculatorRequest(BaseModel):
    expression: str


class SearchRequest(BaseModel):
    query: str


class CalculatorResponse(BaseModel):
    result: str
    expression: str


class SearchResponse(BaseModel):
    results: list[dict]
    query: str


# -----------------------------------------------
# Mock Search Database
# -----------------------------------------------
SEARCH_DB = {
    "python programming": {
        "title": "Python Programming Language",
        "snippet": "Python is a high-level, general-purpose programming language. Its design philosophy emphasizes code readability with the use of significant indentation. Python is dynamically typed and garbage-collected."
    },
    "machine learning": {
        "title": "Machine Learning Overview",
        "snippet": "Machine learning is a branch of artificial intelligence that focuses on building systems that learn from and make decisions based on data. It encompasses supervised learning, unsupervised learning, and reinforcement learning."
    },
    "distributed systems": {
        "title": "Distributed Systems Fundamentals",
        "snippet": "A distributed system is a collection of autonomous computing elements that appears to its users as a single coherent system. Key challenges include handling partial failures, network partitions, and maintaining consistency."
    },
    "docker containers": {
        "title": "Docker Container Platform",
        "snippet": "Docker is a set of platform-as-a-service products that use OS-level virtualization to deliver software in packages called containers. Containers bundle their own software, libraries, and configuration files."
    },
    "neural networks": {
        "title": "Neural Networks Explained",
        "snippet": "Artificial neural networks are computing systems loosely inspired by biological neural networks. They consist of interconnected nodes organized in layers that process signals using adaptable weights."
    },
    "api design": {
        "title": "API Design Best Practices",
        "snippet": "Good API design follows principles of consistency, simplicity, and discoverability. REST APIs use HTTP methods and status codes. GraphQL provides a flexible query language for clients."
    },
    "kubernetes": {
        "title": "Kubernetes Container Orchestration",
        "snippet": "Kubernetes automates deploying, scaling, and operating application containers across clusters of hosts. It provides container-centric management and supports declarative configuration."
    },
    "database": {
        "title": "Database Systems",
        "snippet": "Databases provide organized storage and retrieval of data. Relational databases use SQL and enforce ACID properties. NoSQL databases offer flexible schemas and horizontal scaling for different use cases."
    },
}


# -----------------------------------------------
# Endpoints
# -----------------------------------------------
@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/calculate", response_model=CalculatorResponse)
def calculate(req: CalculatorRequest):
    """Evaluate a mathematical expression safely."""
    try:
        allowed = {
            'sqrt': math.sqrt, 'pow': pow, 'abs': abs,
            'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
            'log': math.log, 'log10': math.log10, 'log2': math.log2,
            'pi': math.pi, 'e': math.e,
            'ceil': math.ceil, 'floor': math.floor,
            'round': round, 'min': min, 'max': max,
        }
        result = eval(req.expression, {"__builtins__": {}}, allowed)
        return CalculatorResponse(result=str(result), expression=req.expression)
    except Exception as e:
        return CalculatorResponse(
            result=f"Error: {str(e)}",
            expression=req.expression
        )


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest):
    """Mock web search returning pre-defined results."""
    query_lower = req.query.lower()
    results = []

    for key, entry in SEARCH_DB.items():
        # Check if any keyword from the query matches the DB key
        if any(word in key for word in query_lower.split()):
            results.append(entry)

    if not results:
        results.append({
            "title": "No Results",
            "snippet": f"No results found for '{req.query}'. Try broader search terms."
        })

    return SearchResponse(results=results, query=req.query)


@app.get("/time")
def get_time():
    """Return the current UTC time."""
    now = datetime.now(timezone.utc)
    return {
        "utc": now.isoformat(),
        "formatted": now.strftime("%Y-%m-%d %H:%M:%S UTC")
    }


@app.get("/tools")
def list_tools():
    """List available tools in this service."""
    return {
        "tools": [
            {"name": "calculate", "method": "POST", "path": "/calculate",
             "description": "Evaluate a math expression"},
            {"name": "search", "method": "POST", "path": "/search",
             "description": "Search for information (mock)"},
            {"name": "time", "method": "GET", "path": "/time",
             "description": "Get current UTC time"},
        ]
    }
