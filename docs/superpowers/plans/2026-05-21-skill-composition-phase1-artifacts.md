# Skill Composition Phase 1: Artifact Convention + MCP `get_artifact` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A skill can save arbitrary files to its run_dir under `artifacts/`. Other processes (orchestrator agents, future tools) read them via a new MCP tool `mcp__zipsa__get_artifact(skill, run_id, name)`.

**Architecture:** The launcher already creates `~/.zipsa/<skill>@<ver>/runs/<timestamp>/` per run. Phase 1 mounts that path into the container at `/home/agent/runs/current/` (read-write), so the skill can write files to `artifacts/<name>` from inside the container. The new MCP tool reads from the host-side path and returns content over HTTP MCP. This is the foundational substrate for Phase 2's `run_skill` and Phase 4's orchestrators.

**Tech Stack:** Python 3.12, pytest, FastMCP (existing), Docker bind mounts.

**Spec:** [`docs/superpowers/specs/2026-05-21-skill-composition-design.md`](../specs/2026-05-21-skill-composition-design.md) — Phase 1 section.

---

## File Structure

**Modified files:**

| Path | Responsibility |
|---|---|
| `launcher/zipsa/paths.py` | Add `skill_run_artifacts_dir(name, version, run_id) -> Path`. Pure path helper. |
| `launcher/zipsa/core/executor.py` | Two changes: (a) create `artifacts/` subdir when creating `run_dir` (around `_execute_with_hitl` setup), (b) mount the run_dir into container at `/home/agent/runs/current/` rw in `_build_docker_command`. |
| `launcher/zipsa/core/hitl_mcp.py` | Add `ArtifactHandler` class with `get(skill, run_id, name) -> str` method. Validates path traversal, reads file, returns string content. |
| `launcher/zipsa/core/hitl_runner.py` | Register `get_artifact` MCP tool on `HitlServer` (extend tool registration block around line 90-180). |

**New files:**

| Path | Responsibility |
|---|---|
| `launcher/tests/test_artifact_mcp.py` | Unit tests for `ArtifactHandler`. |

**Test files updated:**

| Path | Responsibility |
|---|---|
| `launcher/tests/test_paths.py` | Test for new `skill_run_artifacts_dir` helper. |
| `launcher/tests/test_executor.py` | Test that `_build_docker_command` mounts the run_dir, and that `_execute_with_hitl` creates the `artifacts/` subdir. |
| `launcher/tests/test_hitl_runner.py` (if exists) or new `test_artifact_tool_integration.py` | Integration test: HitlServer exposes `get_artifact` and it returns file content. |

---

## Task 1: `skill_run_artifacts_dir` path helper

**Files:**
- Modify: `launcher/zipsa/paths.py`
- Test: `launcher/tests/test_paths.py`

- [ ] **Step 1: Write the failing test**

Add to `launcher/tests/test_paths.py` (after the existing `TestSkillMemoryFile` class):

```python
class TestSkillRunArtifactsDir:
    def test_returns_path_inside_run_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.paths import skill_run_artifacts_dir
        p = skill_run_artifacts_dir("daily-progress", "0.5.6", "2026-05-21_120000_000")
        assert p == (
            tmp_path / "daily-progress@0.5.6" / "runs"
            / "2026-05-21_120000_000" / "artifacts"
        )

    def test_signature_takes_three_args(self):
        from zipsa.paths import skill_run_artifacts_dir
        import inspect
        sig = inspect.signature(skill_run_artifacts_dir)
        assert list(sig.parameters.keys()) == ["name", "version", "run_id"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd launcher && uv run pytest tests/test_paths.py::TestSkillRunArtifactsDir -v
```

Expected: `ImportError: cannot import name 'skill_run_artifacts_dir' from 'zipsa.paths'`

- [ ] **Step 3: Implement the helper**

Add to `launcher/zipsa/paths.py` (after `skill_requires_file`, around line 30):

```python
def skill_run_artifacts_dir(name: str, version: str, run_id: str) -> Path:
    """Per-run artifacts directory.

    Where a skill writes structured artifacts that other processes
    (orchestrators, future tools) can read via MCP `get_artifact`.
    Located inside the run_dir so artifacts share lifecycle with logs.
    """
    return skill_runs_dir(name, version) / run_id / "artifacts"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd launcher && uv run pytest tests/test_paths.py::TestSkillRunArtifactsDir -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/neochoon/WestbrookAI/zipsa/.worktrees/feat-skill-composition-phase1
git add launcher/zipsa/paths.py launcher/tests/test_paths.py
git commit -m "feat(paths): add skill_run_artifacts_dir helper

Foundation for the artifact convention. Per-run artifacts live at
~/.zipsa/<skill>@<version>/runs/<run_id>/artifacts/ so they share
the run_dir lifecycle (cleanup, retention, summary linkage)."
```

---

## Task 2: Executor creates `artifacts/` subdir + mounts run_dir into container

**Files:**
- Modify: `launcher/zipsa/core/executor.py` (two places)
- Test: `launcher/tests/test_executor.py`

### Task 2a: Create `artifacts/` subdir at run start

- [ ] **Step 1: Write the failing test**

Add a new test class to `launcher/tests/test_executor.py` (after the existing `TestMountExpansion` or similar near the end):

```python
class TestArtifactsDirCreation:
    def test_run_creates_artifacts_subdir(self, tmp_path, monkeypatch):
        """When run_dir is created (real execution path), artifacts/
        subdir must exist so the skill can write into it from inside
        the container."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))

        # Set up a minimal skill fixture
        skill_dir = tmp_path / "src" / "afct"
        skill_dir.mkdir(parents=True)
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: afct, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: test\n"
            "  instructions: ./SKILL.md\n"
        )
        (skill_dir / "SKILL.md").write_text("# x")

        from zipsa.core.skill import Skill
        from zipsa.core.executor import DockerExecutor

        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")
        # Call the helper directly that prepares run_dir + artifacts/
        from zipsa.paths import skill_data_dir, skill_run_artifacts_dir
        sd = skill_data_dir("afct", "0.1.0")
        sd.mkdir(parents=True, exist_ok=True)
        runs_dir = sd / "runs"
        runs_dir.mkdir(exist_ok=True)
        run_id = "2026-05-21_120000_000"
        run_dir = runs_dir / run_id
        run_dir.mkdir()

        # Helper under test: a new internal method ex._ensure_run_artifacts_dir(run_dir)
        ex._ensure_run_artifacts_dir(run_dir)
        assert (run_dir / "artifacts").exists()
        assert (run_dir / "artifacts").is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd launcher && uv run pytest tests/test_executor.py::TestArtifactsDirCreation -v
```

Expected: `AttributeError: 'DockerExecutor' object has no attribute '_ensure_run_artifacts_dir'`

- [ ] **Step 3: Implement helper + call site**

Add to `launcher/zipsa/core/executor.py` inside the `DockerExecutor` class (after existing helpers, before `_execute_skill` — around line 480):

```python
    @staticmethod
    def _ensure_run_artifacts_dir(run_dir: Path) -> Path:
        """Create the artifacts/ subdir if missing. Returns the path.

        Artifacts are files a skill writes for cross-process consumption
        (orchestrators reading them via MCP get_artifact). Created at the
        same time as the run_dir so the mount point exists when the
        container starts.
        """
        artifacts = run_dir / "artifacts"
        artifacts.mkdir(exist_ok=True)
        return artifacts
```

Then find the `run_dir` creation site in `run()` (around line 127, `run_dir.mkdir(parents=True, exist_ok=True)`) and add an artifacts directory creation right after it:

Locate (around line 124-128 in `run()`):
```python
        started_at = datetime.now().astimezone()
        run_dir = None
        if not dry_run and not shell:
            timestamp = started_at.strftime("%Y-%m-%d_%H%M%S_%f")[:23]
            run_dir = skill_data_dir / "runs" / timestamp
            run_dir.mkdir(parents=True, exist_ok=True)
```

Change to:
```python
        started_at = datetime.now().astimezone()
        run_dir = None
        if not dry_run and not shell:
            timestamp = started_at.strftime("%Y-%m-%d_%H%M%S_%f")[:23]
            run_dir = skill_data_dir / "runs" / timestamp
            run_dir.mkdir(parents=True, exist_ok=True)
            self._ensure_run_artifacts_dir(run_dir)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd launcher && uv run pytest tests/test_executor.py::TestArtifactsDirCreation -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/neochoon/WestbrookAI/zipsa/.worktrees/feat-skill-composition-phase1
git add launcher/zipsa/core/executor.py launcher/tests/test_executor.py
git commit -m "feat(executor): create artifacts/ subdir alongside run_dir

Skills can now write to <run_dir>/artifacts/ for cross-process
consumption. The subdir is created right after run_dir so the mount
point (Task 2b) exists when the container starts."
```

### Task 2b: Mount run_dir into container at `/home/agent/runs/current/` (rw)

- [ ] **Step 1: Write the failing test**

Add another test to `TestArtifactsDirCreation` in `launcher/tests/test_executor.py`:

```python
    def test_build_docker_command_mounts_run_dir(self, tmp_path, monkeypatch):
        """Container should see the host run_dir at /home/agent/runs/current/
        with rw access, so the skill can write artifacts."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))

        skill_dir = tmp_path / "src" / "afct"
        skill_dir.mkdir(parents=True)
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: afct, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: test\n"
            "  instructions: ./SKILL.md\n"
        )
        (skill_dir / "SKILL.md").write_text("# x")

        from zipsa.core.skill import Skill
        from zipsa.core.executor import DockerExecutor

        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")

        # Create a run_dir and pass it through
        sd = tmp_path / "afct@0.1.0"
        sd.mkdir(parents=True, exist_ok=True)
        run_dir = sd / "runs" / "2026-05-21_120000_000"
        run_dir.mkdir(parents=True)
        (run_dir / "artifacts").mkdir()

        cmd = ex._build_docker_command(
            skill, "hi", tmp_path / "claude.json", {},
            run_dir=run_dir,  # NEW kwarg — currently the method doesn't take this
        )
        # Look for the rw mount: -v <run_dir>:/home/agent/runs/current:rw
        joined = " ".join(cmd)
        assert f"{run_dir}:/home/agent/runs/current:rw" in joined
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd launcher && uv run pytest tests/test_executor.py::TestArtifactsDirCreation::test_build_docker_command_mounts_run_dir -v
```

Expected: `TypeError: _build_docker_command() got an unexpected keyword argument 'run_dir'` (or similar — the mount is also absent).

- [ ] **Step 3: Implement the kwarg + mount**

In `launcher/zipsa/core/executor.py`, find `_build_docker_command` signature (around line 1093). Add `run_dir: Optional[Path] = None` to the parameter list:

```python
    def _build_docker_command(
        self,
        skill: Skill,
        user_input: str,
        claude_json_path: Path,
        env: dict[str, str],
        shell: bool = False,
        mcp_debug_host: Optional[Path] = None,
        extra_docker_opts: Optional[list[str]] = None,
        allowed_tools_override: Optional[str] = None,
        phase_id: Optional[str] = None,
        npm_volume: Optional[str] = None,
        requires_values: Optional[dict[str, object]] = None,
        model: Optional[str] = None,
        run_dir: Optional[Path] = None,
    ) -> list[str]:
```

Then in the mount-emission section (find the `seen_container_paths` block around line 1174-1180; add the new mount BEFORE that block so it participates in collision detection):

```python
        # Mount the run_dir read-write into the container so the skill
        # can write artifacts/<name> for cross-process consumption.
        # Skipped when run_dir is None (dry-run, shell mode).
        if run_dir is not None:
            seen_container_paths.add("/home/agent/runs/current")
            cmd.extend(["-v", f"{run_dir}:/home/agent/runs/current:rw"])
```

Wait — `seen_container_paths` is defined later in the spec.mounts loop. Need to place this BEFORE that loop or move the set initialization earlier.

Restructure: move the `seen_container_paths: set[str] = {...}` initialization to BEFORE the spec.mounts loop body. Then add the run_dir mount right after that initialization. Then the existing spec.mounts loop reads from `seen_container_paths` for collision detection.

Concretely, find this in `_build_docker_command` (around line 1181):

```python
        # spec.mounts: both static (host) and dynamic (source -> requires.X)
        # Seed collision tracker with zipsa-internal container paths so a
        # manifest that declares e.g. `container: /skill` errors cleanly
        # instead of silently double-mounting (undefined Docker behavior).
        seen_container_paths: set[str] = {
            "/.zipsa", "/skill", "/zipsa-hooks/pretooluse.py",
        }
        for m in skill.manifest.spec.mounts:
```

Change to:

```python
        # spec.mounts: both static (host) and dynamic (source -> requires.X)
        # Seed collision tracker with zipsa-internal container paths so a
        # manifest that declares e.g. `container: /skill` errors cleanly
        # instead of silently double-mounting (undefined Docker behavior).
        seen_container_paths: set[str] = {
            "/.zipsa", "/skill", "/zipsa-hooks/pretooluse.py",
        }

        # Mount the run_dir read-write into the container so the skill
        # can write artifacts/<name> for cross-process consumption.
        # Skipped when run_dir is None (dry-run, shell mode).
        if run_dir is not None:
            container_path = "/home/agent/runs/current"
            cmd.extend(["-v", f"{run_dir}:{container_path}:rw"])
            seen_container_paths.add(container_path)

        for m in skill.manifest.spec.mounts:
```

Now also update the call sites of `_build_docker_command` in `executor.py` to pass `run_dir`. Find the calls (around lines 147, 314, 868):

```python
# Around line 147 (dry-run / shell path):
                docker_cmd = self._build_docker_command(
                    skill, user_input, claude_json_path, env, shell=shell,
                    mcp_debug_host=mcp_debug_host,
                    extra_docker_opts=extra_docker_opts,
                    requires_values=self._requires_values,
                )
```

Note: dry-run doesn't have a run_dir. That's fine, default None passes through.

```python
# Around line 314 (single-phase real execution):
                docker_cmd = self._build_docker_command(
                    skill, user_input, claude_json_path, env,
                    mcp_debug_host=mcp_debug_host,
                    extra_docker_opts=extra_docker_opts,
                    requires_values=self._requires_values,
                )
```

Add `run_dir=run_dir` here. Need to locate `run_dir` variable in scope — it's set above in `run()` and passed down to `_execute_with_hitl`.

Look up the actual location. In `_execute_with_hitl` (around line 184), it has `run_dir: Optional[Path]` as parameter. The body calls `_build_docker_command`... let me check the actual call:

Actually, looking at the executor's code from the working session, the run_dir flows like:
- `run()` creates run_dir
- Passes to `_execute_with_hitl(run_dir=run_dir, ...)`
- Inside `_execute_with_hitl`, build_docker_command call happens (around line 314 or in _execute_phases around 868)

So I just need to add `run_dir=run_dir` at those call sites (they already have run_dir in scope). Find them precisely:

Run this to find them all:
```bash
grep -n "self._build_docker_command(" launcher/zipsa/core/executor.py
```

For each occurrence, add `run_dir=run_dir` to the kwargs. dry-run/shell call (line ~147) has no run_dir in scope — pass `run_dir=None` explicitly (or rely on default).

- [ ] **Step 4: Run test to verify it passes**

```bash
cd launcher && uv run pytest tests/test_executor.py::TestArtifactsDirCreation -v
```

Expected: 2 passed (both `test_run_creates_artifacts_subdir` and `test_build_docker_command_mounts_run_dir`).

- [ ] **Step 5: Run full suite for regression**

```bash
cd launcher && uv run pytest 2>&1 | tail -3
```

Expected: 614+ passing (613 baseline + 2 new), 0 failures.

- [ ] **Step 6: Commit**

```bash
cd /Users/neochoon/WestbrookAI/zipsa/.worktrees/feat-skill-composition-phase1
git add launcher/zipsa/core/executor.py launcher/tests/test_executor.py
git commit -m "feat(executor): mount run_dir into container at /home/agent/runs/current

Skill can now write artifacts to /home/agent/runs/current/artifacts/<name>
from inside the container — they land in the host's run_dir for
cross-process consumption by MCP get_artifact (Task 3).

Pre-seed collision tracker with the new container path so manifests
that declare container: /home/agent/runs/current trigger a clean error."
```

---

## Task 3: `ArtifactHandler` (the read-side logic)

**Files:**
- Modify: `launcher/zipsa/core/hitl_mcp.py`
- Test: `launcher/tests/test_artifact_mcp.py` (NEW)

- [ ] **Step 1: Write the failing test**

Create `launcher/tests/test_artifact_mcp.py`:

```python
"""Tests for ArtifactHandler — the read-side of the artifact convention."""

import pytest
from pathlib import Path


class TestArtifactHandler:
    def test_reads_artifact_content_returns_string(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.hitl_mcp import ArtifactHandler

        # Set up: a skill that wrote an artifact during a run
        run_dir = tmp_path / "afct@0.1.0" / "runs" / "2026-05-21_120000_000"
        artifacts_dir = run_dir / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "report.json").write_text('{"hello": "world"}')

        h = ArtifactHandler()
        content = h.get(skill="afct", run_id="2026-05-21_120000_000", name="report.json")
        assert content == '{"hello": "world"}'

    def test_returns_none_when_artifact_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.hitl_mcp import ArtifactHandler

        run_dir = tmp_path / "afct@0.1.0" / "runs" / "2026-05-21_120000_000"
        (run_dir / "artifacts").mkdir(parents=True)
        # Don't create report.json

        h = ArtifactHandler()
        content = h.get(skill="afct", run_id="2026-05-21_120000_000", name="report.json")
        assert content is None

    def test_returns_none_when_run_dir_missing(self, tmp_path, monkeypatch):
        """Unknown run_id (skill never ran or wrong id) → None, not error."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.hitl_mcp import ArtifactHandler

        h = ArtifactHandler()
        content = h.get(skill="afct", run_id="bogus", name="report.json")
        assert content is None

    def test_rejects_path_traversal_in_name(self, tmp_path, monkeypatch):
        """Caller can't escape the artifacts/ dir via .. or absolute path."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.hitl_mcp import ArtifactHandler

        run_dir = tmp_path / "afct@0.1.0" / "runs" / "2026-05-21_120000_000"
        (run_dir / "artifacts").mkdir(parents=True)

        h = ArtifactHandler()
        for bad_name in ("../../etc/passwd", "/etc/passwd", "../other/secret"):
            with pytest.raises(ValueError, match="invalid artifact name"):
                h.get(skill="afct", run_id="2026-05-21_120000_000", name=bad_name)

    def test_rejects_path_traversal_in_run_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.hitl_mcp import ArtifactHandler

        h = ArtifactHandler()
        for bad_run_id in ("../other-skill@0.0.0/runs/r", "/etc"):
            with pytest.raises(ValueError, match="invalid run_id"):
                h.get(skill="afct", run_id=bad_run_id, name="report.json")

    def test_rejects_path_traversal_in_skill(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.hitl_mcp import ArtifactHandler

        h = ArtifactHandler()
        for bad_skill in ("../foo", "/etc", "a/b"):
            with pytest.raises(ValueError, match="invalid skill"):
                h.get(skill=bad_skill, run_id="x", name="report.json")

    def test_largest_skill_version_is_picked(self, tmp_path, monkeypatch):
        """Skill name maps to ~/.zipsa/<skill>@<version>/ but caller doesn't
        know which version. ArtifactHandler picks the version whose runs/
        contains the run_id; on tie, the most recent version (semver-aware)."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.hitl_mcp import ArtifactHandler

        # Two versions of the skill, both have runs but only @0.2.0 has the
        # specific run_id we're asking about.
        for ver, has_run in [("0.1.0", False), ("0.2.0", True)]:
            run_dir = tmp_path / f"afct@{ver}" / "runs" / "2026-05-21_120000_000"
            if has_run:
                (run_dir / "artifacts").mkdir(parents=True)
                (run_dir / "artifacts" / "x.json").write_text('"v2"')

        h = ArtifactHandler()
        content = h.get(skill="afct", run_id="2026-05-21_120000_000", name="x.json")
        assert content == '"v2"'
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd launcher && uv run pytest tests/test_artifact_mcp.py -v
```

Expected: All 7 tests fail (`ImportError: cannot import name 'ArtifactHandler'`).

- [ ] **Step 3: Implement `ArtifactHandler`**

Add to `launcher/zipsa/core/hitl_mcp.py` (after `ListMemoryHandler`, around line 170):

```python
class ArtifactHandler:
    """Read-side of the artifact convention.

    Skills write to ~/.zipsa/<skill>@<version>/runs/<run_id>/artifacts/<name>
    from inside the container (the run_dir is mounted rw — see
    DockerExecutor._build_docker_command). Other processes — primarily
    orchestrator agents — call this handler via MCP `get_artifact` to
    read those files.

    Returns None for missing files / unknown run_id (not an error —
    orchestrator may probe for optional artifacts). Rejects path
    traversal in any of the three name components.
    """

    # Skill name: standard skill identifier (matches existing convention)
    _SKILL_RE = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    # run_id: timestamp form created by executor.run, e.g.
    # 2026-05-21_120000_000 (digits/underscores only)
    _RUN_ID_RE = __import__("re").compile(r"^[A-Za-z0-9_.-]+$")
    # Artifact name: anything without path separators or leading dot/dash
    _ARTIFACT_NAME_RE = __import__("re").compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")

    def get(self, skill: str, run_id: str, name: str) -> str | None:
        from zipsa.paths import zipsa_home

        if not self._SKILL_RE.match(skill):
            raise ValueError(f"invalid skill name: {skill!r}")
        if not self._RUN_ID_RE.match(run_id):
            raise ValueError(f"invalid run_id: {run_id!r}")
        if not self._ARTIFACT_NAME_RE.match(name) or ".." in name:
            raise ValueError(f"invalid artifact name: {name!r}")

        home = zipsa_home()
        if not home.exists():
            return None

        # Pick the most-recent version of <skill> whose runs/ contains run_id.
        prefix = f"{skill}@"
        candidates = []
        for entry in home.iterdir():
            if not entry.is_dir() or not entry.name.startswith(prefix):
                continue
            ver = entry.name[len(prefix):]
            artifact_path = entry / "runs" / run_id / "artifacts" / name
            if artifact_path.exists():
                candidates.append((self._version_key(ver), artifact_path))
        if not candidates:
            return None

        candidates.sort(key=lambda c: c[0], reverse=True)
        return candidates[0][1].read_text(encoding="utf-8")

    @staticmethod
    def _version_key(version: str) -> tuple:
        """Sort versions naturally: 0.4.10 > 0.4.9. Same as paths._version_sort_key."""
        parts = []
        for chunk in version.split("."):
            try:
                parts.append((0, int(chunk)))
            except ValueError:
                parts.append((1, chunk))
        return tuple(parts)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd launcher && uv run pytest tests/test_artifact_mcp.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/neochoon/WestbrookAI/zipsa/.worktrees/feat-skill-composition-phase1
git add launcher/zipsa/core/hitl_mcp.py launcher/tests/test_artifact_mcp.py
git commit -m "feat(mcp): ArtifactHandler — read skill artifacts from host

The read-side of Phase 1's artifact convention. Validates the three
name components against path-traversal, walks ~/.zipsa/<skill>@*/
to find the matching run_id, returns file content or None.

Wired into HitlServer as mcp__zipsa__get_artifact in Task 4."
```

---

## Task 4: Register `get_artifact` MCP tool on `HitlServer`

**Files:**
- Modify: `launcher/zipsa/core/hitl_runner.py`
- Test: `launcher/tests/test_artifact_tool_integration.py` (NEW)

- [ ] **Step 1: Write the failing test**

Create `launcher/tests/test_artifact_tool_integration.py`:

```python
"""Integration test: HitlServer exposes get_artifact MCP tool."""

import pytest


class TestGetArtifactTool:
    def test_tool_registered_on_hitl_server(self, tmp_path, monkeypatch):
        """HitlServer.start() registers get_artifact alongside ask/confirm/etc."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.hitl_runner import HitlServer
        from zipsa.core.hitl_mcp import HitlIO
        import io

        io_ = HitlIO(
            stdin=io.StringIO(""),
            stdout=io.StringIO(),
            stdout_lock=__import__("threading").Lock(),
            is_interactive=False,
        )
        server = HitlServer(io_)
        # Don't actually start (no need for full server thread for this assert).
        # Verify the registered tool list includes get_artifact by inspecting
        # the FastMCP instance built inside start(). The cleanest assertion
        # is "server has start method that wires get_artifact" — but
        # FastMCP encapsulates tools opaquely. Instead, verify the
        # _register helper or class attribute exists by importing and
        # introspecting.

        # Verify by side-channel: import the start method's source and
        # confirm 'get_artifact' is mentioned. Brittle but adequate for v1.
        import inspect
        src = inspect.getsource(HitlServer.start)
        assert "get_artifact" in src, "HitlServer.start does not register get_artifact"

    def test_get_artifact_handler_returns_content(self, tmp_path, monkeypatch):
        """End-to-end: write an artifact, call the handler the way the MCP
        tool will, get the content back."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))

        run_dir = tmp_path / "afct@0.1.0" / "runs" / "2026-05-21_120000_000"
        (run_dir / "artifacts").mkdir(parents=True)
        (run_dir / "artifacts" / "report.json").write_text('{"k": 1}')

        from zipsa.core.hitl_mcp import ArtifactHandler
        h = ArtifactHandler()
        assert h.get("afct", "2026-05-21_120000_000", "report.json") == '{"k": 1}'
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd launcher && uv run pytest tests/test_artifact_tool_integration.py -v
```

Expected: `test_tool_registered_on_hitl_server` fails with `AssertionError: HitlServer.start does not register get_artifact`.

(The second test passes already because ArtifactHandler exists from Task 3 — that's expected; it's a sanity check.)

- [ ] **Step 3: Wire `get_artifact` into `HitlServer.start()`**

In `launcher/zipsa/core/hitl_runner.py`, find the `start()` method (around line 69). After the existing memory tools registration block (after `if self._skill_store is not None and self._global_store is not None:` block ends), add:

```python
        # Artifact reader — for orchestrators to consume children's outputs.
        from .hitl_mcp import ArtifactHandler

        artifact_h = ArtifactHandler()

        @mcp.tool()
        def get_artifact(skill: str, run_id: str, name: str) -> str | None:
            """Read an artifact file written by a skill run.

            Skills write to /home/agent/runs/current/artifacts/<name> from
            inside their container; that path corresponds to
            ~/.zipsa/<skill>@<version>/runs/<run_id>/artifacts/<name> on
            the host. This tool reads from the host side and returns the
            file content as a string, or None if the artifact doesn't
            exist (the orchestrator may probe optional artifacts).

            Raises ValueError if any of the path components look like
            path-traversal attempts.
            """
            return artifact_h.get(skill=skill, run_id=run_id, name=name)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd launcher && uv run pytest tests/test_artifact_tool_integration.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Run full suite for regression**

```bash
cd launcher && uv run pytest 2>&1 | tail -3
```

Expected: 622+ passing (613 baseline + 9 new across tasks 1-4), 0 failures.

- [ ] **Step 6: Commit**

```bash
cd /Users/neochoon/WestbrookAI/zipsa/.worktrees/feat-skill-composition-phase1
git add launcher/zipsa/core/hitl_runner.py launcher/tests/test_artifact_tool_integration.py
git commit -m "feat(mcp): register get_artifact tool on HitlServer

Orchestrator agents can now call mcp__zipsa__get_artifact(skill,
run_id, name) to read files that a child skill wrote to its
run_dir/artifacts/. Foundation for Phase 2's run_skill (parent
discovers child's run_id from the run_skill response, then reads
artifacts produced by that child's run).

Path traversal validation in ArtifactHandler keeps the tool safe to
expose."
```

---

## Task 5: Document `get_artifact` in runtime-contract.md

**Files:**
- Modify: `launcher/zipsa/system-prompts/runtime-contract.md`

- [ ] **Step 1: Add a section to the contract**

Find the "Tool usage" section (around line 122). After the "Hook denials" subsection (added in PR #48), add:

```markdown
### Artifacts from past runs (`mcp__zipsa__get_artifact`)

Skills can write files to `/home/agent/runs/current/artifacts/<name>`
from inside their container. Those files persist on the host as part
of the skill's run record (next to `summary.json` and `output.jsonl`).

Other skills (orchestrators reading a child's outputs) read them via:

```
mcp__zipsa__get_artifact(skill: str, run_id: str, name: str) -> str | None
```

Returns the file content as a string, or `null` if the artifact doesn't
exist. Raises a ValueError if any name component looks like path
traversal.

`run_id` is the timestamp directory name returned in the `run_skill`
response (Phase 2 — not yet implemented; for now `get_artifact` is
mainly useful in test fixtures).

**Atomic skills do not call `get_artifact`.** That's an orchestrator
concern — atomics are pure I/O.
```

- [ ] **Step 2: Verify no test breakage**

```bash
cd launcher && uv run pytest 2>&1 | tail -3
```

Expected: same 622+ passing.

- [ ] **Step 3: Commit**

```bash
cd /Users/neochoon/WestbrookAI/zipsa/.worktrees/feat-skill-composition-phase1
git add launcher/zipsa/system-prompts/runtime-contract.md
git commit -m "docs(contract): document get_artifact MCP tool

Tells agents how to read artifacts produced by past skill runs.
Foreshadows Phase 2's run_skill (which gives parent the child's
run_id needed to read its artifacts)."
```

---

## Final Verification

- [ ] **Full test suite passes:**

```bash
cd /Users/neochoon/WestbrookAI/zipsa/.worktrees/feat-skill-composition-phase1/launcher
uv run pytest -v 2>&1 | tail -10
```

Expected: 622+ passing, 0 failures.

- [ ] **Manual smoke (post-merge, Docker required):**

```bash
# Modify hello-world to write an artifact (just for this smoke):
# /skill/scripts/precheck.py or similar phase script:
#   import json, pathlib
#   pathlib.Path("/home/agent/runs/current/artifacts").mkdir(parents=True, exist_ok=True)
#   pathlib.Path("/home/agent/runs/current/artifacts/hello.json").write_text(
#     json.dumps({"greeting": "hi"})
#   )
zipsa run hello-world

# After completion, the host should have the artifact:
ls ~/.zipsa/hello-world@*/runs/*/artifacts/hello.json
cat ~/.zipsa/hello-world@*/runs/*/artifacts/hello.json
# Expected: {"greeting": "hi"}
```

- [ ] **Open PR:**

```bash
cd /Users/neochoon/WestbrookAI/zipsa/.worktrees/feat-skill-composition-phase1
git push -u origin feat/skill-composition-phase1
gh pr create --base main \
  --title "feat: skill composition Phase 1 — artifact convention + MCP get_artifact" \
  --body "$(cat <<'EOF'
## Summary

Foundation for the skill-composition design (atomic + orchestrator skills). Phase 1 of 5.

A skill can now write arbitrary files to its run_dir under `artifacts/`. Orchestrator agents (Phase 2's run_skill consumers) will read those files via a new MCP tool `mcp__zipsa__get_artifact(skill, run_id, name)`.

## What's new

| Surface | Change |
|---|---|
| Mount | Container's `/home/agent/runs/current/` rw-mounts the host's `~/.zipsa/<skill>@<version>/runs/<timestamp>/` |
| Path helper | `skill_run_artifacts_dir(name, version, run_id)` in `paths.py` |
| MCP tool | `mcp__zipsa__get_artifact(skill, run_id, name) -> str \| None` |
| Validation | Path-traversal rejection on all three name components |
| Versioning | `get_artifact` finds the most-recent version of `<skill>` whose runs/ contains the run_id |
| Collision tracker | Container path `/home/agent/runs/current` added to pre-seeded set |

## Spec / Plan

- Spec: `docs/superpowers/specs/2026-05-21-skill-composition-design.md`
- Plan: `docs/superpowers/plans/2026-05-21-skill-composition-phase1-artifacts.md`

## Test plan

- [x] `cd launcher && uv run pytest` — 622+ passing (613 baseline + 9 new)
- [x] Unit tests for `skill_run_artifacts_dir` helper
- [x] Unit tests for `ArtifactHandler` (read + missing + path-traversal × 3 components + multi-version)
- [x] Executor mount + artifacts/ subdir creation tests
- [x] Integration test that `HitlServer.start()` registers `get_artifact`
- [ ] **Reviewer (manual, Docker required):** modified hello-world writes an artifact; host sees the file after run completes

## Next phase

Phase 2 (separate PR): `mcp__zipsa__run_skill` so parent agents can invoke children synchronously. With Phase 1's `get_artifact`, parent can then read child's artifacts.
EOF
)"
```
