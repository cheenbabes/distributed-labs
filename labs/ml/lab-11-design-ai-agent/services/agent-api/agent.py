"""
The Agent Loop: Observe -> Decide -> Act -> Repeat

This is a ReAct (Reason + Act) agent. The LLM:
1. Receives the task + conversation history
2. Reasons about what to do next
3. Decides to either call a tool or respond to the user
4. If tool call: execute tool, add result to history, go to step 1
5. If final answer: return to user
"""
import json
import time
import logging
from typing import Optional
from tools import TOOLS, execute_tool
from guardrails import validate_input, validate_tool_call

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a helpful assistant with access to tools.

Available tools:
{tool_descriptions}

To use a tool, respond with a JSON block:
```json
{{"tool": "tool_name", "args": {{"param1": "value1"}}}}
```

To give a final answer, just respond normally without a tool call.

Think step by step. Use tools when you need information or calculations. Give a final answer when you have enough information."""


class AgentState:
    """Tracks the state of a single agent task execution."""

    def __init__(self, task: str, max_steps: int = 10, max_time: float = 120.0):
        self.task = task
        self.history = []
        self.steps = 0
        self.max_steps = max_steps
        self.max_time = max_time
        self.total_tokens = 0
        self.tool_calls = []
        self.start_time = time.time()

    def add_message(self, role: str, content: str):
        self.history.append({"role": role, "content": content})

    def is_over_limit(self) -> Optional[str]:
        if self.steps >= self.max_steps:
            return f"Max steps reached ({self.max_steps})"
        elapsed = time.time() - self.start_time
        if elapsed > self.max_time:
            return f"Timeout ({elapsed:.0f}s)"
        return None


def build_prompt(state: AgentState) -> str:
    """Build the full prompt with system message and conversation history."""
    tool_desc = "\n".join([
        f"- {name}: {tool['description']} | Args: {json.dumps(tool['parameters'])}"
        for name, tool in TOOLS.items()
    ])

    messages = [SYSTEM_PROMPT.format(tool_descriptions=tool_desc)]
    messages.append(f"\nTask: {state.task}\n")

    for msg in state.history:
        if msg["role"] == "tool_result":
            messages.append(f"Tool result: {msg['content']}")
        elif msg["role"] == "assistant":
            messages.append(f"Assistant: {msg['content']}")

    messages.append("\nWhat should I do next?")
    return "\n".join(messages)


def parse_tool_call(response: str) -> Optional[dict]:
    """Extract a tool call JSON from the LLM response."""
    try:
        # Look for ```json ... ``` block
        if '```json' in response:
            json_str = response.split('```json')[1].split('```')[0].strip()
        elif '{"tool"' in response:
            # Find the JSON object inline
            start = response.index('{"tool"')
            depth = 0
            end = start
            for i, c in enumerate(response[start:], start):
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                if depth == 0:
                    end = i + 1
                    break
            json_str = response[start:end]
        else:
            return None

        call = json.loads(json_str)
        if "tool" in call:
            return call
    except (json.JSONDecodeError, ValueError, IndexError):
        pass
    return None


def run_agent(task: str, llm_fn, max_steps: int = 10) -> dict:
    """
    Run the agent loop.

    Args:
        task: The task to complete
        llm_fn: Function that takes a prompt string and returns an LLM response string
        max_steps: Maximum number of reasoning steps

    Returns:
        dict with answer, steps, tool_calls, elapsed_seconds, and status flags
    """
    # Validate input through guardrails
    input_check = validate_input(task)
    if not input_check["valid"]:
        return {
            "answer": f"Request rejected: {input_check['reason']}",
            "steps": 0,
            "tool_calls": [],
            "elapsed_seconds": 0,
            "rejected": True
        }

    state = AgentState(task, max_steps=max_steps)

    while True:
        # Check execution limits
        limit_msg = state.is_over_limit()
        if limit_msg:
            last_content = state.history[-1]["content"] if state.history else "none"
            return {
                "answer": f"Agent stopped: {limit_msg}. Partial progress: {last_content[:200]}",
                "steps": state.steps,
                "tool_calls": state.tool_calls,
                "elapsed_seconds": time.time() - state.start_time,
                "limit_reached": True
            }

        # Build the prompt and get LLM response
        prompt = build_prompt(state)
        response = llm_fn(prompt)
        state.steps += 1
        state.add_message("assistant", response)

        # Check if the response contains a tool call
        tool_call = parse_tool_call(response)

        if tool_call is None:
            # No tool call means the LLM is giving a final answer
            return {
                "answer": response,
                "steps": state.steps,
                "tool_calls": state.tool_calls,
                "elapsed_seconds": time.time() - state.start_time
            }

        # Validate the tool call through guardrails
        tool_check = validate_tool_call(tool_call)
        if not tool_check["valid"]:
            state.add_message("tool_result", f"Tool call rejected: {tool_check['reason']}")
            continue

        # Execute the tool
        tool_name = tool_call["tool"]
        tool_args = tool_call.get("args", {})

        logger.info(f"Step {state.steps}: Calling tool '{tool_name}' with args {tool_args}")

        try:
            result = execute_tool(tool_name, tool_args)
            state.tool_calls.append({
                "tool": tool_name,
                "args": tool_args,
                "result": str(result)[:500]
            })
            state.add_message("tool_result", str(result))
        except Exception as e:
            error_msg = f"Tool '{tool_name}' failed: {str(e)}"
            state.tool_calls.append({
                "tool": tool_name,
                "args": tool_args,
                "error": str(e)
            })
            state.add_message("tool_result", error_msg)
