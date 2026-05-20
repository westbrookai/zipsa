"""Per-user, per-skill host-side configuration storage.

Schema declared in skill manifest's spec.requires:; values prompted on
first run and stored at ~/.zipsa/<skill>@<version>/requires.yaml.
Read by the launcher BEFORE the container starts (e.g. to expand mounts).

This module is pure — file I/O is explicit, no global state, easy to
test with tmp_path.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import yaml

if TYPE_CHECKING:
    from zipsa.core.models import RequiresEntry


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


class RequiresError(Exception):
    """Base for requires-resolution failures (allows callers to catch either
    unset or stale with a single `except RequiresError` block)."""


class RequiresUnsetError(RequiresError):
    """No values saved and no TTY to prompt."""


class RequiresStaleError(RequiresError):
    """Saved values reference paths that no longer exist; no TTY to re-prompt."""


def resolve_requires(
    skill_name: str,
    skill_version: str,
    spec: dict[str, "RequiresEntry"],  # type: ignore[name-defined]
    stream_in,
    stream_out,
    is_interactive: bool,
) -> dict[str, Any]:
    """End-to-end resolution: load + classify + (prompt OR error) + save + return.

    Raises:
        RequiresUnsetError when values are missing and no TTY.
        RequiresStaleError when saved values went stale and no TTY.
    """
    from zipsa.paths import skill_requires_file

    req_file = skill_requires_file(skill_name, skill_version)
    saved = load_requires(req_file) if req_file.exists() else {}
    ok, needs_prompt, needs_revalidation = classify_state(spec, saved)

    if not needs_prompt and not needs_revalidation:
        return ok

    if not is_interactive:
        if needs_prompt:
            raise RequiresUnsetError(
                f"{skill_name} requires configuration for: {', '.join(needs_prompt)}. "
                f"Run: zipsa configure {skill_name}"
            )
        if needs_revalidation:
            raise RequiresStaleError(
                f"{skill_name} has stale paths in requires: {', '.join(needs_revalidation)}. "
                f"Run: zipsa configure {skill_name}"
            )

    # Interactive: handle carry-over for first-time missing, then prompt
    if needs_prompt and not saved:
        carry = carry_over_from_previous(skill_name, skill_version, spec)
        if carry is not None:
            prev_ver, prev_values = carry
            stream_out.write(f"\n[zipsa] Found previous install: {skill_name}@{prev_ver}\n")
            for k, v in prev_values.items():
                if isinstance(v, list):
                    stream_out.write(f"  {k}: {len(v)} item(s)\n")
                else:
                    stream_out.write(f"  {k}: {v!r}\n")
            stream_out.write("Carry over? [Y/n]: ")
            stream_out.flush()
            # Intentional: EOF here (readline() == "") and bare-enter both
            # collapse to "" after strip(), which matches the Y default below.
            # That's the desired UX — the prompt advertises Y as default, so
            # input-unavailable should accept the default rather than abort.
            line = stream_in.readline().strip().lower()
            if line in ("", "y", "yes"):
                # Apply prev values, then re-classify to see what still needs prompting
                merged = dict(prev_values)
                ok2, np2, nr2 = classify_state(spec, merged)
                ok.update(ok2)
                needs_prompt = np2
                needs_revalidation = nr2
                saved = merged

    # Prompt for anything still missing or stale
    to_prompt = [(k, "needs_prompt") for k in needs_prompt] + \
                [(k, "needs_revalidation") for k in needs_revalidation]
    for key, reason in to_prompt:
        entry = spec[key]
        stream_out.write(f"\n{key} — {entry.prompt}\n")
        current = saved.get(key) if reason == "needs_revalidation" else None
        try:
            value = prompt_for_value(entry, stream_in, stream_out, current=current)
        except EOFError:
            raise RequiresUnsetError(
                f"input ended before {key} could be set"
            )
        ok[key] = value
        saved[key] = value

    # Persist
    save_requires(req_file, {**saved, **ok})
    return ok


def prompt_for_value(
    entry: "RequiresEntry",
    stream_in,  # readable, with .readline()
    stream_out,  # writable, .write() + .flush()
    current: Any = None,
    max_attempts: int = 3,
) -> Any:
    """Prompt the user for a single value matching `entry.type`.

    Returns the validated (and normalized) value. Raises ValueError after
    `max_attempts` failed validations. Raises EOFError if stream_in returns
    "" before any line (treated as Ctrl+D / no TTY).
    """
    def _writeln(msg: str = "") -> None:
        stream_out.write(msg + "\n")
        stream_out.flush()

    if current is not None:
        _writeln("Current:")
        if isinstance(current, list):
            for item in current:
                _writeln(f"  {item}")
        else:
            _writeln(f"  {current}")
        _writeln("Press enter to keep, or type new value(s):")

    for attempt in range(max_attempts):
        try:
            if entry.type == "list[directory]":
                lines: list[str] = []
                first_line = stream_in.readline()
                if first_line == "":
                    raise EOFError("no input")
                first_line = first_line.rstrip("\n")
                if first_line == "":
                    if current is not None:
                        return current
                    _writeln("(empty: please enter at least one path)")
                    continue
                lines.append(first_line)
                while True:
                    line = stream_in.readline()
                    if line == "":  # EOF
                        break
                    line = line.rstrip("\n")
                    if line == "":
                        break
                    lines.append(line)
                value = lines
            else:
                # string or directory: single line
                line = stream_in.readline()
                if line == "":
                    raise EOFError("no input")
                line = line.rstrip("\n")
                if line == "" and current is not None:
                    return current
                value = line

            return validate_value(entry.type, value)

        except EOFError:
            raise
        except ValueError as e:
            _writeln(f"  ✗ {e}")
            if attempt < max_attempts - 1:
                _writeln(f"  Try again ({max_attempts - attempt - 1} attempts left):")
            continue

    raise ValueError(f"validation failed after {max_attempts} attempts")
