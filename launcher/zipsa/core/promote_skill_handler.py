"""PromoteSkillHandler — host-side body of mcp__zipsa__promote.

The last step of `zipsa create`: once the authoring conversation settles
on a name, move the staging skill into the repo's skills/<name>/. This
is the only step that touches the repo — until promote, a discarded
draft leaves no trace.

Validates the name (kebab slug), the staging path (must be under
~/.zipsa/staging), and that the destination is free, before moving.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .. import paths as zipsa_paths

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


class PromoteSkillHandler:
    def __init__(self, dest_root: Path) -> None:
        # The repo's skills/ directory where promoted skills land.
        self._dest_root = Path(dest_root)

    def run(self, *, staging_path: str, name: str) -> dict:
        if not _NAME_RE.match(name):
            return self._fail(
                "promote_bad_name",
                f"name must be kebab-case (a-z, 0-9, hyphen; letter first): {name!r}",
            )

        path = Path(staging_path).resolve()
        staging_root = (zipsa_paths.zipsa_home() / "staging").resolve()
        try:
            path.relative_to(staging_root)
        except ValueError:
            return self._fail(
                "promote_path_outside_staging",
                f"path must be under {staging_root}: {staging_path}",
            )

        if not path.is_dir():
            return self._fail(
                "promote_staging_not_found", f"no such staging dir: {path}",
            )

        dest = self._dest_root / name
        if dest.exists():
            return self._fail(
                "promote_name_taken", f"skills/{name} already exists",
            )

        self._dest_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))

        return {"status": "ok", "name": name, "path": str(dest)}

    @staticmethod
    def _fail(code: str, message: str) -> dict:
        return {
            "status": "failed",
            "error": {"code": code, "message": message},
        }
