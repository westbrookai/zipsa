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
        - spec.tools.builtin: ["Read", "Write"] -> "Read,Write"
        - spec.mcp[n].allowed_tools: ["read_file"] -> "mcp__<server>__read_file"

        Returns:
            Comma-separated tool names
        """
        tools = list(self.manifest.spec.tools.builtin)

        for server in self.manifest.spec.mcp:
            for tool in server.allowed_tools:
                tools.append(f"mcp__{server.name}__{tool}")

        return ",".join(tools)

    def build_claude_json(
        self,
        output_dir: Optional[Path] = None,
        container_workspace: str = "/home/agent/workspace",
        hitl_port: Optional[int] = None,
        mcp_url_override: Optional[str] = None,
        mcp_token_override: Optional[str] = None,
    ) -> Path:
        """Generate .claude.json file for skill.

        Args:
            output_dir: Directory to write files into.
                        Defaults to ~/.zipsa/<name>@<version>/.
            container_workspace: Container working directory.
                        Stdio servers with mounts get their container path
                        auto-appended as /home/agent/workspace/<server-name>.
            hitl_port: Host port for the zipsa HITL MCP server. When set, an
                        additional `zipsa` HTTP MCP entry is injected so the
                        agent in the container can reach the host-side server
                        via host.docker.internal. The Authorization header is
                        produced at runtime from the ZIPSA_HITL_TOKEN env var.
            mcp_url_override: When running as a child skill, the parent's MCP
                        URL to use instead of generating a localhost URL. Must
                        be set together with mcp_token_override.
            mcp_token_override: When running as a child skill, the parent-
                        supplied token to embed directly in the headersHelper.
                        Must be set together with mcp_url_override.

        Returns:
            Path to created .claude.json file

        Raises:
            ValueError: If only one of mcp_url_override / mcp_token_override
                        is provided (both must be set together).
        """
        # Validate override pair — both must be set or neither.
        if (mcp_url_override is None) != (mcp_token_override is None):
            if mcp_url_override is None:
                raise ValueError(
                    "mcp_url_override must be provided when mcp_token_override is set"
                )
            else:
                raise ValueError(
                    "mcp_token_override must be provided when mcp_url_override is set"
                )
        if output_dir is None:
            from zipsa.paths import skill_data_dir as _skill_data_dir
            output_dir = _skill_data_dir(self.name, self.manifest.metadata.version)

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
                # Auto-generate headersHelper for oauth2 servers if not explicitly set
                headers_helper = server.headersHelper
                if not headers_helper and server.auth and server.auth.type == "oauth2":
                    token_var = f"ZIPSA_TOKEN_{server.name.upper().replace('-', '_')}"
                    headers_helper = f'echo "{{\\"Authorization\\": \\"Bearer ${token_var}\\"}}"'
                if headers_helper:
                    server_config["headersHelper"] = headers_helper
                mcp_servers[server.name] = server_config

        if mcp_url_override is not None:
            # Child skill path: use the parent's URL and embed the token directly.
            mcp_servers["zipsa"] = {
                "type": "http",
                "url": mcp_url_override,
                "headersHelper": (
                    f'echo \'{{"Authorization": "Bearer {mcp_token_override}"}}\''
                ),
            }
        elif hitl_port is not None:
            # Top-level run path: point at our own HitlServer; token is read
            # from the ZIPSA_HITL_TOKEN env var at runtime.
            mcp_servers["zipsa"] = {
                "type": "http",
                "url": f"http://host.docker.internal:{hitl_port}/mcp",
                "headersHelper": (
                    'echo \'{"Authorization": "Bearer \'"$ZIPSA_HITL_TOKEN"\'"}\''
                ),
            }

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
        # Use atomic write (temp + rename) so a concurrent Docker container
        # that mounts this directory never sees a partially-written file.
        # This is especially important on macOS/colima where bind-mount
        # changes from the host may be visible to the VM mid-write.
        _tmp = claude_json_path.with_suffix(".json.tmp")
        _tmp.write_text(config_text)
        _tmp.replace(claude_json_path)
        (output_dir / ".claude.json.org").write_text(config_text)

        # Hooks live in ~/.claude/settings.json, not .claude.json. The executor
        # copies this file into the container so the PreToolUse hook is wired up.
        settings_config = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/zipsa-hooks/pretooluse.py",
                            }
                        ],
                    }
                ]
            }
        }
        (output_dir / "settings.json").write_text(json.dumps(settings_config, indent=2))

        return claude_json_path
