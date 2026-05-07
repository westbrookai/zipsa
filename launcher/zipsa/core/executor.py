"""Docker executor for skill execution."""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional
from .skill import Skill
from ..runtimes import get_runtime


CONTAINER_WORKSPACE = "/home/agent/workspace"


class DockerExecutor:
    """Orchestrates Docker container execution for skills."""

    def __init__(
        self,
        runtime: str = "claude",
        image: str = "ghcr.io/westbrookai/zipsa-runtime:latest",
    ):
        """Initialize Docker executor.

        Args:
            runtime: Runtime to use (claude, codex, gemini)
            image: Docker image to run
        """
        self.runtime = get_runtime(runtime)
        self.image = image

    def run(
        self,
        skill: Skill,
        user_input: str,
        env: Optional[dict[str, str]] = None,
        dry_run: bool = False,
        shell: bool = False,
        mcp_debug: bool = False,
        extra_docker_opts: Optional[list[str]] = None,
    ) -> Optional[Iterator[dict]]:
        """Execute skill in Docker container.

        Args:
            skill: Skill to execute
            user_input: User's input/query
            env: Environment variables
            dry_run: If True, print command without executing
            shell: If True, start interactive bash instead of running skill

        Returns:
            Iterator of parsed output events (None for dry_run or shell)

        Raises:
            RuntimeError: If Docker execution fails
        """
        env = env or {}

        # Auto-extract environment variables from MCP servers
        for server in skill.manifest.spec.mcp:
            for env_var in server.env:
                # Only add if not already set and exists in host environment
                if env_var not in env:
                    import os
                    if env_var in os.environ:
                        env[env_var] = os.environ[env_var]
                    else:
                        print(f"Warning: MCP server '{server.name}' requires environment variable '{env_var}' but it's not set")

        # Centralized skill data directory: ~/.zipsa/<name>@<version>/
        skill_data_dir = (
            Path.home() / ".zipsa" / f"{skill.name}@{skill.manifest.metadata.version}"
        )
        skill_data_dir.mkdir(parents=True, exist_ok=True)

        # Create run directory for logging (skip for dry-run and shell mode)
        run_dir = None
        if not dry_run and not shell:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")[:23]
            run_dir = skill_data_dir / "runs" / timestamp
            run_dir.mkdir(parents=True, exist_ok=True)

        # Generate .claude.json in centralized directory
        claude_json_path = skill.build_claude_json(
            output_dir=skill_data_dir,
            container_workspace=CONTAINER_WORKSPACE,
        )

        # Prepare MCP debug file path (host side) if requested
        mcp_debug_host = None
        if mcp_debug and run_dir:
            mcp_debug_host = run_dir / "mcp-debug.log"
            mcp_debug_host.touch()

        env_file = skill_data_dir / ".env"
        try:
            # Build Docker command (also writes env file)
            docker_cmd = self._build_docker_command(
                skill, user_input, claude_json_path, env, shell=shell,
                mcp_debug_host=mcp_debug_host,
                extra_docker_opts=extra_docker_opts,
            )

            if dry_run:
                mcp_config = json.loads(claude_json_path.read_text())
                self._print_dry_run(skill, docker_cmd, mcp_config)
                return None

            if shell:
                self._run_shell(docker_cmd, claude_json_path)
                return None

            # Execute and return generator
            return self._execute_skill(docker_cmd, claude_json_path, skill, run_dir, env_file)

        except Exception:
            raise
        finally:
            # Clean up env file for dry_run and shell modes
            # (normal execution cleanup happens inside _execute_skill)
            if (dry_run or shell) and env_file.exists():
                env_file.unlink()

    def _execute_skill(
        self,
        docker_cmd: list[str],
        claude_json_path: Path,
        skill: Skill,
        run_dir: Optional[Path],
        env_file: Optional[Path] = None,
    ) -> Iterator[dict]:
        """Execute Docker command and stream output.

        Args:
            docker_cmd: Docker command array
            claude_json_path: Path to .claude.json file (not cleaned up after execution)
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

            # Clean up env file (contains secrets, should not persist)
            if env_file and env_file.exists():
                env_file.unlink()

    def _run_shell(self, docker_cmd: list[str], claude_json_path: Path) -> None:
        """Run interactive bash shell in Docker container.

        Args:
            docker_cmd: Docker command array (with -it and bash)
            claude_json_path: Path to .claude.json file (not cleaned up after execution)
        """
        # Execute Docker with interactive shell
        print("Starting interactive bash shell in container...")
        print(".claude.json and mounts are ready. Type 'exit' to quit.")
        print()
        subprocess.run(docker_cmd)

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

    def _write_env_file(self, output_dir: Path, env: dict[str, str]) -> Path:
        """Write env vars to output_dir/.env and return the path."""
        output_dir.mkdir(parents=True, exist_ok=True)
        env_file = output_dir / ".env"
        with open(env_file, "w") as f:
            for key, value in env.items():
                f.write(f"{key}={value}\n")
        return env_file

    def _build_docker_command(
        self,
        skill: Skill,
        user_input: str,
        claude_json_path: Path,
        env: dict[str, str],
        shell: bool = False,
        mcp_debug_host: Optional[Path] = None,
        extra_docker_opts: Optional[list[str]] = None,
    ) -> list[str]:
        """Build full docker run command.

        Args:
            skill: Skill being executed
            user_input: User input
            claude_json_path: Path to .claude.json file (on host)
            env: Environment variables
            shell: If True, build command for interactive shell

        Returns:
            Command array for subprocess
        """
        cmd = [
            "docker",
            "run",
            "--rm",
        ]

        # Extra docker options (e.g. port forwarding for OAuth flows)
        if extra_docker_opts:
            cmd.extend(extra_docker_opts)

        # Add interactive TTY flags for shell mode
        if shell:
            cmd.extend(["-it"])

        cmd.extend([
            "--name",
            f"zipsa-{skill.name}-{id(self)}",
        ])

        # Global env file (~/.zipsa/.env) takes lower precedence, added first
        global_env_file = Path.home() / ".zipsa" / ".env"
        if global_env_file.exists():
            cmd.extend(["--env-file", str(global_env_file)])

        # Per-execution env file (~/.zipsa/<name>@<version>/.env), added last to take precedence
        skill_data_dir = (
            Path.home() / ".zipsa" / f"{skill.name}@{skill.manifest.metadata.version}"
        )
        env_file = self._write_env_file(skill_data_dir, env)
        cmd.extend(["--env-file", str(env_file)])

        # Mount .claude.json (contains MCP config + onboarding settings)
        # Note: Must be writable - claude updates this file during execution
        cmd.extend(["-v", f"{claude_json_path}:/home/agent/.claude.json"])

        # Mount .claude.json.org (read-only original for comparison)
        claude_json_org_path = claude_json_path.parent / ".claude.json.org"
        if claude_json_org_path.exists():
            cmd.extend(["-v", f"{claude_json_org_path}:/home/agent/.claude.json.org:ro"])

        # Mount global credentials if they exist
        # Note: Must be writable - claude may refresh tokens during execution
        global_creds = Path.home() / ".zipsa" / ".credentials.json"
        if global_creds.exists():
            cmd.extend(["-v", f"{global_creds}:/home/agent/.claude/.credentials.json"])

        # MCP stdio mounts (from manifest) — container path auto-generated
        for server in skill.manifest.spec.mcp:
            if server.type == "stdio" and server.mount:
                host_path = Path(server.mount.host).expanduser().resolve()
                container_path = f"{CONTAINER_WORKSPACE}/{server.name}"
                mode = server.mount.mode
                cmd.extend(["-v", f"{host_path}:{container_path}:{mode}"])

        # MCP debug file mount (bind-mount host log file into container)
        if mcp_debug_host:
            cmd.extend(["-v", f"{mcp_debug_host}:/home/agent/mcp-debug.log"])

        # Image
        cmd.append(self.image)

        # Shell mode: just run bash
        if shell:
            cmd.append("bash")
        else:
            # Runtime-specific command (from plugin)
            system_prompt = self._build_system_prompt(skill)
            allowed_tools = skill.get_allowed_tools()
            mcp_debug_container = "/home/agent/mcp-debug.log" if mcp_debug_host else None

            # Collect container paths for all stdio MCP mounts so Claude Code
            # includes them in its ListRoots response (needed by secure-filesystem-server)
            extra_dirs = [
                f"{CONTAINER_WORKSPACE}/{server.name}"
                for server in skill.manifest.spec.mcp
                if server.type == "stdio" and server.mount
            ]

            # MCP config is now in .claude.json (mounted to /home/agent/.claude.json)
            runtime_cmd = self.runtime.build_command(
                skill_name=skill.name,
                user_input=user_input,
                system_prompt=system_prompt,
                allowed_tools=allowed_tools,
                workspace=Path(CONTAINER_WORKSPACE),
                env=env,
                mcp_debug_file=mcp_debug_container,
                extra_dirs=extra_dirs,
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
        mcp_paths_section = ""
        mounted_servers = [
            s for s in skill.manifest.spec.mcp
            if s.type == "stdio" and s.mount
        ]
        if mounted_servers:
            lines = ["# MCP Server Paths"]
            for server in mounted_servers:
                lines.append(f"- {server.name}: {CONTAINER_WORKSPACE}/{server.name}")
            mcp_paths_section = "\n".join(lines) + "\n\n"

        return f"""You are the {skill.name} agent (v{skill.manifest.metadata.version}).

# Purpose
{skill.manifest.spec.purpose}

# Instructions
{skill.instructions}

{mcp_paths_section}# Available tools
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
        # Show env file keys (values hidden)
        env_file = Path.home() / ".zipsa" / f"{skill.name}@{skill.manifest.metadata.version}" / ".env"
        if env_file.exists():
            keys = [line.split("=")[0] for line in env_file.read_text().splitlines() if "=" in line]
            if keys:
                print("Environment (from env file):")
                for key in keys:
                    print(f"  {key}=***")
                print()

        print("Docker command:")
        for i, arg in enumerate(cmd):
            print(f"  [{i:2d}] {arg}")
