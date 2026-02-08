# ML Lab 11: Design an AI Agent

You've seen chatbots answer questions. But an agent doesn't just talk -- it acts. It decides which tools to use, calls them, interprets the results, and keeps going until the job is done. In this capstone lab, you'll build a ReAct agent from scratch: the reasoning loop, tool execution, memory, and guardrails that prevent it from going off the rails.

## What You'll Learn

- How the ReAct (Reason + Act) agent loop works step by step
- How to give an LLM access to tools (calculator, search, RAG knowledge base)
- How guardrails protect against prompt injection and runaway execution
- How to observe and debug agent behavior through tracing and metrics
- Why the hardest part of agents isn't the LLM -- it's the tool orchestration

## Architecture

```
┌──────────────┐     ┌──────────────────────────────────────────────┐
│   User Task  │────▶│              Agent API                       │
│              │     │                                              │
└──────────────┘     │  ┌─────────────────────────────────────┐    │
                     │  │         Agent Loop (ReAct)           │    │
                     │  │                                     │    │
                     │  │  1. Build prompt with history        │    │
                     │  │  2. Call LLM                         │    │
                     │  │  3. Parse response                   │    │
                     │  │  4. Tool call? Execute & loop        │    │
                     │  │  5. Final answer? Return             │    │
                     │  └──────────┬──────────────────────────┘    │
                     │             │                                │
                     │  ┌──────────▼──────────────────────────┐    │
                     │  │        Guardrails                    │    │
                     │  │  - Input validation                  │    │
                     │  │  - Tool call validation              │    │
                     │  │  - Execution limits                  │    │
                     │  └─────────────────────────────────────┘    │
                     └──────────────────────────────────────────────┘
                                    │
                     ┌──────────────┼──────────────┐
                     ▼              ▼              ▼
              ┌──────────┐  ┌──────────┐  ┌──────────┐
              │ Ollama   │  │ ChromaDB │  │  Tools   │
              │ (LLM)    │  │ (RAG)    │  │ Service  │
              └──────────┘  └──────────┘  └──────────┘
```

## Prerequisites

- Docker and Docker Compose
- At least 4GB of free RAM (Ollama needs memory for the LLM)

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

### 2. Pull the LLM Model

```bash
docker exec ml-lab-11-ollama ollama pull tinyllama
```

### 3. Open the Services

| Service | URL | Purpose |
|---------|-----|---------|
| Agent API | http://localhost:8000/docs | Interactive API documentation |
| Tool Service | http://localhost:8002/docs | Tool execution endpoints |
| ChromaDB | http://localhost:8001 | Vector database (RAG tool) |
| Grafana | http://localhost:3001 | Monitoring dashboards (admin/admin) |
| Prometheus | http://localhost:9090 | Metrics |
| Ollama | http://localhost:11434 | LLM runtime |

### 4. Submit a Task

```bash
curl -s http://localhost:8000/task \
  -H "Content-Type: application/json" \
  -d '{"task": "What is the square root of 144 plus the square root of 256?"}' | python3 -m json.tool
```

### 5. Open the Notebook

Open `notebooks/agent_exploration.ipynb` in Jupyter or VS Code to work through the exercises.

## Lab Exercises

### Exercise 1: Run the Agent Loop

Submit tasks that require different numbers of steps. Start with a simple calculation (1 tool call), then a question that requires search + calculation (2 tool calls), then a complex question requiring multiple lookups. Trace the agent's reasoning at each step.

**You'll answer:** How does the agent decide when to use a tool vs give a final answer? What does the conversation history look like after multiple steps?

### Exercise 2: Tool Orchestration

Submit tasks that require the agent to chain tools together: search for information, then calculate something based on the results. Observe how the agent passes results from one tool call to the next.

**You'll answer:** Can the agent combine results from multiple tools? What happens when a tool returns an error?

### Exercise 3: Guardrails in Action

Test the guardrails by submitting prompt injection attempts, dangerous tool arguments, and tasks designed to cause infinite loops. Verify that the guardrails catch each attack.

**You'll answer:** Which attacks does the system catch? Which ones slip through? What would you add to make it more robust?

### Exercise 4: Observing Agent Behavior

Use the metrics and logging to understand how the agent behaves in practice. Track steps per task, tool usage patterns, and failure rates. Identify which types of tasks are hardest for the agent.

**You'll answer:** What is the average number of steps per task? Which tools are used most frequently? How often does the agent hit its step limit?

## Key Takeaways

1. **The agent loop is simple** -- Observe, Decide, Act, Repeat. The complexity is in reliable tool parsing and error handling.
2. **Guardrails are non-negotiable** -- without input validation, tool restrictions, and execution limits, agents can be hijacked or run forever.
3. **Tool design matters more than LLM quality** -- clear tool descriptions and predictable return formats make the agent more reliable.
4. **Agents fail in new ways** -- the LLM might hallucinate tool calls, misparse results, or loop without making progress.
5. **Observability is essential** -- you cannot debug agent behavior without step-by-step tracing and metrics.

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Ollama model not found

Make sure to pull the model after starting the services:

```bash
docker exec ml-lab-11-ollama ollama pull tinyllama
```

### Agent not calling tools

The LLM might not follow the tool-calling format reliably. Try:
- Using a more capable model: `docker exec ml-lab-11-ollama ollama pull mistral`
- Submitting simpler, more direct tasks
- Checking the agent API logs: `docker compose logs agent-api`

### Agent stuck in a loop

The max_steps limit (default 10) prevents infinite loops. You can lower it:

```bash
curl -s http://localhost:8000/task \
  -H "Content-Type: application/json" \
  -d '{"task": "your task here", "max_steps": 5}'
```

### Port conflicts

```bash
lsof -i :8000 -i :8001 -i :8002 -i :11434
```

Kill conflicting processes or change ports in `docker-compose.yml`.

### Container out of memory

```bash
docker stats --no-stream
```

## What's Next

Congratulations -- you've completed the ML Labs series! You've gone from training your first model to building a full AI agent with tool use and guardrails. The concepts you've learned form the foundation of modern ML engineering.
