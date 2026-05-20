# Requires Config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `spec.requires:` manifest field plus `~/.zipsa/<skill>@<version>/requires.yaml` storage so the launcher can prompt the user once for host-side values (e.g. project parent directories), persist them, validate on every run, and wire them into docker `-v` mounts via new `source:` + `container_prefix:` directive fields.

**Architecture:** A new pure-Python core module (`zipsa/core/requires.py`) owns load/save/validate/carry-over. Pydantic schema in `models.py` declares the manifest fields. Executor reads validated values to expand mounts. CLI gets a new `configure` command and `run` gains a pre-flight requires check. `install_health` returns a count so `zipsa list` can render a warning. No new external dependencies.

**Tech Stack:** Python 3.12, Pydantic, Typer (CLI), pytest, PyYAML (already used for manifests).

**Spec:** [`docs/superpowers/specs/2026-05-20-requires-config-design.md`](../specs/2026-05-20-requires-config-design.md)

---

## File Structure

**New files (4):**

| Path | Responsibility |
|---|---|
| `launcher/zipsa/core/requires.py` | Pure module: load/save/validate/classify/prompt/carry-over. ~250 lines. |
| `launcher/tests/test_requires.py` | Unit tests for `requires.py`. ~300 lines. |
| `launcher/tests/test_configure_command.py` | Integration tests for `zipsa configure`. ~200 lines. |
| `launcher/tests/fixtures/skills/requires-demo/` (dir with `manifest.yaml` + `SKILL.md`) | Fixture skill declaring `spec.requires` for E2E tests. |

**Modified files (10):**

| Path | Change |
|---|---|
| `launcher/zipsa/core/models.py` | New `RequiresEntry`, extend `SkillSpec` with `requires:`, extend `SkillMount` with `source:` + `container_prefix:`, add cross-field validator. |
| `launcher/zipsa/paths.py` | Add `skill_requires_file(name, version)`. |
| `launcher/zipsa/core/executor.py` | `_build_docker_command` expands dynamic mounts; new param `requires_values`. |
| `launcher/zipsa/cli.py` | New `configure` command; `run` calls pre-flight requires resolution; `list` shows configure-status indicator. |
| `launcher/zipsa/core/install_health.py` | `InstallHealth` gets `requires_total` and `requires_set` fields. |
| `launcher/tests/test_models.py` | Schema validation cases for `requires` + `source`/`container_prefix`. |
| `launcher/tests/test_executor.py` | Mount expansion + collision integration tests. |
| `launcher/tests/test_cli.py` | `run` pre-flight tests (TTY/no-TTY/stale/carry-over). |
| `launcher/tests/test_install_health.py` | New `requires_total`/`requires_set` tests. |
| `launcher/CLAUDE.md` | Document the `requires` pattern (one new section). |
| `skills/README.md` | Manifest writer guide section on `requires`. |

---

## Task 1: Manifest schema — `spec.requires` + dynamic mount fields

**Files:**
- Modify: `launcher/zipsa/core/models.py:48-180`
- Modify: `launcher/tests/test_models.py` (append new test class)

### Step 1.1: Write failing test for `RequiresEntry` accepting valid types

- [ ] Add to `launcher/tests/test_models.py`:

```python
class TestRequiresEntry:
    def test_directory_type_loads(self):
        from zipsa.core.models import RequiresEntry
        e = RequiresEntry(type="directory", prompt="Where?")
        assert e.type == "directory"
        assert e.prompt == "Where?"

    def test_list_directory_type_loads(self):
        from zipsa.core.models import RequiresEntry
        e = RequiresEntry(type="list[directory]", prompt="List them")
        assert e.type == "list[directory]"

    def test_string_type_loads(self):
        from zipsa.core.models import RequiresEntry
        e = RequiresEntry(type="string", prompt="Tell me")
        assert e.type == "string"

    def test_unsupported_type_rejected(self):
        from pydantic import ValidationError
        from zipsa.core.models import RequiresEntry
        with pytest.raises(ValidationError):
            RequiresEntry(type="int", prompt="How many?")

    def test_empty_prompt_rejected(self):
        from pydantic import ValidationError
        from zipsa.core.models import RequiresEntry
        with pytest.raises(ValidationError):
            RequiresEntry(type="directory", prompt="")
```

- [ ] **Step 1.2: Run to confirm failure**

```
cd launcher && uv run pytest tests/test_models.py::TestRequiresEntry -v
```
Expected: `ImportError: cannot import name 'RequiresEntry'`

- [ ] **Step 1.3: Implement `RequiresEntry`**

Add to `launcher/zipsa/core/models.py` (between `SkillLimits` and `PhaseSpec`, around line 131):

```python
class RequiresEntry(BaseModel):
    """One per-user, per-skill host-side value the launcher must obtain
    before starting the container.

    Declared in spec.requires.<key>; values stored in
    ~/.zipsa/<skill>@<version>/requires.yaml.
    """

    type: Literal["string", "directory", "list[directory]"]
    prompt: str

    @field_validator("prompt")
    @classmethod
    def _prompt_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("prompt must be non-empty")
        return v
```

- [ ] **Step 1.4: Run to confirm pass**

```
cd launcher && uv run pytest tests/test_models.py::TestRequiresEntry -v
```
Expected: 5 passed

### Step 1.5: Add `requires` field to `SkillSpec`, test key-name validation

- [ ] Append to the same test class:

```python
    def test_requires_block_in_spec_loads(self):
        from zipsa.core.models import SkillSpec
        spec = SkillSpec(
            purpose="test",
            instructions="./SKILL.md",
            requires={
                "project_roots": {"type": "list[directory]", "prompt": "Where?"},
            },
        )
        assert "project_roots" in spec.requires
        assert spec.requires["project_roots"].type == "list[directory]"

    def test_requires_key_must_be_lowercase_underscore(self):
        from pydantic import ValidationError
        from zipsa.core.models import SkillSpec
        with pytest.raises(ValidationError, match="lowercase"):
            SkillSpec(
                purpose="test",
                instructions="./SKILL.md",
                requires={"BadKey": {"type": "string", "prompt": "?"}},
            )

    def test_requires_key_must_not_have_dot(self):
        from pydantic import ValidationError
        from zipsa.core.models import SkillSpec
        with pytest.raises(ValidationError):
            SkillSpec(
                purpose="test",
                instructions="./SKILL.md",
                requires={"a.b": {"type": "string", "prompt": "?"}},
            )

    def test_requires_empty_dict_is_default(self):
        from zipsa.core.models import SkillSpec
        spec = SkillSpec(purpose="test", instructions="./SKILL.md")
        assert spec.requires == {}
```

- [ ] **Step 1.6: Run to confirm failure**

```
cd launcher && uv run pytest tests/test_models.py::TestRequiresEntry -v
```
Expected: 4 new failures (`SkillSpec` has no `requires` field).

- [ ] **Step 1.7: Add `requires` to `SkillSpec` + validator**

Add a module-level constant near line 9:

```python
_REQUIRES_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
```

In `class SkillSpec` (around line 146), add field after `config`:

```python
    requires: dict[str, RequiresEntry] = Field(default_factory=dict)
```

Add validator inside `SkillSpec`:

```python
    @field_validator("requires")
    @classmethod
    def _validate_requires_keys(cls, v: dict[str, RequiresEntry]) -> dict[str, RequiresEntry]:
        for key in v.keys():
            if not _REQUIRES_KEY_RE.match(key):
                raise ValueError(
                    f"requires key {key!r} must be lowercase letters/digits/"
                    "underscores and start with a letter"
                )
        return v
```

- [ ] **Step 1.8: Run to confirm pass**

```
cd launcher && uv run pytest tests/test_models.py::TestRequiresEntry -v
```
Expected: 9 passed

### Step 1.9: Extend `SkillMount` with `source` + `container_prefix`; cross-field validator

- [ ] Add new test class to `launcher/tests/test_models.py`:

```python
class TestDynamicMount:
    def test_static_mount_still_works(self):
        from zipsa.core.models import SkillMount
        m = SkillMount(host="~/x", container="/y", mode="ro")
        assert m.host == "~/x"
        assert m.source is None
        assert m.container_prefix is None

    def test_source_with_container_loads(self):
        from zipsa.core.models import SkillMount
        m = SkillMount(source="requires.obsidian_vault", container="/vault", mode="ro")
        assert m.source == "requires.obsidian_vault"
        assert m.container == "/vault"
        assert m.host is None

    def test_source_with_container_prefix_loads(self):
        from zipsa.core.models import SkillMount
        m = SkillMount(source="requires.project_roots", container_prefix="/projects/", mode="ro")
        assert m.source == "requires.project_roots"
        assert m.container_prefix == "/projects/"

    def test_host_and_source_together_rejected(self):
        from pydantic import ValidationError
        from zipsa.core.models import SkillMount
        with pytest.raises(ValidationError, match="mutually exclusive"):
            SkillMount(host="~/x", source="requires.y", container="/z")

    def test_container_and_container_prefix_together_rejected(self):
        from pydantic import ValidationError
        from zipsa.core.models import SkillMount
        with pytest.raises(ValidationError, match="container and container_prefix"):
            SkillMount(source="requires.y", container="/z", container_prefix="/zz/")

    def test_container_prefix_must_end_with_slash(self):
        from pydantic import ValidationError
        from zipsa.core.models import SkillMount
        with pytest.raises(ValidationError, match="end with '/'"):
            SkillMount(source="requires.y", container_prefix="/projects")

    def test_source_without_container_or_prefix_rejected(self):
        from pydantic import ValidationError
        from zipsa.core.models import SkillMount
        with pytest.raises(ValidationError, match="must set"):
            SkillMount(source="requires.y")

    def test_neither_host_nor_source_rejected(self):
        from pydantic import ValidationError
        from zipsa.core.models import SkillMount
        with pytest.raises(ValidationError, match="must set"):
            SkillMount(container="/y")

    def test_source_must_start_with_requires_dot(self):
        from pydantic import ValidationError
        from zipsa.core.models import SkillMount
        with pytest.raises(ValidationError, match="requires\\."):
            SkillMount(source="config.x", container="/y")
```

- [ ] **Step 1.10: Run to confirm failure**

```
cd launcher && uv run pytest tests/test_models.py::TestDynamicMount -v
```
Expected: All 9 fail.

- [ ] **Step 1.11: Extend `SkillMount`**

Replace `class SkillMount` in `launcher/zipsa/core/models.py:56-75` with:

```python
class SkillMount(BaseModel):
    """Generic bind mount.

    Two forms:
      - Static: set `host` + `container`. host expanded at run time.
      - Dynamic: set `source` (e.g. 'requires.project_roots') + either
        `container` (single directory) or `container_prefix`
        (list[directory] expanded to one mount per item).
    """

    host: Optional[str] = None
    source: Optional[str] = None
    container: Optional[str] = None
    container_prefix: Optional[str] = None
    mode: Literal["ro", "rw"] = "ro"

    @field_validator("container")
    @classmethod
    def _container_must_be_absolute(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.startswith("/"):
            raise ValueError(
                f"container path must be absolute (starts with '/'), got {v!r}"
            )
        return v

    @field_validator("container_prefix")
    @classmethod
    def _container_prefix_rules(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not v.startswith("/"):
            raise ValueError(
                f"container_prefix must be absolute (starts with '/'), got {v!r}"
            )
        if not v.endswith("/"):
            raise ValueError(
                f"container_prefix must end with '/', got {v!r}"
            )
        return v

    @field_validator("source")
    @classmethod
    def _source_must_reference_requires(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.startswith("requires."):
            raise ValueError(
                f"source must start with 'requires.', got {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _check_field_combinations(self) -> "SkillMount":
        # host XOR source
        if self.host is not None and self.source is not None:
            raise ValueError("'host' and 'source' are mutually exclusive")
        if self.host is None and self.source is None:
            raise ValueError("must set either 'host' (static) or 'source' (dynamic)")

        # container XOR container_prefix
        if self.container is not None and self.container_prefix is not None:
            raise ValueError("'container' and 'container_prefix' are mutually exclusive")
        if self.container is None and self.container_prefix is None:
            raise ValueError("must set either 'container' or 'container_prefix'")

        # Static mounts can't use container_prefix
        if self.host is not None and self.container_prefix is not None:
            raise ValueError("static mounts (host) cannot use container_prefix")

        return self
```

Add to the import block at the top of `models.py` (around line 1-7):

```python
from pydantic import BaseModel, Field, field_validator, model_validator
```

(Replace the existing `from pydantic import` line.)

- [ ] **Step 1.12: Run to confirm pass**

```
cd launcher && uv run pytest tests/test_models.py::TestDynamicMount tests/test_models.py::TestRequiresEntry -v
```
Expected: 18 passed

### Step 1.13: Cross-validate `source` references existing `requires` key (manifest-level)

This validator lives on `SkillSpec` because it needs both `requires:` and `mounts:`.

- [ ] Add tests:

```python
class TestRequiresMountIntegration:
    def test_source_referencing_existing_key_passes(self):
        from zipsa.core.models import SkillSpec
        spec = SkillSpec(
            purpose="t", instructions="./SKILL.md",
            requires={"project_roots": {"type": "list[directory]", "prompt": "?"}},
            mounts=[{"source": "requires.project_roots", "container_prefix": "/projects/"}],
        )
        assert spec.mounts[0].source == "requires.project_roots"

    def test_source_referencing_unknown_key_rejected(self):
        from pydantic import ValidationError
        from zipsa.core.models import SkillSpec
        with pytest.raises(ValidationError, match="unknown requires key"):
            SkillSpec(
                purpose="t", instructions="./SKILL.md",
                requires={"project_roots": {"type": "list[directory]", "prompt": "?"}},
                mounts=[{"source": "requires.nonexistent", "container": "/x"}],
            )

    def test_directory_type_must_use_container(self):
        from pydantic import ValidationError
        from zipsa.core.models import SkillSpec
        with pytest.raises(ValidationError, match="container.*for single directory"):
            SkillSpec(
                purpose="t", instructions="./SKILL.md",
                requires={"obsidian_vault": {"type": "directory", "prompt": "?"}},
                mounts=[{"source": "requires.obsidian_vault", "container_prefix": "/v/"}],
            )

    def test_list_directory_type_must_use_container_prefix(self):
        from pydantic import ValidationError
        from zipsa.core.models import SkillSpec
        with pytest.raises(ValidationError, match="container_prefix.*for list"):
            SkillSpec(
                purpose="t", instructions="./SKILL.md",
                requires={"project_roots": {"type": "list[directory]", "prompt": "?"}},
                mounts=[{"source": "requires.project_roots", "container": "/x"}],
            )

    def test_string_type_cannot_be_mount_source(self):
        from pydantic import ValidationError
        from zipsa.core.models import SkillSpec
        with pytest.raises(ValidationError, match="cannot be used as a mount source"):
            SkillSpec(
                purpose="t", instructions="./SKILL.md",
                requires={"voice": {"type": "string", "prompt": "?"}},
                mounts=[{"source": "requires.voice", "container": "/x"}],
            )
```

- [ ] **Step 1.14: Run to confirm failure**

```
cd launcher && uv run pytest tests/test_models.py::TestRequiresMountIntegration -v
```
Expected: All 5 fail (no cross-validator yet).

- [ ] **Step 1.15: Add `SkillSpec.model_validator` for mount-requires consistency**

Add to `class SkillSpec` in `launcher/zipsa/core/models.py` (after the `_reject_path_traversal` validator):

```python
    @model_validator(mode="after")
    def _check_mount_requires_consistency(self) -> "SkillSpec":
        for i, mount in enumerate(self.mounts):
            if mount.source is None:
                continue
            key = mount.source.removeprefix("requires.")
            if key not in self.requires:
                raise ValueError(
                    f"mounts[{i}].source references unknown requires key: {key!r}"
                )
            entry = self.requires[key]
            if entry.type == "directory" and mount.container_prefix is not None:
                raise ValueError(
                    f"mounts[{i}]: use 'container' for single directory "
                    f"(requires.{key} has type=directory)"
                )
            if entry.type == "list[directory]" and mount.container is not None:
                raise ValueError(
                    f"mounts[{i}]: use 'container_prefix' for list "
                    f"(requires.{key} has type=list[directory])"
                )
            if entry.type == "string":
                raise ValueError(
                    f"mounts[{i}]: requires.{key} (type=string) cannot be used as a mount source"
                )
        return self
```

- [ ] **Step 1.16: Run all model tests to confirm pass**

```
cd launcher && uv run pytest tests/test_models.py -v
```
Expected: all passing including existing ones (no regression).

### Step 1.17: Commit Task 1

```bash
cd /Users/neochoon/WestbrookAI/zipsa
git add launcher/zipsa/core/models.py launcher/tests/test_models.py
git commit -m "feat(models): add spec.requires + dynamic mount source fields

- RequiresEntry: per-skill host-side value declaration (type + prompt)
- SkillSpec.requires: dict[str, RequiresEntry] with lowercase-key validator
- SkillMount extended: source (requires.X), container_prefix (list expansion)
- Cross-validator on SkillSpec checks mount source ↔ requires type compatibility

Pure schema work; no consumers yet. Next task wires the storage module."
```

---

## Task 2: Core `requires.py` module + `paths.py` helper

**Files:**
- Create: `launcher/zipsa/core/requires.py`
- Modify: `launcher/zipsa/paths.py`
- Create: `launcher/tests/test_requires.py`

### Step 2.1: Write failing test for `skill_requires_file()` path helper

- [ ] Add to `launcher/tests/test_paths.py` (append new class):

```python
class TestSkillRequiresFile:
    def test_returns_path_inside_skill_data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.paths import skill_requires_file
        p = skill_requires_file("daily-progress", "0.4.0")
        assert p == tmp_path / "daily-progress@0.4.0" / "requires.yaml"
```

- [ ] **Step 2.2: Run to confirm failure**

```
cd launcher && uv run pytest tests/test_paths.py::TestSkillRequiresFile -v
```
Expected: `ImportError: cannot import name 'skill_requires_file'`

- [ ] **Step 2.3: Implement helper**

Add to `launcher/zipsa/paths.py` after `skill_env_file` (around line 25):

```python
def skill_requires_file(name: str, version: str) -> Path:
    return skill_data_dir(name, version) / "requires.yaml"
```

- [ ] **Step 2.4: Run to confirm pass**

```
cd launcher && uv run pytest tests/test_paths.py::TestSkillRequiresFile -v
```
Expected: 1 passed

### Step 2.5: Write failing tests for `validate_value()`

- [ ] Create `launcher/tests/test_requires.py`:

```python
"""Tests for the requires-config core module."""

from pathlib import Path
import io
import pytest


class TestValidateValue:
    def test_string_non_empty_returns_value(self):
        from zipsa.core.requires import validate_value
        assert validate_value("string", "hi") == "hi"

    def test_string_empty_raises(self):
        from zipsa.core.requires import validate_value
        with pytest.raises(ValueError, match="must be non-empty"):
            validate_value("string", "")

    def test_string_whitespace_only_raises(self):
        from zipsa.core.requires import validate_value
        with pytest.raises(ValueError, match="must be non-empty"):
            validate_value("string", "   ")

    def test_string_non_string_raises(self):
        from zipsa.core.requires import validate_value
        with pytest.raises(ValueError, match="expected string"):
            validate_value("string", 123)

    def test_directory_existing_returns_absolute(self, tmp_path):
        from zipsa.core.requires import validate_value
        d = tmp_path / "code"
        d.mkdir()
        result = validate_value("directory", str(d))
        assert result == str(d.resolve())

    def test_directory_with_tilde_expanded(self, tmp_path, monkeypatch):
        from zipsa.core.requires import validate_value
        monkeypatch.setenv("HOME", str(tmp_path))
        d = tmp_path / "code"
        d.mkdir()
        result = validate_value("directory", "~/code")
        assert result == str(d.resolve())

    def test_directory_missing_raises(self, tmp_path):
        from zipsa.core.requires import validate_value
        with pytest.raises(ValueError, match="does not exist"):
            validate_value("directory", str(tmp_path / "nope"))

    def test_directory_file_not_dir_raises(self, tmp_path):
        from zipsa.core.requires import validate_value
        f = tmp_path / "f"
        f.write_text("hi")
        with pytest.raises(ValueError, match="not a directory"):
            validate_value("directory", str(f))

    def test_list_directory_all_valid(self, tmp_path):
        from zipsa.core.requires import validate_value
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        result = validate_value("list[directory]", [str(a), str(b)])
        assert result == [str(a.resolve()), str(b.resolve())]

    def test_list_directory_one_invalid_raises(self, tmp_path):
        from zipsa.core.requires import validate_value
        a = tmp_path / "a"
        a.mkdir()
        with pytest.raises(ValueError, match="does not exist"):
            validate_value("list[directory]", [str(a), str(tmp_path / "missing")])

    def test_list_directory_non_list_raises(self):
        from zipsa.core.requires import validate_value
        with pytest.raises(ValueError, match="expected list"):
            validate_value("list[directory]", "not a list")

    def test_unsupported_type_raises(self):
        from zipsa.core.requires import validate_value
        with pytest.raises(ValueError, match="unsupported type"):
            validate_value("int", 5)
```

- [ ] **Step 2.6: Run to confirm failure**

```
cd launcher && uv run pytest tests/test_requires.py::TestValidateValue -v
```
Expected: All 12 fail (module doesn't exist).

- [ ] **Step 2.7: Implement `validate_value`**

Create `launcher/zipsa/core/requires.py`:

```python
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
```

- [ ] **Step 2.8: Run to confirm pass**

```
cd launcher && uv run pytest tests/test_requires.py::TestValidateValue -v
```
Expected: 12 passed

### Step 2.9: Write failing tests for `load_requires` + `save_requires` (atomic)

- [ ] Append to `launcher/tests/test_requires.py`:

```python
class TestLoadSave:
    def test_load_missing_file_returns_empty_dict(self, tmp_path):
        from zipsa.core.requires import load_requires
        assert load_requires(tmp_path / "missing.yaml") == {}

    def test_save_then_load_roundtrip(self, tmp_path):
        from zipsa.core.requires import load_requires, save_requires
        f = tmp_path / "requires.yaml"
        save_requires(f, {"project_roots": ["/a", "/b"]})
        assert load_requires(f) == {"project_roots": ["/a", "/b"]}

    def test_save_is_atomic_tmp_then_rename(self, tmp_path, monkeypatch):
        """Inject a write failure between tmp write and rename; original file unchanged."""
        from zipsa.core.requires import save_requires
        f = tmp_path / "requires.yaml"
        save_requires(f, {"x": "original"})

        # Make Path.replace blow up to simulate atomic rename failure
        from pathlib import Path as PathClass
        orig_replace = PathClass.replace

        def failing_replace(self, target):
            raise OSError("simulated rename failure")

        monkeypatch.setattr(PathClass, "replace", failing_replace)

        with pytest.raises(OSError):
            save_requires(f, {"x": "new"})

        # Restore for read
        monkeypatch.setattr(PathClass, "replace", orig_replace)

        from zipsa.core.requires import load_requires
        assert load_requires(f) == {"x": "original"}

    def test_save_creates_parent_directory(self, tmp_path):
        from zipsa.core.requires import save_requires, load_requires
        f = tmp_path / "subdir" / "requires.yaml"
        save_requires(f, {"k": "v"})
        assert load_requires(f) == {"k": "v"}

    def test_load_invalid_yaml_raises(self, tmp_path):
        from zipsa.core.requires import load_requires
        f = tmp_path / "bad.yaml"
        f.write_text(":\n:: invalid yaml")
        with pytest.raises(yaml.YAMLError):
            load_requires(f)

    def test_load_non_dict_raises(self, tmp_path):
        from zipsa.core.requires import load_requires
        f = tmp_path / "list.yaml"
        f.write_text("- 1\n- 2\n")
        with pytest.raises(ValueError, match="must be a dict"):
            load_requires(f)
```

- [ ] **Step 2.10: Run to confirm failure**

```
cd launcher && uv run pytest tests/test_requires.py::TestLoadSave -v
```
Expected: all 6 fail.

- [ ] **Step 2.11: Implement load + save**

Append to `launcher/zipsa/core/requires.py`:

```python
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
```

Make sure the yaml import is at the top of the test file:

```python
import yaml
```

- [ ] **Step 2.12: Run to confirm pass**

```
cd launcher && uv run pytest tests/test_requires.py::TestLoadSave -v
```
Expected: 6 passed

### Step 2.13: Write failing tests for `classify_state`

- [ ] Append to `launcher/tests/test_requires.py`:

```python
class TestClassifyState:
    def test_all_ok(self, tmp_path):
        from zipsa.core.requires import classify_state
        from zipsa.core.models import RequiresEntry
        d = tmp_path / "x"
        d.mkdir()
        spec = {"path": RequiresEntry(type="directory", prompt="?")}
        saved = {"path": str(d)}
        ok, needs_prompt, needs_revalidation = classify_state(spec, saved)
        assert ok == {"path": str(d)}
        assert needs_prompt == []
        assert needs_revalidation == []

    def test_missing_value_marked_needs_prompt(self, tmp_path):
        from zipsa.core.requires import classify_state
        from zipsa.core.models import RequiresEntry
        spec = {"path": RequiresEntry(type="directory", prompt="?")}
        ok, needs_prompt, needs_revalidation = classify_state(spec, {})
        assert ok == {}
        assert needs_prompt == ["path"]
        assert needs_revalidation == []

    def test_type_mismatch_marked_needs_prompt(self, tmp_path):
        from zipsa.core.requires import classify_state
        from zipsa.core.models import RequiresEntry
        spec = {"path": RequiresEntry(type="directory", prompt="?")}
        ok, needs_prompt, needs_revalidation = classify_state(spec, {"path": ["not a string"]})
        assert needs_prompt == ["path"]

    def test_stale_path_marked_needs_revalidation(self, tmp_path):
        from zipsa.core.requires import classify_state
        from zipsa.core.models import RequiresEntry
        spec = {"path": RequiresEntry(type="directory", prompt="?")}
        saved = {"path": str(tmp_path / "nope")}
        ok, needs_prompt, needs_revalidation = classify_state(spec, saved)
        assert ok == {}
        assert needs_revalidation == ["path"]

    def test_extra_saved_keys_ignored(self, tmp_path):
        from zipsa.core.requires import classify_state
        from zipsa.core.models import RequiresEntry
        d = tmp_path / "x"
        d.mkdir()
        spec = {"path": RequiresEntry(type="directory", prompt="?")}
        saved = {"path": str(d), "stale_key": "leftover"}
        ok, needs_prompt, needs_revalidation = classify_state(spec, saved)
        assert ok == {"path": str(d)}
        assert needs_prompt == []
```

- [ ] **Step 2.14: Run to confirm failure**

```
cd launcher && uv run pytest tests/test_requires.py::TestClassifyState -v
```
Expected: all 5 fail.

- [ ] **Step 2.15: Implement `classify_state`**

Append to `launcher/zipsa/core/requires.py`:

```python
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
```

- [ ] **Step 2.16: Run to confirm pass**

```
cd launcher && uv run pytest tests/test_requires.py::TestClassifyState -v
```
Expected: 5 passed

### Step 2.17: Write failing tests for `carry_over_from_previous`

- [ ] Append to `launcher/tests/test_requires.py`:

```python
class TestCarryOver:
    def test_carry_from_one_previous_version(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.requires import carry_over_from_previous, save_requires
        from zipsa.paths import skill_data_dir

        prev = skill_data_dir("daily-progress", "0.4.0")
        prev.mkdir(parents=True)
        save_requires(prev / "requires.yaml", {"project_roots": ["/a"]})

        from zipsa.core.models import RequiresEntry
        current_spec = {"project_roots": RequiresEntry(type="list[directory]", prompt="?")}
        result = carry_over_from_previous("daily-progress", "0.5.0", current_spec)
        assert result is not None
        prev_version, values = result
        assert prev_version == "0.4.0"
        assert values == {"project_roots": ["/a"]}

    def test_no_previous_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.requires import carry_over_from_previous
        from zipsa.core.models import RequiresEntry
        result = carry_over_from_previous(
            "new-skill", "0.1.0",
            {"x": RequiresEntry(type="string", prompt="?")},
        )
        assert result is None

    def test_picks_most_recent_when_multiple_previous(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.requires import carry_over_from_previous, save_requires
        from zipsa.paths import skill_data_dir

        for v, val in [("0.4.0", "v4"), ("0.4.9", "v4_9"), ("0.5.0", "v5")]:
            d = skill_data_dir("s", v)
            d.mkdir(parents=True)
            save_requires(d / "requires.yaml", {"x": val})

        from zipsa.core.models import RequiresEntry
        result = carry_over_from_previous("s", "0.6.0", {"x": RequiresEntry(type="string", prompt="?")})
        assert result is not None
        prev_version, values = result
        assert prev_version == "0.5.0"
        assert values == {"x": "v5"}

    def test_excludes_keys_with_type_mismatch(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.requires import carry_over_from_previous, save_requires
        from zipsa.paths import skill_data_dir
        d = skill_data_dir("s", "0.4.0")
        d.mkdir(parents=True)
        save_requires(d / "requires.yaml", {"x": "old_string_value"})

        # Current schema changed type: x is now list[directory]
        from zipsa.core.models import RequiresEntry
        result = carry_over_from_previous(
            "s", "0.5.0",
            {"x": RequiresEntry(type="list[directory]", prompt="?")},
        )
        # x excluded (type mismatch), and no other keys carry over → None
        assert result is None

    def test_partial_carry_over(self, tmp_path, monkeypatch):
        """Old version had 2 keys; new schema renamed one → only the matching key carries over."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.requires import carry_over_from_previous, save_requires
        from zipsa.paths import skill_data_dir
        d = skill_data_dir("s", "0.4.0")
        d.mkdir(parents=True)
        save_requires(d / "requires.yaml", {"a": "hi", "old_name": "x"})

        from zipsa.core.models import RequiresEntry
        result = carry_over_from_previous(
            "s", "0.5.0",
            {"a": RequiresEntry(type="string", prompt="?"),
             "new_name": RequiresEntry(type="string", prompt="?")},
        )
        assert result is not None
        prev_version, values = result
        assert prev_version == "0.4.0"
        assert values == {"a": "hi"}  # old_name excluded; new_name not present

    def test_self_excluded_from_previous_search(self, tmp_path, monkeypatch):
        """Should not consider the current version as 'previous'."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.requires import carry_over_from_previous, save_requires
        from zipsa.paths import skill_data_dir
        d = skill_data_dir("s", "0.5.0")  # SAME version
        d.mkdir(parents=True)
        save_requires(d / "requires.yaml", {"x": "self"})

        from zipsa.core.models import RequiresEntry
        result = carry_over_from_previous("s", "0.5.0", {"x": RequiresEntry(type="string", prompt="?")})
        assert result is None
```

- [ ] **Step 2.18: Run to confirm failure**

```
cd launcher && uv run pytest tests/test_requires.py::TestCarryOver -v
```
Expected: all 6 fail.

- [ ] **Step 2.19: Implement `carry_over_from_previous`**

Append to `launcher/zipsa/core/requires.py`:

```python
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
```

- [ ] **Step 2.20: Run to confirm pass + full module test**

```
cd launcher && uv run pytest tests/test_requires.py tests/test_paths.py -v
```
Expected: all passing.

### Step 2.21: Commit Task 2

```bash
cd /Users/neochoon/WestbrookAI/zipsa
git add launcher/zipsa/core/requires.py launcher/zipsa/paths.py launcher/tests/test_requires.py launcher/tests/test_paths.py
git commit -m "feat(requires): core load/save/validate/carry-over module

- validate_value: string/directory/list[directory] with absolute path resolution
- load_requires/save_requires: YAML, atomic write via tmp+rename
- classify_state: partition keys into (ok, needs_prompt, needs_revalidation)
- carry_over_from_previous: find latest prev version, filter by type match

No I/O of TTY here — that lives in the CLI layer. This module stays pure
for testability."
```

---

## Task 3: Executor mount expansion

**Files:**
- Modify: `launcher/zipsa/core/executor.py:1167-1170`
- Modify: `launcher/tests/test_executor.py` (append new test class)

### Step 3.1: Write failing test for dynamic mount expansion

- [ ] Append to `launcher/tests/test_executor.py`:

```python
class TestMountExpansion:
    def test_static_mount_unchanged(self, tmp_path):
        """Existing static mounts: -v <host>:<container>:<mode>."""
        from zipsa.core.executor import DockerExecutor
        from zipsa.core.skill import Skill

        skill_dir = tmp_path / "s"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# x")
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: t\n"
            "  instructions: ./SKILL.md\n"
            "  mounts:\n"
            f"    - {{host: {tmp_path}, container: /static, mode: ro}}\n"
        )
        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")
        cmd = ex._build_docker_command(
            skill, "hi", tmp_path / "claude.json", {},
            requires_values={},
        )
        assert any(f"{tmp_path.resolve()}:/static:ro" in arg for arg in cmd)

    def test_single_directory_dynamic_mount(self, tmp_path):
        from zipsa.core.executor import DockerExecutor
        from zipsa.core.skill import Skill

        vault = tmp_path / "vault"
        vault.mkdir()
        skill_dir = tmp_path / "s"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# x")
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: t\n"
            "  instructions: ./SKILL.md\n"
            "  requires:\n"
            "    vault: {type: directory, prompt: 'where'}\n"
            "  mounts:\n"
            "    - {source: requires.vault, container: /vault, mode: ro}\n"
        )
        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")
        cmd = ex._build_docker_command(
            skill, "hi", tmp_path / "claude.json", {},
            requires_values={"vault": str(vault)},
        )
        assert any(f"{vault}:/vault:ro" in arg for arg in cmd)

    def test_list_directory_dynamic_expands_per_item(self, tmp_path):
        from zipsa.core.executor import DockerExecutor
        from zipsa.core.skill import Skill

        a = tmp_path / "code"
        b = tmp_path / "personal"
        a.mkdir()
        b.mkdir()
        skill_dir = tmp_path / "s"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# x")
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: t\n"
            "  instructions: ./SKILL.md\n"
            "  requires:\n"
            "    project_roots: {type: list[directory], prompt: '?'}\n"
            "  mounts:\n"
            "    - {source: requires.project_roots, container_prefix: /projects/, mode: ro}\n"
        )
        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")
        cmd = ex._build_docker_command(
            skill, "hi", tmp_path / "claude.json", {},
            requires_values={"project_roots": [str(a), str(b)]},
        )
        assert any(f"{a}:/projects/code:ro" in arg for arg in cmd)
        assert any(f"{b}:/projects/personal:ro" in arg for arg in cmd)

    def test_basename_collision_raises(self, tmp_path):
        from zipsa.core.executor import DockerExecutor, MountCollisionError
        from zipsa.core.skill import Skill

        a = tmp_path / "code" / "zipsa"
        b = tmp_path / "personal" / "zipsa"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        skill_dir = tmp_path / "s"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# x")
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: t\n"
            "  instructions: ./SKILL.md\n"
            "  requires:\n"
            "    project_roots: {type: list[directory], prompt: '?'}\n"
            "  mounts:\n"
            "    - {source: requires.project_roots, container_prefix: /projects/, mode: ro}\n"
        )
        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")
        with pytest.raises(MountCollisionError, match="zipsa"):
            ex._build_docker_command(
                skill, "hi", tmp_path / "claude.json", {},
                requires_values={"project_roots": [str(a), str(b)]},
            )
```

- [ ] **Step 3.2: Run to confirm failure**

```
cd launcher && uv run pytest tests/test_executor.py::TestMountExpansion -v
```
Expected: `TypeError` (unexpected keyword `requires_values`) or `ImportError: cannot import name 'MountCollisionError'`.

- [ ] **Step 3.3: Add `MountCollisionError` + extend `_build_docker_command`**

In `launcher/zipsa/core/executor.py`, near the top with other exceptions (after imports, before the first class definition):

```python
class MountCollisionError(ValueError):
    """Raised when two dynamic mount entries resolve to the same container path."""
```

Find `_build_docker_command` method signature (line 1093ish) and add a parameter. Locate the existing signature:

```python
    def _build_docker_command(
        self,
        ...,
    ) -> list[str]:
```

Add this parameter (keyword-only to preserve callers without `requires_values`):

```python
    def _build_docker_command(
        self,
        ...,
        requires_values: Optional[dict[str, object]] = None,
    ) -> list[str]:
        requires_values = requires_values or {}
        ...
```

Then replace the static-mount loop at lines 1167-1170:

```python
        # Generic spec.mounts entries — explicit container path, independent of MCP
        for m in skill.manifest.spec.mounts:
            host_path = Path(m.host).expanduser().resolve()
            cmd.extend(["-v", f"{host_path}:{m.container}:{m.mode}"])
```

With:

```python
        # spec.mounts: both static (host) and dynamic (source -> requires.X)
        seen_container_paths: set[str] = set()
        for m in skill.manifest.spec.mounts:
            if m.host is not None:
                # Static mount
                host_path = Path(m.host).expanduser().resolve()
                cmd.extend(["-v", f"{host_path}:{m.container}:{m.mode}"])
                seen_container_paths.add(m.container)
                continue

            # Dynamic mount (m.source is "requires.<key>")
            key = m.source.removeprefix("requires.")
            value = requires_values.get(key)
            if value is None:
                # Should have been caught in pre-flight; defensive
                raise ValueError(
                    f"mount source 'requires.{key}' has no value at run time"
                )

            if isinstance(value, str):
                # Single directory
                host_path = Path(value).expanduser().resolve()
                if m.container in seen_container_paths:
                    raise MountCollisionError(
                        f"container path {m.container} already used by another mount"
                    )
                cmd.extend(["-v", f"{host_path}:{m.container}:{m.mode}"])
                seen_container_paths.add(m.container)

            elif isinstance(value, list):
                # list[directory] expanded with container_prefix + basename(item)
                for item in value:
                    host_path = Path(item).expanduser().resolve()
                    container_path = m.container_prefix + host_path.name
                    if container_path in seen_container_paths:
                        raise MountCollisionError(
                            f"basename collision in requires.{key}: "
                            f"multiple paths resolve to {container_path}"
                        )
                    cmd.extend(["-v", f"{host_path}:{container_path}:{m.mode}"])
                    seen_container_paths.add(container_path)
            else:
                raise ValueError(
                    f"requires.{key} has unexpected type {type(value).__name__}"
                )
```

- [ ] **Step 3.4: Run new tests to confirm pass**

```
cd launcher && uv run pytest tests/test_executor.py::TestMountExpansion -v
```
Expected: 4 passed

- [ ] **Step 3.5: Run full executor + models tests for regression**

```
cd launcher && uv run pytest tests/test_executor.py tests/test_models.py -v
```
Expected: all passing (existing tests unaffected because `requires_values` defaults to `None` / `{}`).

### Step 3.6: Commit Task 3

```bash
cd /Users/neochoon/WestbrookAI/zipsa
git add launcher/zipsa/core/executor.py launcher/tests/test_executor.py
git commit -m "feat(executor): expand requires.X mounts at run time

_build_docker_command now accepts requires_values: dict.
- Static (host) mounts: unchanged behavior
- Dynamic (source=requires.X, container): single -v
- Dynamic (source=requires.X, container_prefix): one -v per list item,
  container path = prefix + basename(item)
- Raises MountCollisionError when two dynamic mounts resolve to the same
  container path (basename collision in a list)

CLI integration (passing requires_values down from the run flow) lands
in a later task."
```

---

## Task 4: `zipsa configure` command

**Files:**
- Modify: `launcher/zipsa/cli.py` (add `configure` command + helpers)
- Create: `launcher/tests/test_configure_command.py`

### Step 4.1: Write failing tests for `configure` (interactive)

- [ ] Create `launcher/tests/test_configure_command.py`:

```python
"""Integration tests for the `zipsa configure` command."""

import io
import pytest
import yaml
from pathlib import Path
from typer.testing import CliRunner

from zipsa.cli import app


runner = CliRunner()


def _install_demo_skill(tmp_path: Path, requires_block: str) -> Path:
    """Create a fixture skill linked into ZIPSA_HOME/skills."""
    src = tmp_path / "src" / "demo"
    src.mkdir(parents=True)
    (src / "manifest.yaml").write_text(
        "apiVersion: zipsa.dev/v1alpha1\n"
        "kind: Skill\n"
        "metadata: {name: demo, version: 0.1.0}\n"
        "spec:\n"
        "  purpose: test\n"
        "  instructions: ./SKILL.md\n"
        + requires_block
    )
    (src / "SKILL.md").write_text("# demo")
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "demo").symlink_to(src)
    return src


class TestConfigureCommand:
    def test_first_run_saves_values(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        code_dir = tmp_path / "Code"
        code_dir.mkdir()
        _install_demo_skill(tmp_path,
            "  requires:\n"
            "    project_roots:\n"
            "      type: list[directory]\n"
            "      prompt: 'where?'\n"
        )
        # Simulate input: one path, then empty line
        result = runner.invoke(app, ["configure", "demo"], input=f"{code_dir}\n\n")
        assert result.exit_code == 0, result.output
        saved = yaml.safe_load((tmp_path / "demo@0.1.0" / "requires.yaml").read_text())
        assert saved == {"project_roots": [str(code_dir.resolve())]}

    def test_no_tty_exits_4(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_demo_skill(tmp_path,
            "  requires:\n"
            "    name:\n"
            "      type: string\n"
            "      prompt: 'name?'\n"
        )
        # When click/typer receives no input AND prompt fails (no TTY), exit 4.
        # We simulate this by intercepting sys.stdin to a closed stream.
        monkeypatch.setattr("sys.stdin", io.StringIO(""))  # empty + non-TTY
        # We also need is_interactive=False — patch isatty
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        result = runner.invoke(app, ["configure", "demo"])
        assert result.exit_code == 4

    def test_unknown_skill_exits_1(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        (tmp_path / "skills").mkdir()
        result = runner.invoke(app, ["configure", "nope"])
        assert result.exit_code == 1
        assert "not installed" in result.output.lower()

    def test_skill_without_requires_no_op(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_demo_skill(tmp_path, "")  # no requires block
        result = runner.invoke(app, ["configure", "demo"])
        assert result.exit_code == 0
        assert "no required configuration" in result.output.lower()

    def test_string_type_collects_one_line(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_demo_skill(tmp_path,
            "  requires:\n"
            "    name:\n"
            "      type: string\n"
            "      prompt: 'name?'\n"
        )
        result = runner.invoke(app, ["configure", "demo"], input="hello\n")
        assert result.exit_code == 0
        saved = yaml.safe_load((tmp_path / "demo@0.1.0" / "requires.yaml").read_text())
        assert saved == {"name": "hello"}

    def test_directory_type_validates_existence(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_demo_skill(tmp_path,
            "  requires:\n"
            "    home:\n"
            "      type: directory\n"
            "      prompt: 'home?'\n"
        )
        # First input: nonexistent path → re-prompt. Second: valid → save.
        valid = tmp_path / "valid"
        valid.mkdir()
        result = runner.invoke(app, ["configure", "demo"], input=f"/no/such/dir\n{valid}\n")
        assert result.exit_code == 0
        assert "does not exist" in result.output.lower()
        saved = yaml.safe_load((tmp_path / "demo@0.1.0" / "requires.yaml").read_text())
        assert saved == {"home": str(valid.resolve())}

    def test_three_failed_attempts_exits_1(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_demo_skill(tmp_path,
            "  requires:\n"
            "    home:\n"
            "      type: directory\n"
            "      prompt: 'home?'\n"
        )
        # 3 invalid attempts → exit 1
        result = runner.invoke(app, ["configure", "demo"], input="/no\n/no\n/no\n")
        assert result.exit_code == 1
        # No file created
        assert not (tmp_path / "demo@0.1.0" / "requires.yaml").exists()

    def test_existing_values_enter_keeps_them(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_demo_skill(tmp_path,
            "  requires:\n"
            "    name:\n"
            "      type: string\n"
            "      prompt: 'name?'\n"
        )
        (tmp_path / "demo@0.1.0").mkdir(parents=True)
        (tmp_path / "demo@0.1.0" / "requires.yaml").write_text("name: original\n")
        # Press enter → keep
        result = runner.invoke(app, ["configure", "demo"], input="\n")
        assert result.exit_code == 0
        saved = yaml.safe_load((tmp_path / "demo@0.1.0" / "requires.yaml").read_text())
        assert saved == {"name": "original"}
```

- [ ] **Step 4.2: Run to confirm failure**

```
cd launcher && uv run pytest tests/test_configure_command.py -v
```
Expected: All fail (`configure` command doesn't exist; helpers don't exist).

- [ ] **Step 4.3: Implement `prompt_for_value` in core module first**

Append to `launcher/zipsa/core/requires.py`:

```python
def prompt_for_value(
    entry: "RequiresEntry",  # type: ignore[name-defined]
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
        _writeln(f"Current:")
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

        except ValueError as e:
            _writeln(f"  ✗ {e}")
            if attempt < max_attempts - 1:
                _writeln(f"  Try again ({max_attempts - attempt - 1} attempts left):")
            continue

    raise ValueError(f"validation failed after {max_attempts} attempts")
```

- [ ] **Step 4.4: Write unit tests for `prompt_for_value`**

Append to `launcher/tests/test_requires.py`:

```python
class TestPromptForValue:
    def test_string_input(self):
        import io
        from zipsa.core.requires import prompt_for_value
        from zipsa.core.models import RequiresEntry
        entry = RequiresEntry(type="string", prompt="?")
        out = io.StringIO()
        result = prompt_for_value(entry, io.StringIO("hi\n"), out)
        assert result == "hi"

    def test_directory_input_normalizes(self, tmp_path):
        import io
        from zipsa.core.requires import prompt_for_value
        from zipsa.core.models import RequiresEntry
        d = tmp_path / "x"
        d.mkdir()
        entry = RequiresEntry(type="directory", prompt="?")
        out = io.StringIO()
        result = prompt_for_value(entry, io.StringIO(f"{d}\n"), out)
        assert result == str(d.resolve())

    def test_list_directory_multi_line(self, tmp_path):
        import io
        from zipsa.core.requires import prompt_for_value
        from zipsa.core.models import RequiresEntry
        a = tmp_path / "a"; a.mkdir()
        b = tmp_path / "b"; b.mkdir()
        entry = RequiresEntry(type="list[directory]", prompt="?")
        out = io.StringIO()
        result = prompt_for_value(entry, io.StringIO(f"{a}\n{b}\n\n"), out)
        assert result == [str(a.resolve()), str(b.resolve())]

    def test_enter_with_current_keeps(self):
        import io
        from zipsa.core.requires import prompt_for_value
        from zipsa.core.models import RequiresEntry
        entry = RequiresEntry(type="string", prompt="?")
        out = io.StringIO()
        result = prompt_for_value(entry, io.StringIO("\n"), out, current="kept")
        assert result == "kept"

    def test_invalid_then_valid(self, tmp_path):
        import io
        from zipsa.core.requires import prompt_for_value
        from zipsa.core.models import RequiresEntry
        d = tmp_path / "x"; d.mkdir()
        entry = RequiresEntry(type="directory", prompt="?")
        out = io.StringIO()
        result = prompt_for_value(entry, io.StringIO(f"/no/such\n{d}\n"), out)
        assert result == str(d.resolve())

    def test_three_invalid_raises(self):
        import io
        from zipsa.core.requires import prompt_for_value
        from zipsa.core.models import RequiresEntry
        entry = RequiresEntry(type="directory", prompt="?")
        out = io.StringIO()
        with pytest.raises(ValueError, match="failed after 3"):
            prompt_for_value(entry, io.StringIO("/no\n/no\n/no\n"), out)

    def test_eof_immediately_raises_eoferror(self):
        import io
        from zipsa.core.requires import prompt_for_value
        from zipsa.core.models import RequiresEntry
        entry = RequiresEntry(type="string", prompt="?")
        out = io.StringIO()
        with pytest.raises(EOFError):
            prompt_for_value(entry, io.StringIO(""), out)
```

- [ ] **Step 4.5: Run prompt tests to confirm pass**

```
cd launcher && uv run pytest tests/test_requires.py::TestPromptForValue -v
```
Expected: 7 passed

- [ ] **Step 4.6: Implement `configure` command + supporting helpers in CLI**

Add to `launcher/zipsa/cli.py` imports (near other imports at the top):

```python
import sys
from zipsa.core.requires import (
    load_requires, save_requires, prompt_for_value, classify_state,
    carry_over_from_previous,
)
from zipsa.paths import skill_requires_file
```

Add the command (after the `list_installed` command, around line 515):

```python
@app.command()
def configure(
    name: Annotated[str, typer.Argument(help="Installed skill name")],
):
    """Set host-side values that the skill needs to run (spec.requires)."""
    try:
        skill = Skill.load(_resolve_skill_path(name))
    except SkillNotInstalledError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Error loading {name!r}: {e}", err=True)
        raise typer.Exit(1)

    spec = skill.manifest.spec.requires
    if not spec:
        typer.echo(f"{name} has no required configuration.")
        raise typer.Exit(0)

    if not sys.stdin.isatty():
        typer.echo("Error: configure requires an interactive terminal.", err=True)
        raise typer.Exit(4)

    typer.echo(f"\n[zipsa] {name}@{skill.manifest.metadata.version}\n")

    req_file = skill_requires_file(name, skill.manifest.metadata.version)
    saved = load_requires(req_file) if req_file.exists() else {}

    new_values: dict[str, object] = dict(saved)  # start from existing
    try:
        for key, entry in spec.items():
            typer.echo(f"{key} — {entry.prompt}")
            current = saved.get(key)
            try:
                value = prompt_for_value(entry, sys.stdin, sys.stdout, current=current)
            except EOFError:
                typer.echo("Error: input ended before prompt completed.", err=True)
                raise typer.Exit(4)
            except ValueError as e:
                typer.echo(f"Error: {e}", err=True)
                raise typer.Exit(1)
            new_values[key] = value
            if isinstance(value, list):
                typer.echo(f"  ✓ saved {len(value)} item(s)")
            else:
                typer.echo(f"  ✓ saved")
            typer.echo()
    except KeyboardInterrupt:
        typer.echo("\nCancelled. No changes saved.")
        raise typer.Exit(130)

    save_requires(req_file, new_values)
    typer.echo(f"Saved to {req_file}")
```

- [ ] **Step 4.7: Run configure-command tests to confirm pass**

```
cd launcher && uv run pytest tests/test_configure_command.py -v
```
Expected: 8 passed

- [ ] **Step 4.8: Regression check**

```
cd launcher && uv run pytest -v 2>&1 | tail -20
```
Expected: all passing.

### Step 4.9: Commit Task 4

```bash
cd /Users/neochoon/WestbrookAI/zipsa
git add launcher/zipsa/core/requires.py launcher/zipsa/cli.py launcher/tests/test_requires.py launcher/tests/test_configure_command.py
git commit -m "feat(cli): zipsa configure command for spec.requires values

- prompt_for_value: reusable interactive prompt with 3-attempt retry
- configure command: walks all spec.requires keys, shows current+saves new
- atomic write via core requires.save_requires (tmp + rename)
- No TTY → exit 4; unknown skill → exit 1; no requires → exit 0 + no-op
- Ctrl+C → exit 130, no partial save"
```

---

## Task 5: `zipsa run` pre-flight requires check

**Files:**
- Modify: `launcher/zipsa/cli.py` (insert pre-flight before `executor.run`)
- Modify: `launcher/zipsa/core/executor.py` (`DockerExecutor.run` accepts `requires_values`)
- Modify: `launcher/tests/test_cli.py` (append new test class)

### Step 5.1: Write failing tests for `run` pre-flight

- [ ] Append to `launcher/tests/test_cli.py`:

```python
class TestRunPreflightRequires:
    """The run command must resolve spec.requires before invoking the executor."""

    def _install_skill_with_requires(self, tmp_path):
        src = tmp_path / "src" / "needs-cfg"
        src.mkdir(parents=True)
        (src / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: needs-cfg, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: test\n"
            "  instructions: ./SKILL.md\n"
            "  requires:\n"
            "    project_roots:\n"
            "      type: list[directory]\n"
            "      prompt: 'where?'\n"
            "  mounts:\n"
            "    - {source: requires.project_roots, container_prefix: /projects/, mode: ro}\n"
        )
        (src / "SKILL.md").write_text("# x")
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "needs-cfg").symlink_to(src)
        return src

    def test_run_no_tty_no_saved_values_exits_4(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        self._install_skill_with_requires(tmp_path)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        result = runner.invoke(app, ["run", "needs-cfg", "hi", "--dry-run"])
        assert result.exit_code == 4
        assert "requires" in result.output.lower()

    def test_run_dry_run_with_saved_values_proceeds(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        self._install_skill_with_requires(tmp_path)
        code = tmp_path / "Code"; code.mkdir()
        (tmp_path / "needs-cfg@0.1.0").mkdir(parents=True)
        (tmp_path / "needs-cfg@0.1.0" / "requires.yaml").write_text(
            f"project_roots:\n  - {code}\n"
        )
        result = runner.invoke(app, ["run", "needs-cfg", "hi", "--dry-run"])
        assert result.exit_code == 0, result.output
        # Dry-run prints the docker command — should contain our mount
        assert f"{code}:/projects/Code:ro" in result.output

    def test_run_stale_path_no_tty_exits_4(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        self._install_skill_with_requires(tmp_path)
        (tmp_path / "needs-cfg@0.1.0").mkdir(parents=True)
        (tmp_path / "needs-cfg@0.1.0" / "requires.yaml").write_text(
            "project_roots:\n  - /no/such/dir\n"
        )
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        result = runner.invoke(app, ["run", "needs-cfg", "hi", "--dry-run"])
        assert result.exit_code == 4
        assert "stale" in result.output.lower() or "no longer" in result.output.lower()

    def test_run_skill_without_requires_unchanged(self, tmp_path, monkeypatch):
        """Regression: existing skills (no spec.requires) skip the pre-flight entirely."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        src = tmp_path / "src" / "plain"
        src.mkdir(parents=True)
        (src / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: plain, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: test\n"
            "  instructions: ./SKILL.md\n"
        )
        (src / "SKILL.md").write_text("# x")
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "plain").symlink_to(src)
        # No requires.yaml, no TTY — must still succeed in dry-run
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        result = runner.invoke(app, ["run", "plain", "hi", "--dry-run"])
        assert result.exit_code == 0, result.output

    def test_run_carry_over_no_tty_exits_4(self, tmp_path, monkeypatch):
        """Carry-over needs user confirmation; no TTY → exit 4."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        # Install at 0.1.0; pre-populate 0.0.9
        self._install_skill_with_requires(tmp_path)
        code = tmp_path / "Code"; code.mkdir()
        (tmp_path / "needs-cfg@0.0.9").mkdir(parents=True)
        (tmp_path / "needs-cfg@0.0.9" / "requires.yaml").write_text(
            f"project_roots:\n  - {code}\n"
        )
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        result = runner.invoke(app, ["run", "needs-cfg", "hi", "--dry-run"])
        assert result.exit_code == 4
```

- [ ] **Step 5.2: Run to confirm failure**

```
cd launcher && uv run pytest tests/test_cli.py::TestRunPreflightRequires -v
```
Expected: All 5 fail (pre-flight not yet implemented).

- [ ] **Step 5.3: Add `resolve_requires` helper to core module**

Append to `launcher/zipsa/core/requires.py`:

```python
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
                f"{skill_name} needs configuration: {', '.join(needs_prompt)}. "
                f"Run: zipsa configure {skill_name}"
            )
        if needs_revalidation:
            raise RequiresStaleError(
                f"{skill_name} has stale paths in: {', '.join(needs_revalidation)}. "
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


class RequiresUnsetError(Exception):
    """No values saved and no TTY to prompt."""


class RequiresStaleError(Exception):
    """Saved values reference paths that no longer exist; no TTY to re-prompt."""
```

- [ ] **Step 5.4: Modify `DockerExecutor.run` to accept and forward `requires_values`**

In `launcher/zipsa/core/executor.py`, find the `run` method signature (around line 80-160 — look for `def run(self, skill, ...)`). Add a new kwarg `requires_values` and pass it down.

First locate the call site:

```
grep -n "def run\|_execute_with_hitl\|_build_docker_command" launcher/zipsa/core/executor.py
```

Add the param to `run`:

```python
    def run(
        self,
        skill: Skill,
        user_input: str = "",
        env: Optional[dict[str, str]] = None,
        dry_run: bool = False,
        shell: bool = False,
        mcp_debug: bool = False,
        extra_docker_opts: Optional[list[str]] = None,
        requires_values: Optional[dict[str, object]] = None,
    ):
        ...
```

Store on `self` so the multi-phase loop can pick it up. Add to `__init__` (around line 30):

```python
        self._requires_values: dict[str, object] = {}
```

Set it at the start of `run`:

```python
        self._requires_values = requires_values or {}
```

Then pass to `_build_docker_command` everywhere it is called. Find the calls (there are a few — for single-phase, multi-phase, and shell). In each, add:

```python
        docker_cmd = self._build_docker_command(
            ...,
            requires_values=self._requires_values,
        )
```

- [ ] **Step 5.5: Wire into `cli.py` run command**

In `launcher/zipsa/cli.py`, locate `def run(...)` (line 152). After the `_validate_children(skill)` call (line 231), BEFORE `executor = DockerExecutor(...)` (line 234), insert:

```python
        # Resolve spec.requires values (or fail with a clear message).
        from zipsa.core.requires import (
            resolve_requires, RequiresUnsetError, RequiresStaleError,
        )
        requires_values: dict = {}
        if skill.manifest.spec.requires:
            try:
                requires_values = resolve_requires(
                    skill.name,
                    skill.manifest.metadata.version,
                    skill.manifest.spec.requires,
                    sys.stdin,
                    sys.stdout,
                    is_interactive=sys.stdin.isatty(),
                )
            except RequiresUnsetError as e:
                typer.echo(f"Error: {e}", err=True)
                raise typer.Exit(4)
            except RequiresStaleError as e:
                typer.echo(f"Error: {e}", err=True)
                raise typer.Exit(4)
```

Pass it to `executor.run`:

```python
        output = executor.run(
            skill, user_input=user_input, env=env_dict, dry_run=dry_run,
            shell=shell, mcp_debug=mcp_debug, extra_docker_opts=docker_opt,
            requires_values=requires_values,
        )
```

- [ ] **Step 5.6: Run new tests to confirm pass**

```
cd launcher && uv run pytest tests/test_cli.py::TestRunPreflightRequires -v
```
Expected: 5 passed

- [ ] **Step 5.7: Regression check**

```
cd launcher && uv run pytest -v 2>&1 | tail -20
```
Expected: all passing.

### Step 5.8: Commit Task 5

```bash
cd /Users/neochoon/WestbrookAI/zipsa
git add launcher/zipsa/core/requires.py launcher/zipsa/core/executor.py launcher/zipsa/cli.py launcher/tests/test_cli.py
git commit -m "feat(cli): zipsa run resolves spec.requires before docker run

- core: resolve_requires() handles load/classify/prompt/carry-over/save
- core: RequiresUnsetError + RequiresStaleError surface as exit 4
- executor.run accepts requires_values, plumbed to _build_docker_command
- run command calls resolve_requires when spec.requires is non-empty
- Backward compatible: skills without spec.requires unaffected (skipped)"
```

---

## Task 6: `install_health` requires status + `zipsa list` indicator

**Files:**
- Modify: `launcher/zipsa/core/install_health.py`
- Modify: `launcher/zipsa/cli.py` (the `list_installed` renderer)
- Modify: `launcher/tests/test_install_health.py`
- Modify: `launcher/tests/test_cli.py` (list rendering)

### Step 6.1: Write failing tests for `InstallHealth.requires_total/requires_set`

- [ ] Append to `launcher/tests/test_install_health.py`:

```python
class TestRequiresStatus:
    def test_skill_without_requires_reports_zero(self, tmp_path):
        from zipsa.core.install_health import check_install
        d = tmp_path / "s"
        d.mkdir()
        (d / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 1.0.0}\n"
            "spec: {purpose: x, instructions: ./SKILL.md}\n"
        )
        (d / "SKILL.md").write_text("# x")
        h = check_install(d)
        assert h.requires_total == 0
        assert h.requires_set == 0

    def test_skill_with_requires_no_values(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        d = tmp_path / "skills" / "s"
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 1.0.0}\n"
            "spec:\n"
            "  purpose: x\n"
            "  instructions: ./SKILL.md\n"
            "  requires:\n"
            "    a: {type: string, prompt: 'a?'}\n"
            "    b: {type: string, prompt: 'b?'}\n"
        )
        (d / "SKILL.md").write_text("# x")
        from zipsa.core.install_health import check_install
        h = check_install(d)
        assert h.requires_total == 2
        assert h.requires_set == 0

    def test_skill_with_requires_some_values_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        d = tmp_path / "skills" / "s"
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 1.0.0}\n"
            "spec:\n"
            "  purpose: x\n"
            "  instructions: ./SKILL.md\n"
            "  requires:\n"
            "    a: {type: string, prompt: 'a?'}\n"
            "    b: {type: string, prompt: 'b?'}\n"
        )
        (d / "SKILL.md").write_text("# x")
        # Save only a
        (tmp_path / "s@1.0.0").mkdir()
        (tmp_path / "s@1.0.0" / "requires.yaml").write_text("a: hello\n")
        from zipsa.core.install_health import check_install
        h = check_install(d)
        assert h.requires_total == 2
        assert h.requires_set == 1
```

- [ ] **Step 6.2: Run to confirm failure**

```
cd launcher && uv run pytest tests/test_install_health.py::TestRequiresStatus -v
```
Expected: `AttributeError: 'InstallHealth' object has no attribute 'requires_total'`

- [ ] **Step 6.3: Extend `InstallHealth`**

Modify `launcher/zipsa/core/install_health.py`:

```python
@dataclass(frozen=True)
class InstallHealth:
    """Result of a health check on one installed-skill entry."""
    ok: bool
    reason: Optional[str] = None  # set iff ok is False
    requires_total: int = 0       # number of declared spec.requires entries
    requires_set: int = 0         # number currently present in requires.yaml
```

Modify `check_install` to populate these (after successful `Skill.load(path)`):

```python
    try:
        from .skill import Skill
        skill = Skill.load(path)
    except Exception as e:
        head = str(e).splitlines()[0] if str(e) else type(e).__name__
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
```

- [ ] **Step 6.4: Run to confirm pass**

```
cd launcher && uv run pytest tests/test_install_health.py -v
```
Expected: all passing (existing + new).

### Step 6.5: Add `⚠ needs configure` indicator to `zipsa list`

- [ ] Write failing test in `launcher/tests/test_cli.py`:

```python
class TestListRequiresIndicator:
    def test_list_shows_needs_configure_when_unset(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from zipsa.cli import app
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        d = tmp_path / "skills" / "needs"
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: needs, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: x\n"
            "  instructions: ./SKILL.md\n"
            "  requires:\n"
            "    a: {type: string, prompt: 'a?'}\n"
            "    b: {type: string, prompt: 'b?'}\n"
        )
        (d / "SKILL.md").write_text("# x")
        result = CliRunner().invoke(app, ["list"])
        assert result.exit_code == 0
        assert "needs configure" in result.output
        assert "2 required" in result.output
        assert "0 set" in result.output

    def test_list_no_indicator_when_all_set(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from zipsa.cli import app
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        d = tmp_path / "skills" / "done"
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: done, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: x\n"
            "  instructions: ./SKILL.md\n"
            "  requires:\n"
            "    a: {type: string, prompt: 'a?'}\n"
        )
        (d / "SKILL.md").write_text("# x")
        (tmp_path / "done@0.1.0").mkdir()
        (tmp_path / "done@0.1.0" / "requires.yaml").write_text("a: hello\n")
        result = CliRunner().invoke(app, ["list"])
        assert result.exit_code == 0
        assert "needs configure" not in result.output

    def test_list_no_indicator_for_skills_without_requires(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from zipsa.cli import app
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        d = tmp_path / "skills" / "plain"
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: plain, version: 0.1.0}\n"
            "spec: {purpose: x, instructions: ./SKILL.md}\n"
        )
        (d / "SKILL.md").write_text("# x")
        result = CliRunner().invoke(app, ["list"])
        assert result.exit_code == 0
        assert "needs configure" not in result.output
```

- [ ] **Step 6.6: Run to confirm failure**

```
cd launcher && uv run pytest tests/test_cli.py::TestListRequiresIndicator -v
```
Expected: 3 failures.

- [ ] **Step 6.7: Wire the indicator into `list_installed`**

In `launcher/zipsa/cli.py`, find `list_installed` (line 387). The loop populates `installed.append({...})` (line 464-471). Add `health` to the dict (the `check_install` call is already made at line 403):

```python
        installed.append({
            "skill": skill,
            "meta": install_meta,
            "total_runs": total_runs,
            "successful_runs": successful_runs,
            "is_link": item.is_symlink(),
            "item": item,
            "health": health,  # NEW
        })
```

Find the rendering loop (line 480). After the existing line:

```python
        typer.echo(f"  {name}{version}{label}")
```

Insert the indicator:

```python
        health = entry["health"]
        if health.requires_total > 0 and health.requires_set < health.requires_total:
            warn = typer.style(
                f"  ⚠ needs configure ({health.requires_total} required, "
                f"{health.requires_set} set)",
                fg=typer.colors.YELLOW,
            )
            typer.echo(f"  {name}{version}{label}{warn}")
```

Wait — that double-prints. Restructure: collect the suffix first, then echo once. Replace lines 480-490 (the `for entry in installed:` body up to the `typer.echo(f"  {name}{version}{label}")`):

```python
    for entry in installed:
        skill = entry["skill"]
        meta = entry["meta"]
        health = entry["health"]

        name = typer.style(skill.name, fg=typer.colors.BRIGHT_CYAN, bold=True)
        version = typer.style(f"@{skill.manifest.metadata.version}", fg=typer.colors.CYAN)
        if entry["is_link"]:
            label = typer.style(" (linked)", fg=typer.colors.YELLOW)
        else:
            label = ""

        if health.requires_total > 0 and health.requires_set < health.requires_total:
            warn = typer.style(
                f"  ⚠ needs configure ({health.requires_total} required, "
                f"{health.requires_set} set)",
                fg=typer.colors.YELLOW,
            )
        else:
            warn = ""

        typer.echo(f"  {name}{version}{label}{warn}")
```

- [ ] **Step 6.8: Run to confirm pass**

```
cd launcher && uv run pytest tests/test_cli.py::TestListRequiresIndicator -v
```
Expected: 3 passed

- [ ] **Step 6.9: Regression check**

```
cd launcher && uv run pytest -v 2>&1 | tail -20
```
Expected: all passing.

### Step 6.10: Commit Task 6

```bash
cd /Users/neochoon/WestbrookAI/zipsa
git add launcher/zipsa/core/install_health.py launcher/zipsa/cli.py launcher/tests/test_install_health.py launcher/tests/test_cli.py
git commit -m "feat(list): show requires config status indicator

- InstallHealth gains requires_total + requires_set fields
- check_install populates them using classify_state from requires module
- zipsa list renders '⚠ needs configure (M required, N set)' when M > N
- Skills without spec.requires unchanged"
```

---

## Task 7: Fixture skill + docs

**Files:**
- Create: `launcher/tests/fixtures/skills/requires-demo/manifest.yaml`
- Create: `launcher/tests/fixtures/skills/requires-demo/SKILL.md`
- Modify: `launcher/CLAUDE.md`
- Modify: `skills/README.md`

### Step 7.1: Create fixture skill

- [ ] Create `launcher/tests/fixtures/skills/requires-demo/manifest.yaml`:

```yaml
apiVersion: zipsa.dev/v1alpha1
kind: Skill
metadata:
  name: requires-demo
  version: 0.1.0
  author: zipsa
  description: |
    Demo skill exercising spec.requires + dynamic mount.

spec:
  purpose: |
    Verify that a skill declaring spec.requires resolves values
    from the launcher and exposes them inside the container.

  instructions: ./SKILL.md

  requires:
    project_roots:
      type: list[directory]
      prompt: |
        Which directories contain your projects? (one per line, ~ expanded)

  mounts:
    - source: requires.project_roots
      container_prefix: /projects/
      mode: ro

  tools:
    builtin:
      - Read

  limits:
    max_turns: 2
    max_cost_usd: 0.05
    timeout_seconds: 15
```

- [ ] Create `launcher/tests/fixtures/skills/requires-demo/SKILL.md`:

```markdown
# requires-demo Skill

A fixture for spec.requires + dynamic mount tests.

## What it does

List the directories that appear under `/projects/` (these come from
the user's `requires.project_roots` setting via dynamic mount). Emit
the contract JSON with the count.

## Steps

1. `ls /projects/` to enumerate the mounted entries.
2. Emit final JSON: `{"status":"ok","result":{"project_count": N}}`
```

### Step 7.2: Document the `requires` pattern in launcher CLAUDE.md

- [ ] Find the right insertion point in `launcher/CLAUDE.md` (after manifest-related section, before quality checklist). Add a new section:

```markdown
---

## `spec.requires:` — per-user host-side config

When a skill needs host-side values that vary per user (project
directory paths, vault locations, etc.) AND the launcher needs them
*before the container starts* (e.g. to set mount flags), declare them
in `spec.requires:`.

**Manifest:**

```yaml
spec:
  requires:
    project_roots:
      type: list[directory]
      prompt: |
        Which directories contain your git projects?
        (one path per line, ~ is expanded)

  mounts:
    - source: requires.project_roots
      container_prefix: /projects/
      mode: ro
```

**Types (v1):** `string`, `directory`, `list[directory]`.

**Flow:** On first `zipsa run`, the launcher prompts the user inline,
validates each value, and saves to
`~/.zipsa/<skill>@<version>/requires.yaml`. Subsequent runs read the
saved file. Use `zipsa configure <skill>` to update values later.

**Use `spec.requires` for:** mount paths, env-file paths, anything the
launcher reads pre-container. NOT for values the agent uses at run
time (those still belong in skill memory via `ask_once`).

See spec for full details: `docs/superpowers/specs/2026-05-20-requires-config-design.md`.
```

### Step 7.3: Add manifest-writer guide in skills/README.md

- [ ] Find the manifest reference section in `skills/README.md`. Add a `requires` subsection. Locate a good insertion point (after `spec.mounts` if it exists, otherwise after `spec.config`):

```markdown
### `spec.requires` — host-side values prompted from the user

Use when the skill needs per-user values that the launcher must know
*before starting the container* (e.g. mount sources).

```yaml
spec:
  requires:
    project_roots:
      type: list[directory]   # also: string, directory
      prompt: |
        Which directories contain your git projects?
        (one path per line, ~ is expanded)
```

Reference the value from `spec.mounts`:

```yaml
spec:
  mounts:
    - source: requires.project_roots
      container_prefix: /projects/   # one mount per item
      mode: ro
```

The launcher prompts the user on first `zipsa run`, saves to
`~/.zipsa/<skill>@<version>/requires.yaml`, and reads it on every
subsequent run. Users can re-set values with `zipsa configure <skill>`.
```

### Step 7.4: Run full suite as a final regression gate

```
cd launcher && uv run pytest -v 2>&1 | tail -30
```
Expected: all passing (target: 500+ tests, all green).

### Step 7.5: Commit Task 7

```bash
cd /Users/neochoon/WestbrookAI/zipsa
git add launcher/tests/fixtures/skills/requires-demo/ launcher/CLAUDE.md skills/README.md
git commit -m "docs+fixture: requires-demo skill + manifest writer guides

- tests/fixtures/skills/requires-demo: minimal skill exercising
  spec.requires + dynamic mount, usable for future smoke runs
- launcher/CLAUDE.md: 'spec.requires' section for launcher contributors
- skills/README.md: 'spec.requires' subsection for skill authors"
```

---

## Final verification

- [ ] **Full test suite green:**

```bash
cd /Users/neochoon/WestbrookAI/zipsa/launcher
uv run pytest -v
```

Expected: ≥ 500 tests passing (baseline ~472 + ~50 new), 0 failures.

- [ ] **Manual smoke (Docker required, post-merge):**

```bash
# 1. Existing skills unchanged
zipsa run hello-world "hi"
# expect: exit 0, normal output

# 2. Install fixture as a real link
zipsa install --link launcher/tests/fixtures/skills/requires-demo

# 3. First run prompts inline
zipsa run requires-demo "list projects"
# expect: prompt for project_roots; after typing a real path, run proceeds

# 4. Configure file exists
cat ~/.zipsa/requires-demo@0.1.0/requires.yaml
# expect: yaml with the path we typed

# 5. Second run no prompt
zipsa run requires-demo "list projects"
# expect: no prompt, container starts immediately, /projects/<name> visible

# 6. Reconfigure
zipsa configure requires-demo
# expect: walks through, shows current, accepts new value

# 7. Non-interactive failure
mv ~/.zipsa/requires-demo@0.1.0/requires.yaml /tmp/saved.yaml
zipsa run requires-demo "hi" < /dev/null
# expect: exit 4, "needs configuration" message
# restore: mv /tmp/saved.yaml ~/.zipsa/requires-demo@0.1.0/requires.yaml
```

- [ ] **Open PR:**

```bash
cd /Users/neochoon/WestbrookAI/zipsa
git push -u origin feat/requires-config
gh pr create --base main \
  --title "feat: requires config (per-skill, per-user host-side values)" \
  --body "$(cat <<'EOF'
## Summary

New launcher concept: `spec.requires:` in manifest declares host-side
values the launcher must obtain before starting the container. First
consumer (in a follow-up PR) is daily-progress wanting git project
parent directories for agenthud `--with-git`.

## What's new

| Surface | Change |
|---|---|
| Manifest | `spec.requires.<key>: {type, prompt}` with type ∈ {string, directory, list[directory]} |
| Manifest | `spec.mounts[].source: requires.X` + `container_prefix: /x/` for list expansion |
| Launcher | Pre-flight in `zipsa run` resolves required values (prompts inline if missing) |
| CLI | New `zipsa configure <skill>` command |
| CLI | `zipsa list` shows `⚠ needs configure (M required, N set)` indicator |
| Storage | `~/.zipsa/<skill>@<version>/requires.yaml` (atomic write) |
| Lifecycle | Carry-over from latest previous version on first new-version run |
| Errors | Missing values + no TTY → exit 4 (user_declined) |

## Commits

7 logical commits, one per task in the plan.

## Test plan

- [x] `cd launcher && uv run pytest` — all tests passing
- [ ] **Reviewer (manual, Docker required):** see plan §Final verification

## Spec / plan

- Spec: `docs/superpowers/specs/2026-05-20-requires-config-design.md`
- Plan: `docs/superpowers/plans/2026-05-20-requires-config.md`

## Out of scope (BACKLOG candidates after first consumer ships)

- Env-var injection from requires
- `zipsa configure <skill> <key>` / `--reset` / `--show`
- Types: `file`, `secret`, `int`, `bool`
- Per-path mapping for basename collisions
- List partial edits

## First consumer (separate PR)

daily-progress migration: declare `project_roots` in manifest, add the
dynamic mount, bump to agenthud@0.9.2 with `--with-git`.
EOF
)"
```
