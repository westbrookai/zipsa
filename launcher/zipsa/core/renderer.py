"""Output renderer for skill execution events."""

import json
from enum import Enum
from typing import Iterator


class OutputMode(str, Enum):
    pretty = "pretty"
    answer = "answer"
    json = "json"


# ANSI color codes
_GRAY = "\033[90m"
_RESET = "\033[0m"


def render(events: Iterator[dict], mode: OutputMode) -> None:
    """Render an event stream to stdout according to the given mode."""
    if mode == OutputMode.json:
        for event in events:
            print(json.dumps(event, ensure_ascii=False))
        return

    turn = 0
    for event in events:
        result = _format(event, mode, turn)
        if result is None:
            continue
        if isinstance(result, tuple):
            output, turn = result
        else:
            output = result
        print(output)


def _format(event: dict, mode: OutputMode, turn: int) -> "str | tuple[str, int] | None":
    """Format a single event. Returns (output, new_turn), output string, or None to skip."""
    event_type = event.get("type")

    if event_type in ("system", "rate_limit_event"):
        return None

    if event_type == "assistant":
        message = event.get("message", {})
        content = message.get("content", [])
        if not content:
            return None
        block = content[0]
        block_type = block.get("type")

        if block_type == "thinking":
            turn += 1
            thinking = block.get("thinking", "")
            if mode == OutputMode.pretty:
                return (f"\n{_GRAY}[Turn {turn}]{_RESET}\n{_GRAY}Thinking:{_RESET} {thinking}", turn)
            return None

        elif block_type == "tool_use":
            if mode != OutputMode.pretty:
                return None
            name = block.get("name", "Unknown")
            inp = block.get("input", {})
            items = list(inp.items())[:3]
            args = "  ".join(f"{k}={str(v)[:80]}" for k, v in items)
            return f"\n{_GRAY}Tool:{_RESET} {name}\n  {args}"

        elif block_type == "text":
            text = block.get("text", "")
            if mode == OutputMode.pretty:
                turn += 1
                return (f"\n{_GRAY}[Turn {turn}]{_RESET}\n{_GRAY}Answer:{_RESET} {text}", turn)
            elif mode == OutputMode.answer:
                return text
            return None

    if event_type == "user":
        if mode != OutputMode.pretty:
            return None
        message = event.get("message", {})
        content = message.get("content", [])
        if not content or content[0].get("type") != "tool_result":
            return None
        tool_result = event.get("tool_use_result", {})
        if isinstance(tool_result, str):
            return f"{_GRAY}Result:{_RESET} {tool_result}"
        elif isinstance(tool_result, dict):
            if "result" in tool_result:
                return f"{_GRAY}Result:{_RESET} {tool_result['result']}"
            elif "matches" in tool_result:
                return f"{_GRAY}Result:{_RESET} Found {', '.join(tool_result['matches'])}"
            elif "code" in tool_result:
                code = tool_result.get("code")
                code_text = tool_result.get("codeText", "")
                return f"{_GRAY}Result:{_RESET} HTTP {code} {code_text}"
            else:
                return f"{_GRAY}Result:{_RESET} Success"
        first = content[0].get("content", "")
        if isinstance(first, str):
            return f"{_GRAY}Result:{_RESET} {first}"
        return f"{_GRAY}Result:{_RESET} Success"

    if event_type == "result":
        if mode != OutputMode.pretty:
            return None
        is_error = event.get("is_error", False)
        duration_s = event.get("duration_ms", 0) / 1000
        num_turns = event.get("num_turns", 0)
        cost = event.get("total_cost_usd", 0)
        status = "Error" if is_error else "Success"
        sep = "=" * 50
        return f"\n{sep}\n{status}\nDuration: {duration_s:.1f}s | Turns: {num_turns} | Cost: ${cost:.4f}\n{sep}"

    return None
