"""Docker executor for skill execution."""

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator, Optional

from .skill import Skill
from ..runtimes import get_runtime


class DockerExecutor:
    """Orchestrates Docker container execution for skills."""

    def __init__(
        self,
        runtime: str = "claude",
        image: str = "ghcr.io/westbrookai/zipsa-runtime:latest",
        workspace: Optional[Path] = None,
    ):
        """Initialize Docker executor.

        Args:
            runtime: Runtime to use (claude, codex, gemini)
            image: Docker image to run
            workspace: Workspace directory (defaults to current directory)
        """
        self.runtime = get_runtime(runtime)
        self.image = image
        self.workspace = workspace or Path.cwd()

    def run(
        self,
        skill: Skill,
        user_input: str,
        env: Optional[dict[str, str]] = None,
        dry_run: bool = False,
    ) -> Optional[Iterator[dict]]:
        """Execute skill in Docker container.

        Args:
            skill: Skill to execute
            user_input: User's input/query
            env: Environment variables
            dry_run: If True, print command without executing

        Returns:
            Iterator of parsed output events (None for dry_run)

        Raises:
            RuntimeError: If Docker execution fails
        """
        env = env or {}

        # Create temp directory in workspace for MCP config
        temp_dir = self.workspace / ".zipsa"
        temp_dir.mkdir(exist_ok=True)

        # Create temp MCP config file
        mcp_config_path = temp_dir / f"mcp-config-{id(self)}.json"
        mcp_config = skill.build_mcp_config()
        mcp_config_path.write_text(json.dumps(mcp_config))

        try:
            # Build Docker command
            docker_cmd = self._build_docker_command(
                skill, user_input, mcp_config_path, env
            )

            if dry_run:
                self._print_dry_run(skill, docker_cmd, mcp_config)
                return None

            # Execute and return generator
            return self._execute_skill(docker_cmd, mcp_config_path)

        except Exception:
            # Cleanup on error
            mcp_config_path.unlink(missing_ok=True)
            raise

    def _execute_skill(
        self, docker_cmd: list[str], mcp_config_path: Path
    ) -> Iterator[dict]:
        """Execute Docker command and stream output.

        Args:
            docker_cmd: Docker command array
            mcp_config_path: Path to temp MCP config file

        Yields:
            Parsed output events

        Raises:
            RuntimeError: If Docker execution fails
        """
        try:
            # Execute Docker
            process = subprocess.Popen(
                docker_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            # Stream output through runtime parser
            raw_stream = iter(process.stdout.readline, "")
            parsed_stream = self.runtime.parse_output(raw_stream)

            yield from parsed_stream

            process.wait()

            if process.returncode != 0:
                raise RuntimeError(
                    f"Docker execution failed with code {process.returncode}"
                )

        finally:
            # Cleanup temp MCP config file
            mcp_config_path.unlink(missing_ok=True)

    def _build_docker_command(
        self,
        skill: Skill,
        user_input: str,
        mcp_config_path: Path,
        env: dict[str, str],
    ) -> list[str]:
        """Build full docker run command.

        Args:
            skill: Skill being executed
            user_input: User input
            mcp_config_path: Path to temp MCP config (on host)
            env: Environment variables

        Returns:
            Command array for subprocess
        """
        cmd = [
            "docker",
            "run",
            "--rm",
            "--name",
            f"zipsa-{skill.name}-{id(self)}",
        ]

        # Environment variables
        for key, value in env.items():
            cmd.extend(["-e", f"{key}={value}"])

        # Volume mounts
        cmd.extend(
            [
                "-v",
                f"{self.workspace}:/workspace",
            ]
        )
        # NOTE: MCP config is now inside workspace, so no separate mount needed

        # Mount Claude credentials if they exist
        credentials_path = self.workspace / "credentials.json"
        claude_json_path = self.workspace / "claude.json"

        if credentials_path.exists():
            cmd.extend(["-v", f"{credentials_path}:/home/agent/.claude/.credentials.json"])

        if claude_json_path.exists():
            cmd.extend(["-v", f"{claude_json_path}:/home/agent/.claude.json:ro"])

        # MCP stdio mounts (from manifest)
        for server in skill.manifest.spec.mcp:
            if server.type == "stdio" and server.mount:
                host_path = Path(server.mount.host).expanduser().resolve()
                container_path = server.mount.container
                mode = server.mount.mode
                cmd.extend(["-v", f"{host_path}:{container_path}:{mode}"])

        # Image
        cmd.append(self.image)

        # Runtime-specific command (from plugin)
        system_prompt = self._build_system_prompt(skill)
        allowed_tools = skill.get_allowed_tools()

        # MCP config is inside workspace
        relative_config_path = mcp_config_path.relative_to(self.workspace)
        container_config_path = Path("/workspace") / relative_config_path

        runtime_cmd = self.runtime.build_command(
            skill_name=skill.name,
            user_input=user_input,
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            mcp_config_path=container_config_path,  # Container path inside /workspace
            workspace=Path("/workspace"),
            env=env,
        )

        cmd.extend(runtime_cmd)

        return cmd

    def _build_system_prompt(self, skill: Skill) -> str:
        """Build system prompt from skill metadata.

        Args:
            skill: Skill instance

        Returns:
            Complete system prompt string
        """
        return f"""You are the {skill.name} agent (v{skill.manifest.metadata.version}).

# Purpose
{skill.manifest.spec.purpose}

# Instructions
{skill.instructions}

# Available tools
You may ONLY use these tools: {skill.get_allowed_tools()}
If a task requires other tools, refuse politely.

# Behavior rules
- Single-task focused: only do what your purpose describes
- Be concise: no preamble, just answer
- Decline gracefully for off-topic requests
"""

    def _print_dry_run(self, skill: Skill, cmd: list[str], mcp_config: dict):
        """Print dry run information.

        Args:
            skill: Skill being executed
            cmd: Docker command
            mcp_config: MCP configuration
        """
        print("=== DRY RUN ===")
        print(f"Skill: {skill.name} v{skill.manifest.metadata.version}")
        print(f"Allowed tools: {skill.get_allowed_tools()}")
        print()
        print("MCP config:")
        print(json.dumps(mcp_config, indent=2))
        print()
        print("Docker command:")
        for i, arg in enumerate(cmd):
            # Mask sensitive environment variables
            if arg.startswith("CLAUDE_CODE_OAUTH_TOKEN="):
                key, value = arg.split("=", 1)
                masked = f"{value[:6]}..." if len(value) > 6 else value
                print(f"  [{i:2d}] {key}={masked}")
            else:
                print(f"  [{i:2d}] {arg}")
