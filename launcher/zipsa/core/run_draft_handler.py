"""The forge `run` tool: test the draft via the real run-time (an LLM
following SKILL.md). Wraps run_skill_llm, scoped to the staging skill."""
from __future__ import annotations

from pathlib import Path

from ..run_llm import run_skill_llm


class RunDraftHandler:
    def __init__(self, *, image: str, skill_root: Path) -> None:
        self._image = image
        self._root = Path(skill_root)

    def run(self, *, args: str = "",
            mounts: "list[tuple[str, str]] | None" = None) -> dict:
        rc = run_skill_llm(
            self._root, args, image=self._image,
            extra_mounts=[(Path(h), c) for h, c in (mounts or [])],
        )
        return {"status": "ok" if rc == 0 else "failed", "exit_code": rc}
