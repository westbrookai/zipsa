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

# Mirrors cli._MAX_CALL_DEPTH. Kept in sync via test
# (test_max_call_depth_matches_cli) so the two enforcement points
# (in-process here + env-var-based check in cli for direct invocation)
# agree on the cap.
_MAX_CALL_DEPTH = 5

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

        # Enforce cycle + depth caps IN-PROCESS using the caller's tracked
        # chain. Env-var propagation alone wouldn't work because every
        # run_skill call funnels back to the same parent HitlServer
        # process — os.environ in the parent never grows.
        new_trace = caller.trace + (caller.skill,)
        new_depth = caller.depth + 1
        if name in new_trace:
            return {
                "status": "failed", "exit_code": 2, "skill": name,
                "version": None, "run_id": None, "summary": None,
                "error": {
                    "code": "skill_cycle_detected",
                    "message": f"'{name}' is already in the call chain ({' -> '.join(new_trace)} -> {name})",
                },
            }
        if new_depth >= _MAX_CALL_DEPTH:
            return {
                "status": "failed", "exit_code": 2, "skill": name,
                "version": None, "run_id": None, "summary": None,
                "error": {
                    "code": "skill_depth_exceeded",
                    "message": f"call depth {new_depth} >= cap {_MAX_CALL_DEPTH} (chain: {' -> '.join(new_trace)})",
                },
            }

        # Resolve the child's spec.requires from the PARENT's stdin if
        # needed. Child subprocesses are spawned with stdin=DEVNULL (HITL
        # routes through MCP, not stdin), so a child whose requires.yaml
        # is missing would otherwise exit 4 immediately. We do the
        # resolution here, write the child's requires.yaml, then spawn —
        # the child reads the file like any other invocation.
        requires_fail = self._resolve_child_requires(name)
        if requires_fail is not None:
            return requires_fail

        # Mint and register child token with the full chain so that when
        # the child itself calls run_skill, _its_ caller resolves with
        # accurate depth/trace.
        child_token = secrets.token_urlsafe(32)
        self._server.register_caller(
            child_token,
            CallerInfo(skill=name, version="*", depth=new_depth, trace=new_trace),
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

    def _resolve_child_requires(self, name: str) -> Optional[dict]:
        """Ensure the child's spec.requires is satisfied before spawn.

        Loads the child skill from the installed dir, classifies its
        requires state, and — if anything still needs prompting — drives
        `resolve_requires` against the PARENT's HitlIO (the only stdin in
        the call chain that's actually attached to a terminal). On
        success, the child's requires.yaml is written to disk and the
        child subprocess can read it normally despite running with
        stdin=DEVNULL.

        Returns None when the child is good to spawn. Returns a fail dict
        (same shape as `_fail`) when something blocks spawn, so the
        caller can `return` it directly.
        """
        from .skill import Skill
        from .requires import RequiresError, resolve_requires

        install = zipsa_paths.installed_skill_dir(name)
        if not install.exists():
            return self._fail(
                "child_not_installed",
                f"'{name}' is not installed under ZIPSA_HOME/skills/",
            )
        try:
            child_skill = Skill.load(install)
        except Exception as e:
            return self._fail("child_unloadable", str(e))

        spec_requires = child_skill.manifest.spec.requires
        if not spec_requires:
            return None

        io_ = getattr(self._server, "_io", None)
        if io_ is None:
            # HitlServer should always have _io. Fail loud rather than
            # silently dropping the resolution.
            return self._fail(
                "parent_io_unavailable",
                "parent HitlServer has no _io reference for requires prompt",
            )

        try:
            resolve_requires(
                child_skill.name,
                child_skill.manifest.metadata.version,
                spec_requires,
                io_.stdin,
                io_.stdout,
                is_interactive=io_.is_interactive,
            )
        except RequiresError as e:
            return self._fail("child_requires_unset", str(e))
        return None

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
