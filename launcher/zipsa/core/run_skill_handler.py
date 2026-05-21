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
        """Spawn child skill subprocess. Return routing + summary dict."""
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
            result = subprocess.run(
                cmd, env=env, stdin=subprocess.DEVNULL,
                capture_output=True, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as e:
            return self._fail("child_timeout", str(e))

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

        Look up the installed skill by name. Versions are usually mapped
        under ~/.zipsa/<name>@<version>/ — pick the version that matches
        caller.version, falling back to any version if caller's version
        isn't pinned in the registry.
        """
        from .skill import Skill
        # Try the symlinked install location.
        # Caller.version might be "*" for tokens that haven't been
        # re-registered yet — fallback to first available version.
        home = zipsa_paths.zipsa_home()
        if caller.version != "*":
            install = home / f"{caller.skill}@{caller.version}"
            if install.exists():
                skill = Skill.load(install)
                return list(skill.manifest.spec.children)
        # Fallback: any installed version for this skill name
        matches = sorted(home.glob(f"{caller.skill}@*"))
        if not matches:
            return []
        skill = Skill.load(matches[-1])
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
