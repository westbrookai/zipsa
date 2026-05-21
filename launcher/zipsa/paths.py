"""Centralized path resolution for the zipsa home directory.

Set ZIPSA_HOME to override the default ~/.zipsa location (useful in tests).
"""

import os
from pathlib import Path


def zipsa_home() -> Path:
    env = os.environ.get("ZIPSA_HOME")
    return Path(env) if env else Path.home() / ".zipsa"


def skill_data_dir(name: str, version: str) -> Path:
    return zipsa_home() / f"{name}@{version}"


def skill_runs_dir(name: str, version: str) -> Path:
    return skill_data_dir(name, version) / "runs"


def skill_env_file(name: str, version: str) -> Path:
    return skill_data_dir(name, version) / ".env"


def skill_requires_file(name: str, version: str) -> Path:
    return skill_data_dir(name, version) / "requires.yaml"


def skill_run_artifacts_dir(name: str, version: str, run_id: str) -> Path:
    """Per-run artifacts directory.

    Where a skill writes structured artifacts that other processes
    (orchestrators, future tools) can read via MCP `get_artifact`.
    Located inside the run_dir so artifacts share lifecycle with logs.
    """
    return skill_runs_dir(name, version) / run_id / "artifacts"


def skill_memory_file(name: str) -> Path:
    """Per-skill memory store, cross-version by design.

    Located under ~/.zipsa/memory/<skill>/skill-mem.json so user values
    captured via ask_once (Notion workspace, X voice, etc.) survive
    skill version bumps. Sibling of ~/.zipsa/memory/global-mem.json.
    """
    return zipsa_home() / "memory" / name / "skill-mem.json"


def resolve_skill_memory_path(name: str) -> Path:
    """Return the cross-version skill_memory_file path, performing a
    one-time migration from the latest legacy per-version location if
    the new path is empty but legacy data exists.

    Returns the new path (which may or may not yet exist after this
    call: it exists iff a legacy file was found, or iff a prior write
    has already created it). Callers should treat the returned path
    like any new MemoryStore path — write-on-first-use is fine.
    """
    import shutil
    new = skill_memory_file(name)
    if new.exists():
        return new
    legacy = latest_legacy_skill_memory(name)
    if legacy is not None:
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(legacy, new)
    return new


def latest_legacy_skill_memory(name: str) -> "Path | None":
    """Find the most recent ~/.zipsa/<name>@<version>/memory/skill-mem.json,
    if any. Used for one-time migration to skill_memory_file().

    Returns None when no legacy file exists for this skill.
    """
    home = zipsa_home()
    if not home.exists():
        return None
    prefix = f"{name}@"
    candidates: list[tuple[tuple, Path]] = []
    for entry in home.iterdir():
        if not entry.is_dir() or not entry.name.startswith(prefix):
            continue
        legacy = entry / "memory" / "skill-mem.json"
        if not legacy.exists():
            continue
        version = entry.name[len(prefix):]
        candidates.append((_version_sort_key(version), legacy))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _version_sort_key(version: str) -> tuple:
    """Sort versions by (major, minor, patch, rest). 0.4.10 > 0.4.9.
    Numeric chunks sort before non-numeric (matches requires._version_key)."""
    parts = []
    for chunk in version.split("."):
        try:
            parts.append((0, int(chunk)))
        except ValueError:
            parts.append((1, chunk))
    return tuple(parts)


def global_env_file() -> Path:
    return zipsa_home() / ".env"


def credentials_dir() -> Path:
    return zipsa_home() / "credentials"


class SkillNotInstalledError(Exception):
    pass


def skills_dir() -> Path:
    return zipsa_home() / "skills"


def installed_skill_dir(name: str) -> Path:
    return skills_dir() / name


def resolve_skill(name: str) -> Path:
    path = installed_skill_dir(name)
    if not path.exists():
        raise SkillNotInstalledError(
            f"Skill '{name}' not found. Try: zipsa install <source>"
        )
    return path
