"""Skill loader and configuration builder."""

from pathlib import Path
from typing import Optional
import yaml

from .models import SkillManifest


class Skill:
    """Skill definition loader and configuration builder."""

    def __init__(self, manifest: SkillManifest, skill_dir: Path):
        """Initialize skill with manifest and directory.

        Args:
            manifest: Validated skill manifest
            skill_dir: Path to skill directory
        """
        self.manifest = manifest
        self.skill_dir = skill_dir
        self._instructions: Optional[str] = None

    @classmethod
    def load(cls, skill_path: str | Path) -> "Skill":
        """Load skill from directory or manifest path.

        Args:
            skill_path: Path to skill directory or manifest.yaml file

        Returns:
            Loaded Skill instance

        Raises:
            FileNotFoundError: If manifest doesn't exist
        """
        skill_path = Path(skill_path)

        if skill_path.is_file():
            # Direct manifest.yaml path
            manifest_path = skill_path
            skill_dir = skill_path.parent
        else:
            # Directory path
            manifest_path = skill_path / "manifest.yaml"
            skill_dir = skill_path

        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")

        # Parse YAML
        with open(manifest_path) as f:
            data = yaml.safe_load(f)

        # Validate with Pydantic
        manifest = SkillManifest.model_validate(data)

        return cls(manifest, skill_dir)

    @property
    def name(self) -> str:
        """Get skill name from metadata."""
        return self.manifest.metadata.name

    @property
    def instructions(self) -> str:
        """Get skill instructions (lazy loaded from SKILL.md).

        Returns:
            Content of SKILL.md file
        """
        if self._instructions is None:
            instructions_path = self.skill_dir / self.manifest.spec.instructions
            self._instructions = instructions_path.read_text()
        return self._instructions

    def build_mcp_config(self) -> dict:
        """Generate MCP config JSON (passthrough from manifest).

        Returns:
            MCP config in Claude Code format:
            {
                "mcpServers": {
                    "server-name": {
                        "command": "npx",
                        "args": ["-y", "package"]
                    }
                }
            }
        """
        config = {"mcpServers": {}}

        for server in self.manifest.spec.mcp:
            if server.type == "stdio":
                config["mcpServers"][server.name] = {
                    "command": server.command,
                    "args": server.args,
                }
            elif server.type == "http":
                config["mcpServers"][server.name] = {
                    "type": "http",
                    "url": server.url,
                }

        return config

    def get_allowed_tools(self) -> str:
        """Build --allowedTools comma-separated string.

        Converts:
        - builtin: ["Read", "Write"] -> "Read,Write"
        - mcp: ["server:method"] -> "mcp__server__method"

        Returns:
            Comma-separated tool names
        """
        tools = []

        # Builtin tools (as-is)
        tools.extend(self.manifest.spec.tools.builtin)

        # MCP tools (convert "server:method" to "mcp__server__method")
        for mcp_tool in self.manifest.spec.tools.mcp:
            # Replace : with __
            tool_name = mcp_tool.replace(":", "__")
            tools.append(f"mcp__{tool_name}")

        return ",".join(tools)
