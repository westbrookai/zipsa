# Broken linked installs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** `zipsa list` shows broken install entries (instead of silently filtering them); `zipsa install` replaces a broken entry transparently. Two bugs that have already bitten the user twice in two days.

**Architecture:** New tiny module `zipsa/core/install_health.py` with `check_install(path) -> InstallHealth` dataclass. Both `list` and `install` CLI commands call it. No change to `Skill.load`, executor, manifest model, or anything downstream of those.

**Tech Stack:** unchanged (Python 3.10+, pydantic, typer, pytest).

---

## Commit boundaries

| Commit | What |
|---|---|
| **1** | `feat(install-health): InstallHealth + check_install helper` (Task 1, pure module + tests) |
| **2** | `feat(cli): list shows broken entries; install replaces broken transparently` (Tasks 2+3 bundled — both consume the helper, both touch cli.py) |

---

## File map

| File | Role |
|---|---|
| `launcher/zipsa/core/install_health.py` (new) | `InstallHealth` dataclass + `check_install(path)` |
| `launcher/zipsa/cli.py` | `list` command renders broken rows; `install` command replaces broken entries |
| `launcher/tests/test_install_health.py` (new) | Unit tests on `check_install` |
| `launcher/tests/test_cli.py` | Integration tests for the two CLI behaviors |

---

## Task 1: `install_health.py` — detect broken installs

**Files:**
- Create: `launcher/zipsa/core/install_health.py`
- Create: `launcher/tests/test_install_health.py`

- [ ] **Step 1: Write the failing test**

```python
# launcher/tests/test_install_health.py
"""Tests for the install-health detection helper."""

from pathlib import Path

import pytest

from zipsa.core.install_health import InstallHealth, check_install


class TestHealthyDetection:
    def test_real_directory_with_valid_manifest_is_ok(self, tmp_path):
        d = tmp_path / "skill-a"
        d.mkdir()
        (d / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata:\n"
            "  name: skill-a\n"
            "  version: 1.0.0\n"
            "spec:\n"
            "  purpose: Test.\n"
            "  instructions: ./SKILL.md\n"
        )
        h = check_install(d)
        assert isinstance(h, InstallHealth)
        assert h.ok is True
        assert h.reason is None

    def test_valid_symlink_with_valid_manifest_is_ok(self, tmp_path):
        src = tmp_path / "src" / "skill-a"
        src.mkdir(parents=True)
        (src / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata:\n"
            "  name: skill-a\n"
            "  version: 1.0.0\n"
            "spec:\n"
            "  purpose: Test.\n"
            "  instructions: ./SKILL.md\n"
        )
        link = tmp_path / "installed" / "skill-a"
        link.parent.mkdir()
        link.symlink_to(src)
        h = check_install(link)
        assert h.ok is True


class TestBrokenDetection:
    def test_dangling_symlink_reports_linked_source_missing(self, tmp_path):
        gone = tmp_path / "removed-source"
        link = tmp_path / "skill-a"
        link.symlink_to(gone)  # gone never created
        h = check_install(link)
        assert h.ok is False
        assert "Linked source missing" in h.reason
        assert str(gone) in h.reason

    def test_directory_without_manifest_reports_missing_manifest(self, tmp_path):
        d = tmp_path / "skill-a"
        d.mkdir()
        # no manifest.yaml
        h = check_install(d)
        assert h.ok is False
        assert "manifest.yaml not found" in h.reason

    def test_invalid_manifest_reports_invalid(self, tmp_path):
        d = tmp_path / "skill-a"
        d.mkdir()
        (d / "manifest.yaml").write_text("not: { valid yaml: : :")
        h = check_install(d)
        assert h.ok is False
        assert "Invalid manifest" in h.reason

    def test_manifest_failing_pydantic_validation_reports_invalid(self, tmp_path):
        d = tmp_path / "skill-a"
        d.mkdir()
        (d / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {}  # missing required fields\n"
            "spec: {}\n"
        )
        h = check_install(d)
        assert h.ok is False
        assert "Invalid manifest" in h.reason

    def test_symlink_to_dir_without_manifest_reports_missing_manifest(self, tmp_path):
        src = tmp_path / "src" / "skill-a"
        src.mkdir(parents=True)
        # source exists but has no manifest
        link = tmp_path / "installed" / "skill-a"
        link.parent.mkdir()
        link.symlink_to(src)
        h = check_install(link)
        assert h.ok is False
        assert "manifest.yaml not found" in h.reason
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd launcher && uv run pytest tests/test_install_health.py -v
```
Expected: ModuleNotFoundError on `zipsa.core.install_health`.

- [ ] **Step 3: Implement `install_health.py`**

```python
# launcher/zipsa/core/install_health.py
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
            target = path.readlink()
        except OSError:
            target = "<unreadable>"
        return InstallHealth(ok=False, reason=f"Linked source missing: {target}")

    if not path.exists():
        return InstallHealth(ok=False, reason="Install entry does not exist")

    manifest = path / "manifest.yaml"
    if not manifest.exists():
        return InstallHealth(ok=False, reason="manifest.yaml not found")

    # Try to load the manifest. We use the same code path as production
    # (Skill.load) so any future load-time validation is caught here too.
    try:
        # Local import keeps install_health side-effect-free at import time.
        from .skill import Skill
        Skill.load(path)
    except Exception as e:
        head = str(e).splitlines()[0] if str(e) else type(e).__name__
        # Keep the reason short — long pydantic stacks blow out terminals.
        head = head[:160]
        return InstallHealth(ok=False, reason=f"Invalid manifest: {head}")

    return InstallHealth(ok=True)
```

- [ ] **Step 4: Run tests**

```bash
cd launcher && uv run pytest tests/test_install_health.py -v
```
Expected: all passing.

Full suite: `uv run pytest`. Expected: 431 baseline + 7 new = 438 passing.

- [ ] **Step 5: Commit (boundary 1)**

```bash
git add launcher/zipsa/core/install_health.py launcher/tests/test_install_health.py
git commit -m "feat(install-health): InstallHealth + check_install helper

Single helper for detecting whether an installed-skill entry can
load. Used in the next commit by both 'zipsa list' (to render broken
rows instead of filtering them) and 'zipsa install' (to replace
broken entries transparently instead of erroring with 'already
installed')."
```

---

## Task 2+3: CLI changes — `list` + `install`

**Files:**
- Modify: `launcher/zipsa/cli.py` (the `list` and `install` commands)
- Modify: `launcher/tests/test_cli.py`

### Step 1: Locate current behavior

Read the `list` command in cli.py. Find the loop that walks `~/.zipsa/skills/`. Today it does roughly:
```python
for item in installed_dir.iterdir():
    try:
        skill = Skill.load(item)
        # ... print healthy row ...
    except Exception:
        continue  # silently skip
```

We'll replace the bare `continue` with a broken-row render.

Read the `install` command. Find where it raises "already installed". Today, roughly:
```python
existing = installed_dir / name
if existing.exists():
    if not force:
        typer.echo(f"Error: Skill '{name}' is already installed. Use --force to overwrite.", err=True)
        raise typer.Exit(1)
```

We'll insert a `check_install` call here: if the existing entry is broken, skip the "already installed" error and proceed to install (which overwrites).

### Step 2: Write failing integration tests

Add to `launcher/tests/test_cli.py`:

```python
class TestListBrokenEntries:
    """zipsa list must SHOW broken entries with a marker + reason +
    recovery hint, not silently filter them."""

    def test_list_renders_broken_dangling_symlink(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from zipsa.cli import app

        # Build a fake zipsa_home with one healthy and one broken entry.
        zhome = tmp_path / "zipsa-home"
        skills_dir = zhome / "skills"
        skills_dir.mkdir(parents=True)

        # Healthy: real dir with valid manifest
        healthy = skills_dir / "healthy-skill"
        healthy.mkdir()
        (healthy / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\nkind: Skill\n"
            "metadata: {name: healthy-skill, version: 1.0.0}\n"
            "spec: {purpose: ok, instructions: ./SKILL.md}\n"
        )

        # Broken: dangling symlink
        gone = tmp_path / "removed-source"
        broken = skills_dir / "broken-skill"
        broken.symlink_to(gone)

        monkeypatch.setattr("zipsa.cli.zipsa_home", lambda: zhome)

        runner = CliRunner()
        result = runner.invoke(app, ["list"])

        assert result.exit_code == 0, result.output
        # Both names appear in output
        assert "healthy-skill" in result.output
        assert "broken-skill" in result.output
        # Broken marker and reason both present
        assert "broken" in result.output.lower()
        assert "Linked source missing" in result.output
        assert str(gone) in result.output
        # Recovery hint
        assert "zipsa install --link" in result.output

    def test_list_count_includes_broken(self, tmp_path, monkeypatch):
        """Installed skills (N): N counts broken entries too — they
        ARE installed, they just don't load."""
        from typer.testing import CliRunner
        from zipsa.cli import app

        zhome = tmp_path / "zipsa-home"
        skills_dir = zhome / "skills"
        skills_dir.mkdir(parents=True)

        for i in range(2):
            d = skills_dir / f"healthy-{i}"
            d.mkdir()
            (d / "manifest.yaml").write_text(
                "apiVersion: zipsa.dev/v1alpha1\nkind: Skill\n"
                f"metadata: {{name: healthy-{i}, version: 1.0.0}}\n"
                "spec: {purpose: ok, instructions: ./SKILL.md}\n"
            )
        (skills_dir / "broken").symlink_to(tmp_path / "gone")

        monkeypatch.setattr("zipsa.cli.zipsa_home", lambda: zhome)

        runner = CliRunner()
        result = runner.invoke(app, ["list"])

        assert "(3)" in result.output or "Installed skills (3)" in result.output


class TestInstallReplacesBroken:
    """zipsa install replaces a broken entry transparently — no --force
    needed, message says what happened."""

    def test_install_link_replaces_broken_entry(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from zipsa.cli import app

        zhome = tmp_path / "zipsa-home"
        skills_dir = zhome / "skills"
        skills_dir.mkdir(parents=True)

        # Existing broken entry: dangling symlink named "test-skill"
        (skills_dir / "test-skill").symlink_to(tmp_path / "gone")

        # New source to install
        new_src = tmp_path / "new-src"
        new_src.mkdir()
        (new_src / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\nkind: Skill\n"
            "metadata: {name: test-skill, version: 1.0.0}\n"
            "spec: {purpose: ok, instructions: ./SKILL.md}\n"
        )
        (new_src / "SKILL.md").write_text("# Test")

        monkeypatch.setattr("zipsa.cli.zipsa_home", lambda: zhome)

        runner = CliRunner()
        result = runner.invoke(app, ["install", "--link", str(new_src)])

        assert result.exit_code == 0, result.output
        # Output mentions the replacement
        assert "Replaced broken link" in result.output
        assert "test-skill" in result.output
        # Symlink now points to the new source
        link = skills_dir / "test-skill"
        assert link.is_symlink()
        assert link.resolve() == new_src.resolve()

    def test_install_link_healthy_existing_still_errors_without_force(self, tmp_path, monkeypatch):
        """Regression: healthy existing install + new install without
        --force still errors with 'already installed'."""
        from typer.testing import CliRunner
        from zipsa.cli import app

        zhome = tmp_path / "zipsa-home"
        skills_dir = zhome / "skills"
        skills_dir.mkdir(parents=True)

        # Healthy existing entry
        existing = skills_dir / "test-skill"
        existing.mkdir()
        (existing / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\nkind: Skill\n"
            "metadata: {name: test-skill, version: 1.0.0}\n"
            "spec: {purpose: ok, instructions: ./SKILL.md}\n"
        )

        # New source
        new_src = tmp_path / "new-src"
        new_src.mkdir()
        (new_src / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\nkind: Skill\n"
            "metadata: {name: test-skill, version: 2.0.0}\n"
            "spec: {purpose: ok, instructions: ./SKILL.md}\n"
        )
        (new_src / "SKILL.md").write_text("# Test")

        monkeypatch.setattr("zipsa.cli.zipsa_home", lambda: zhome)

        runner = CliRunner()
        result = runner.invoke(app, ["install", "--link", str(new_src)])

        assert result.exit_code != 0
        assert "already installed" in result.output.lower()
```

The exact `zipsa.cli.zipsa_home` patch target depends on how cli.py imports `zipsa_home`. If cli imports it as `from .paths import zipsa_home`, then `zipsa.cli.zipsa_home` is correct. If cli imports the module (`from . import paths`) and uses `paths.zipsa_home()`, patch `zipsa.cli.paths.zipsa_home`. The implementer adapts.

### Step 3: Run tests to verify they fail

```bash
cd launcher && uv run pytest tests/test_cli.py::TestListBrokenEntries tests/test_cli.py::TestInstallReplacesBroken -v
```

Expected: failures — list filters silently, install errors with "already installed".

### Step 4: Implement

In `cli.py`:

**Import** (top of file):
```python
from .core.install_health import check_install
```

**`list` command** — find the for-loop walking `~/.zipsa/skills/`. Replace the bare `except: continue` skip with:

```python
for item in sorted(installed_dir.iterdir()):
    health = check_install(item)
    if not health.ok:
        # Render broken row
        typer.echo(f"  {item.name}  ✗ broken")
        typer.echo(f"    {health.reason}")
        typer.echo(f"    Fix: zipsa install --link <new-path>  "
                   f"(or: zipsa uninstall {item.name})")
        continue
    # ... existing healthy-row rendering ...
```

Make sure the `Installed skills (N):` count includes broken entries. If today it's pre-computed by filtering `iterdir()`, change it to count ALL entries in `iterdir()` (including broken ones).

**`install` command** — find the "already installed" check. Wrap with health check:

```python
existing = installed_dir / name
if existing.exists() or existing.is_symlink():  # is_symlink catches dangling links
    health = check_install(existing)
    if not health.ok:
        # Broken entry → replace transparently, no --force needed
        typer.echo(f"Replaced broken link: {name} (linked)")
        # remove the broken entry, then fall through to normal install
        if existing.is_symlink() or existing.is_file():
            existing.unlink()
        else:
            import shutil
            shutil.rmtree(existing)
        # ... proceed with normal install logic ...
    elif not force:
        typer.echo(f"Error: Skill '{name}' is already installed. Use --force to overwrite.", err=True)
        raise typer.Exit(1)
    # else (healthy + --force): existing behavior — overwrite
```

The exact placement depends on the current install command structure. Preserve all other paths (force flag, install from github, etc.).

### Step 5: Run tests

```bash
cd launcher && uv run pytest tests/test_cli.py::TestListBrokenEntries tests/test_cli.py::TestInstallReplacesBroken -v
uv run pytest  # full suite
```
Expected: new tests passing; no regressions.

### Step 6: Commit (boundary 2)

```bash
git add launcher/zipsa/cli.py launcher/tests/test_cli.py
git commit -m "feat(cli): list shows broken entries; install replaces broken transparently

zipsa list — instead of silently filtering entries that won't load,
render a broken row with the failure reason and recovery hint.
Count includes broken (they ARE installed; they just don't load).

zipsa install — when the existing entry at the target name is broken
(per check_install), replace it transparently with no --force
required, and print 'Replaced broken link: <name> (linked)'.
Healthy existing entries still need --force to overwrite — that
path is unchanged.

The two CLI commands now agree about whether a skill is installed,
fixing the bug where 'list' said no but 'install' said yes."
```

---

## Wrap-up

After both commits:

- [ ] `git log --oneline ea14c76..HEAD` — 2 task commits + 1 spec commit at the head.
- [ ] `uv run pytest` from `launcher/` — green (~440 passing).
- [ ] Manual smoke:
  ```bash
  # Reproduce the bug we hit today
  mkdir /tmp/broken-test-src
  cat > /tmp/broken-test-src/manifest.yaml <<'EOF'
  apiVersion: zipsa.dev/v1alpha1
  kind: Skill
  metadata: {name: broken-test, version: 0.1.0}
  spec: {purpose: test, instructions: ./SKILL.md}
  EOF
  echo "# Test" > /tmp/broken-test-src/SKILL.md

  zipsa install --link /tmp/broken-test-src
  rm -rf /tmp/broken-test-src    # simulate worktree removal
  zipsa list                      # should show broken-test with reason + Fix line
  mkdir /tmp/broken-test-src
  cat > /tmp/broken-test-src/manifest.yaml <<'EOF'
  apiVersion: zipsa.dev/v1alpha1
  kind: Skill
  metadata: {name: broken-test, version: 0.2.0}
  spec: {purpose: test, instructions: ./SKILL.md}
  EOF
  echo "# Test" > /tmp/broken-test-src/SKILL.md
  zipsa install --link /tmp/broken-test-src
  # should print "Replaced broken link: broken-test (linked)"
  zipsa list                      # should now show healthy broken-test
  zipsa uninstall broken-test
  rm -rf /tmp/broken-test-src
  ```
- [ ] Push branch, open PR. Reference spec + plan.
- [ ] PR description should call out: "BACKLOG entry from 2026-05-18 (originally PR #19) — bit twice in two days, ships now. doctor + worktree-hook remain in BACKLOG."
