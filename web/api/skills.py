"""Installed-skill catalog endpoint.

Walks `~/.zipsa/skills/` (via launcher's `paths.skills_dir()`) and
returns a JSON-friendly list. Broken manifests are skipped silently;
once we wire up a "broken skill" surface in the UI we'll add a
separate field for them.

Each entry includes lifetime run statistics aggregated across every
`<name>@<version>` data dir under `~/.zipsa/` — mirroring the CLI's
`zipsa list` behavior so the web shows the same numbers the user
already sees on the command line.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from zipsa import paths as zipsa_paths
from zipsa.core.skill import Skill


# Only these two files are exposed via the file-view endpoint — keeps
# the route from becoming a path-traversal hole. Anything else 404s.
_VIEWABLE_FILES = frozenset({"manifest.yaml", "SKILL.md"})


router = APIRouter(prefix="/api/skills", tags=["skills"])


def _compute_run_stats(skill_name: str) -> tuple[int, int]:
    """Count summary.json entries across all <name>@* data dirs.

    Returns (total_runs, successful_runs). Garbage summaries are
    skipped silently to match the CLI's resilience to partial logs.
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


def _list_installed() -> list[dict]:
    root = zipsa_paths.skills_dir()
    if not root.exists():
        return []
    out: list[dict] = []
    for item in sorted(root.iterdir()):
        if not (item.is_dir() or item.is_symlink()):
            continue
        try:
            skill = Skill.load(item)
        except Exception:
            continue
        total_runs, successful_runs = _compute_run_stats(skill.name)
        # The manifest doesn't pin a runtime — that's chosen at launch
        # time (CLI default or --runtime flag). `model` is an optional
        # dict (e.g. {"name": "claude-sonnet-4-6"}) and may be absent.
        model_dict = skill.manifest.spec.model or {}
        out.append({
            "name": skill.name,
            "version": skill.manifest.metadata.version,
            "purpose": skill.manifest.spec.purpose.strip(),
            "model": model_dict.get("name"),
            "description": skill.manifest.metadata.description,
            "tags": skill.manifest.metadata.tags or [],
            "total_runs": total_runs,
            "successful_runs": successful_runs,
        })
    return out


@router.get("")
def list_skills() -> dict:
    return {"skills": _list_installed()}


@router.get("/{name}/files/{filename}", response_class=PlainTextResponse)
def get_skill_file(name: str, filename: str) -> str:
    """Return raw manifest.yaml or SKILL.md text for browser viewing.

    Allowlist only — never resolve arbitrary filenames against the
    skill directory.
    """
    if filename not in _VIEWABLE_FILES:
        raise HTTPException(status_code=404, detail="file_not_found")
    skill_root = zipsa_paths.skills_dir() / name
    if not skill_root.exists():
        raise HTTPException(status_code=404, detail="skill_not_found")
    fp = skill_root / filename
    if not fp.exists():
        raise HTTPException(status_code=404, detail="file_not_found")
    return fp.read_text()
