"""Parse a skill's `zipsa-dist/pyproject.toml`.

The Hybrid Phases runtime reads a skill's metadata from the standard
PEP 621 `[project]` table plus a `[tool.zipsa]` section. Per-phase
config lives under `[tool.zipsa.phases."<id>"]`. The old manifest
schema (`apiVersion` / `kind` / `metadata` / `spec.*`) is intentionally
not parsed — leftover keys load fine, they're just ignored.

See `docs/zipsa-runtime-spec-2026-06-11.md` §1.1.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError


class ProjectInfo(BaseModel):
    """PEP 621 `[project]` table — the part we read."""

    model_config = ConfigDict(extra="ignore")

    name: str
    version: str
    description: str | None = None
    dependencies: list[str] = []


class Limits(BaseModel):
    """`[tool.zipsa.limits]` — whole-skill caps."""

    model_config = ConfigDict(extra="ignore")

    max_cost_usd: float | None = None
    timeout_seconds: int | None = None


class PhaseOverride(BaseModel):
    """`[tool.zipsa.phases."<id>"]` — per-phase config override.

    Every field is optional. Absence means the runtime default applies.
    """

    model_config = ConfigDict(extra="ignore")

    max_turns: int | None = None
    allowed_tools: list[str] = []
    cost_warn_threshold_usd: float | None = None
    model: dict[str, Any] | None = None


class ZipsaConfig(BaseModel):
    """`[tool.zipsa]` — runtime config for the skill."""

    model_config = ConfigDict(extra="ignore")

    description: str
    credentials: list[str] = []
    schedule: str | None = None
    allows_staging_run: bool = False
    max_run_depth: int = 3
    limits: Limits = Limits()
    phases: dict[str, PhaseOverride] = {}


class PyprojectMeta(BaseModel):
    """Parsed view of a skill's pyproject.toml."""

    model_config = ConfigDict(extra="ignore")

    project: ProjectInfo
    zipsa: ZipsaConfig


class PyprojectError(Exception):
    """Raised when the skill's pyproject.toml is missing, can't be
    parsed, or fails schema validation.
    """


def load_pyproject(skill_root: Path) -> PyprojectMeta:
    """Load and validate `<skill_root>/zipsa-dist/pyproject.toml`.

    The file must contain a `[project]` table with at least `name` and
    `version`, plus a `[tool.zipsa]` section with at least
    `description`. Everything else has a sensible default.
    """
    dist = skill_root / "zipsa-dist"
    if not dist.is_dir():
        raise PyprojectError(
            f"{skill_root}: missing zipsa-dist/ directory"
        )

    pyproject_path = dist / "pyproject.toml"
    if not pyproject_path.is_file():
        raise PyprojectError(
            f"{skill_root}: missing zipsa-dist/pyproject.toml"
        )

    try:
        raw = tomllib.loads(pyproject_path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise PyprojectError(
            f"{pyproject_path}: failed to parse TOML: {e}"
        ) from e

    project = raw.get("project")
    if not isinstance(project, dict):
        raise PyprojectError(
            f"{pyproject_path}: missing [project] table"
        )

    tool = raw.get("tool") or {}
    zipsa_raw = tool.get("zipsa")
    if not isinstance(zipsa_raw, dict):
        raise PyprojectError(
            f"{pyproject_path}: missing [tool.zipsa] section"
        )

    try:
        return PyprojectMeta(project=project, zipsa=zipsa_raw)
    except ValidationError as e:
        raise PyprojectError(
            f"{pyproject_path}: invalid schema — {e}"
        ) from e
