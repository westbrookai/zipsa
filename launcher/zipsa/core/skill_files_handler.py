"""SkillFilesHandler — write skill-builder's draft to staging.

Powers the `mcp__zipsa__write_skill_files` MCP tool. The skill-builder
agent calls this after the draft phase to materialize the three files
that compose a new skill:

  ~/.zipsa/staging/<name>/
  ├── SKILL.md                  ← author's natural-language source
  └── zipsa-dist/
      ├── instruction.md        ← agent-facing instructions (PR #95)
      └── manifest.yaml         ← launcher config

The tool's authority is tight on purpose — three filenames, one
target tree, name validated as a single safe segment. Anything else
404s rather than silently widening what an agent can write to disk.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Mapping

from .. import paths as zipsa_paths


_ALLOWED_FILES = frozenset({
    "SKILL.md",
    "zipsa-dist/manifest.yaml",
    "zipsa-dist/instruction.md",
})


def _is_unsafe_segment(value: str) -> bool:
    """Single POSIX path segment — matches ArtifactHandler's check.
    Non-empty, no separators, no NUL, no '..', not absolute."""
    if not value:
        return True
    if len(value) > 255:
        return True
    if "\\" in value:
        return True
    if "\x00" in value:
        return True
    p = PurePosixPath(value)
    if p.is_absolute():
        return True
    if ".." in p.parts:
        return True
    if len(p.parts) != 1:
        return True
    return False


class SkillFilesHandler:
    """Write skill-builder's draft files into ~/.zipsa/staging/<name>/."""

    def write(self, *, name: str, files: Mapping[str, object]) -> dict:
        if _is_unsafe_segment(name):
            raise RuntimeError(
                f"SKILL_NAME_BAD: name must be a single safe segment, got {name!r}"
            )
        if not files:
            raise RuntimeError("SKILL_FILES_EMPTY: at least one file required")

        # Validate every filename against the allowlist before writing
        # anything — refuse the whole call rather than half-write a draft.
        for filename, content in files.items():
            if filename not in _ALLOWED_FILES:
                raise RuntimeError(
                    f"SKILL_FILE_BAD_NAME: filename {filename!r} is not in the "
                    f"allowlist {sorted(_ALLOWED_FILES)}"
                )
            if not isinstance(content, str):
                raise RuntimeError(
                    f"SKILL_FILE_BAD_CONTENT: file {filename!r} content must be a "
                    f"string, got {type(content).__name__}"
                )

        staging_dir = (zipsa_paths.zipsa_home() / "staging" / name).resolve()
        # Defense-in-depth: the resolved target must land under staging.
        # _is_unsafe_segment already rejects `..` etc, but a future change
        # to name normalization shouldn't be the thing that turns this
        # into a write-anywhere hole.
        staging_root = (zipsa_paths.zipsa_home() / "staging").resolve()
        try:
            staging_dir.relative_to(staging_root)
        except ValueError as e:
            raise RuntimeError(
                f"SKILL_NAME_BAD: resolved path escapes staging root"
            ) from e

        staging_dir.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        for filename, content in files.items():
            target = staging_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            written.append(filename)

        return {"path": str(staging_dir), "written_files": written}
