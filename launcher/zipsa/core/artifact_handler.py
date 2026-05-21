"""ArtifactHandler — read skill-written artifacts from a run's artifacts dir.

A skill running in its container writes structured output to
``/home/agent/runs/current/artifacts/<name>``. On the host that lands in
``<run_dir>/artifacts/<name>``. The orchestrator (or any host-side
caller) reads those files through this handler — which is the value
producer behind the MCP ``get_artifact`` tool.

Why a separate file from ``hitl_mcp.py``: artifacts are not user-facing
I/O; they're cross-process data exchange between skills.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath

from .. import paths as zipsa_paths


_MAX_ARTIFACT_BYTES = 10 * 1024 * 1024  # 10 MiB cap; artifacts are summaries, not blobs


class ArtifactHandler:
    """Read an artifact file written by a skill into its run_dir/artifacts/."""

    def run(self, *, skill: str, version: str, run_id: str, name: str) -> dict:
        if self._is_unsafe_name(name):
            raise RuntimeError(
                f"ARTIFACT_BAD_NAME: name must be a flat filename, got {name!r}"
            )

        artifacts_dir = zipsa_paths.skill_run_artifacts_dir(skill, version, run_id)
        path = artifacts_dir / name

        if not path.is_file():
            raise RuntimeError(
                f"ARTIFACT_NOT_FOUND: {skill}@{version}/runs/{run_id}/artifacts/{name}"
            )

        size = path.stat().st_size
        if size > _MAX_ARTIFACT_BYTES:
            raise RuntimeError(
                f"ARTIFACT_TOO_LARGE: {name} is {size} bytes (cap {_MAX_ARTIFACT_BYTES})"
            )

        if name.endswith(".json"):
            try:
                content: object = json.loads(path.read_text())
            except json.JSONDecodeError as e:
                raise RuntimeError(f"ARTIFACT_BAD_JSON: {name}: {e}") from e
        else:
            content = path.read_text()

        return {"name": name, "size": size, "content": content}

    @staticmethod
    def _is_unsafe_name(name: str) -> bool:
        """A safe artifact name is a flat filename: no path separators,
        no '..' parts, not absolute. We compare via PurePosixPath since
        Docker / linux path semantics drive the container side."""
        if not name:
            return True
        p = PurePosixPath(name)
        if p.is_absolute():
            return True
        if len(p.parts) != 1:
            return True
        if ".." in p.parts:
            return True
        # Also reject Windows-style separator just in case
        if "\\" in name:
            return True
        return False
