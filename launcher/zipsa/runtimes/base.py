"""Abstract base class for agent runtime plugins."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator, Optional


class AgentRuntime(ABC):
    """Abstract base for agent runtimes (Claude, Codex, Gemini)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Runtime identifier (claude, codex, gemini).

        Returns:
            Runtime name string
        """
        pass

    @abstractmethod
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
        """Build CLI command for this runtime.

        Args:
            skill_name: Name of the skill being executed
            user_input: User's input/query
            system_prompt: System prompt (purpose + instructions + rules)
            allowed_tools: Comma-separated allowed tools
            workspace: Workspace path (in container)
            env: Environment variables

        Returns:
            Command array for subprocess execution
        """
        pass

    @abstractmethod
    def parse_output(self, stream: Iterator[str]) -> Iterator[dict]:
        """Parse runtime-specific output into common format.

        Args:
            stream: Iterator of output lines from runtime

        Yields:
            Dictionaries with common format:
            {"type": "text", "content": "..."}
            {"type": "tool_use", "name": "...", ...}
            etc.
        """
        pass

    def supports_mcp(self) -> bool:
        """Does this runtime support MCP?

        Returns:
            True if MCP is supported, False otherwise
        """
        return True

    def format_event_compact(self, event: dict) -> Optional[str]:
        """Summarize one parse_output event as a ~280-char line.

        Used by core/run_log_handler.py (mcp__zipsa__read_run_log) to
        let skill-builder analyze prior runs without dumping the raw
        stream into context. Co-locating this with parse_output (the
        sibling that produces these events) keeps the codec pair
        together — SDK shape changes update both methods in one edit.

        Default impl returns the type string as a marker. Each concrete
        runtime should override with a richer formatter.
        """
        t = event.get("type") if isinstance(event, dict) else None
        return t if isinstance(t, str) and len(t) < 60 else None
