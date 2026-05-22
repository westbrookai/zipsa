"""Output renderer for skill execution events."""

import json
import re
from enum import Enum
from pathlib import Path
from typing import Iterator, Optional


class OutputMode(str, Enum):
    pretty = "pretty"
    answer = "answer"
    json = "json"


# ANSI color codes
_GRAY = "\033[90m"
_CYAN = "\033[96m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _extract_envelope(text: Optional[str]) -> Optional[dict]:
    """Parse the skill-contract JSON envelope out of an agent text block.

    The skill contract requires the agent to end every phase with a JSON
    object: {status, phase, result, state_updates, user_facing_summary,
    error}. We peek at it so the footer can surface the fields the skill
    explicitly produced for the user — not just SDK metrics.

    Returns the parsed dict if found, else None. Tolerant of either a
    ```json fenced block or raw text body.
    """
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*\n(.+?)\n\s*```", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    try:
        obj = json.loads(text.strip())
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    return None


def _extract_phase_status(text: Optional[str]) -> Optional[str]:
    """Back-compat wrapper around _extract_envelope. Returns the phase
    'status' field if the envelope parses, else None."""
    env = _extract_envelope(text)
    if env is None:
        return None
    status = env.get("status")
    return status if isinstance(status, str) else None


def _format_size(num_bytes: int) -> str:
    """Human-readable file size — single-decimal KB/MB; whole-number B."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def _format_result_compact(result: dict) -> str:
    """Render the envelope's `result` dict as 'k=v, k=v' lines.

    Skips nested dicts/lists (only scalar values surface here — anything
    structural belongs in an artifact, not the inline footer). Truncates
    long string values to keep one line readable.
    """
    if not isinstance(result, dict):
        return str(result)
    parts: list[str] = []
    for k, v in result.items():
        if isinstance(v, (dict, list)):
            continue
        if isinstance(v, str) and len(v) > 60:
            v = v[:57] + "..."
        parts.append(f"{k}={v}")
    return ", ".join(parts)


def render(events: Iterator[dict], mode: OutputMode) -> None:
    """Render an event stream to stdout according to the given mode."""
    if mode == OutputMode.json:
        for event in events:
            print(json.dumps(event, ensure_ascii=False), flush=True)
        return

    turn = 0
    last_text: Optional[str] = None
    for event in events:
        # Track the last assistant text block so the result event can
        # peek at the phase contract's status.
        if event.get("type") == "assistant":
            blocks = event.get("message", {}).get("content", [])
            for b in blocks:
                if b.get("type") == "text":
                    last_text = b.get("text", "")
        # A new phase resets the tracked text — each phase has its own outcome.
        elif event.get("type") == "zipsa_phase_start":
            last_text = None

        result = _format(event, mode, turn, last_text=last_text)
        if result is None:
            continue
        if isinstance(result, tuple):
            output, turn = result
        else:
            output = result
        print(output, flush=True)


def _format(
    event: dict,
    mode: OutputMode,
    turn: int,
    last_text: Optional[str] = None,
) -> "str | tuple[str, int] | None":
    """Format a single event. Returns (output, new_turn), output string, or None to skip."""
    event_type = event.get("type")

    if event_type in ("system", "rate_limit_event"):
        return None

    if event_type == "zipsa_limits_breach":
        if mode == OutputMode.json:
            return None  # printed verbatim in json mode at the top of render()
        scope = event.get("scope", "?")
        kind = event.get("kind", "?")
        value = event.get("value", 0)
        limit = event.get("limit", 0)
        phase = event.get("phase", "?")
        if kind == "cost":
            value_s = f"${value:.4f}"
            limit_s = f"${limit:.4f}"
        elif kind == "time":
            value_s = f"{value:.1f}s"
            limit_s = f"{limit:.1f}s"
        else:  # turns
            value_s = f"{int(value)} turns"
            limit_s = f"{int(limit)} turns"
        return (
            f"\n{_RED}✗ Limit exceeded — {scope} {kind} for phase '{phase}': "
            f"{value_s} > {limit_s}{_RESET}"
        )

    if event_type == "zipsa_phase_error":
        if mode == OutputMode.json:
            return None  # already printed in json mode above
        phase_id = event.get("phase", "?")
        error = event.get("error", "unknown error")
        return f"\n\033[91m✗ Phase '{phase_id}' aborted: {error}\033[0m"

    if event_type == "zipsa_phase_start":
        if mode != OutputMode.pretty:
            return None
        phase_id = event.get("phase", "")
        phase_idx = event.get("phase_idx", 0)
        total = event.get("total_phases", 1)
        goal = event.get("goal", "")
        bar = "━" * 50
        header = f"{_CYAN}{_BOLD}━━━ Phase {phase_idx + 1}/{total}: {phase_id} {bar}{_RESET}"
        return (f"\n{header}\n{_GRAY}{goal}{_RESET}", 0)

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
            if name.startswith("mcp__zipsa__"):
                inp = block.get("input", {}) or {}
                short = name[len("mcp__zipsa__"):]
                if short in ("ask", "confirm", "choose"):
                    # Always-asks tools — the MCP server prints the prompt block
                    return f"\n{_GRAY}[asking user]{_RESET}"
                if short == "ask_once":
                    # May or may not actually ask depending on cache state — show
                    # the key so the reader can correlate with prompt (or its absence)
                    key = inp.get("key", "?")
                    return f"\n{_GRAY}[ask_once: {key}]{_RESET}"
                if short in ("recall", "remember", "forget"):
                    key = inp.get("key", "?")
                    return f"\n{_GRAY}[memory: {short} {key}]{_RESET}"
                if short == "list_memory":
                    scope = inp.get("scope", "skill")
                    return f"\n{_GRAY}[memory: list ({scope})]{_RESET}"
                # Unknown future zipsa tool — generic marker
                return f"\n{_GRAY}[{short}]{_RESET}"
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

    if event_type == "result":
        if mode != OutputMode.pretty:
            return None
        is_error = event.get("is_error", False)
        duration_s = event.get("duration_ms", 0) / 1000
        num_turns = event.get("num_turns", 0)
        cost = event.get("total_cost_usd", 0)
        # Prefer the phase's contract status over the SDK's is_error.
        # `is_error` is True only if the SDK call itself blew up; a
        # phase that returns status=failed in its JSON still has
        # is_error=False. The user cares about the phase outcome.
        envelope = _extract_envelope(last_text)
        phase_status = envelope.get("status") if envelope else None
        if phase_status == "failed":
            status = f"{_RED}Failed{_RESET}"
        elif phase_status == "out_of_scope":
            status = f"{_YELLOW}Out of scope{_RESET}"
        elif is_error:
            status = f"{_RED}Error{_RESET}"
        else:
            status = "Success"
        sep = "=" * 50
        lines = [sep, status]
        if envelope:
            ufs = envelope.get("user_facing_summary")
            if isinstance(ufs, str) and ufs.strip():
                lines.append(f"{_GRAY}Summary:{_RESET} {ufs}")
            result_obj = envelope.get("result")
            if isinstance(result_obj, dict) and result_obj:
                compact = _format_result_compact(result_obj)
                if compact:
                    lines.append(f"{_GRAY}Result:{_RESET} {compact}")
        lines.append(f"Duration: {duration_s:.1f}s | Turns: {num_turns} | Cost: ${cost:.4f}")
        lines.append(sep)
        return "\n" + "\n".join(lines)

    if event_type == "zipsa_run_complete":
        if mode != OutputMode.pretty:
            return None
        run_dir_s = event.get("run_dir")
        if not run_dir_s:
            return None
        run_dir = Path(run_dir_s)
        artifacts_dir = run_dir / "artifacts"
        lines = [f"\n{_GRAY}Run dir:{_RESET} {run_dir}"]
        if artifacts_dir.exists():
            files = sorted(
                p for p in artifacts_dir.iterdir() if p.is_file()
            )
            if files:
                lines.append(f"{_GRAY}Artifacts:{_RESET}")
                for f in files:
                    try:
                        size = _format_size(f.stat().st_size)
                    except OSError:
                        size = "?"
                    lines.append(f"  {f.name} ({size})")
            else:
                lines.append(f"{_GRAY}Artifacts:{_RESET} (none)")
        return "\n".join(lines)

    return None
