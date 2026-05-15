"""Docker executor for skill execution."""

import json
import os
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional
from .skill import Skill
from ..runtimes import get_runtime
from ..auth.oauth import OAuthManager
from .. import paths as zipsa_paths


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
                    if env_var in os.environ:
                        env[env_var] = os.environ[env_var]
                    else:
                        print(f"Warning: MCP server '{server.name}' requires environment variable '{env_var}' but it's not set")

        # OAuth pre-flight: ensure tokens for all oauth2 HTTP servers (skip for dry-run)
        if not dry_run:
            self._ensure_oauth_credentials(skill, env)

        # Centralized skill data directory: ~/.zipsa/<name>@<version>/
        skill_data_dir = zipsa_paths.skill_data_dir(skill.name, skill.manifest.metadata.version)
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

            # Multi-phase: delegate to phase loop
            if skill.manifest.spec.phases:
                return self._execute_phases(
                    skill, user_input, env, run_dir, claude_json_path,
                    mcp_debug, extra_docker_opts,
                )

            # Single-phase: existing path
            return self._execute_skill(docker_cmd, claude_json_path, skill, run_dir, env_file, user_input)

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
        user_input: str = "",
    ) -> Iterator[dict]:
        """Execute Docker command and stream output.

        Args:
            docker_cmd: Docker command array
            claude_json_path: Path to .claude.json file (not cleaned up after execution)
            skill: Skill being executed
            run_dir: Directory to save run logs (None to skip logging)
            env_file: Environment file path (optional)
            user_input: User's input/query for this execution

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
                    self._save_metadata(run_dir, skill, cost_exceeded, limits, user_input=user_input)
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

    def _save_metadata(self, run_dir: Path, skill: Skill, cost_exceeded: bool = False, limits=None, user_input: str = "") -> None:
        """Extract metrics from output.jsonl and save to metadata.json.

        Extracts execution metrics from the result event.

        Args:
            run_dir: Run directory containing output.jsonl
            skill: Skill that was executed
            cost_exceeded: Whether cost limit was exceeded
            limits: Skill limits configuration
            user_input: User's input/query for this execution
        """
        output_file = run_dir / "output.jsonl"
        metadata_file = run_dir / "metadata.json"

        if not output_file.exists():
            return

        # Scan all events: find result event and last assistant text
        result_event = None
        last_assistant_text = None
        with open(output_file, 'r') as f:
            for line in f:
                try:
                    event = json.loads(line.strip())
                    event_type = event.get("type")
                    if event_type == "result":
                        result_event = event
                    elif event_type == "assistant":
                        content = event.get("message", {}).get("content", [])
                        for block in content:
                            if block.get("type") == "text":
                                last_assistant_text = block.get("text", "")
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
                "error": "No result event found - execution may have failed",
                "user_input": user_input
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
                "model_usage": result_event.get("modelUsage", {}),
                "user_input": user_input
            }

        # Override is_error if skill's final JSON reports a non-ok status.
        # Claude Code reports is_error=False for a clean process exit even when
        # the skill itself returned status=failed in its contract JSON output.
        skill_out = self._extract_skill_output(last_assistant_text)
        skill_status = skill_out.get("status") if skill_out else None
        if skill_status and skill_status != "ok":
            metadata["is_error"] = True
            metadata["skill_status"] = skill_status

        # Add limit information if applicable
        if limits and limits.max_cost_usd:
            metadata["cost_exceeded"] = cost_exceeded
            metadata["cost_limit_usd"] = limits.max_cost_usd

        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)

    def _load_skill_state(self, skill: Skill) -> dict:
        state_file = zipsa_paths.skill_data_dir(
            skill.name, skill.manifest.metadata.version
        ) / "state.json"
        if state_file.exists():
            try:
                return json.loads(state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _apply_skill_state(self, skill: Skill, updates: dict) -> None:
        state = self._load_skill_state(skill)
        for key, value in updates.items():
            if value is None:
                state.pop(key, None)
            else:
                state[key] = value
        state_file = zipsa_paths.skill_data_dir(
            skill.name, skill.manifest.metadata.version
        ) / "state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    def _execute_phases(
        self,
        skill: Skill,
        user_input: str,
        env: dict[str, str],
        run_dir: Optional[Path],
        claude_json_path: Path,
        mcp_debug: bool,
        extra_docker_opts: Optional[list[str]],
    ) -> Iterator[dict]:
        from .models import PhaseSpec

        phases = skill.manifest.spec.phases
        if not phases:
            # Implicit single-phase wrapper for legacy skills
            phases = [PhaseSpec(
                id="main",
                goal=skill.manifest.spec.purpose,
                allowed_tools=skill.get_allowed_tools().split(",") if skill.get_allowed_tools() else [],
                limits=skill.manifest.spec.limits,
            )]

        agg_limits = skill.manifest.spec.limits
        cumulative_turns = 0
        cumulative_cost = 0.0
        previous_output = None
        skill_state = self._load_skill_state(skill)
        last_phase_out: dict | None = None
        skill_data_dir = zipsa_paths.skill_data_dir(skill.name, skill.manifest.metadata.version)

        # Create ephemeral npm cache volume shared across all phases.
        # Only created when the skill declares stdio MCP servers (which use npx).
        npm_volume: str | None = None
        has_stdio_mcp = any(s.type == "stdio" for s in skill.manifest.spec.mcp)
        if has_stdio_mcp:
            npm_volume = f"zipsa-{skill.name}-{id(self)}-npm"
            subprocess.run(
                ["docker", "volume", "create", npm_volume],
                check=True,
                capture_output=True,
            )

        try:
            for phase_idx, phase in enumerate(phases):
                retries = 0
                user_answer = None

                # Aggregate limit check before starting (and before printing the header)
                if agg_limits:
                    if agg_limits.max_turns and cumulative_turns >= agg_limits.max_turns:
                        yield {"type": "zipsa_phase_error", "phase": phase.id,
                               "error": "Aggregate max_turns exceeded"}
                        return
                    if agg_limits.max_cost_usd and cumulative_cost >= agg_limits.max_cost_usd:
                        yield {"type": "zipsa_phase_error", "phase": phase.id,
                               "error": f"Aggregate cost limit exceeded (${cumulative_cost:.4f} >= ${agg_limits.max_cost_usd:.2f})"}
                        return

                yield {
                    "type": "zipsa_phase_start",
                    "phase": phase.id,
                    "phase_idx": phase_idx,
                    "total_phases": len(phases),
                    "goal": phase.goal,
                }

                while True:
                    phase_allowed_tools = ",".join(phase.allowed_tools)
                    self._write_phase_allow_file(
                        claude_json_path.parent, phase.id, list(phase.allowed_tools),
                    )
                    user_message = self._build_user_message(
                        skill, phase.id, phase.goal, phase_allowed_tools,
                        previous_output, skill_state, user_input, user_answer,
                    )

                    # Per-phase artifact directory
                    dir_name = f"{phase_idx}-{phase.id}"
                    if retries > 0:
                        dir_name = f"{dir_name}.retry-{retries}"
                    phase_dir = (run_dir / "phases" / dir_name) if run_dir else None
                    if phase_dir:
                        phase_dir.mkdir(parents=True, exist_ok=True)

                    docker_cmd = self._build_docker_command(
                        skill, user_message, claude_json_path, env,
                        allowed_tools_override=phase_allowed_tools,
                        extra_docker_opts=extra_docker_opts,
                        phase_id=phase.id,
                        npm_volume=npm_volume,
                    )

                    # Stream events, capture last assistant text + metrics
                    last_assistant_text = None
                    timed_out = False
                    try:
                        for event in self._execute_skill(
                            docker_cmd, claude_json_path, skill, phase_dir,
                            skill_data_dir / ".env", user_message,
                        ):
                            if event.get("type") == "assistant":
                                content = event.get("message", {}).get("content", [])
                                for block in content:
                                    if block.get("type") == "text":
                                        last_assistant_text = block.get("text", "")
                                        break
                            elif event.get("type") == "result":
                                cumulative_turns += event.get("num_turns", 0) or 0
                                cumulative_cost += event.get("total_cost_usd", 0) or 0
                            yield event
                    except RuntimeError as exc:
                        if "timed out" in str(exc).lower():
                            timed_out = True
                            yield {"type": "zipsa_phase_error", "phase": phase.id,
                                   "error": str(exc)}
                            return  # state_updates NOT applied on timeout
                        raise

                    if timed_out:
                        return

                    # Extract and validate skill output
                    phase_out = self._extract_skill_output(last_assistant_text) or {}

                    # Phase field validation
                    reported_phase = phase_out.get("phase")
                    if reported_phase and reported_phase != phase.id:
                        phase_out = {
                            "status": "failed",
                            "phase": phase.id,
                            "result": None,
                            "state_updates": None,
                            "next_phase_input": None,
                            "user_facing_summary": (
                                f"Phase ID mismatch: expected '{phase.id}', got '{reported_phase}'"
                            ),
                            "needs_input": None,
                            "error": {"code": "phase_id_mismatch"},
                        }

                    status = phase_out.get("status", "failed")

                    if status == "ok":
                        if phase_out.get("state_updates"):
                            self._apply_skill_state(skill, phase_out["state_updates"])
                            skill_state = self._load_skill_state(skill)
                        previous_output = phase_out.get("next_phase_input")
                        last_phase_out = phase_out
                        break

                    elif status == "needs_input":
                        if retries >= 3:
                            yield {"type": "zipsa_phase_error", "phase": phase.id,
                                   "error": "needs_input exceeded 3 retries"}
                            return
                        question = phase_out.get("needs_input", {})
                        prompt_text = question.get("prompt") or question.get("question") or str(question)
                        print(f"\n[{phase.id}] {prompt_text}")
                        user_answer = input("> ").strip()
                        retries += 1
                        continue  # per-phase limits reset, aggregate accumulates

                    else:  # failed | out_of_scope — state_updates NOT applied
                        last_phase_out = phase_out
                        yield {"type": "zipsa_phase_error", "phase": phase.id,
                               "status": status, "output": phase_out}
                        return

            # All phases completed
            if last_phase_out:
                yield {"type": "zipsa_run_complete", "result": last_phase_out}
        finally:
            if npm_volume:
                subprocess.run(
                    ["docker", "volume", "rm", npm_volume],
                    capture_output=True,
                )

    def _write_env_file(self, output_dir: Path, env: dict[str, str]) -> Path:
        """Write env vars to output_dir/.env and return the path."""
        output_dir.mkdir(parents=True, exist_ok=True)
        env_file = output_dir / ".env"
        with open(env_file, "w") as f:
            for key, value in env.items():
                f.write(f"{key}={value}\n")
        env_file.chmod(0o600)
        return env_file

    def _write_phase_allow_file(
        self,
        output_dir: Path,
        phase_id: str,
        allowed_tools: list[str],
    ) -> Path:
        """Write the per-phase tool allow list consumed by the PreToolUse hook.

        The file lives next to .claude.json so it's already covered by the
        /.zipsa read-only mount.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "phase-allow.json"
        path.write_text(json.dumps({"phase_id": phase_id, "allowed_tools": allowed_tools}))
        return path

    def _ensure_oauth_credentials(self, skill: "Skill", env: dict[str, str]) -> None:
        """Inject ZIPSA_TOKEN_<NAME> for all oauth2 HTTP servers that lack a token in env."""
        oauth_servers = [
            s for s in skill.manifest.spec.mcp
            if s.type == "http" and getattr(s, "auth", None) and s.auth.type == "oauth2"
        ]
        if not oauth_servers:
            return

        manager = OAuthManager()
        print("Checking credentials...")

        for server in oauth_servers:
            token_var = f"ZIPSA_TOKEN_{server.name.upper().replace('-', '_')}"
            if token_var in env:
                print(f"  {server.name}: token already set")
                continue
            token = manager.ensure_credentials(server.name, server.url)
            env[token_var] = token
            print(f"  {server.name}: authorized")

    def _build_docker_command(
        self,
        skill: Skill,
        user_input: str,
        claude_json_path: Path,
        env: dict[str, str],
        shell: bool = False,
        mcp_debug_host: Optional[Path] = None,
        extra_docker_opts: Optional[list[str]] = None,
        allowed_tools_override: Optional[str] = None,
        phase_id: Optional[str] = None,
        npm_volume: Optional[str] = None,
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
        # Each opt is shell-split so "-p 56535:56535" works as well as ["-p", "56535:56535"]
        for opt in (extra_docker_opts or []):
            cmd.extend(shlex.split(opt))

        # Add interactive TTY flags for shell mode
        if shell:
            cmd.extend(["-it"])

        name_parts = ["zipsa", skill.name]
        if phase_id:
            name_parts.append(phase_id)
        name_parts.append(str(id(self)))
        cmd.extend(["--name", "-".join(name_parts)])

        # Global env file (~/.zipsa/.env) takes lower precedence, added first
        _global_env = zipsa_paths.global_env_file()
        if _global_env.exists():
            cmd.extend(["--env-file", str(_global_env)])

        # Per-execution env file (~/.zipsa/<name>@<version>/.env), added last to take precedence
        skill_data_dir = zipsa_paths.skill_data_dir(skill.name, skill.manifest.metadata.version)
        env_file = self._write_env_file(skill_data_dir, env)
        cmd.extend(["--env-file", str(env_file)])

        # Mount skill data directory read-only to /.zipsa.
        # .claude.json is copied into the container overlay FS at startup (see bash wrapper
        # below) so Claude Code can atomically rename it without hitting EBUSY — which occurs
        # when the file itself is a bind-mount point.
        skill_data_dir = claude_json_path.parent
        cmd.extend(["-v", f"{skill_data_dir}:/.zipsa:ro"])

        # Mount the PreToolUse hook script (read-only). The hook reads
        # /.zipsa/phase-allow.json (regenerated per phase) to enforce the
        # phase's tool whitelist and Bash command prefixes.
        hook_script = Path(__file__).parent.parent / "hooks" / "pretooluse.py"
        cmd.extend(["-v", f"{hook_script}:/zipsa-hooks/pretooluse.py:ro"])

        # Ephemeral npm cache volume shared across phases (avoids re-downloading per phase)
        if npm_volume:
            cmd.extend(["-v", f"{npm_volume}:/npm-cache", "-e", "NPM_CONFIG_CACHE=/npm-cache"])

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

        # Preamble copies .claude.json from the read-only /.zipsa mount into the
        # container's overlay FS so Claude Code can atomically rename it.
        cp_preamble = "cp /.zipsa/.claude.json /home/agent/.claude.json"

        if shell:
            # exec replaces the copy-shell with a fresh interactive bash
            cmd.extend(["bash", "-c", f"{cp_preamble} && exec bash"])
        else:
            # Runtime-specific command (from plugin)
            system_prompt = self._build_system_prompt(skill)
            allowed_tools = allowed_tools_override if allowed_tools_override is not None else skill.get_allowed_tools()
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

            cmd.extend(["bash", "-c", f"{cp_preamble} && {shlex.join(runtime_cmd)}"])

        return cmd

    def _build_system_prompt(self, skill: Skill) -> str:
        prompts_dir = Path(__file__).parent.parent / "system-prompts"
        contract = (prompts_dir / "runtime-contract.md").read_text(encoding="utf-8")
        template = (prompts_dir / "system-prompt-template.md").read_text(encoding="utf-8")

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

        skill_body = f"""You are the {skill.name} agent (v{skill.manifest.metadata.version}).

# Purpose
{skill.manifest.spec.purpose}

# Instructions
{skill.instructions}

{mcp_paths_section}# Behavior rules
- Single-task focused: only do what your purpose describes
- Be concise: no preamble, just answer
- Decline gracefully for off-topic requests
"""

        meta = skill.manifest.metadata
        return template.format(
            contract=contract,
            skill_name=meta.name,
            skill_version=meta.version,
            skill_body=skill_body,
        )

    def _build_user_message(
        self,
        skill: Skill,
        phase_id: str,
        phase_goal: str,
        phase_allowed_tools: str,
        previous_phase_output: str | None,
        skill_state: dict,
        user_query: str,
        user_answer: str | None = None,
    ) -> str:
        prompts_dir = Path(__file__).parent.parent / "system-prompts"
        template = (prompts_dir / "user-message-template.md").read_text(encoding="utf-8")

        now = datetime.now().astimezone()
        tz_offset = now.strftime("%z")
        tz_offset_fmt = f"UTC{tz_offset[:3]}:{tz_offset[3:]}"

        query = user_query
        if user_answer is not None:
            query = f"{user_query}\n\nuser_answer: {user_answer}"

        config_json = json.dumps(skill.manifest.spec.config, ensure_ascii=False)
        state_json = json.dumps(skill_state, ensure_ascii=False)
        prev_output = json.dumps(previous_phase_output, ensure_ascii=False)

        return template.format(
            date=now.strftime("%Y-%m-%d"),
            time=now.strftime("%H:%M:%S"),
            timezone=f"{now.strftime('%Z')} ({tz_offset_fmt})",
            phase_id=phase_id,
            phase_goal=phase_goal,
            allowed_tools=phase_allowed_tools,
            previous_phase_output=prev_output,
            skill_state=state_json,
            user_query=query,
            config=config_json,
        )

    @staticmethod
    def _extract_skill_output(text: str | None) -> dict | None:
        """Extract skill contract JSON from final assistant text.

        Tries four strategies in order:
        1. Direct json.loads on stripped text
        2. Extract last ```json ... ``` fenced block
        3. Find last {...} object containing a "status" key
        4. Fail with error.code=invalid_output_format
        """
        import re
        if not text:
            return None

        # Strategy 1: direct parse
        try:
            data = json.loads(text.strip())
            if isinstance(data, dict) and "status" in data:
                return data
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 2: last ```json ... ``` block
        blocks = re.findall(r"```json\s*(.*?)```", text, re.DOTALL)
        if blocks:
            try:
                data = json.loads(blocks[-1].strip())
                if isinstance(data, dict) and "status" in data:
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

        # Strategy 3: last {...} containing "status"
        brace_matches = list(re.finditer(r"\{", text))
        for m in reversed(brace_matches):
            depth = 0
            for i, ch in enumerate(text[m.start():]):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[m.start(): m.start() + i + 1]
                        try:
                            data = json.loads(candidate)
                            if isinstance(data, dict) and "status" in data:
                                return data
                        except (json.JSONDecodeError, ValueError):
                            pass
                        break

        # Strategy 4: failed to extract
        return {
            "status": "failed",
            "phase": "unknown",
            "result": None,
            "state_updates": None,
            "next_phase_input": None,
            "user_facing_summary": "Skill output could not be parsed.",
            "needs_input": None,
            "error": {
                "code": "invalid_output_format",
                "raw_output": (text or "")[:2000],
            },
        }

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
        env_file = zipsa_paths.skill_env_file(skill.name, skill.manifest.metadata.version)
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
