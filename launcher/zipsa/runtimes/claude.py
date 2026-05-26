"""Claude Code runtime implementation."""

import json
from pathlib import Path
from typing import Iterator, Optional

from .base import AgentRuntime
from . import register_runtime


@register_runtime("claude")
class ClaudeRuntime(AgentRuntime):
    """Claude Code CLI runtime."""

    @property
    def name(self) -> str:
        """Runtime identifier."""
        return "claude"

    # Placeholder sent to `claude --print` when the launcher passes an
    # empty user_input. Empty `--print ''` makes claude exit 1 immediately
    # (no user turn to respond to). This marker is meaningful: the agent
    # sees it as the user message, the runtime contract's "Empty
    # user_query" section tells it what to do.
    _EMPTY_USER_INPUT_PLACEHOLDER = (
        "[zipsa: no user_query provided — see the runtime contract's "
        "'Empty user_query' section]"
    )

    def build_command(
        self,
        skill_name: str,
        user_input: str,
        system_prompt: str,
        allowed_tools: str,
        workspace: Path,
        env: dict[str, str],
        mcp_debug_file: Optional[str] = None,
        extra_dirs: Optional[list[str]] = None,
        model: Optional[str] = None,
    ) -> list[str]:
        """Build Claude Code CLI command.

        Returns command array for Claude Code with all necessary flags.
        MCP servers are configured via .claude.json (mounted to /home/agent/.claude.json).

        If model is given, passes it to claude via --model so the actual
        runtime model matches what the manifest declares (used to be only
        used for pricing/limits).
        """
        effective_input = user_input if user_input else self._EMPTY_USER_INPUT_PLACEHOLDER
        cmd = [
            "claude",
            "--print",
            effective_input,
            "--allowedTools",
            allowed_tools,
            "--dangerously-skip-permissions",
            "--output-format=stream-json",
            "--verbose",
        ]
        if model:
            cmd.extend(["--model", model])
        for d in (extra_dirs or []):
            cmd.extend(["--add-dir", d])
        if mcp_debug_file:
            cmd.extend(["--debug", "--debug-file", mcp_debug_file])
        # Put --append-system-prompt LAST so all the short flags
        # (--allowedTools, --model, --debug, etc.) land first in
        # dry-run output. The system prompt is several hundred lines;
        # scrolling past it just to find the model flag is friction.
        cmd.extend(["--append-system-prompt", system_prompt])
        return cmd

    def parse_output(self, stream: Iterator[str]) -> Iterator[dict]:
        """Parse Claude Code stream-json output.

        Claude Code outputs one JSON object per line in stream-json format.
        Non-JSON lines are treated as plain text.

        Yields:
            Parsed JSON objects or text dictionaries
        """
        for line in stream:
            line = line.strip()
            if not line:
                continue

            try:
                # Try to parse as JSON
                yield json.loads(line)
            except json.JSONDecodeError:
                # Plain text output
                yield {"type": "text", "content": line}

    # ─────────────────────────────────────────────────────────────────────
    # Codec pair: parse_output (above) reads raw SDK stream into dicts.
    # format_event_compact (below) summarizes one such dict into a
    # ~280-char line for analysis tools.
    #
    # Co-located on purpose. If Claude SDK changes event shapes (new
    # block type, renamed field, …), BOTH halves need updates — keeping
    # them next to each other makes the dependency obvious and prevents
    # cross-file drift. Consumer of format_event_compact:
    #   core/run_log_handler.py (mcp__zipsa__read_run_log).
    # ─────────────────────────────────────────────────────────────────────

    # Per-event total ≈ 280 chars after capping (matches the 1.79MB /
    # 62-turn worst case we measured: ~17KB output, well under the
    # handler's 100KB output cap).
    _BLOCK_CAP_THINK = 200
    _BLOCK_CAP_TEXT = 200
    _BLOCK_CAP_TOOL_INPUT = 150
    _BLOCK_CAP_TOOL_RESULT = 150

    def format_event_compact(self, event: dict) -> Optional[str]:
        """Return a ~280-char one-line summary of an event, or None
        if the event has no useful payload to surface.

        Format vocabulary (kept stable so skill-builder's analysis
        instructions can rely on it):
          - `S: <subtype>`           — system event
          - `A: 💭 ... | 🔧 Name(args) | 💬 text` — assistant turn
          - `U: ✓ result`            — user tool_result
          - `R: cost=$X turns=N`     — final result
          - `<type>`                 — fallback marker for unknown types
        """
        try:
            t = event.get("type")
        except AttributeError:
            return None
        if t == "system":
            return f"S: {event.get('subtype', '?')}"
        if t == "result":
            cost = event.get("total_cost_usd", 0) or 0
            turns = event.get("num_turns", 0) or 0
            return f"R: cost=${cost:.4f} turns={turns}"
        if t == "assistant":
            return self._fmt_assistant(event)
        if t == "user":
            return self._fmt_user(event)
        # Unknown / minor events (rate_limit_event, text, …). Return a
        # short marker so the timeline isn't full of blanks.
        return t if isinstance(t, str) and len(t) < 60 else None

    def _fmt_assistant(self, event: dict) -> Optional[str]:
        msg = event.get("message") or {}
        content = msg.get("content") or []
        if not content:
            return None
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "thinking":
                t = (block.get("thinking") or "")[: self._BLOCK_CAP_THINK]
                parts.append(f"💭 {t}")
            elif bt == "tool_use":
                name = block.get("name", "?")
                args = block.get("input", {})
                args_str = json.dumps(args, ensure_ascii=False)[: self._BLOCK_CAP_TOOL_INPUT]
                parts.append(f"🔧 {name}({args_str})")
            elif bt == "text":
                txt = (block.get("text") or "")[: self._BLOCK_CAP_TEXT]
                parts.append(f"💬 {txt}")
            # Silently skip other block types (image, etc.) for now.
        if not parts:
            return None
        return "A: " + " | ".join(parts)

    def _fmt_user(self, event: dict) -> Optional[str]:
        msg = event.get("message") or {}
        content = msg.get("content") or []
        if not content:
            return None
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                inner = block.get("content")
                # Content can be a string or a list of {type:text, text:...}
                if isinstance(inner, str):
                    body = inner
                elif isinstance(inner, list):
                    body = " ".join(
                        (b.get("text") if isinstance(b, dict) else str(b))
                        or ""
                        for b in inner
                    )
                else:
                    body = str(inner)
                parts.append(f"✓ {body[: self._BLOCK_CAP_TOOL_RESULT]}")
        if not parts:
            return None
        return "U: " + " | ".join(parts)
