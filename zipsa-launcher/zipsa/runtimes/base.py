"""Abstract base class for agent runtime plugins."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator


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
