"""Per-user, per-skill host-side configuration storage.

Schema declared in skill manifest's spec.requires:; values prompted on
first run and stored at ~/.zipsa/<skill>@<version>/requires.yaml.
Read by the launcher BEFORE the container starts (e.g. to expand mounts).

This module is pure — file I/O is explicit, no global state, easy to
test with tmp_path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

import yaml


def validate_value(type_name: str, value: Any) -> Any:
    """Validate `value` against `type_name`. Returns the normalized value
    (absolute paths for directory types). Raises ValueError on mismatch."""
    if type_name == "string":
        if not isinstance(value, str):
            raise ValueError(f"expected string, got {type(value).__name__}")
        if not value.strip():
            raise ValueError("string must be non-empty")
        return value

    if type_name == "directory":
        if not isinstance(value, str):
            raise ValueError(f"expected string path, got {type(value).__name__}")
        p = Path(value).expanduser()
        if not p.exists():
            raise ValueError(f"directory does not exist: {value}")
        if not p.is_dir():
            raise ValueError(f"path is not a directory: {value}")
        return str(p.resolve())

    if type_name == "list[directory]":
        if not isinstance(value, list):
            raise ValueError(f"expected list, got {type(value).__name__}")
        return [validate_value("directory", v) for v in value]

    raise ValueError(f"unsupported type: {type_name}")


def load_requires(path: Path) -> dict[str, Any]:
    """Load values from a requires.yaml. Returns {} when the file is missing."""
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"requires.yaml must be a dict, got {type(data).__name__}")
    return data


def save_requires(path: Path, values: dict[str, Any]) -> None:
    """Write values to a requires.yaml atomically (tmp + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(values, sort_keys=True, default_flow_style=False))
    tmp.replace(path)


def classify_state(
    spec: dict[str, "RequiresEntry"],  # type: ignore[name-defined]
    saved: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Partition required keys into (ok, needs_prompt, needs_revalidation).

    - ok: values present + type ok + (if path) exists
    - needs_prompt: missing OR type mismatch (will be RE-asked)
    - needs_revalidation: type ok but path went stale (was valid, now isn't)
    """
    ok: dict[str, Any] = {}
    needs_prompt: list[str] = []
    needs_revalidation: list[str] = []

    for key, entry in spec.items():
        if key not in saved:
            needs_prompt.append(key)
            continue

        value = saved[key]
        # Check type structurally first
        try:
            if entry.type == "string":
                if not isinstance(value, str) or not value.strip():
                    needs_prompt.append(key)
                    continue
                ok[key] = value
            elif entry.type == "directory":
                if not isinstance(value, str):
                    needs_prompt.append(key)
                    continue
                p = Path(value).expanduser()
                if p.exists() and p.is_dir():
                    ok[key] = str(p.resolve())
                else:
                    needs_revalidation.append(key)
            elif entry.type == "list[directory]":
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    needs_prompt.append(key)
                    continue
                resolved = []
                stale = False
                for v in value:
                    p = Path(v).expanduser()
                    if not (p.exists() and p.is_dir()):
                        stale = True
                        break
                    resolved.append(str(p.resolve()))
                if stale:
                    needs_revalidation.append(key)
                else:
                    ok[key] = resolved
        except (OSError, ValueError):
            needs_prompt.append(key)

    return ok, needs_prompt, needs_revalidation


def _version_key(version: str) -> tuple:
    """Sort versions by (major, minor, patch, rest). 0.4.10 > 0.4.9."""
    parts = []
    for chunk in version.split("."):
        try:
            parts.append((0, int(chunk)))
        except ValueError:
            parts.append((1, chunk))
    return tuple(parts)


def carry_over_from_previous(
    skill_name: str,
    new_version: str,
    spec: dict[str, "RequiresEntry"],  # type: ignore[name-defined]
) -> Optional[tuple[str, dict[str, Any]]]:
    """Look for an earlier version of this skill with a requires.yaml, and
    return (previous_version, filtered_values) where filtered_values
    contains only keys whose current schema's type structurally matches
    the saved value.

    Returns None when no previous version exists OR no keys can be carried.
    """
    from zipsa.paths import zipsa_home

    home = zipsa_home()
    if not home.exists():
        return None

    prefix = f"{skill_name}@"
    candidates: list[tuple[tuple, str, dict[str, Any]]] = []
    for entry in home.iterdir():
        if not entry.is_dir():
            continue
        if not entry.name.startswith(prefix):
            continue
        ver = entry.name[len(prefix):]
        if ver == new_version:
            continue  # Don't consider self
        req_file = entry / "requires.yaml"
        if not req_file.exists():
            continue
        try:
            values = load_requires(req_file)
        except (yaml.YAMLError, ValueError):
            continue
        candidates.append((_version_key(ver), ver, values))

    if not candidates:
        return None

    # Take the most recent previous version's values
    candidates.sort(key=lambda c: c[0], reverse=True)
    _, prev_version, prev_values = candidates[0]

    # Filter to keys whose structural type matches current spec
    filtered: dict[str, Any] = {}
    for key, entry in spec.items():
        if key not in prev_values:
            continue
        val = prev_values[key]
        if entry.type == "string" and isinstance(val, str) and val.strip():
            filtered[key] = val
        elif entry.type == "directory" and isinstance(val, str):
            filtered[key] = val
        elif entry.type == "list[directory]" and isinstance(val, list) and all(isinstance(v, str) for v in val):
            filtered[key] = val

    if not filtered:
        return None
    return prev_version, filtered
