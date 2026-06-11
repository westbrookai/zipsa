"""Filename-based phase discovery for the Hybrid Phases runtime.

A skill's `zipsa-dist/` directory holds one file per phase. Filenames
follow `<dotted-int>.<slug>.{py,md}`:

  1.preflight.py        →  id=(1,),   slug="preflight",        kind="py"
  2.fetch.md            →  id=(2,),   slug="fetch",            kind="md"
  3.1.fetch-from-db.py  →  id=(3,1),  slug="fetch-from-db",    kind="py"

Phases sort by the int tuple of their dotted id, so `10` follows `2`,
and sub-phases (`3.1`, `3.2`) sit between their parent level (`3`) and
the next (`4`).

Files that don't match the pattern (helpers, READMEs, configs) are
silently ignored — that's how skills ship supporting modules alongside
their phases.

See `docs/zipsa-runtime-spec-2026-06-11.md` §1.2 for the contract.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# <dotted-int>.<kebab-slug>.<ext>. Slug must start with a letter; allows
# lowercase letters, digits, and hyphens after that. The dotted-int part
# requires at least one segment, optional additional `.<int>` segments.
_PHASE_FILENAME = re.compile(
    r"^(?P<id>\d+(?:\.\d+)*)\.(?P<slug>[a-z][a-z0-9-]*)\.(?P<ext>py|md)$"
)


@dataclass(frozen=True)
class Phase:
    """One discovered phase."""

    id_tuple: tuple[int, ...]
    id_str: str
    slug: str
    kind: str  # "py" or "md"
    path: Path


class PhaseDiscoveryError(Exception):
    """Raised when a skill cannot be loaded as a sequence of phases.

    Reasons:
    - missing `zipsa-dist/` directory
    - `zipsa-dist/` exists but contains no phase files
    - two files share the same phase id
    """


def discover_phases(skill_root: Path) -> list[Phase]:
    """Return the skill's phases in execution order.

    `skill_root` is the directory that contains `SKILL.md` and
    `zipsa-dist/`. Only files inside `zipsa-dist/` whose names match the
    phase-filename pattern become phases; everything else is ignored.

    The returned list is sorted by phase id (numeric tuple, not
    lexicographic).

    A warning is logged if the first phase is `.md` — production skills
    should start with `.py` so deterministic preflight runs before any
    LLM cost.
    """
    dist = skill_root / "zipsa-dist"
    if not dist.is_dir():
        raise PhaseDiscoveryError(
            f"{skill_root}: missing zipsa-dist/ directory"
        )

    phases: list[Phase] = []
    seen_ids: dict[str, Path] = {}

    for entry in sorted(dist.iterdir()):
        if not entry.is_file():
            continue
        match = _PHASE_FILENAME.match(entry.name)
        if match is None:
            continue
        id_str = match.group("id")
        if id_str in seen_ids:
            raise PhaseDiscoveryError(
                f"{skill_root}: duplicate phase id '{id_str}' — "
                f"{seen_ids[id_str].name} and {entry.name}"
            )
        seen_ids[id_str] = entry
        id_tuple = tuple(int(part) for part in id_str.split("."))
        phases.append(
            Phase(
                id_tuple=id_tuple,
                id_str=id_str,
                slug=match.group("slug"),
                kind=match.group("ext"),
                path=entry,
            )
        )

    if not phases:
        raise PhaseDiscoveryError(
            f"{skill_root}: no phases found in zipsa-dist/"
        )

    phases.sort(key=lambda p: p.id_tuple)

    if phases[0].kind == "md":
        logger.warning(
            "%s: first phase is .md (%s). Production skills should "
            "start with .py for deterministic preflight.",
            skill_root,
            phases[0].path.name,
        )

    return phases
