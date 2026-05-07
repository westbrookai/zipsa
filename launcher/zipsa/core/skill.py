"""Skill loader and configuration builder."""

import json
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
        skill_path = Path(skill_path).resolve()

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
                server_config = {
                    "type": "http",
                    "url": server.url,
                }
                if server.connection:
                    server_config["connection"] = server.connection
                config["mcpServers"][server.name] = server_config

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

    def build_claude_json(
        self,
        output_dir: Optional[Path] = None,
        container_workspace: str = "/home/agent/workspace",
    ) -> Path:
        """Generate .claude.json file for skill.

        Args:
            output_dir: Directory to write files into.
                        Defaults to ~/.zipsa/<name>@<version>/.
            container_workspace: Container working directory.
                        Stdio servers with mounts get their container path
                        auto-appended as /home/agent/workspace/<server-name>.

        Returns:
            Path to created .claude.json file
        """
        if output_dir is None:
            output_dir = (
                Path.home() / ".zipsa" / f"{self.name}@{self.manifest.metadata.version}"
            )

        output_dir.mkdir(parents=True, exist_ok=True)

        mcp_servers = {}
        for server in self.manifest.spec.mcp:
            if server.type == "stdio":
                args = list(server.args)
                if server.mount:
                    args.append(f"{container_workspace}/{server.name}")
                mcp_servers[server.name] = {
                    "command": server.command,
                    "args": args,
                }
            elif server.type == "http":
                server_config: dict = {
                    "type": "http",
                    "url": server.url,
                }
                if server.connection:
                    server_config["connection"] = server.connection
                if server.headersHelper:
                    server_config["headersHelper"] = server.headersHelper
                mcp_servers[server.name] = server_config

        claude_config = {
            "hasCompletedOnboarding": True,
            "projects": {
                container_workspace: {
                    "hasTrustDialogAccepted": True,
                    "mcpServers": mcp_servers,
                }
            },
        }

        config_text = json.dumps(claude_config, indent=2)
        claude_json_path = output_dir / ".claude.json"
        claude_json_path.write_text(config_text)
        (output_dir / ".claude.json.org").write_text(config_text)

        return claude_json_path
