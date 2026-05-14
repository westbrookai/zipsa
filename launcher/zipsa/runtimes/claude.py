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
    ) -> list[str]:
        """Build Claude Code CLI command.

        Returns command array for Claude Code with all necessary flags.
        MCP servers are configured via .claude.json (mounted to /home/agent/.claude.json).
        """
        cmd = [
            "claude",
            "--print",
            user_input,
            "--append-system-prompt",
            system_prompt,
            "--allowedTools",
            allowed_tools,
            "--dangerously-skip-permissions",
            "--output-format=stream-json",
            "--verbose",
        ]
        for d in (extra_dirs or []):
            cmd.extend(["--add-dir", d])
        if mcp_debug_file:
            cmd.extend(["--debug", "--debug-file", mcp_debug_file])
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
