"""RunSkillHandler — subprocess wrapper that runs a child skill via
`uv run zipsa run`, then reads the child's summary.json and returns
{status, exit_code, skill, version, run_id, summary}.

This is the value producer behind the MCP `run_skill` tool. The handler:
  1. Validates the child is in the caller's spec.children
  2. Mints a child-specific Bearer token and registers it on the
     HitlServer against CallerInfo(child_skill, version="*")
  3. Spawns the child subprocess with env vars (ZIPSA_PARENT_MCP_URL,
     ZIPSA_PARENT_MCP_TOKEN, ZIPSA_CALL_TRACE, ZIPSA_CALL_DEPTH) and
     stdin=DEVNULL (child launcher doesn't need stdin; HITL goes
     through the parent's server)
  4. Locates the child's run_dir (mtime sort), reads summary.json
  5. Re-registers the token with the child's actual version once known
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .. import paths as zipsa_paths
from .caller_context import CallerInfo, current_caller

if TYPE_CHECKING:
    from .hitl_runner import HitlServer


class RunSkillHandler:
    def __init__(self, server: "HitlServer") -> None:
        self._server = server

    def run(self, *, name: str, args: str = "") -> dict:
        """Spawn child skill subprocess. Return routing + summary dict.

        The subprocess is launched in a background thread so the calling
        MCP tool handler (running on uvicorn's worker pool) doesn't block
        the event loop. While we wait for the child to exit, the parent's
        HitlServer must still accept incoming requests from the child's
        container (the child reuses parent's server for HITL + memory +
        artifacts). Blocking the worker thread would deadlock.
        """
        caller = current_caller.get()
        if caller is None:
            return self._fail("caller_unknown", "no caller context")

        permitted = self._resolve_caller_children(caller)
        if name not in permitted:
            return self._fail(
                "skill_not_in_children",
                f"'{caller.skill}' did not declare '{name}' in spec.children",
            )

        # Mint and register child token (version unknown until summary read)
        child_token = secrets.token_urlsafe(32)
        self._server.register_caller(
            child_token, CallerInfo(skill=name, version="*"),
        )

        env = self._build_child_env(caller, child_token)
        cmd = ["uv", "run", "zipsa", "run", name, args]

        timeout_s = int(os.environ.get("ZIPSA_RUN_SKILL_TIMEOUT", "600"))
        try:
            # Popen + poll lets us drive the wait via a sleep loop in our
            # own thread, releasing the GIL between polls so other
            # uvicorn-handled requests (from the child container) can run.
            # subprocess.run with timeout blocks in C without giving the
            # event loop a chance to dispatch new connections.
            # Discard stdout, capture stderr only. Two reasons:
            # (1) Child's stdout is verbose stream-json; PIPE'd it would
            # fill the 64KB OS pipe buffer in seconds, blocking the child
            # launcher process — which would prevent the child container
            # from completing MCP calls (deadlock).
            # (2) The child's audit trail is summary.json + run_dir; we
            # never need stdout. We do want stderr to surface cycle/depth
            # error messages (exit 2 path below).
            stderr_buf = bytearray()
            stderr_buf_lock = __import__("threading").Lock()
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

            import threading
            stderr_thread = threading.Thread(
                target=_drain_stderr, daemon=True,
                name=f"run_skill-stderr-{name}",
            )
            stderr_thread.start()

            import time as _t
            deadline = _t.time() + timeout_s
            while proc.poll() is None:
                if _t.time() > deadline:
                    proc.kill()
                    proc.wait(timeout=5)
                    return self._fail("child_timeout", f"timed out after {timeout_s}s")
                _t.sleep(0.1)
            stderr_thread.join(timeout=2)

            class _Result:
                returncode = proc.returncode
                stdout = b""
                stderr = bytes(stderr_buf)
            result = _Result()
        except Exception as e:
            return self._fail("subprocess_error", str(e))

        # Child launcher exits 2 for cycle/depth violations BEFORE creating
        # a run_dir or writing summary.json. Surface those cleanly using
        # the launcher's stderr (which contains the descriptive error
        # message) rather than falling through to summary_not_found and
        # masking the real cause.
        if result.returncode == 2:
            stderr_text = (result.stderr or b"").decode("utf-8", errors="replace").strip()
            code = "skill_cycle_or_depth"
            if "skill_cycle_detected" in stderr_text:
                code = "skill_cycle_detected"
            elif "skill_depth_exceeded" in stderr_text:
                code = "skill_depth_exceeded"
            return {
                "status": "failed",
                "exit_code": 2,
                "skill": name,
                "version": None,
                "run_id": None,
                "summary": None,
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

        # Re-register with actual version now that summary is in
        actual_version = summary.get("version", "*")
        self._server.register_caller(
            child_token, CallerInfo(skill=name, version=actual_version),
        )

        return {
            "status": summary.get("status", "ok"),
            "exit_code": result.returncode,
            "skill": summary.get("skill", name),
            "version": actual_version,
            "run_id": run_dir.name,
            "summary": summary,
        }

    def _build_child_env(self, caller: CallerInfo, child_token: str) -> dict[str, str]:
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
        return env

    def _resolve_caller_children(self, caller: CallerInfo) -> list[str]:
        """Load caller's manifest, return spec.children.

        Installed skills live under ~/.zipsa/skills/<name>/ (a symlink or
        copy of the source dir containing manifest.yaml). The <name>@<ver>
        sibling dirs hold run data + state, NOT manifests, so don't look
        there.
        """
        from .skill import Skill
        install = zipsa_paths.installed_skill_dir(caller.skill)
        if not install.exists():
            return []
        skill = Skill.load(install)
        return list(skill.manifest.spec.children)

    def _find_latest_run_dir(self, name: str) -> Optional[Path]:
        """Locate the most recently created run dir for this child name
        across all installed versions. Race-prone if concurrent runs
        happen — accepted limitation for v1."""
        home = zipsa_paths.zipsa_home()
        candidates = sorted(home.glob(f"{name}@*/runs/*"), key=lambda p: p.stat().st_mtime)
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
            "error": {"code": code, "message": message},
        }
