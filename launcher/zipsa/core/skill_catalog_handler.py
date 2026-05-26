"""SkillCatalogHandler — list installed skills as structured JSON.

Powers the `mcp__zipsa__list_skills_catalog` MCP tool used by the
skill-builder agent during the discover/interview phase:
  - "is the skill the user wants already installed?"
  - "what atomic children could this orchestrator compose?"

Mirrors what the CLI's `zipsa list` and the web's `/api/skills` show
(name, version, purpose, model, run stats) so all three views agree.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .. import paths as zipsa_paths


def _compute_run_stats(skill_name: str) -> tuple[int, int]:
    """Total + ok-status runs aggregated across every <name>@* dir.

    Garbage summaries are skipped silently to match the CLI's behaviour
    when a run dir is corrupted or half-written.
    """
    home = zipsa_paths.zipsa_home()
    if not home.exists():
        return 0, 0
    total = 0
    successful = 0
    prefix = f"{skill_name}@"
    for entry in home.iterdir():
        if not entry.is_dir() or not entry.name.startswith(prefix):
            continue
        runs_dir = entry / "runs"
        if not runs_dir.exists():
            continue
        for run_dir in runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            summary = run_dir / "summary.json"
            if not summary.exists():
                continue
            try:
                data = json.loads(summary.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            total += 1
            if data.get("status") == "ok":
                successful += 1
    return total, successful


class SkillCatalogHandler:
    """List installed skills with run statistics."""

    def run(self) -> dict:
        # Local import: Skill.load pulls in models/pydantic which we
        # don't want to import at module-load time for handler files.
        from .skill import Skill

        root = zipsa_paths.skills_dir()
        if not root.exists():
            return {"skills": []}

        out: list[dict] = []
        for item in sorted(root.iterdir()):
            if not (item.is_dir() or item.is_symlink()):
                continue
            try:
                skill = Skill.load(item)
            except Exception:
                # Broken installs are surfaced by `zipsa list` separately;
                # this MCP tool only returns loadable entries so the agent
                # gets a usable catalog instead of guessing what's wrong.
                continue
            total, successful = _compute_run_stats(skill.name)
            model_dict = skill.manifest.spec.model or {}
            out.append({
                "name": skill.name,
                "version": skill.manifest.metadata.version,
                "purpose": skill.manifest.spec.purpose.strip(),
                "model": model_dict.get("name"),
                "description": skill.manifest.metadata.description,
                "tags": skill.manifest.metadata.tags or [],
                "total_runs": total,
                "successful_runs": successful,
            })
        return {"skills": out}
