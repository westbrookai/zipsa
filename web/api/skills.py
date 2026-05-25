"""Installed-skill catalog endpoint.

Walks `~/.zipsa/skills/` (via launcher's `paths.skills_dir()`) and
returns a JSON-friendly list. Broken manifests are skipped silently;
once we wire up a "broken skill" surface in the UI we'll add a
separate field for them.
"""

from fastapi import APIRouter

from zipsa import paths as zipsa_paths
from zipsa.core.skill import Skill


router = APIRouter(prefix="/api/skills", tags=["skills"])


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
        })
    return out


@router.get("")
def list_skills() -> dict:
    return {"skills": _list_installed()}
