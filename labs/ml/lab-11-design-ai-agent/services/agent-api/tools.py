"""Tool definitions and execution for the agent."""
import math
import requests
import os

TOOLS = {
    "calculator": {
        "description": "Evaluate a mathematical expression. Supports +, -, *, /, sqrt, pow, etc.",
        "parameters": {"expression": "string - the math expression to evaluate"}
    },
    "search": {
        "description": "Search for information on a topic. Returns a brief summary.",
        "parameters": {"query": "string - the search query"}
    },
    "rag_query": {
        "description": "Query the knowledge base for information about distributed systems, ML, or Python.",
        "parameters": {"question": "string - the question to answer from the knowledge base"}
    },
    "get_current_time": {
        "description": "Get the current date and time.",
        "parameters": {}
    }
}

# Mock search results for demonstration
MOCK_SEARCH_DB = {
    "python": "Python is a high-level programming language known for readability. Latest version is 3.12. Created by Guido van Rossum in 1991.",
    "machine learning": "Machine learning is a subset of AI where systems learn from data without explicit programming. Key approaches include supervised, unsupervised, and reinforcement learning.",
    "distributed systems": "Distributed systems are collections of independent computers that appear as a single system to users. Key challenges include consensus, fault tolerance, and consistency.",
    "docker": "Docker is a platform for developing, shipping, and running applications in containers. It uses OS-level virtualization to deliver software in packages.",
    "kubernetes": "Kubernetes is an open-source container orchestration platform for automating deployment, scaling, and management of containerized applications.",
    "rag": "Retrieval-Augmented Generation (RAG) combines information retrieval with text generation. It retrieves relevant documents and uses them as context for an LLM.",
    "neural network": "Neural networks are computing systems inspired by biological neural networks. They consist of layers of interconnected nodes that process information using connectionist approaches.",
    "api": "An API (Application Programming Interface) is a set of protocols and tools for building software applications. REST and GraphQL are common API architectures.",
}


def execute_tool(name: str, args: dict) -> str:
    """Execute a tool by name with the given arguments."""
    if name == "calculator":
        return _calculator(args.get("expression", ""))
    elif name == "search":
        return _search(args.get("query", ""))
    elif name == "rag_query":
        return _rag_query(args.get("question", ""))
    elif name == "get_current_time":
        return _get_time()
    else:
        raise ValueError(f"Unknown tool: {name}")


def _calculator(expression: str) -> str:
    """Safely evaluate a mathematical expression."""
    try:
        allowed = {
            'sqrt': math.sqrt, 'pow': pow, 'abs': abs,
            'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
            'log': math.log, 'log10': math.log10, 'log2': math.log2,
            'pi': math.pi, 'e': math.e,
            'ceil': math.ceil, 'floor': math.floor,
            'round': round, 'min': min, 'max': max,
        }
        result = eval(expression, {"__builtins__": {}}, allowed)
        return f"Result: {result}"
    except Exception as e:
        return f"Error evaluating '{expression}': {e}"


def _search(query: str) -> str:
    """Mock search that returns pre-defined results."""
    query_lower = query.lower()
    results = []
    for key, value in MOCK_SEARCH_DB.items():
        if key in query_lower:
            results.append(value)

    if results:
        return " | ".join(results)
    return f"No results found for '{query}'. Try searching for: {', '.join(MOCK_SEARCH_DB.keys())}"


def _rag_query(question: str) -> str:
    """Query ChromaDB directly for document retrieval."""
    try:
        import chromadb
        chroma_url = os.getenv("CHROMA_URL", "http://chromadb:8000")
        host = chroma_url.replace("http://", "").split(":")[0]
        port = int(chroma_url.split(":")[-1])

        client = chromadb.HttpClient(host=host, port=port)
        collection = client.get_or_create_collection("documents")

        if collection.count() == 0:
            return "Knowledge base is empty. No documents have been ingested yet."

        results = collection.query(query_texts=[question], n_results=2)
        if results["documents"] and results["documents"][0]:
            chunks = results["documents"][0]
            return "\n---\n".join(chunks)
        return "No relevant documents found in the knowledge base."
    except Exception as e:
        return f"Knowledge base query failed: {e}"


def _get_time() -> str:
    """Return the current date and time."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return f"Current UTC time: {now.strftime('%Y-%m-%d %H:%M:%S')} (UTC)"
