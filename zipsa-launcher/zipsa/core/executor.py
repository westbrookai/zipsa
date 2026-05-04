"""Docker executor for skill execution."""

import json
import subprocess
import sys
import tempfile
from datetime import datetime
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

        # Create run directory for logging (skip for dry-run)
        run_dir = None
        if not dry_run:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")[:23]
            run_dir = skill.skill_dir / ".zipsa" / "runs" / timestamp
            run_dir.mkdir(parents=True, exist_ok=True)

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
            return self._execute_skill(docker_cmd, mcp_config_path, skill, run_dir)

        except Exception:
            # Cleanup on error
            mcp_config_path.unlink(missing_ok=True)
            raise
        finally:
            # Cleanup temp file for dry_run (non-dry_run cleanup is in _execute_skill)
            if dry_run:
                mcp_config_path.unlink(missing_ok=True)

    def _execute_skill(
        self, docker_cmd: list[str], mcp_config_path: Path, skill: Skill, run_dir: Optional[Path]
    ) -> Iterator[dict]:
        """Execute Docker command and stream output.

        Args:
            docker_cmd: Docker command array
            mcp_config_path: Path to temp MCP config file
            skill: Skill being executed
            run_dir: Directory to save run logs (None to skip logging)

        Yields:
            Parsed output events

        Raises:
            RuntimeError: If Docker execution fails or limits exceeded
        """
        output_file = None
        if run_dir:
            output_file = run_dir / "output.jsonl"

        # Get limits from skill manifest
        limits = skill.manifest.spec.limits
        turn_count = 0
        cost_exceeded = False
        process = None

        try:
            # Execute Docker
            process = subprocess.Popen(
                docker_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            # Stream output through runtime parser with limits checking
            raw_stream = iter(process.stdout.readline, "")

            # Save to file while parsing
            if output_file:
                with open(output_file, 'w', buffering=1) as f:  # Line buffering
                    for line in raw_stream:
                        if line:
                            # Write to file
                            f.write(line)

                            # Parse and yield
                            parsed_events = self.runtime.parse_output([line])
                            for event in parsed_events:
                                # Check max_turns limit
                                if limits and limits.max_turns:
                                    if event.get("type") == "assistant":
                                        message = event.get("message", {})
                                        content = message.get("content", [])
                                        if content and content[0].get("type") == "thinking":
                                            turn_count += 1
                                            if turn_count > limits.max_turns:
                                                # Gracefully terminate, then force kill if needed
                                                process.terminate()
                                                try:
                                                    process.wait(timeout=5)
                                                except subprocess.TimeoutExpired:
                                                    process.kill()
                                                    process.wait()
                                                raise RuntimeError(
                                                    f"Exceeded max_turns: {limits.max_turns}"
                                                )

                                # Check max_cost limit (post-execution validation)
                                if limits and limits.max_cost_usd:
                                    if event.get("type") == "result":
                                        actual_cost = event.get("total_cost_usd", 0)
                                        if actual_cost > limits.max_cost_usd:
                                            cost_exceeded = True
                                            print(
                                                f"Warning: Cost ${actual_cost:.4f} exceeded limit ${limits.max_cost_usd:.4f}",
                                                file=sys.stderr
                                            )

                                yield event
            else:
                # No logging - just parse and yield with limits checking
                for line in raw_stream:
                    if line:
                        parsed_events = self.runtime.parse_output([line])
                        for event in parsed_events:
                            # Check max_turns limit
                            if limits and limits.max_turns:
                                if event.get("type") == "assistant":
                                    message = event.get("message", {})
                                    content = message.get("content", [])
                                    if content and content[0].get("type") == "thinking":
                                        turn_count += 1
                                        if turn_count > limits.max_turns:
                                            # Gracefully terminate, then force kill if needed
                                            process.terminate()
                                            try:
                                                process.wait(timeout=5)
                                            except subprocess.TimeoutExpired:
                                                process.kill()
                                                process.wait()
                                            raise RuntimeError(
                                                f"Exceeded max_turns: {limits.max_turns}"
                                            )

                            # Check max_cost limit
                            if limits and limits.max_cost_usd:
                                if event.get("type") == "result":
                                    actual_cost = event.get("total_cost_usd", 0)
                                    if actual_cost > limits.max_cost_usd:
                                        cost_exceeded = True
                                        print(
                                            f"Warning: Cost ${actual_cost:.4f} exceeded limit ${limits.max_cost_usd:.4f}",
                                            file=sys.stderr
                                        )

                            yield event

            # Wait for process with timeout
            timeout = limits.timeout_seconds if limits else None
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Gracefully terminate, then force kill if needed
                process.terminate()
                try:
                    process.wait(timeout=5)  # Wait up to 5 seconds for graceful shutdown
                except subprocess.TimeoutExpired:
                    process.kill()  # Force kill if it doesn't terminate
                    process.wait()
                raise RuntimeError(
                    f"Execution timed out after {timeout} seconds"
                )

            if process.returncode != 0:
                raise RuntimeError(
                    f"Docker execution failed with code {process.returncode}"
                )

        except KeyboardInterrupt:
            # User pressed Ctrl+C - terminate Docker process
            if process:
                print("\nInterrupted by user - terminating execution...", file=sys.stderr)
                process.terminate()
                try:
                    process.wait(timeout=5)  # Wait up to 5 seconds for graceful shutdown
                except subprocess.TimeoutExpired:
                    process.kill()  # Force kill if it doesn't terminate
                    process.wait()
            raise

        finally:
            # Ensure Docker process is terminated
            if process and process.poll() is None:  # Process still running
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                except Exception:
                    pass  # Best effort cleanup

            # Generate summary and metadata if logging enabled
            if run_dir:
                try:
                    self._save_summary(run_dir)
                    self._save_metadata(run_dir, skill, cost_exceeded, limits)
                except Exception as e:
                    # Don't fail execution due to logging errors
                    print(f"Warning: Failed to save run logs: {e}", file=sys.stderr)

            # Cleanup temp MCP config file
            mcp_config_path.unlink(missing_ok=True)

    def _save_summary(self, run_dir: Path) -> None:
        """Generate summary.jsonl from output.jsonl.

        Filters for important events only:
        - system (init only)
        - assistant (all)
        - user (all)
        - result (all)
        - any event with "error" in type

        Args:
            run_dir: Run directory containing output.jsonl
        """
        output_file = run_dir / "output.jsonl"
        summary_file = run_dir / "summary.jsonl"

        if not output_file.exists():
            return

        important_types = {"system", "assistant", "user", "result"}

        with open(output_file, 'r') as inf, open(summary_file, 'w') as outf:
            for line in inf:
                line = line.strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                    event_type = event.get("type", "")

                    # Include important events
                    if event_type in important_types or "error" in event_type.lower():
                        # For system events, only keep init
                        if event_type == "system":
                            if event.get("subtype") == "init":
                                outf.write(line + '\n')
                        else:
                            outf.write(line + '\n')
                except json.JSONDecodeError:
                    # Skip malformed lines
                    continue

    def _save_metadata(self, run_dir: Path, skill: Skill, cost_exceeded: bool = False, limits = None) -> None:
        """Extract metrics from output.jsonl and save to metadata.json.

        Extracts execution metrics from the result event.

        Args:
            run_dir: Run directory containing output.jsonl
            skill: Skill that was executed
            cost_exceeded: Whether cost limit was exceeded
            limits: Skill limits configuration
        """
        output_file = run_dir / "output.jsonl"
        metadata_file = run_dir / "metadata.json"

        if not output_file.exists():
            return

        # Find result event
        result_event = None
        with open(output_file, 'r') as f:
            for line in f:
                try:
                    event = json.loads(line.strip())
                    if event.get("type") == "result":
                        result_event = event
                        break
                except json.JSONDecodeError:
                    continue

        if not result_event:
            # Execution failed before result
            metadata = {
                "run_id": run_dir.name,
                "skill_name": skill.name,
                "skill_version": skill.manifest.metadata.version,
                "timestamp": datetime.now().isoformat(),
                "is_error": True,
                "error": "No result event found - execution may have failed"
            }
        else:
            # Extract from result event
            metadata = {
                "run_id": run_dir.name,
                "skill_name": skill.name,
                "skill_version": skill.manifest.metadata.version,
                "timestamp": datetime.now().isoformat(),
                "duration_ms": result_event.get("duration_ms"),
                "duration_api_ms": result_event.get("duration_api_ms"),
                "num_turns": result_event.get("num_turns"),
                "total_cost_usd": result_event.get("total_cost_usd"),
                "is_error": result_event.get("is_error", False),
                "stop_reason": result_event.get("stop_reason"),
                "terminal_reason": result_event.get("terminal_reason"),
                "usage": result_event.get("usage", {}),
                "model_usage": result_event.get("modelUsage", {})
            }

        # Add limit information if applicable
        if limits and limits.max_cost_usd:
            metadata["cost_exceeded"] = cost_exceeded
            metadata["cost_limit_usd"] = limits.max_cost_usd

        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)

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

        # Mount global credentials if they exist
        global_creds = Path.home() / ".zipsa" / ".credentials.json"
        if global_creds.exists():
            cmd.extend(["-v", f"{global_creds}:/home/agent/.claude/.credentials.json:ro"])

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
