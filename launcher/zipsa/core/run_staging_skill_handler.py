"""RunStagingSkillHandler — invoke a skill that lives in
~/.zipsa/staging/<name>/ (not yet installed).

Powers mcp__zipsa__run_staging_skill. Only callers whose own manifest
declared `spec.allows_staging_run: true` are permitted — this is a
privileged tool meant for authoring meta-skills (skill-builder), not
production skills.

Same subprocess-zipsa-run pattern as RunSkillHandler, with one
mechanism difference: instead of looking the child up by installed
name, we set ZIPSA_STAGING_RUN_PATH in the subprocess env so
cli._resolve_skill_path loads from the staging dir. The result dict
adds `is_staging: true` so downstream code (skill-builder's analysis
loop) can distinguish staging runs from regular ones — RunSkillHandler
returns `is_staging: false` for the same shape compatibility.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import threading
import time as _t
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Optional

from .. import paths as zipsa_paths
from .caller_context import CallerInfo, current_caller

if TYPE_CHECKING:
    from .hitl_runner import HitlServer


def _is_unsafe_segment(value: str) -> bool:
    if not value:
        return True
    if len(value) > 255:
        return True
    if "\\" in value or "\x00" in value:
        return True
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or len(p.parts) != 1:
        return True
    return False


class RunStagingSkillHandler:
    def __init__(self, server: "HitlServer") -> None:
        self._server = server

    def run(self, *, name: str, args: str = "") -> dict:
        caller = current_caller.get()
        if caller is None:
            return self._fail("caller_unknown", "no caller context")

        if _is_unsafe_segment(name):
            return self._fail(
                "staging_skill_bad_name",
                f"name must be a flat path segment, got {name!r}",
            )

        # Permission: caller's manifest must declare allows_staging_run.
        # Production skills should never get this authority by default.
        if not self._caller_allows_staging_run(caller):
            return self._fail(
                "staging_run_not_allowed",
                f"'{caller.skill}' did not declare spec.allows_staging_run: true",
            )

        staging_dir = zipsa_paths.zipsa_home() / "staging" / name
        if not staging_dir.exists():
            return self._fail(
                "staging_skill_not_found",
                f"~/.zipsa/staging/{name}/ does not exist",
            )

        # Sanity-check the staging skill loads — fail early rather than
        # spawning a subprocess that's destined to crash on Skill.load.
        try:
            from .skill import Skill
            Skill.load(staging_dir)
        except Exception as e:
            return self._fail(
                "staging_skill_unloadable",
                f"{type(e).__name__}: {str(e).splitlines()[0]}",
            )

        # Mint per-call child token + register with caller chain. Same
        # mechanism RunSkillHandler uses — child reuses parent's
        # HitlServer for HITL/memory/artifacts via env-vars below.
        child_token = secrets.token_urlsafe(32)
        new_trace = caller.trace + (caller.skill,)
        new_depth = caller.depth + 1
        self._server.register_caller(
            child_token,
            CallerInfo(skill=name, version="*", depth=new_depth, trace=new_trace),
        )

        env = self._build_child_env(
            caller, child_token, staging_dir=staging_dir,
        )
        # `name` is still passed so the child run dir is named consistently
        # (~/.zipsa/<name>@<version>/runs/...). ZIPSA_STAGING_RUN_PATH
        # tells cli._resolve_skill_path to load from the override path
        # instead of looking up the installed skill of that name.
        cmd = ["uv", "run", "zipsa", "run", name, args]

        timeout_s = int(os.environ.get("ZIPSA_RUN_SKILL_TIMEOUT", "600"))
        try:
            stderr_buf = bytearray()
            stderr_buf_lock = threading.Lock()
            proc = subprocess.Popen(
                cmd, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )

            def _drain_stderr():
                if proc.stderr is None:
                    return
                while True:
                    chunk = proc.stderr.read(4096)
                    if not chunk:
                        return
                    with stderr_buf_lock:
                        stderr_buf.extend(chunk)

            stderr_thread = threading.Thread(
                target=_drain_stderr, daemon=True,
                name=f"run_staging-stderr-{name}",
            )
            stderr_thread.start()

            deadline = _t.time() + timeout_s
            while proc.poll() is None:
                if _t.time() > deadline:
                    proc.kill()
                    proc.wait(timeout=5)
                    return self._fail(
                        "child_timeout", f"timed out after {timeout_s}s"
                    )
                _t.sleep(0.1)
            stderr_thread.join(timeout=2)

            returncode = proc.returncode
            stderr_text = bytes(stderr_buf).decode("utf-8", errors="replace").strip()
        except Exception as e:
            return self._fail("subprocess_error", str(e))

        if returncode == 2:
            code = "skill_cycle_or_depth"
            if "skill_cycle_detected" in stderr_text:
                code = "skill_cycle_detected"
            elif "skill_depth_exceeded" in stderr_text:
                code = "skill_depth_exceeded"
            return {
                "status": "failed", "exit_code": 2, "skill": name,
                "version": None, "run_id": None, "summary": None,
                "is_staging": True,
                "error": {"code": code, "message": stderr_text or "child exited 2"},
            }

        run_dir = self._find_latest_run_dir(name)
        if run_dir is None:
            return self._fail(
                "summary_not_found",
                f"child returned but no run dir found for '{name}' under ZIPSA_HOME",
            )
        try:
            summary = json.loads((run_dir / "summary.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError) as e:
            return self._fail("summary_unreadable", str(e))

        actual_version = summary.get("version", "*")
        self._server.register_caller(
            child_token, CallerInfo(skill=name, version=actual_version),
        )

        return {
            "status": summary.get("status", "ok"),
            "exit_code": returncode,
            "skill": summary.get("skill", name),
            "version": actual_version,
            "run_id": run_dir.name,
            "summary": summary,
            "is_staging": True,
        }

    def _caller_allows_staging_run(self, caller: CallerInfo) -> bool:
        from .skill import Skill
        install = zipsa_paths.installed_skill_dir(caller.skill)
        if not install.exists():
            return False
        try:
            skill = Skill.load(install)
        except Exception:
            return False
        return bool(getattr(skill.manifest.spec, "allows_staging_run", False))

    def _build_child_env(
        self,
        caller: CallerInfo,
        child_token: str,
        *,
        staging_dir: Path,
    ) -> dict[str, str]:
        env = os.environ.copy()
        env["ZIPSA_PARENT_MCP_URL"] = (
            f"http://host.docker.internal:{self._server.port}/mcp"
        )
        env["ZIPSA_PARENT_MCP_TOKEN"] = child_token
        existing_trace = env.get("ZIPSA_CALL_TRACE", "")
        existing_list = [s for s in existing_trace.split(",") if s]
        env["ZIPSA_CALL_TRACE"] = ",".join(existing_list + [caller.skill])
        existing_depth = int(env.get("ZIPSA_CALL_DEPTH", "0"))
        env["ZIPSA_CALL_DEPTH"] = str(existing_depth + 1)
        # The mechanism: cli._resolve_skill_path reads this and loads
        # from the staging path instead of the installed skills dir.
        env["ZIPSA_STAGING_RUN_PATH"] = str(staging_dir.resolve())
        return env

    def _find_latest_run_dir(self, name: str) -> Optional[Path]:
        """Locate the most recently created run dir for this staging
        name across all installed versions (race-prone if two runs
        happen back-to-back; accepted for v1)."""
        home = zipsa_paths.zipsa_home()
        candidates = sorted(
            home.glob(f"{name}@*/runs/*"),
            key=lambda p: p.stat().st_mtime,
        )
        return candidates[-1] if candidates else None

    @staticmethod
    def _fail(code: str, message: str) -> dict:
        return {
            "status": "failed",
            "exit_code": -1,
            "skill": None,
            "version": None,
            "run_id": None,
            "summary": None,
            "is_staging": True,
            "error": {"code": code, "message": message},
        }
