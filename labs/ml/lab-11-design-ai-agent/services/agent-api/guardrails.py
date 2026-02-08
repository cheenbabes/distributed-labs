"""Input and output guardrails for the agent.

Guardrails protect the agent from:
1. Prompt injection attacks (malicious inputs)
2. Dangerous tool calls (destructive operations)
3. Runaway execution (too many steps or time)
"""
import re

# -----------------------------------------------
# Blocked input patterns (prompt injection)
# -----------------------------------------------
BLOCKED_PATTERNS = [
    r"ignore\s+(previous|all|above)\s+instructions",
    r"you\s+are\s+now",
    r"act\s+as\s+if",
    r"pretend\s+to\s+be",
    r"system\s*:\s*",
    r"new\s+instructions?\s*:",
    r"forget\s+(everything|all|your)",
    r"override\s+(your|the|all)",
    r"disregard\s+(previous|all|your)",
]

# -----------------------------------------------
# Blocked tool argument patterns
# -----------------------------------------------
BLOCKED_TOOLS_PATTERNS = [
    r"rm\s+-rf",
    r"drop\s+table",
    r"delete\s+from",
    r"format\s+c:",
    r"os\.system",
    r"subprocess",
    r"__import__",
    r"eval\s*\(",
    r"exec\s*\(",
]

MAX_INPUT_LENGTH = 2000


def validate_input(text: str) -> dict:
    """Validate user input for safety.

    Returns:
        dict with 'valid' (bool) and optionally 'reason' (str)
    """
    if not text or not text.strip():
        return {"valid": False, "reason": "Input is empty"}

    if len(text) > MAX_INPUT_LENGTH:
        return {
            "valid": False,
            "reason": f"Input too long ({len(text)} chars > {MAX_INPUT_LENGTH} max)"
        }

    text_lower = text.lower()
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, text_lower):
            return {
                "valid": False,
                "reason": "Input contains blocked pattern (possible prompt injection)"
            }

    return {"valid": True}


def validate_tool_call(call: dict) -> dict:
    """Validate a tool call before execution.

    Returns:
        dict with 'valid' (bool) and optionally 'reason' (str)
    """
    from tools import TOOLS

    tool_name = call.get("tool", "")
    if tool_name not in TOOLS:
        return {"valid": False, "reason": f"Unknown tool: {tool_name}"}

    # Check tool arguments for dangerous patterns
    args_str = str(call.get("args", {})).lower()
    for pattern in BLOCKED_TOOLS_PATTERNS:
        if re.search(pattern, args_str):
            return {
                "valid": False,
                "reason": "Tool arguments contain blocked pattern"
            }

    # Validate calculator expressions are not too long
    if tool_name == "calculator":
        expr = call.get("args", {}).get("expression", "")
        if len(expr) > 200:
            return {
                "valid": False,
                "reason": f"Calculator expression too long ({len(expr)} > 200 chars)"
            }

    return {"valid": True}


def check_execution_limits(steps: int, max_steps: int,
                           elapsed: float, max_time: float) -> dict:
    """Check if the agent has exceeded execution limits.

    Returns:
        dict with 'within_limits' (bool) and optionally 'reason' (str)
    """
    if steps >= max_steps:
        return {
            "within_limits": False,
            "reason": f"Max steps ({max_steps}) reached"
        }
    if elapsed >= max_time:
        return {
            "within_limits": False,
            "reason": f"Timeout ({max_time}s) reached"
        }
    return {"within_limits": True}
