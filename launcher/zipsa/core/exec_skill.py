"""Loader + Pydantic models for exec-format skill metadata (#156).

The exec-world analog of `Skill.load`. An exec-format skill carries no
`manifest.yaml`; its metadata splits across two layers by owner/audience:

  - SKILL.md YAML frontmatter — standard Agent Skills / Claude Code fields
    (`name`, `description`, optional `allowed-tools`/`disallowed-tools`,
    `model`). Plain Claude Code honors these runtime-free.
  - zipsa/package.yaml — zipsa-only sidecar (`version` REQUIRED, optional
    `author`, `tags`, `limits`, `requires`). Plain Claude ignores it.

Identity = frontmatter `name` + package.yaml `version`.

Design decisions:
- `requires` folds the mount mapping into each requirement (no separate
  `mounts:` section): directory → `container`, list[directory] →
  `container_prefix`. This keeps a host-side dependency and its mount in
  one place — the audience-honest shape for two-mode portability.
- `allowed-tools`/`disallowed-tools` accept BOTH a space/comma-separated
  string (the Claude Code frontmatter form) AND a YAML list, normalized
  to a list, so authors can write either.

Gotchas:
- Missing `name` (SKILL.md) or `version` (package.yaml) raises
  `ExecSkillError` naming the field AND the file — these are the two
  identity fields and the most common authoring mistake.
- See `docs/superpowers/specs/2026-06-18-exec-skill-metadata.md`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


class ExecSkillError(Exception):
    """Raised when an exec-format skill's metadata cannot be loaded.

    Reasons: missing SKILL.md / package.yaml, malformed YAML, missing the
    required `name` or `version` field, or an invalid requirement shape.
    """


_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(?P<body>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)",
    re.DOTALL,
)


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Return the YAML mapping between the leading `---` fences of `text`.

    Returns an empty dict when there is no frontmatter block. Raises
    `ExecSkillError` if the block is present but not a YAML mapping.
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}
    try:
        data = yaml.safe_load(match.group("body"))
    except yaml.YAMLError as exc:
        raise ExecSkillError(f"SKILL.md frontmatter is not valid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ExecSkillError(
            f"SKILL.md frontmatter must be a YAML mapping, got "
            f"{type(data).__name__}"
        )
    return data


def _to_tool_list(value: Any) -> Optional[list[str]]:
    """Normalize an allowed-tools/disallowed-tools value to a list[str].

    Accepts a YAML list, or a string with space- and/or comma-separated
    tool names (the Claude Code frontmatter form). Returns None for an
    absent value.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [tok for tok in re.split(r"[\s,]+", value.strip()) if tok]
    raise ExecSkillError(
        f"tools must be a string or list, got {type(value).__name__}"
    )


class ExecLimits(BaseModel):
    """Optional zipsa runtime guardrails (Claude Code has no such limits)."""

    model_config = ConfigDict(extra="forbid")

    max_turns: Optional[int] = None
    max_cost_usd: Optional[float] = None
    timeout_seconds: Optional[int] = None


class Requirement(BaseModel):
    """One host-side directory the skill needs, with its mount folded in.

    v1 supports `directory` and `list[directory]` only (the types that
    need mounting). A `directory` carries `container` (single mount
    point); a `list[directory]` carries `container_prefix` (expanded as
    `<prefix>/<basename>` per item). `preserve_host_path` mounts each
    value at its own absolute host path instead.
    """

    model_config = ConfigDict(extra="forbid")

    type: str  # "directory" | "list[directory]"
    prompt: str
    container: Optional[str] = None
    container_prefix: Optional[str] = None
    mode: str = "ro"
    preserve_host_path: bool = False

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in ("directory", "list[directory]"):
            raise ValueError(
                f"unsupported requirement type {v!r} "
                "(v1 supports: directory, list[directory])"
            )
        return v

    @field_validator("prompt")
    @classmethod
    def _prompt_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("prompt must be non-empty")
        return v

    @field_validator("mode")
    @classmethod
    def _mode_valid(cls, v: str) -> str:
        if v not in ("ro", "rw"):
            raise ValueError(f"mode must be 'ro' or 'rw', got {v!r}")
        return v

    @model_validator(mode="after")
    def _mount_shape(self) -> "Requirement":
        if self.preserve_host_path:
            if self.container is not None or self.container_prefix is not None:
                raise ValueError(
                    "preserve_host_path cannot be combined with 'container' "
                    "or 'container_prefix' (the container path IS the host path)"
                )
            return self
        if self.type == "list[directory]":
            if self.container is not None:
                raise ValueError(
                    "list[directory] uses 'container_prefix', not 'container'"
                )
        else:  # directory
            if self.container_prefix is not None:
                raise ValueError(
                    "directory uses 'container', not 'container_prefix'"
                )
        return self


class ExecSkill(BaseModel):
    """Parsed metadata for an exec-format skill (frontmatter + package.yaml).

    Identity is `name` (frontmatter) + `version` (package.yaml).
    """

    model_config = ConfigDict(extra="ignore")

    # Layer 1 — SKILL.md frontmatter
    name: str
    description: Optional[str] = None
    allowed_tools: Optional[list[str]] = None
    disallowed_tools: Optional[list[str]] = None
    model: Optional[str] = None

    # Layer 2 — zipsa/package.yaml
    version: str
    author: Optional[str] = None
    tags: Optional[list[str]] = None
    limits: Optional[ExecLimits] = None
    requires: dict[str, Requirement] = Field(default_factory=dict)


def is_exec_format(skill_dir: Path) -> bool:
    """Return True when `skill_dir` looks like an exec-format skill.

    An exec-format skill has:
      - SKILL.md (required)
      - EITHER scripts/ (new layout) OR zipsa-dist/ (legacy exec layout)
      - NO manifest.yaml at the skill root (the legacy root-manifest marker)
      - NO zipsa-dist/manifest.yaml (the new-structure legacy marker — its
        presence means DockerExecutor owns the skill even with no root manifest)

    The last check distinguishes exec skills that happen to have a zipsa-dist/
    folder from "new-structure" legacy skills whose manifest lives inside
    zipsa-dist/ rather than at the root.
    """
    skill_dir = Path(skill_dir)
    return (
        (skill_dir / "SKILL.md").is_file()
        and (
            (skill_dir / "scripts").is_dir()
            or (skill_dir / "zipsa-dist").is_dir()
        )
        and not (skill_dir / "manifest.yaml").exists()
        and not (skill_dir / "zipsa-dist" / "manifest.yaml").exists()
    )


def load_exec_skill(skill_dir: Path) -> ExecSkill:
    """Load an exec-format skill's metadata from `skill_dir`.

    Parses SKILL.md frontmatter and zipsa/package.yaml into an
    `ExecSkill`. Raises `ExecSkillError` (naming the field + file) on any
    missing required field or invalid shape.
    """
    skill_dir = Path(skill_dir)
    skill_md = skill_dir / "SKILL.md"
    package_yaml = skill_dir / "zipsa" / "package.yaml"

    if not skill_md.is_file():
        raise ExecSkillError(f"{skill_dir}: missing SKILL.md")
    if not package_yaml.is_file():
        raise ExecSkillError(
            f"{skill_dir}: missing zipsa/package.yaml (exec-skill package manifest)"
        )

    frontmatter = parse_frontmatter(skill_md.read_text())

    try:
        package = yaml.safe_load(package_yaml.read_text())
    except yaml.YAMLError as exc:
        raise ExecSkillError(
            f"{package_yaml}: not valid YAML: {exc}"
        ) from exc
    if package is None:
        package = {}
    if not isinstance(package, dict):
        raise ExecSkillError(
            f"{package_yaml}: must be a YAML mapping, got {type(package).__name__}"
        )

    if "name" not in frontmatter or not frontmatter.get("name"):
        raise ExecSkillError(
            f"{skill_md}: missing required frontmatter field 'name'"
        )
    if "version" not in package or not package.get("version"):
        raise ExecSkillError(
            f"{package_yaml}: missing required field 'version'"
        )

    data: dict[str, Any] = {
        "name": frontmatter.get("name"),
        "description": frontmatter.get("description"),
        "allowed_tools": _to_tool_list(frontmatter.get("allowed-tools")),
        "disallowed_tools": _to_tool_list(frontmatter.get("disallowed-tools")),
        "model": frontmatter.get("model"),
        "version": str(package.get("version")),
        "author": package.get("author"),
        "tags": package.get("tags"),
        "limits": package.get("limits"),
        "requires": package.get("requires") or {},
    }

    try:
        return ExecSkill.model_validate(data)
    except ValidationError as exc:
        raise ExecSkillError(
            f"{skill_dir}: invalid exec-skill metadata: {exc}"
        ) from exc
