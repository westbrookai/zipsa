"""Static validation for exec-format skills (#159).

The exec-world analog of manifest-schema validation. `zipsa validate`
runs `Skill.load` for legacy skills; exec skills (no manifest.yaml) have
no validation path, so the skill-builder leaned on `zipsa exec` runs as
the de-facto check. This module is the STATIC pre-flight — schema +
structure, no container, no exec.

Design decisions:
- Collect ALL problems in one pass (not fail-fast): an author should see
  every error at once, the way a compiler reports them, rather than
  fixing one and re-running to find the next.
- Reuse the loader (`load_exec_skill`) and phase discovery
  (`discover_phases`) as the sources of truth — validate just turns their
  exceptions into collected errors and adds the standard-compliance
  checks the loader is deliberately lenient about (`description`).

Gotchas:
- `description` is Optional in `ExecSkill` (the loader is lenient) but
  REQUIRED here: the Agent-Skills standard requires it, and validate is
  the strict gate.
- Malformed PEP 723 TOML is a static error (uv run --script would fail at
  run time); a `.py` phase with no `# /// script` block is fine.
- See `docs/superpowers/specs/2026-06-18-validate-exec-skills.md`.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .exec_skill import ExecSkill, ExecSkillError, load_exec_skill
from .phase_discovery import Phase, PhaseDiscoveryError, discover_phases

# PEP 723 inline-metadata block: `# /// script` … `# ///` (commented TOML).
# Mirrors exec_runner._PEP723_RE; kept local so validate doesn't reach into
# the runner's privates.
_PEP723_RE = re.compile(
    r"^# /// script\s*\n((?:#[^\n]*\n)+?)# ///$",
    re.MULTILINE,
)


@dataclass
class ExecValidation:
    """The outcome of statically validating one exec-format skill."""

    skill_dir: Path
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skill: Optional[ExecSkill] = None
    phases: list[Phase] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Valid iff no errors. Warnings do not fail validation."""
        return not self.errors


def _pep723_toml_error(phase_path: Path) -> Optional[str]:
    """Return an error message if a `.py` phase's PEP 723 block is invalid.

    Returns None when the file is not `.py`, has no block, or the block
    parses cleanly as TOML.
    """
    if phase_path.suffix != ".py":
        return None
    try:
        text = phase_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"{phase_path.name}: cannot read phase file ({exc})"
    match = _PEP723_RE.search(text)
    if match is None:
        return None
    lines = []
    for line in match.group(1).splitlines():
        if line.startswith("# "):
            lines.append(line[2:])
        elif line == "#":
            lines.append("")
        else:
            lines.append(line[1:])
    try:
        tomllib.loads("\n".join(lines))
    except tomllib.TOMLDecodeError as exc:
        return f"{phase_path.name}: invalid PEP 723 inline metadata (not valid TOML): {exc}"
    return None


def validate_exec_skill(skill_dir: Path) -> ExecValidation:
    """Statically validate an exec-format skill directory.

    Checks (errors fail, warnings don't): metadata schema (loader),
    required `description`, phase structure (discovery), and PEP 723 block
    validity. Collects everything in one pass.
    """
    skill_dir = Path(skill_dir)
    report = ExecValidation(skill_dir=skill_dir)

    # 1. Metadata schema — name/version/requires shape, SKILL.md + package.yaml.
    try:
        report.skill = load_exec_skill(skill_dir)
    except ExecSkillError as exc:
        report.errors.append(str(exc).splitlines()[0])

    # 2. description present (standard requires it; loader is lenient).
    if report.skill is not None and not (report.skill.description or "").strip():
        report.errors.append(
            f"{skill_dir / 'SKILL.md'}: missing required frontmatter field "
            "'description' (the Agent Skills standard requires it)"
        )

    # 3. Phase structure — phase dir present, ≥1 phase, no duplicate ids.
    try:
        report.phases = discover_phases(skill_dir)
    except PhaseDiscoveryError as exc:
        report.errors.append(str(exc).splitlines()[0])

    # 5. First phase should be deterministic (.py) — warning only.
    if report.phases and report.phases[0].kind == "md":
        report.warnings.append(
            f"first phase is .md ({report.phases[0].path.name}); production "
            "skills should start with a .py preflight before any LLM cost"
        )

    # 4. PEP 723 blocks in .py phases must be valid TOML.
    for phase in report.phases:
        msg = _pep723_toml_error(phase.path)
        if msg is not None:
            report.errors.append(msg)

    return report
