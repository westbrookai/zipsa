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
from typing import TypedDict

from .. import paths as zipsa_paths


class ArtifactResult(TypedDict):
    name: str
    size: int
    content: object  # parsed JSON object for .json, str otherwise


_MAX_ARTIFACT_BYTES = 10 * 1024 * 1024  # 10 MiB cap; artifacts are summaries, not blobs


class ArtifactHandler:
    """Read an artifact file written by a skill into its run_dir/artifacts/."""

    def run(self, *, skill: str, version: str, run_id: str, name: str) -> ArtifactResult:
        # All four inputs interpolate into the on-disk path. Validate each
        # as a single safe path segment up-front so the audit-log claim
        # "this read came from <skill>@<version>" stays honest — a `run_id`
        # of "../../victim@0.1.0/runs/x" would otherwise resolve to a
        # different skill's directory but still pass the ZIPSA_HOME
        # containment guard below.
        for field, value in (("skill", skill), ("version", version),
                              ("run_id", run_id), ("name", name)):
            if self._is_unsafe_segment(value):
                raise RuntimeError(
                    f"ARTIFACT_BAD_NAME: {field} must be a flat path segment, got {value!r}"
                )

        artifacts_dir = zipsa_paths.skill_run_artifacts_dir(skill, version, run_id)
        path = artifacts_dir / name

        # Defense-in-depth: even with per-segment validation above, require
        # the resolved path to land under ZIPSA_HOME. Catches symlink
        # shenanigans on the host side that the segment check can't see.
        try:
            resolved = path.resolve(strict=False)
            home = zipsa_paths.zipsa_home().resolve()
            resolved.relative_to(home)
        except ValueError as e:
            raise RuntimeError(
                f"ARTIFACT_BAD_NAME: resolved path escapes ZIPSA_HOME"
            ) from e

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
                content = json.loads(path.read_text())
            except json.JSONDecodeError as e:
                raise RuntimeError(f"ARTIFACT_BAD_JSON: {name}: {e}") from e
        else:
            content = path.read_text()

        return {"name": name, "size": size, "content": content}

    @staticmethod
    def _is_unsafe_segment(value: str) -> bool:
        """A safe path segment: non-empty, ≤ 255 chars (POSIX NAME_MAX),
        no path separators, no NUL, no '..', not absolute. Compared via
        PurePosixPath since the container side runs Linux."""
        if not value:
            return True
        if len(value) > 255:
            return True
        # Reject Windows separator explicitly — PurePosixPath treats it
        # as a literal char so "..\\foo" would otherwise look like one part.
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
