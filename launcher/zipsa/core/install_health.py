"""Detect whether an installed-skill entry can actually be loaded.

`zipsa install --link <path>` creates a symlink at
`~/.zipsa/skills/<name>` pointing at the source. If the source is later
removed (e.g. a worktree is cleaned up), the symlink dangles and the
entry can no longer be loaded — but the directory entry in
`~/.zipsa/skills/` is still there. Without explicit health detection,
`zipsa list` silently filters it out and `zipsa install` rejects new
attempts to install over it. This module exposes a single helper used
by both commands to render / handle the broken case explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class InstallHealth:
    """Result of a health check on one installed-skill entry."""
    ok: bool
    reason: Optional[str] = None  # set iff ok is False
    requires_total: int = 0       # number of declared spec.requires entries
    requires_set: int = 0         # number currently present in requires.yaml


def check_install(path: Path) -> InstallHealth:
    """Inspect an installed-skill directory entry and report its health.

    `path` is the on-disk entry inside `~/.zipsa/skills/`. It may be a
    real directory, a valid symlink to one, or a dangling symlink.

    Returns:
        InstallHealth(ok=True, reason=None) on success.
        InstallHealth(ok=False, reason="<short message>") otherwise.
    """
    # Dangling symlink: Path.exists() returns False on broken links,
    # while Path.is_symlink() returns True. Resolve the target so the
    # message names the missing path.
    if path.is_symlink() and not path.exists():
        try:
            raw_target = path.readlink()
            # Resolve relative targets to absolute so the message is useful.
            if not raw_target.is_absolute():
                target = (path.parent / raw_target).resolve()
            else:
                target = raw_target
        except OSError:
            target = "<unreadable>"
        return InstallHealth(ok=False, reason=f"Linked source missing: {target}")

    if not path.exists():
        return InstallHealth(ok=False, reason="Install entry does not exist")

    # Skill-builder writes the new layout under zipsa-dist/; legacy
    # skills still have a root-level manifest.yaml. Either is fine.
    dist_manifest = path / "zipsa-dist" / "manifest.yaml"
    legacy_manifest = path / "manifest.yaml"
    if not dist_manifest.exists() and not legacy_manifest.exists():
        return InstallHealth(ok=False, reason="manifest.yaml not found")

    # Try to load the manifest. We use the same code path as production
    # (Skill.load) so any future load-time validation is caught here too.
    try:
        # Local import keeps install_health side-effect-free at import time.
        from .skill import Skill
        skill = Skill.load(path)
    except Exception as e:
        head = str(e).splitlines()[0] if str(e) else type(e).__name__
        # Keep the reason short — long pydantic stacks blow out terminals.
        head = head[:160]
        return InstallHealth(ok=False, reason=f"Invalid manifest: {head}")

    requires_spec = skill.manifest.spec.requires
    requires_total = len(requires_spec)
    requires_set = 0
    if requires_total > 0:
        from .requires import load_requires, classify_state
        from zipsa.paths import skill_requires_file
        req_file = skill_requires_file(skill.name, skill.manifest.metadata.version)
        saved = load_requires(req_file) if req_file.exists() else {}
        ok_map, _np, _nr = classify_state(requires_spec, saved)
        requires_set = len(ok_map)

    return InstallHealth(ok=True, requires_total=requires_total, requires_set=requires_set)
