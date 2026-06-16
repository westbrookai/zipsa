"""Run a single skill script as a run-time tool call.

Run-time analogue of ExecSkillHandler: where create tests a whole draft
(run_phases over staging), run-time lets the orchestrating LLM invoke
ONE script at a time (run_phase), scoped to the skill being run.
"""
from __future__ import annotations

from pathlib import Path

from .phase_discovery import PhaseDiscoveryError, discover_phases
from ..exec_runner import run_phase


class RunScriptHandler:
    def __init__(self, docker_image: "str | None", skill_root: Path) -> None:
        self._image = docker_image
        self._root = skill_root.resolve()

    @staticmethod
    def _fail(code: str, message: str) -> dict:
        return {"status": "failed", "error": {"code": code, "message": message}}

    def _resolve(self, script: str) -> "Path | None":
        # Match against discovered phases by id, slug, "id.slug", or filename.
        # A discovery failure (no zipsa-dist/, bad phase names) means the
        # script isn't runnable either — treat as "not found". Anything
        # unexpected propagates rather than masquerading as not-found.
        try:
            phases = discover_phases(self._root)
        except PhaseDiscoveryError:
            return None
        for p in phases:
            if script in (p.id_str, p.slug, f"{p.id_str}.{p.slug}", p.path.name):
                return p.path
        return None

    def run(self, *, script: str, args: str = "", prev: "dict | None" = None,
            mounts: "list[tuple[str, str]] | None" = None) -> dict:
        path = self._resolve(script)
        if path is None:
            return self._fail("script_not_found", f"no such script: {script}")
        outcome = run_phase(
            path,
            skill_name=self._root.name,
            user_query=args,
            skill_root=self._root,
            docker_image=self._image,
            prev=prev or {},
            extra_mounts=[(Path(h).expanduser().resolve(), c) for h, c in (mounts or [])],
        )
        return {
            "status": "ok" if outcome.exit_code == 0 else "failed",
            "script": path.name,
            "result": outcome.result,
            "exit_code": outcome.exit_code,
            "duration_ms": outcome.duration_ms,
            "stderr": outcome.stderr if outcome.exit_code != 0 else "",
        }
