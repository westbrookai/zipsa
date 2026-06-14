"""ExecSkillHandler — host-side body of mcp__zipsa__exec.

The authoring container calls this to test the skill it's drafting.
It runs ON THE HOST: validate the staging path, discover phases, and
run them via exec_runner.run_phases in docker mode — so the host spawns
a fresh runtime container per phase, exactly the path an end user gets.

Containment: the path must resolve under ~/.zipsa/staging. Because
`zipsa create` mounts the staging dir into the authoring container at
its own host path, the path the agent passes back is already
host-valid — no translation, just the containment guard.
"""

from __future__ import annotations

from pathlib import Path

from .. import paths as zipsa_paths
from ..exec_runner import run_phases
from .phase_discovery import PhaseDiscoveryError, discover_phases


class ExecSkillHandler:
    def __init__(self, docker_image: str) -> None:
        self._image = docker_image

    def run(self, *, staging_path: str, args: str = "") -> dict:
        path = Path(staging_path).resolve()

        staging_root = (zipsa_paths.zipsa_home() / "staging").resolve()
        try:
            path.relative_to(staging_root)
        except ValueError:
            return self._fail(
                "exec_path_outside_staging",
                f"path must be under {staging_root}: {staging_path}",
            )

        if not path.is_dir():
            return self._fail(
                "exec_staging_not_found", f"no such staging dir: {path}",
            )

        try:
            phases = discover_phases(path)
        except PhaseDiscoveryError as e:
            return self._fail("exec_no_phases", str(e))

        results = run_phases(
            phases,
            skill_name=path.name,
            user_query=args,
            skill_root=path,
            docker_image=self._image,
        )

        last = results[-1]
        phase_summaries = [
            {
                "id": p.id_str,
                "slug": p.slug,
                "exit_code": r.exit_code,
                "duration_ms": r.duration_ms,
            }
            for p, r in zip(phases, results)
        ]
        return {
            "status": "ok" if last.exit_code == 0 else "failed",
            "skill_name": path.name,
            "mode": last.mode,
            "result": last.result,
            "exit_code": last.exit_code,
            "duration_ms": sum(r.duration_ms for r in results),
            "out_dir": last.out_dir,
            "phases": phase_summaries,
            "stderr": last.stderr if last.exit_code != 0 else "",
        }

    @staticmethod
    def _fail(code: str, message: str) -> dict:
        return {
            "status": "failed",
            "skill_name": None,
            "result": None,
            "exit_code": -1,
            "phases": [],
            "error": {"code": code, "message": message},
        }
