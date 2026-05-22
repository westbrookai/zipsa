"""Docker executor for skill execution."""

import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional
from .dev_overlay import load_dev_overlay
from .limits import LimitBreach, LimitsState, SkillLimits, check_limits, new_state, update_for_event
from .output_parser import extract_skill_output as _extract_skill_output_impl
from .phase_allow import (
    ALWAYS_ON_TOOLS as _ALWAYS_ON_TOOLS_LIST,
    merge_always_on_tools as _merge_always_on_tools_impl,
    write_default_phase_allow_file as _write_default_phase_allow_file_impl,
    write_phase_allow_file as _write_phase_allow_file_impl,
)
from .phase_state import (
    load_resume_state as _load_resume_state_impl,
    write_phase_state as _write_phase_state_impl,
)
from .prompts import (
    build_system_prompt as _build_system_prompt_impl,
    build_user_message as _build_user_message_impl,
)
from .skill import Skill
from .summary import PhaseSummary, build_summary, write_summary
from ..runtimes import get_runtime
from ..auth.oauth import OAuthManager
from .. import paths as zipsa_paths


CONTAINER_WORKSPACE = "/home/agent/workspace"


class MountCollisionError(ValueError):
    """Raised when two dynamic mount entries resolve to the same container path."""


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
        self.dev_overlay = load_dev_overlay()
        # HITL state — set by _execute_skill / _execute_phases before
        # _build_docker_command is invoked.
        self._hitl_port: Optional[int] = None
        self._hitl_token: Optional[str] = None
        # HitlIO reference for summary-time read of accumulated wait
        # time. Set by run() right after constructing the HitlIO.
        self._hitl_io = None
        # Resolved requires values — set by run() from the kwarg.
        self._requires_values: dict[str, object] = {}
        # Image env-var cache (filled on first _get_image_env call). Same
        # tag → same env, so once per process is enough.
        self._image_env_cache: dict[str, dict[str, str]] = {}
        if self.dev_overlay is not None:
            desc = self.dev_overlay.description or "(no description)"
            mounts_n = len(self.dev_overlay.mounts)
            preamble_n = (
                len(self.dev_overlay.preamble)
                if isinstance(self.dev_overlay.preamble, list)
                else (1 if self.dev_overlay.preamble else 0)
            )
            env_n = len(self.dev_overlay.env)
            print(
                f"[zipsa] dev overlay active: {desc} "
                f"(mounts={mounts_n}, preamble={preamble_n}, env={env_n})",
                file=sys.stderr,
            )

    @staticmethod
    def _detect_parent_mcp() -> tuple[Optional[str], Optional[str]]:
        """Detect whether we are running as a child skill invoked by a parent.

        Returns:
            (parent_url, parent_token) if ZIPSA_PARENT_MCP_URL and
            ZIPSA_PARENT_MCP_TOKEN are both set in the environment;
            (None, None) otherwise (top-level run).
        """
        return (
            os.environ.get("ZIPSA_PARENT_MCP_URL"),
            os.environ.get("ZIPSA_PARENT_MCP_TOKEN"),
        )

    def run(
        self,
        skill: Skill,
        user_input: str,
        env: Optional[dict[str, str]] = None,
        dry_run: bool = False,
        shell: bool = False,
        mcp_debug: bool = False,
        extra_docker_opts: Optional[list[str]] = None,
        requires_values: Optional[dict[str, object]] = None,
        resume_from: Optional[int] = None,
        resume_from_run_dir: Optional[Path] = None,
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
        self._requires_values = requires_values or {}

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
            # Prime the image-env cache once per run (~50-200ms first call,
            # free afterwards). Surfaces ZIPSA_RUNTIME_VERSION and
            # CLAUDE_CODE_VERSION in summary.json for debugging.
            self._get_image_env(self.image)

        # Centralized skill data directory: ~/.zipsa/<name>@<version>/
        skill_data_dir = zipsa_paths.skill_data_dir(skill.name, skill.manifest.metadata.version)
        skill_data_dir.mkdir(parents=True, exist_ok=True)

        # Capture the run start time and create run directory for logging
        # (skip for dry-run and shell mode)
        started_at = datetime.now().astimezone()
        run_dir = None
        if not dry_run and not shell:
            timestamp = started_at.strftime("%Y-%m-%d_%H%M%S_%f")[:23]
            run_dir = skill_data_dir / "runs" / timestamp
            run_dir.mkdir(parents=True, exist_ok=True)
            self._ensure_run_artifacts_dir(run_dir)

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

        if dry_run or shell:
            try:
                # Build Docker command (also writes env file)
                docker_cmd = self._build_docker_command(
                    skill, user_input, claude_json_path, env, shell=shell,
                    mcp_debug_host=mcp_debug_host,
                    extra_docker_opts=extra_docker_opts,
                    requires_values=self._requires_values,
                )

                if dry_run:
                    mcp_config = json.loads(claude_json_path.read_text())
                    self._print_dry_run(skill, docker_cmd, mcp_config)
                    return None

                # shell mode: Hook needs an allow file even in shell mode (where
                # the user may invoke claude manually). Use the skill's full
                # tool set.
                _write_default_phase_allow_file_impl(claude_json_path.parent, skill)
                self._run_shell(docker_cmd, claude_json_path)
                return None
            finally:
                # Clean up env file for dry_run and shell modes
                if env_file.exists():
                    env_file.unlink()

        # Real execution: hand off to a generator that owns the HitlServer
        # lifecycle so the server stays up while the caller consumes events.
        return self._execute_with_hitl(
            skill=skill,
            user_input=user_input,
            env=env,
            run_dir=run_dir,
            skill_data_dir=skill_data_dir,
            mcp_debug_host=mcp_debug_host,
            extra_docker_opts=extra_docker_opts,
            env_file=env_file,
            started_at=started_at,
            resume_from=resume_from,
            resume_from_run_dir=resume_from_run_dir,
        )

    def _execute_with_hitl(
        self,
        skill: Skill,
        user_input: str,
        env: dict[str, str],
        run_dir: Optional[Path],
        skill_data_dir: Path,
        mcp_debug_host: Optional[Path],
        extra_docker_opts: Optional[list[str]],
        env_file: Path,
        started_at: Optional[datetime] = None,
        resume_from: Optional[int] = None,
        resume_from_run_dir: Optional[Path] = None,
    ) -> Iterator[dict]:
        """Wrap real execution with HitlServer lifecycle.

        Starts the HITL HTTP MCP server, then (re)builds .claude.json so it
        includes the zipsa MCP entry pointing at the host-side server. The
        server stays up for the full duration of event consumption, then
        stops in the finally block.

        Tracks run-level final status across all code paths and writes
        summary.json to run_dir at the end of every run (best-effort).
        Yields a zipsa_run_complete event as the last event of every run.
        """
        from .hitl_mcp import HitlIO
        from .hitl_runner import HitlServer
        from .memory_store import MemoryStore

        if started_at is None:
            started_at = datetime.now().astimezone()

        # Run-level final status tracking — defaults represent infra_failed
        # (Docker crash, exception, etc.) and are overwritten as the run progresses.
        final_status = "infra_failed"
        final_exit_code = 5
        final_error: Optional[dict] = None
        final_result: Optional[dict] = None
        phase_summaries: list[PhaseSummary] = []
        # Resume / chain metadata. None / 0.0 on fresh runs; populated by
        # _execute_phases via the zipsa_run_complete event when a resume
        # took place. See build_summary docstring for the contract.
        chain_started_at: Optional[str] = None
        resumed_from_id: Optional[str] = None
        accumulated_hitl_wait: float = 0.0
        # Last result event from the Claude SDK stream — used at summary time
        # to extract stop_reason / usage / model_usage (folded in from the
        # old metadata.json so summary.json is the single source of truth).
        last_result_event: Optional[dict] = None
        # Model and Claude CLI version from the first `system.init`
        # event. Claude SDK already reports these in init's payload
        # (`model` and `claude_code_version` fields), so no separate
        # docker inspect needed — saves the ~50-200ms per-run round-trip.
        actual_model: Optional[str] = None
        actual_claude_version: Optional[str] = None
        # Limits state ref for cost/turns at summary time (set by inner generators)
        _limits_state_ref: list[Optional[LimitsState]] = [None]

        stdout_lock = threading.Lock()
        hitl_io = HitlIO(
            stdin=sys.stdin,
            stdout=sys.stdout,
            stdout_lock=stdout_lock,
            is_interactive=sys.stdin.isatty(),
        )
        # Expose for summary-builder to read accumulated wait time.
        self._hitl_io = hitl_io
        # Per-skill memory is cross-version: lives at
        # ~/.zipsa/memory/<skill>/skill-mem.json. resolve_skill_memory_path
        # silently migrates from the latest legacy per-version location
        # (~/.zipsa/<skill>@<ver>/memory/skill-mem.json) on first run after
        # upgrade so user values captured via ask_once survive version bumps.
        skill_memory_path = zipsa_paths.resolve_skill_memory_path(skill.name)
        global_memory_path = zipsa_paths.zipsa_home() / "memory" / "global-mem.json"
        skill_store = MemoryStore(skill_memory_path)
        global_store = MemoryStore(global_memory_path)

        # Detect whether we are running as a child skill invoked by a parent.
        # If so, skip spawning our own HitlServer and point the container at
        # the parent's server using the env-supplied URL and token.
        parent_url, parent_token = self._detect_parent_mcp()
        if parent_url and parent_token:
            hitl_server = None
            self._hitl_port = None
            self._hitl_token = None
            mcp_url_override: Optional[str] = parent_url
            mcp_token_override: Optional[str] = parent_token
        else:
            from .caller_context import CallerInfo
            hitl_server = HitlServer(
                hitl_io,
                skill_store=skill_store,
                global_store=global_store,
                primary_caller=CallerInfo(skill.name, skill.manifest.metadata.version),
            )
            hitl_server.start()
            self._hitl_port = hitl_server.port
            self._hitl_token = hitl_server.token
            mcp_url_override = None
            mcp_token_override = None

        try:
            # Rebuild .claude.json so it includes the zipsa MCP entry.
            # When running as a child skill, use the parent's URL + token
            # directly; for top-level runs, use our own HitlServer's port.
            claude_json_path = skill.build_claude_json(
                output_dir=skill_data_dir,
                container_workspace=CONTAINER_WORKSPACE,
                hitl_port=self._hitl_port,
                mcp_url_override=mcp_url_override,
                mcp_token_override=mcp_token_override,
            )

            if skill.manifest.spec.phases:
                # Multi-phase path: _execute_phases tracks status internally and
                # yields a zipsa_run_complete event as its last event.
                for event in self._execute_phases(
                    skill, user_input, env, run_dir, claude_json_path,
                    mcp_debug_host is not None, extra_docker_opts,
                    _limits_state_ref=_limits_state_ref,
                    resume_from=resume_from,
                    resume_from_run_dir=resume_from_run_dir,
                ):
                    # Capture final status from the complete event or breach event
                    etype = event.get("type")
                    if etype == "result":
                        last_result_event = event
                    elif etype == "system" and event.get("subtype") == "init":
                        if not actual_model:
                            actual_model = event.get("model")
                        if not actual_claude_version:
                            actual_claude_version = event.get("claude_code_version")
                    elif etype == "zipsa_limits_breach":
                        final_status = "limits_exceeded"
                        final_exit_code = 3
                        final_error = {
                            "code": "limits_exceeded",
                            "message": (
                                f"phase {event.get('kind')}: "
                                f"{event.get('value')} > {event.get('limit')}"
                            ),
                            "details": {
                                "scope": event.get("scope"),
                                "kind": event.get("kind"),
                                "value": event.get("value"),
                                "limit": event.get("limit"),
                                "phase": event.get("phase"),
                            },
                        }
                    elif etype == "zipsa_run_complete":
                        final_status = event.get("status", "infra_failed")
                        final_exit_code = event.get("exit_code", 5)
                        final_result = event.get("result")
                        final_error = event.get("error")
                        phase_summaries = event.get("_phase_summaries", [])
                        chain_started_at = event.get("_chain_started_at")
                        resumed_from_id = event.get("_resumed_from")
                        accumulated_hitl_wait = event.get("_accumulated_hitl_wait", 0.0)
                    yield event
                return

            # Single-phase path: execute skill directly.
            # PreToolUse hook needs phase-allow.json too.
            _write_default_phase_allow_file_impl(claude_json_path.parent, skill)
            docker_cmd = self._build_docker_command(
                skill, user_input, claude_json_path, env,
                mcp_debug_host=mcp_debug_host,
                extra_docker_opts=extra_docker_opts,
                requires_values=self._requires_values,
                run_dir=run_dir,
            )
            # Shared limits state for the single phase — needed for summary cost/turns.
            single_limits_state = new_state("main")
            _limits_state_ref[0] = single_limits_state
            last_assistant_text = None
            for event in self._execute_skill(
                docker_cmd, claude_json_path, skill, run_dir, env_file, user_input,
                limits_state=single_limits_state,
            ):
                etype = event.get("type")
                if etype == "assistant":
                    content = event.get("message", {}).get("content", [])
                    for block in content:
                        if block.get("type") == "text":
                            last_assistant_text = block.get("text", "")
                            break
                elif etype == "result":
                    last_result_event = event
                elif etype == "system" and event.get("subtype") == "init":
                    if not actual_model:
                        actual_model = event.get("model")
                    if not actual_claude_version:
                        actual_claude_version = event.get("claude_code_version")
                elif etype == "zipsa_limits_breach":
                    final_status = "limits_exceeded"
                    final_exit_code = 3
                    final_error = {
                        "code": "limits_exceeded",
                        "message": (
                            f"phase {event.get('kind')}: "
                            f"{event.get('value')} > {event.get('limit')}"
                        ),
                        "details": {
                            "scope": event.get("scope"),
                            "kind": event.get("kind"),
                            "value": event.get("value"),
                            "limit": event.get("limit"),
                            "phase": event.get("phase"),
                        },
                    }
                    phase_summaries.append(PhaseSummary(
                        id="main",
                        status="limits_exceeded",
                        cost_usd=single_limits_state.phase_cost_usd,
                        turns=single_limits_state.phase_turns,
                    ))
                    yield event
                    # Breach terminates the run — emit run_complete and return
                    complete_event = {
                        "type": "zipsa_run_complete",
                        "status": final_status,
                        "exit_code": final_exit_code,
                    }
                    yield complete_event
                    return
                yield event

            # Determine final status from skill's contract JSON output
            phase_out = _extract_skill_output_impl(last_assistant_text)
            if phase_out is None:
                # Parse failed — preserve the real cause + raw text.
                phase_out = {
                    "status": "failed",
                    "phase": "main",
                    "result": None,
                    "state_updates": None,
                    "next_phase_input": None,
                    "user_facing_summary": (
                        "Skill output could not be parsed."
                    ),
                    "error": {
                        "code": "invalid_output_format",
                        "raw_output": (last_assistant_text or "")[:2000],
                    },
                }
            status = phase_out.get("status", "failed")

            # Map HITL-related error codes to user_declined (exit 4)
            error_code = (phase_out.get("error") or {}).get("code", "")
            if status in ("failed", "out_of_scope"):
                if error_code in ("hitl_unattended", "user_declined"):
                    final_status = "user_declined"
                    final_exit_code = 4
                    final_error = phase_out.get("error")
                elif status == "out_of_scope":
                    final_status = "out_of_scope"
                    final_exit_code = 2
                    final_error = phase_out.get("error") or {
                        "code": "out_of_scope",
                        "message": phase_out.get("user_facing_summary", ""),
                    }
                else:
                    final_status = "failed"
                    final_exit_code = 1
                    final_error = phase_out.get("error") or {
                        "code": "failed",
                        "message": phase_out.get("user_facing_summary", ""),
                    }
            elif status == "ok":
                final_status = "ok"
                final_exit_code = 0
                final_result = phase_out.get("result")
                final_error = None
            # else: stays infra_failed (should not happen if Docker returned 0)

            # Append single-phase summary
            phase_summaries.append(PhaseSummary(
                id="main",
                status=final_status,
                cost_usd=single_limits_state.phase_cost_usd,
                turns=single_limits_state.phase_turns,
            ))

            complete_event = {
                "type": "zipsa_run_complete",
                "status": final_status,
                "exit_code": final_exit_code,
            }
            yield complete_event

        except RuntimeError:
            # Docker non-zero exit (not breach-terminated) → infra_failed
            final_status = "infra_failed"
            final_exit_code = 5
            final_error = {"code": "docker_failed", "message": "Docker exited with non-zero code"}
            raise
        finally:
            if hitl_server is not None:
                hitl_server.stop()
            self._hitl_port = None
            self._hitl_token = None

            # Write summary.json — best-effort, never fails the run.
            # summary.json is the single source of truth for per-run outcome
            # (it absorbed the old metadata.json's role — see chore: merge
            # metadata.json into summary.json).
            if run_dir is not None:
                try:
                    finished_at = datetime.now().astimezone()
                    ls = _limits_state_ref[0]
                    # Extract the metadata-style fields from the Claude SDK
                    # result event (None if the run didn't reach a result).
                    re = last_result_event or {}
                    # Version fields — best-effort. claude_version + model
                    # come from Claude SDK system.init (captured during
                    # the stream); runtime_version from image ENV via
                    # docker inspect (cached); zipsa_version from
                    # importlib. All None on failure — never blocks.
                    image_env = self._image_env_cache.get(self.image, {})
                    try:
                        from importlib.metadata import version as _pkg_version
                        _zipsa_version = _pkg_version("zipsa")
                    except Exception:
                        _zipsa_version = None
                    # Roll up cost/turns from phase_summaries. On resume,
                    # skipped phases carry the prior run's actual values
                    # (populated in _execute_phases) so the total reflects
                    # the WHOLE chain, not just this invocation. For
                    # non-resume runs the sum equals ls.run_*.
                    total_cost = sum(p.cost_usd for p in phase_summaries)
                    total_turns = sum(p.turns for p in phase_summaries)
                    # chain_started_at: parsed from string (the
                    # _execute_phases event yields ISO string); fall back
                    # to current run's started_at when not resuming.
                    parsed_chain_start = started_at
                    if chain_started_at:
                        try:
                            parsed_chain_start = datetime.fromisoformat(
                                chain_started_at,
                            )
                        except ValueError:
                            pass  # malformed prior summary; use this run's start
                    # HITL wait: current run's accumulated wait from
                    # HitlIO + prior chain accumulator.
                    current_hitl_wait = (
                        self._hitl_io.hitl_wait_seconds[0]
                        if self._hitl_io is not None else 0.0
                    )
                    total_hitl_wait = accumulated_hitl_wait + current_hitl_wait
                    summary = build_summary(
                        status=final_status,
                        exit_code=final_exit_code,
                        skill=skill.name,
                        version=skill.manifest.metadata.version,
                        started_at=started_at,
                        finished_at=finished_at,
                        cost_usd=total_cost,
                        turns=total_turns,
                        phases=phase_summaries,
                        result=final_result if final_status == "ok" else None,
                        error=final_error if final_status != "ok" else None,
                        user_input=user_input,
                        stop_reason=re.get("stop_reason"),
                        usage=re.get("usage"),
                        model_usage=re.get("modelUsage"),
                        zipsa_version=_zipsa_version,
                        runtime_image=self.image,
                        runtime_version=image_env.get("ZIPSA_RUNTIME_VERSION"),
                        claude_version=actual_claude_version or image_env.get("CLAUDE_CODE_VERSION"),
                        model=actual_model,
                        hitl_wait_seconds=total_hitl_wait,
                        chain_started_at=parsed_chain_start,
                        resumed_from=resumed_from_id,
                    )
                    write_summary(run_dir / "summary.json", summary)
                except Exception as e:
                    print(f"Warning: Failed to write summary.json: {e}", file=sys.stderr)

    @staticmethod
    def _stop_process(process) -> None:
        """Gracefully terminate a subprocess (Path B graceful stop).

        Sends SIGTERM and waits up to 5 seconds; escalates to SIGKILL.
        """
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def _get_image_env(self, image: str) -> dict[str, str]:
        """Read env vars baked into the image via `docker inspect`.

        Used at run start to surface ZIPSA_RUNTIME_VERSION and
        CLAUDE_CODE_VERSION in summary.json. Cached per image tag
        (~200ms first call, free after). Returns empty dict on any
        failure (network, missing image, no docker) — debugging
        version surface is best-effort, never blocks the run.
        """
        if image in self._image_env_cache:
            return self._image_env_cache[image]
        env: dict[str, str] = {}
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{json .Config.Env}}", image],
                capture_output=True, text=True, timeout=10, check=True,
            )
            env_list = json.loads(result.stdout.strip())
            for item in env_list:
                if "=" in item:
                    k, v = item.split("=", 1)
                    env[k] = v
        except Exception:
            pass  # best-effort; empty dict is acceptable
        self._image_env_cache[image] = env
        return env

    @staticmethod
    def _ensure_run_artifacts_dir(run_dir: Path) -> Path:
        """Create the artifacts/ subdir if missing. Returns the path.

        Artifacts are files a skill writes for cross-process consumption
        (orchestrators reading them via MCP get_artifact). Created at the
        same time as the run_dir so the mount point exists when the
        container starts.
        """
        artifacts = run_dir / "artifacts"
        artifacts.mkdir(exist_ok=True)
        return artifacts

    # Backward-compat shims. Real implementations in core.phase_state.
    _write_phase_state = staticmethod(_write_phase_state_impl)
    _load_resume_state = staticmethod(_load_resume_state_impl)

    def _execute_skill(
        self,
        docker_cmd: list[str],
        claude_json_path: Path,
        skill: Skill,
        run_dir: Optional[Path],
        env_file: Optional[Path] = None,
        user_input: str = "",
        *,
        phase_id: str = "main",
        phase_limits: Optional[SkillLimits] = None,
        limits_state: Optional[LimitsState] = None,
        model: Optional[str] = None,
    ) -> Iterator[dict]:
        """Execute Docker command and stream output.

        Per-event limit enforcement (Path B): after each parsed event the
        executor calls update_for_event + check_limits from zipsa.core.limits.
        On breach the current event is yielded, the process is terminated
        gracefully, a zipsa_limits_breach event is emitted, and the generator
        returns. The old inline turn/cost/timeout blocks have been removed.

        Args:
            docker_cmd: Docker command array
            claude_json_path: Path to .claude.json file (not cleaned up after execution)
            skill: Skill being executed
            run_dir: Directory to save run logs (None to skip logging)
            env_file: Environment file path (optional)
            user_input: User's input/query for this execution
            phase_id: Identifier for this phase (default "main" for single-phase).
            phase_limits: Per-phase SkillLimits to check against. When None,
                falls back to spec.limits for single-phase / direct invocations.
            limits_state: Shared LimitsState from the caller (multi-phase runs).
                If provided, this state is updated in-place so aggregate counters
                accumulate across phases. If None, a fresh state is created.

        Yields:
            Parsed output events (including zipsa_limits_breach on breach)

        Raises:
            RuntimeError: If Docker execution fails
        """
        output_file = None
        if run_dir:
            output_file = run_dir / "output.jsonl"

        # Model needed before limits_state setup (used in update_for_event
        # for pricing). Caller may override (per-phase model); else fall back
        # to spec.model.name; else default Opus.
        if model is None:
            model = (skill.manifest.spec.model or {}).get("name", "claude-opus-4-7")

        # Limits setup — single call site shared by both branches.
        agg_limits = skill.manifest.spec.limits or SkillLimits()
        if phase_limits is None:
            # Single-phase / direct invocation: phase limit == aggregate limit
            # (preserves pre-existing behaviour for skills without phases).
            phase_limits = agg_limits
        if limits_state is None:
            limits_state = new_state(phase_id)
        else:
            # Caller-owned state carrying aggregate counters across phases.
            # Emit a synthetic zipsa_phase_start so update_for_event resets
            # phase-level counters cleanly while preserving run-level ones.
            update_for_event(
                limits_state,
                {"type": "zipsa_phase_start", "phase": phase_id},
                model,
            )

        cost_exceeded = False
        # Set True iff we initiated the Docker termination (via _stop_process
        # on a limit breach). Used after the stream loop to distinguish "we
        # caused the SIGTERM, returncode 143 is expected" from "Docker
        # genuinely crashed, raise RuntimeError".
        breach_terminated = False
        process = None

        def _stream_with_limits(raw_stream, output_file_handle=None):
            """Unified per-event loop: parse, track limits, yield / stop on breach."""
            nonlocal cost_exceeded, breach_terminated
            for line in raw_stream:
                if not line:
                    continue
                if output_file_handle:
                    output_file_handle.write(line)
                for event in self.runtime.parse_output([line]):
                    # Per-event cost tracking for metadata (kept separately from limits)
                    if event.get("type") == "result":
                        actual_cost = event.get("total_cost_usd", 0) or 0
                        declared = skill.manifest.spec.limits
                        if declared and declared.max_cost_usd and actual_cost > declared.max_cost_usd:
                            cost_exceeded = True

                    # Limits bookkeeping — single call site.
                    update_for_event(limits_state, event, model)
                    breach = check_limits(limits_state, phase_limits, agg_limits)
                    if breach is not None:
                        yield event
                        breach_terminated = True
                        self._stop_process(process)
                        yield {
                            "type": "zipsa_limits_breach",
                            "scope": breach.scope,
                            "kind": breach.kind,
                            "value": breach.value,
                            "limit": breach.limit,
                            "phase": breach.phase,
                        }
                        return  # generator done — caller sees the breach event last
                    yield event

        try:
            # Execute Docker
            process = subprocess.Popen(
                docker_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            raw_stream = iter(process.stdout.readline, "")

            if output_file:
                with open(output_file, "w", buffering=1) as f:
                    yield from _stream_with_limits(raw_stream, output_file_handle=f)
            else:
                yield from _stream_with_limits(raw_stream)

            # Reap the child process (no timeout — limits enforced per-event above).
            process.wait()

            # Skip the "failed" raise if we intentionally terminated due to
            # a limit breach. The user already saw the breach event from the
            # renderer; a follow-up "Docker execution failed with code 143"
            # is misleading noise about our own SIGTERM.
            if process.returncode != 0 and not breach_terminated:
                raise RuntimeError(
                    f"Docker execution failed with code {process.returncode}"
                )

        except KeyboardInterrupt:
            # User pressed Ctrl+C — terminate Docker process.
            if process:
                print("\nInterrupted by user - terminating execution...", file=sys.stderr)
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            raise

        finally:
            # Ensure Docker process is terminated (e.g. after a breach return).
            if process and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                except Exception:
                    pass  # Best-effort cleanup

            # Write events.jsonl (filtered event stream) if logging enabled.
            # Per-run outcome metadata is in summary.json, written by the
            # outer _execute_with_hitl finally.
            if run_dir:
                try:
                    self._save_events(run_dir)
                except Exception as e:
                    print(f"Warning: Failed to save run logs: {e}", file=sys.stderr)

            # Clean up env file (contains secrets, should not persist).
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

    def _save_events(self, run_dir: Path) -> None:
        """Generate events.jsonl (filtered event stream) from output.jsonl.

        Filters for important events only:
        - system (init only)
        - assistant (all)
        - user (all)
        - result (all)
        - any event with "error" in type

        (Renamed from _save_summary / summary.jsonl — that name collided
        with summary.json's role. events.jsonl is the accurate name:
        this file is the filtered event STREAM, not a summary.)

        Args:
            run_dir: Run directory containing output.jsonl
        """
        output_file = run_dir / "output.jsonl"
        events_file = run_dir / "events.jsonl"

        if not output_file.exists():
            return

        important_types = {"system", "assistant", "user", "result"}

        with open(output_file, 'r') as inf, open(events_file, 'w') as outf:
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

    # _save_metadata removed: per-run outcome metadata now lives in
    # summary.json (single source of truth). See chore: merge metadata.json
    # into summary.json. The build_summary() call site captures the same
    # fields (status, cost, turns, duration, usage, stop_reason,
    # model_usage, user_input) from in-memory run state rather than
    # re-scanning output.jsonl after the fact.

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
        _limits_state_ref: Optional[list] = None,
        resume_from: Optional[int] = None,
        resume_from_run_dir: Optional[Path] = None,
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

        # Shared limits state — accumulates run-level counters across all
        # phases so that spec.limits (aggregate) is enforced over the full run.
        shared_limits_state = new_state(phases[0].id)
        if _limits_state_ref is not None:
            _limits_state_ref[0] = shared_limits_state

        # Per-phase rollup for summary.json
        phase_summaries: list[PhaseSummary] = []

        # Resume support: when resume_from is set, skip phases
        # 0..resume_from-1 and seed previous_output from the persisted
        # state.json of phase resume_from-1 in the PRIOR (failed) run.
        # The current run_dir is a fresh empty dir — state.json lives
        # in resume_from_run_dir, which the CLI sets to
        # candidate.run_dir (the failed run we're resuming from).
        start_phase_idx = 0
        prior_phase_costs: dict[str, tuple[float, int]] = {}
        chain_started_at = None
        resumed_from_id = None
        accumulated_hitl_wait = 0.0
        if resume_from is not None and resume_from > 0:
            start_phase_idx = resume_from
            prior_run_dir = resume_from_run_dir or run_dir
            previous_output = _load_resume_state_impl(
                prior_run_dir, resume_from=resume_from,
            )
            # Read prior summary so we can roll forward cost/turns for
            # skipped phases AND propagate chain_started_at + accumulated
            # hitl_wait_seconds. Each resumed run's summary then shows
            # the WHOLE chain's cost/duration, not just this attempt.
            prior_summary: dict = {}
            prior_summary_path = prior_run_dir / "summary.json"
            if prior_summary_path.exists():
                try:
                    prior_summary = json.loads(prior_summary_path.read_text())
                except (json.JSONDecodeError, OSError):
                    prior_summary = {}
            for p in prior_summary.get("phases", []):
                prior_phase_costs[p["id"]] = (
                    float(p.get("cost_usd", 0.0)),
                    int(p.get("turns", 0)),
                )
            # chain_started_at: prefer prior's chain field if present,
            # else its own started_at. Walks the chain backward by one
            # hop each resume; older runs in the chain self-propagated
            # the same way.
            chain_started_at = (
                prior_summary.get("chain_started_at")
                or prior_summary.get("started_at")
            )
            accumulated_hitl_wait = float(
                prior_summary.get("hitl_wait_seconds") or 0.0
            )
            resumed_from_id = prior_run_dir.name

            # Pre-populate phase_summaries with the SKIPPED phases'
            # actual cost/turns from the prior run (not 0/0). And copy
            # each skipped phase's state.json from the prior run into
            # THIS run's per-phase dir, so a future resume can chain
            # from THIS run.
            for skipped_idx in range(resume_from):
                skipped = phases[skipped_idx]
                cost, turns = prior_phase_costs.get(skipped.id, (0.0, 0))
                phase_summaries.append(PhaseSummary(
                    id=skipped.id, status="ok",
                    cost_usd=cost, turns=turns,
                ))
                if run_dir is not None:
                    src = prior_run_dir / "phases" / f"{skipped_idx}-{skipped.id}" / "state.json"
                    if src.exists():
                        dst_dir = run_dir / "phases" / f"{skipped_idx}-{skipped.id}"
                        dst_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy(src, dst_dir / "state.json")
        # Tracks final run status for the zipsa_run_complete event
        run_final_status = "infra_failed"
        run_final_exit_code = 5
        run_final_result: Optional[dict] = None
        run_final_error: Optional[dict] = None

        try:
            for phase_idx, phase in enumerate(phases):
                if phase_idx < start_phase_idx:
                    continue
                yield {
                    "type": "zipsa_phase_start",
                    "phase": phase.id,
                    "phase_idx": phase_idx,
                    "total_phases": len(phases),
                    "goal": phase.goal,
                }

                while True:
                    phase_allowed_tools = ",".join(phase.allowed_tools)
                    _write_phase_allow_file_impl(
                        claude_json_path.parent, phase.id, list(phase.allowed_tools),
                    )
                    user_message = _build_user_message_impl(
                        skill, phase.id, phase.goal, phase_allowed_tools,
                        previous_output, skill_state, user_input,
                        run_id=run_dir.name if run_dir else "unknown",
                    )

                    # Per-phase artifact directory
                    phase_dir = (run_dir / "phases" / f"{phase_idx}-{phase.id}") if run_dir else None
                    if phase_dir:
                        phase_dir.mkdir(parents=True, exist_ok=True)

                    # Per-phase model override: phase.model wins over spec.model.
                    # None means "no override" → runtime/spec default applies.
                    phase_model = (
                        (phase.model or skill.manifest.spec.model or {}).get("name")
                    )

                    docker_cmd = self._build_docker_command(
                        skill, user_message, claude_json_path, env,
                        allowed_tools_override=phase_allowed_tools,
                        extra_docker_opts=extra_docker_opts,
                        phase_id=phase.id,
                        npm_volume=npm_volume,
                        requires_values=self._requires_values,
                        model=phase_model,
                        run_dir=run_dir,
                    )

                    # Stream events, capture last assistant text.
                    # A limits breach surfaces as a zipsa_limits_breach event
                    # (last event from _execute_skill on breach) — stop the run.
                    last_assistant_text = None
                    limits_breached = False
                    for event in self._execute_skill(
                        docker_cmd, claude_json_path, skill, phase_dir,
                        skill_data_dir / ".env", user_message,
                        phase_id=phase.id,
                        phase_limits=phase.limits or SkillLimits(),
                        limits_state=shared_limits_state,
                        model=phase_model,
                    ):
                        if event.get("type") == "assistant":
                            content = event.get("message", {}).get("content", [])
                            for block in content:
                                if block.get("type") == "text":
                                    last_assistant_text = block.get("text", "")
                                    break
                        elif event.get("type") == "zipsa_limits_breach":
                            limits_breached = True
                            # Record this phase's contribution before returning
                            phase_summaries.append(PhaseSummary(
                                id=phase.id,
                                status="limits_exceeded",
                                cost_usd=shared_limits_state.phase_cost_usd,
                                turns=shared_limits_state.phase_turns,
                            ))
                            run_final_status = "limits_exceeded"
                            run_final_exit_code = 3
                            run_final_error = {
                                "code": "limits_exceeded",
                                "message": (
                                    f"phase {event.get('kind')}: "
                                    f"{event.get('value')} > {event.get('limit')}"
                                ),
                                "details": {
                                    "scope": event.get("scope"),
                                    "kind": event.get("kind"),
                                    "value": event.get("value"),
                                    "limit": event.get("limit"),
                                    "phase": event.get("phase"),
                                },
                            }
                            yield event
                            yield {
                                "type": "zipsa_run_complete",
                                "status": run_final_status,
                                "exit_code": run_final_exit_code,
                                "result": None,
                                "error": run_final_error,
                                "_phase_summaries": phase_summaries,

                                "_chain_started_at": chain_started_at,

                                "_resumed_from": resumed_from_id,

                                "_accumulated_hitl_wait": accumulated_hitl_wait,
                            }
                            return  # state_updates NOT applied on breach
                        yield event

                    if limits_breached:
                        return

                    # Extract and validate skill output
                    phase_out = _extract_skill_output_impl(last_assistant_text)

                    if phase_out is None:
                        # Parse failed — record the real cause (the agent
                        # didn't emit a valid envelope). Preserve the raw
                        # text so the user can see what the agent produced.
                        phase_out = {
                            "status": "failed",
                            "phase": phase.id,
                            "result": None,
                            "state_updates": None,
                            "next_phase_input": None,
                            "user_facing_summary": (
                                "Skill output could not be parsed."
                            ),
                            "error": {
                                "code": "invalid_output_format",
                                "raw_output": (last_assistant_text or "")[:2000],
                            },
                        }
                    else:
                        # Phase field validation — only runs when parse
                        # succeeded. (Pre-fix, _extract_skill_output
                        # returned a synthetic envelope with
                        # phase="unknown" which then tripped this check
                        # and clobbered the real invalid_output_format
                        # error code.)
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
                                "error": {"code": "phase_id_mismatch"},
                            }

                    status = phase_out.get("status", "failed")

                    if status == "ok":
                        phase_summaries.append(PhaseSummary(
                            id=phase.id,
                            status="ok",
                            cost_usd=shared_limits_state.phase_cost_usd,
                            turns=shared_limits_state.phase_turns,
                        ))
                        # Persist the envelope so a future invocation
                        # can resume from the next phase.
                        _write_phase_state_impl(phase_dir, phase_out)
                        if phase_out.get("state_updates"):
                            self._apply_skill_state(skill, phase_out["state_updates"])
                            skill_state = self._load_skill_state(skill)
                        previous_output = phase_out.get("next_phase_input")
                        last_phase_out = phase_out
                        break

                    else:  # failed | out_of_scope — state_updates NOT applied
                        error_code = (phase_out.get("error") or {}).get("code", "")
                        if error_code in ("hitl_unattended", "user_declined"):
                            phase_status = "user_declined"
                        elif status == "out_of_scope":
                            phase_status = "out_of_scope"
                        else:
                            phase_status = "failed"

                        phase_summaries.append(PhaseSummary(
                            id=phase.id,
                            status=phase_status,
                            cost_usd=shared_limits_state.phase_cost_usd,
                            turns=shared_limits_state.phase_turns,
                        ))
                        run_final_status = phase_status
                        run_final_exit_code = {
                            "failed": 1, "out_of_scope": 2, "user_declined": 4,
                        }.get(phase_status, 1)
                        run_final_error = phase_out.get("error") or {
                            "code": phase_status,
                            "message": phase_out.get("user_facing_summary", ""),
                        }
                        last_phase_out = phase_out
                        yield {"type": "zipsa_phase_error", "phase": phase.id,
                               "status": status, "output": phase_out}
                        yield {
                            "type": "zipsa_run_complete",
                            "status": run_final_status,
                            "exit_code": run_final_exit_code,
                            "result": None,
                            "error": run_final_error,
                            "_phase_summaries": phase_summaries,

                            "_chain_started_at": chain_started_at,

                            "_resumed_from": resumed_from_id,

                            "_accumulated_hitl_wait": accumulated_hitl_wait,
                        }
                        return

            # All phases completed successfully
            if last_phase_out:
                run_final_status = "ok"
                run_final_exit_code = 0
                run_final_result = last_phase_out.get("result")
                run_final_error = None
                yield {
                    "type": "zipsa_run_complete",
                    "status": run_final_status,
                    "exit_code": run_final_exit_code,
                    "result": last_phase_out,
                    "error": None,
                    "_phase_summaries": phase_summaries,

                    "_chain_started_at": chain_started_at,

                    "_resumed_from": resumed_from_id,

                    "_accumulated_hitl_wait": accumulated_hitl_wait,
                }
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

    # Backward-compat shims. Real implementations live in core.phase_allow.
    _ALWAYS_ON_TOOLS = _ALWAYS_ON_TOOLS_LIST

    @classmethod
    def _merge_always_on_tools(cls, allowed_tools: str) -> str:
        return _merge_always_on_tools_impl(allowed_tools)

    def _write_phase_allow_file(
        self, output_dir: Path, phase_id: str, allowed_tools: list[str],
    ) -> Path:
        return _write_phase_allow_file_impl(output_dir, phase_id, allowed_tools)

    def _write_default_phase_allow_file(self, output_dir: Path, skill: "Skill") -> Path:
        return _write_default_phase_allow_file_impl(output_dir, skill)

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
        requires_values: Optional[dict[str, object]] = None,
        model: Optional[str] = None,
        run_dir: Optional[Path] = None,
    ) -> list[str]:
        """Build full docker run command.

        Args:
            skill: Skill being executed
            user_input: User input
            claude_json_path: Path to .claude.json file (on host)
            env: Environment variables
            shell: If True, build command for interactive shell
            requires_values: Resolved requires values keyed by requires key name

        Returns:
            Command array for subprocess
        """
        requires_values = requires_values or {}
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

        # Per-execution env file (~/.zipsa/<name>@<version>/.env), added last to take precedence.
        # Dev overlay env is merged in (overlay can override skill-supplied env).
        skill_data_dir = zipsa_paths.skill_data_dir(skill.name, skill.manifest.metadata.version)
        merged_env = dict(env)
        if self.dev_overlay is not None:
            merged_env.update(self.dev_overlay.env)
        if self._hitl_token is not None:
            merged_env["ZIPSA_HITL_TOKEN"] = self._hitl_token
        env_file = self._write_env_file(skill_data_dir, merged_env)
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

        # spec.mounts: both static (host) and dynamic (source -> requires.X)
        # Seed collision tracker with zipsa-internal container paths so a
        # manifest that declares e.g. `container: /skill` errors cleanly
        # instead of silently double-mounting (undefined Docker behavior).
        seen_container_paths: set[str] = {
            "/.zipsa", "/skill", "/zipsa-hooks/pretooluse.py",
        }

        # Mount the run_dir read-write into the container so the skill
        # can write artifacts/<name> for cross-process consumption.
        # Skipped when run_dir is None (dry-run, shell mode).
        if run_dir is not None:
            container_path = "/home/agent/runs/current"
            cmd.extend(["-v", f"{run_dir}:{container_path}:rw"])
            seen_container_paths.add(container_path)

        for m in skill.manifest.spec.mounts:
            if m.host is not None:
                # Static mount
                if m.container in seen_container_paths:
                    raise MountCollisionError(
                        f"container path {m.container} already used "
                        "(by a zipsa-internal mount or earlier spec.mounts entry)"
                    )
                host_path = Path(m.host).expanduser().resolve()
                cmd.extend(["-v", f"{host_path}:{m.container}:{m.mode}"])
                seen_container_paths.add(m.container)
                continue

            # Dynamic mount (m.source is "requires.<key>")
            key = m.source.removeprefix("requires.")
            value = requires_values.get(key)
            if value is None:
                # Should have been caught in pre-flight; defensive
                raise ValueError(
                    f"mount source 'requires.{key}' has no value at run time"
                )

            if isinstance(value, str):
                # Single directory
                host_path = Path(value).expanduser().resolve()
                container_path = str(host_path) if m.preserve_host_path else m.container
                if container_path in seen_container_paths:
                    raise MountCollisionError(
                        f"container path {container_path} already used by another mount"
                    )
                cmd.extend(["-v", f"{host_path}:{container_path}:{m.mode}"])
                seen_container_paths.add(container_path)

            elif isinstance(value, list):
                # list[directory]: container path is either prefix+basename
                # (default) or the host path verbatim (preserve_host_path).
                for item in value:
                    host_path = Path(item).expanduser().resolve()
                    if m.preserve_host_path:
                        container_path = str(host_path)
                    else:
                        container_path = m.container_prefix + host_path.name
                    if container_path in seen_container_paths:
                        if m.preserve_host_path:
                            raise MountCollisionError(
                                f"duplicate path in requires.{key}: {container_path}"
                            )
                        raise MountCollisionError(
                            f"basename collision in requires.{key}: "
                            f"multiple paths resolve to {container_path}"
                        )
                    cmd.extend(["-v", f"{host_path}:{container_path}:{m.mode}"])
                    seen_container_paths.add(container_path)
            else:
                raise ValueError(
                    f"requires.{key} has unexpected type {type(value).__name__}"
                )

        # Auto-mount the skill's own source directory so skills can bundle
        # helper scripts (e.g. scripts/post.py) and reach them at /skill.
        cmd.extend(["-v", f"{skill.skill_dir}:/skill:ro"])

        # MCP debug file mount (bind-mount host log file into container)
        if mcp_debug_host:
            cmd.extend(["-v", f"{mcp_debug_host}:/home/agent/mcp-debug.log"])

        # Dev overlay mounts (last so they can shadow earlier ones if path collides)
        if self.dev_overlay is not None:
            for mount in self.dev_overlay.mounts:
                cmd.extend(["-v", mount])

        # Linux Docker Engine needs explicit host-gateway mapping for
        # host.docker.internal to resolve; Docker Desktop (macOS/Windows)
        # provides it natively.
        if self._hitl_port is not None and platform.system() == "Linux":
            cmd.extend(["--add-host=host.docker.internal:host-gateway"])

        # Image
        cmd.append(self.image)

        # Preamble copies .claude.json from the read-only /.zipsa mount into the
        # container's overlay FS so Claude Code can atomically rename it. Also
        # installs settings.json (with PreToolUse hook config) into ~/.claude/.
        cp_preamble = (
            "cp /.zipsa/.claude.json /home/agent/.claude.json && "
            "mkdir -p /home/agent/.claude && "
            "cp /.zipsa/settings.json /home/agent/.claude/settings.json"
        )
        if self.dev_overlay is not None and self.dev_overlay.preamble_str:
            cp_preamble = f"{cp_preamble} && {self.dev_overlay.preamble_str}"

        if shell:
            # exec replaces the copy-shell with a fresh interactive bash
            cmd.extend(["bash", "-c", f"{cp_preamble} && exec bash"])
        else:
            # Runtime-specific command (from plugin)
            system_prompt = _build_system_prompt_impl(skill)
            allowed_tools = allowed_tools_override if allowed_tools_override is not None else skill.get_allowed_tools()
            # Augment with the always-on MCP tools so Claude exposes them
            # to the model. Without this, the hook would allow them but
            # Claude would never offer them — they'd be invisible.
            allowed_tools = _merge_always_on_tools_impl(allowed_tools)
            mcp_debug_container = "/home/agent/mcp-debug.log" if mcp_debug_host else None

            # Collect container paths for all stdio MCP mounts so Claude Code
            # includes them in its ListRoots response (needed by secure-filesystem-server)
            extra_dirs = [
                f"{CONTAINER_WORKSPACE}/{server.name}"
                for server in skill.manifest.spec.mcp
                if server.type == "stdio" and server.mount
            ]

            # MCP config is now in .claude.json (mounted to /home/agent/.claude.json)
            # model: prefer explicit param, else fall back to spec.model.name,
            # else None (let runtime pick its default). Phase-level overrides
            # are computed by _execute_phases and passed via the `model` param.
            effective_model = model or (skill.manifest.spec.model or {}).get("name")
            runtime_cmd = self.runtime.build_command(
                skill_name=skill.name,
                user_input=user_input,
                system_prompt=system_prompt,
                allowed_tools=allowed_tools,
                workspace=Path(CONTAINER_WORKSPACE),
                env=env,
                mcp_debug_file=mcp_debug_container,
                extra_dirs=extra_dirs,
                model=effective_model,
            )

            cmd.extend(["bash", "-c", f"{cp_preamble} && {shlex.join(runtime_cmd)}"])

        return cmd

    # Backward-compat shims: existing tests call these via the class.
    # Real implementations live in core.output_parser and core.prompts.
    _extract_skill_output = staticmethod(_extract_skill_output_impl)

    def _build_system_prompt(self, skill: Skill) -> str:
        return _build_system_prompt_impl(skill)

    def _build_user_message(
        self,
        skill: Skill,
        phase_id: str,
        phase_goal: str,
        phase_allowed_tools: str,
        previous_phase_output: str | None,
        skill_state: dict,
        user_query: str,
        run_id: str = "unknown",
    ) -> str:
        return _build_user_message_impl(
            skill, phase_id, phase_goal, phase_allowed_tools,
            previous_phase_output, skill_state, user_query, run_id,
        )

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
