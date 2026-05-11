# zipsa install Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `zipsa install` (GitHub + local), `zipsa uninstall`, a new `zipsa list` (installed skills + run stats), `zipsa discover` (renamed from old `list`), and switch all commands to name-based skill resolution.

**Architecture:** Skills install to `~/.zipsa/skills/<name>/` with an `_install.json` tracking provenance. A new `zipsa/installer.py` handles source parsing and downloading. `zipsa/paths.py` gains `resolve_skill(name)` used by all CLI commands instead of accepting raw paths.

**Tech Stack:** Python stdlib `urllib` + `tarfile` for GitHub tarball download, `shutil` for local copy, existing `Skill.load()` + Pydantic `ValidationError` for install-time validation, `typer` for CLI.

---

## File Map

| File | Change |
|------|--------|
| `launcher/zipsa/installer.py` | **Create** — source parser, GitHub downloader, local installer |
| `launcher/zipsa/paths.py` | **Modify** — add `skills_dir`, `installed_skill_dir`, `resolve_skill`, `SkillNotInstalledError` |
| `launcher/zipsa/core/executor.py` | **Modify** — add `user_input` to `_save_metadata` |
| `launcher/zipsa/cli.py` | **Modify** — `install`, `uninstall`, new `list`, `discover`; all commands use names |
| `launcher/tests/test_installer.py` | **Create** — tests for installer module |
| `launcher/tests/test_paths.py` | **Modify** — add tests for new path helpers |
| `launcher/tests/test_executor.py` | **Modify** — add `user_input` metadata test |
| `launcher/tests/test_cli.py` | **Modify** — update all command tests to use names, add new command tests |

---

### Task 1: Add `user_input` to `metadata.json`

**Files:**
- Modify: `launcher/zipsa/core/executor.py:299-306` (call site) and `:368-431` (`_save_metadata`)
- Modify: `launcher/tests/test_executor.py`

- [ ] **Step 1: Write the failing test**

Add to `class TestSaveMetadata` in `launcher/tests/test_executor.py` (if that class doesn't exist, add it at the end of the file):

```python
class TestSaveMetadata:
    """Test _save_metadata writes correct metadata.json."""

    def test_user_input_is_recorded_in_metadata(self, tmp_path):
        """metadata.json should contain the user_input field."""
        import json
        executor = DockerExecutor()
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        # Write a minimal output.jsonl with a result event
        (run_dir / "output.jsonl").write_text(
            '{"type": "result", "is_error": false, "duration_ms": 1000, '
            '"duration_api_ms": 800, "num_turns": 2, "total_cost_usd": 0.01, '
            '"stop_reason": "end_turn", "usage": {}, "modelUsage": {}}\n'
        )

        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        executor._save_metadata(run_dir, skill, user_input="test query")

        metadata = json.loads((run_dir / "metadata.json").read_text())
        assert metadata["user_input"] == "test query"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd launcher
uv run pytest tests/test_executor.py::TestSaveMetadata::test_user_input_is_recorded_in_metadata -v
```

Expected: FAIL — `_save_metadata() got an unexpected keyword argument 'user_input'`

- [ ] **Step 3: Update `_save_metadata` signature and body**

In `launcher/zipsa/core/executor.py`, change the `_save_metadata` signature:

```python
def _save_metadata(self, run_dir: Path, skill: Skill, cost_exceeded: bool = False, limits=None, user_input: str = "") -> None:
```

Add `"user_input": user_input` to **both** metadata dicts (the `if not result_event` branch and the `else` branch):

```python
        if not result_event:
            metadata = {
                "run_id": run_dir.name,
                "skill_name": skill.name,
                "skill_version": skill.manifest.metadata.version,
                "timestamp": datetime.now().isoformat(),
                "user_input": user_input,
                "is_error": True,
                "error": "No result event found - execution may have failed"
            }
        else:
            metadata = {
                "run_id": run_dir.name,
                "skill_name": skill.name,
                "skill_version": skill.manifest.metadata.version,
                "timestamp": datetime.now().isoformat(),
                "user_input": user_input,
                "duration_ms": result_event.get("duration_ms"),
                "duration_api_ms": result_event.get("duration_api_ms"),
                "num_turns": result_event.get("num_turns"),
                "total_cost_usd": result_event.get("total_cost_usd"),
                "is_error": result_event.get("is_error", False),
                "stop_reason": result_event.get("stop_reason"),
                "terminal_reason": result_event.get("terminal_reason"),
                "usage": result_event.get("usage", {}),
                "model_usage": result_event.get("modelUsage", {})
            }
```

- [ ] **Step 4: Pass `user_input` from the `run()` call site**

Find the call to `_save_metadata` inside `run()` (around line 303):

```python
                    self._save_metadata(run_dir, skill, cost_exceeded, limits)
```

Change it to:

```python
                    self._save_metadata(run_dir, skill, cost_exceeded, limits, user_input=user_input)
```

- [ ] **Step 5: Run all tests**

```bash
uv run pytest -q
```

Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add launcher/zipsa/core/executor.py launcher/tests/test_executor.py
git commit -m "feat: record user_input in run metadata.json"
```

---

### Task 2: Path helpers for installed skills

**Files:**
- Modify: `launcher/zipsa/paths.py`
- Modify: `launcher/tests/test_paths.py`

- [ ] **Step 1: Write the failing tests**

Add to `launcher/tests/test_paths.py`:

```python
from zipsa.paths import (
    credentials_dir,
    global_env_file,
    installed_skill_dir,
    resolve_skill,
    skill_data_dir,
    skill_env_file,
    skill_runs_dir,
    skills_dir,
    zipsa_home,
    SkillNotInstalledError,
)


class TestInstalledSkillPaths:
    def test_skills_dir(self, tmp_path):
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            assert skills_dir() == tmp_path / "skills"

    def test_installed_skill_dir(self, tmp_path):
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            assert installed_skill_dir("daily-progress") == tmp_path / "skills" / "daily-progress"

    def test_resolve_skill_returns_path_when_installed(self, tmp_path):
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            skill_path = tmp_path / "skills" / "daily-progress"
            skill_path.mkdir(parents=True)
            assert resolve_skill("daily-progress") == skill_path

    def test_resolve_skill_raises_when_not_installed(self, tmp_path):
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            with pytest.raises(SkillNotInstalledError, match="daily-progress"):
                resolve_skill("daily-progress")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_paths.py::TestInstalledSkillPaths -v
```

Expected: FAIL — `ImportError: cannot import name 'skills_dir'`

- [ ] **Step 3: Add helpers to `launcher/zipsa/paths.py`**

Append to the existing file:

```python


class SkillNotInstalledError(Exception):
    pass


def skills_dir() -> Path:
    return zipsa_home() / "skills"


def installed_skill_dir(name: str) -> Path:
    return skills_dir() / name


def resolve_skill(name: str) -> Path:
    path = installed_skill_dir(name)
    if not path.exists():
        raise SkillNotInstalledError(
            f"Skill '{name}' not found. Try: zipsa install <source>"
        )
    return path
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_paths.py -v
```

Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/paths.py launcher/tests/test_paths.py
git commit -m "feat: add skills_dir, installed_skill_dir, resolve_skill to paths"
```

---

### Task 3: GitHub source parser

**Files:**
- Create: `launcher/zipsa/installer.py`
- Create: `launcher/tests/test_installer.py`

- [ ] **Step 1: Write the failing tests**

Create `launcher/tests/test_installer.py`:

```python
"""Tests for zipsa.installer — source parsing, download, local install."""

import pytest
from zipsa.installer import GitHubSource, parse_github_source


class TestParseGithubSource:
    def test_user_repo(self):
        s = parse_github_source("westbrookai/zipsa")
        assert s.user == "westbrookai"
        assert s.repo == "zipsa"
        assert s.subpath == ""
        assert s.ref == "HEAD"

    def test_user_repo_subpath(self):
        s = parse_github_source("westbrookai/zipsa/skills/daily-progress")
        assert s.user == "westbrookai"
        assert s.repo == "zipsa"
        assert s.subpath == "skills/daily-progress"
        assert s.ref == "HEAD"

    def test_with_ref(self):
        s = parse_github_source("westbrookai/zipsa@v0.1.0")
        assert s.ref == "v0.1.0"
        assert s.subpath == ""

    def test_subpath_with_ref(self):
        s = parse_github_source("westbrookai/zipsa/skills/daily-progress@main")
        assert s.subpath == "skills/daily-progress"
        assert s.ref == "main"

    def test_explicit_github_scheme(self):
        s = parse_github_source("github:westbrookai/zipsa/skills/daily-progress")
        assert s.user == "westbrookai"
        assert s.repo == "zipsa"
        assert s.subpath == "skills/daily-progress"

    def test_https_github_url(self):
        s = parse_github_source("https://github.com/westbrookai/zipsa")
        assert s.user == "westbrookai"
        assert s.repo == "zipsa"
        assert s.subpath == ""

    def test_https_github_url_with_tree(self):
        s = parse_github_source("https://github.com/westbrookai/zipsa/tree/main/skills/daily-progress")
        assert s.user == "westbrookai"
        assert s.repo == "zipsa"
        assert s.ref == "main"
        assert s.subpath == "skills/daily-progress"

    def test_invalid_source_raises(self):
        with pytest.raises(ValueError, match="Invalid"):
            parse_github_source("notavalidformat")

    def test_only_one_part_raises(self):
        with pytest.raises(ValueError, match="Invalid"):
            parse_github_source("westbrookai")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_installer.py::TestParseGithubSource -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'zipsa.installer'`

- [ ] **Step 3: Create `launcher/zipsa/installer.py` with parser**

```python
"""Skill installer — source parsing, GitHub download, local copy/link."""

import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class GitHubSource:
    user: str
    repo: str
    subpath: str  # "" for repo root
    ref: str      # "HEAD" if unspecified


def parse_github_source(source: str) -> GitHubSource:
    """Parse a GitHub source string into (user, repo, subpath, ref)."""
    # Strip explicit scheme
    if source.startswith("github:"):
        source = source[7:]
    elif source.startswith("https://github.com/"):
        path = source[len("https://github.com/"):]
        # Handle /tree/{ref}/{subpath} form
        m = re.match(r"([^/]+)/([^/]+)/(?:tree|blob)/([^/]+)(?:/(.+))?$", path)
        if m:
            return GitHubSource(
                user=m.group(1),
                repo=m.group(2),
                ref=m.group(3),
                subpath=m.group(4) or "",
            )
        # Plain https://github.com/user/repo[/...]
        source = path

    # Extract @ref suffix
    ref = "HEAD"
    if "@" in source:
        source, ref = source.rsplit("@", 1)

    parts = source.split("/")
    if len(parts) < 2:
        raise ValueError(
            f"Invalid GitHub source: '{source}'. "
            "Expected format: user/repo[/subpath][@ref]"
        )

    return GitHubSource(
        user=parts[0],
        repo=parts[1],
        subpath="/".join(parts[2:]) if len(parts) > 2 else "",
        ref=ref,
    )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_installer.py::TestParseGithubSource -v
```

Expected: all 9 passing.

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/installer.py launcher/tests/test_installer.py
git commit -m "feat: add GitHub source parser to installer"
```

---

### Task 4: GitHub skill downloader

**Files:**
- Modify: `launcher/zipsa/installer.py`
- Modify: `launcher/tests/test_installer.py`

The downloader needs `urllib.request.urlopen` mocked in tests. The mock returns a fake tarball containing a minimal skill.

- [ ] **Step 1: Write the failing tests**

Add to `launcher/tests/test_installer.py`:

```python
import io
import json
import os
import tarfile
from unittest.mock import patch, MagicMock
import yaml
from zipsa.installer import GitHubSource, install_from_github


def _make_fake_tarball(subpath: str, skill_name: str = "test-skill", version: str = "0.1.0") -> bytes:
    """Build an in-memory tarball mimicking a GitHub API tarball."""
    buf = io.BytesIO()
    manifest_content = yaml.dump({
        "apiVersion": "zipsa.dev/v1alpha1",
        "kind": "Skill",
        "metadata": {"name": skill_name, "version": version},
        "spec": {
            "purpose": "Test skill",
            "instructions": "./SKILL.md",
            "mcp": [],
            "tools": {"builtin": [], "mcp": []},
        },
    }).encode()
    skill_md_content = b"# Test skill instructions"

    root = f"westbrookai-zipsa-abc1234"
    prefix = f"{root}/{subpath}/" if subpath else f"{root}/"

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in [
            (f"{prefix}manifest.yaml", manifest_content),
            (f"{prefix}SKILL.md", skill_md_content),
        ]:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class TestInstallFromGithub:
    def _mock_response(self, tarball: bytes, sha: str = "abc1234def5678"):
        resp = MagicMock()
        resp.read.return_value = tarball
        resp.headers = {"X-GitHub-Resolved-Sha": sha}
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def _mock_commit_response(self, sha: str = "abc1234def5678"):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"sha": sha}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_install_from_github_creates_skill_dir(self, tmp_path):
        """install_from_github downloads and installs skill to skills_dir."""
        tarball = _make_fake_tarball("skills/test-skill")
        sha = "abc1234def5678abcdef"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            with patch("urllib.request.urlopen") as mock_open:
                mock_open.side_effect = [
                    self._mock_commit_response(sha),
                    self._mock_response(tarball),
                ]
                name = install_from_github("westbrookai/zipsa/skills/test-skill")

        assert name == "test-skill"
        skill_dir = tmp_path / "skills" / "test-skill"
        assert skill_dir.exists()
        assert (skill_dir / "manifest.yaml").exists()
        assert (skill_dir / "SKILL.md").exists()

    def test_install_from_github_writes_install_json(self, tmp_path):
        """install_from_github writes _install.json with commit_sha and version."""
        tarball = _make_fake_tarball("skills/test-skill")
        sha = "abc1234def5678abcdef"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            with patch("urllib.request.urlopen") as mock_open:
                mock_open.side_effect = [
                    self._mock_commit_response(sha),
                    self._mock_response(tarball),
                ]
                install_from_github("westbrookai/zipsa/skills/test-skill")

        install_json = tmp_path / "skills" / "test-skill" / "_install.json"
        assert install_json.exists()
        meta = json.loads(install_json.read_text())
        assert meta["commit_sha"] == sha
        assert meta["version"] == "0.1.0"
        assert meta["type"] == "github"
        assert "installed_at" in meta

    def test_install_from_github_fails_if_already_installed(self, tmp_path):
        """install_from_github raises FileExistsError if skill already installed."""
        tarball = _make_fake_tarball("skills/test-skill")
        sha = "abc1234def5678abcdef"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            with patch("urllib.request.urlopen") as mock_open:
                mock_open.side_effect = [
                    self._mock_commit_response(sha),
                    self._mock_response(tarball),
                ]
                install_from_github("westbrookai/zipsa/skills/test-skill")

            with pytest.raises(FileExistsError, match="already installed"):
                with patch("urllib.request.urlopen") as mock_open:
                    mock_open.side_effect = [
                        self._mock_commit_response(sha),
                        self._mock_response(tarball),
                    ]
                    install_from_github("westbrookai/zipsa/skills/test-skill")

    def test_install_from_github_force_overwrites(self, tmp_path):
        """install_from_github with force=True replaces existing installation."""
        tarball = _make_fake_tarball("skills/test-skill")
        sha = "abc1234def5678abcdef"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            with patch("urllib.request.urlopen") as mock_open:
                mock_open.side_effect = [
                    self._mock_commit_response(sha),
                    self._mock_response(tarball),
                    self._mock_commit_response(sha),
                    self._mock_response(tarball),
                ]
                install_from_github("westbrookai/zipsa/skills/test-skill")
                name = install_from_github("westbrookai/zipsa/skills/test-skill", force=True)

        assert name == "test-skill"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_installer.py::TestInstallFromGithub -v
```

Expected: FAIL — `ImportError: cannot import name 'install_from_github'`

- [ ] **Step 3: Add downloader and `install_from_github` to `launcher/zipsa/installer.py`**

Append after the parser:

```python


def _github_headers() -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_commit_sha(source: GitHubSource) -> str:
    """Resolve ref to full commit SHA via GitHub commits API."""
    url = (
        f"https://api.github.com/repos/{source.user}/{source.repo}"
        f"/commits/{source.ref}"
    )
    req = urllib.request.Request(url, headers=_github_headers())
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise FileNotFoundError(
                f"Repository not found: {source.user}/{source.repo}"
            )
        raise RuntimeError(f"Failed to fetch commit info: {e}")


def _download_tarball(source: GitHubSource, dest: Path) -> None:
    """Download GitHub tarball and extract skill files into dest."""
    url = (
        f"https://api.github.com/repos/{source.user}/{source.repo}"
        f"/tarball/{source.ref}"
    )
    req = urllib.request.Request(url, headers=_github_headers())
    try:
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise FileNotFoundError(
                f"Repository or path not found: {source.user}/{source.repo}"
            )
        raise RuntimeError(f"Failed to download: {e}")

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = tar.getmembers()
        if not members:
            raise RuntimeError("Downloaded tarball is empty")

        # Root dir is "user-repo-{sha}/"
        root_prefix = members[0].name.split("/")[0] + "/"

        for member in members:
            if not member.name.startswith(root_prefix):
                continue
            member_rel = member.name[len(root_prefix):]

            if source.subpath:
                if not member_rel.startswith(source.subpath + "/"):
                    continue
                file_rel = member_rel[len(source.subpath) + 1:]
            else:
                file_rel = member_rel

            if not file_rel:
                continue

            target = dest / file_rel
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                f = tar.extractfile(member)
                if f:
                    target.write_bytes(f.read())


def _write_install_json(
    dest: Path,
    source_str: str,
    ref: str,
    version: str,
    install_type: str,
    commit_sha: str = "",
) -> None:
    meta: dict = {
        "source": source_str,
        "ref": ref,
        "version": version,
        "type": install_type,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    if commit_sha:
        meta["commit_sha"] = commit_sha
    (dest / "_install.json").write_text(json.dumps(meta, indent=2))


def install_from_github(source_str: str, force: bool = False) -> str:
    """Download and install a skill from GitHub. Returns installed skill name."""
    from .paths import skills_dir
    from .core.skill import Skill
    from pydantic import ValidationError

    source = parse_github_source(source_str)
    canonical = f"github:{source.user}/{source.repo}"
    if source.subpath:
        canonical += f"/{source.subpath}"
    canonical += f"@{source.ref}"

    commit_sha = _get_commit_sha(source)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _download_tarball(source, tmp_path)

        try:
            skill = Skill.load(tmp_path)
        except FileNotFoundError:
            subpath_hint = source.subpath or "(repo root)"
            raise FileNotFoundError(
                f"No manifest.yaml found at {source.user}/{source.repo}/{subpath_hint}"
            )
        except ValidationError as e:
            raise ValueError(f"Install failed: invalid manifest — {e}")

        name = skill.name
        version = skill.manifest.metadata.version
        dest = skills_dir() / name

        if dest.exists() and not force:
            raise FileExistsError(
                f"Skill '{name}' is already installed. Use --force to overwrite."
            )
        if dest.exists():
            shutil.rmtree(dest)

        shutil.copytree(tmp_path, dest)

    _write_install_json(dest, canonical, source.ref, version, "github", commit_sha)
    return name
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_installer.py -v
```

Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/installer.py launcher/tests/test_installer.py
git commit -m "feat: add GitHub tarball downloader and install_from_github"
```

---

### Task 5: Local skill installer (`--path` and `--link`)

**Files:**
- Modify: `launcher/zipsa/installer.py`
- Modify: `launcher/tests/test_installer.py`

- [ ] **Step 1: Write the failing tests**

Add to `launcher/tests/test_installer.py`:

```python
import yaml as _yaml
from zipsa.installer import install_local


def _make_local_skill(base: Path, name: str = "my-skill", version: str = "0.1.0") -> Path:
    skill_dir = base / name
    skill_dir.mkdir()
    (skill_dir / "manifest.yaml").write_text(_yaml.dump({
        "apiVersion": "zipsa.dev/v1alpha1",
        "kind": "Skill",
        "metadata": {"name": name, "version": version},
        "spec": {
            "purpose": "Local test skill",
            "instructions": "./SKILL.md",
            "mcp": [],
            "tools": {"builtin": [], "mcp": []},
        },
    }))
    (skill_dir / "SKILL.md").write_text("# Instructions")
    return skill_dir


class TestInstallLocal:
    def test_install_path_copies_files(self, tmp_path):
        """--path installs a copy of the local skill."""
        src = _make_local_skill(tmp_path / "src")
        dest_home = tmp_path / "home"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(dest_home)}):
            name = install_local(str(src), link=False)

        assert name == "my-skill"
        installed = dest_home / "skills" / "my-skill"
        assert installed.exists()
        assert not installed.is_symlink()
        assert (installed / "manifest.yaml").exists()

    def test_install_link_creates_symlink(self, tmp_path):
        """--link installs a symlink to the local skill."""
        src = _make_local_skill(tmp_path / "src")
        dest_home = tmp_path / "home"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(dest_home)}):
            name = install_local(str(src), link=True)

        assert name == "my-skill"
        installed = dest_home / "skills" / "my-skill"
        assert installed.is_symlink()
        assert installed.resolve() == src.resolve()

    def test_install_local_writes_install_json(self, tmp_path):
        """install_local writes _install.json with type=copy or link."""
        src = _make_local_skill(tmp_path / "src")
        dest_home = tmp_path / "home"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(dest_home)}):
            install_local(str(src), link=False)

        meta = json.loads(
            (dest_home / "skills" / "my-skill" / "_install.json").read_text()
        )
        assert meta["type"] == "copy"
        assert meta["version"] == "0.1.0"
        assert "commit_sha" not in meta

    def test_install_link_writes_install_json_with_link_type(self, tmp_path):
        src = _make_local_skill(tmp_path / "src")
        dest_home = tmp_path / "home"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(dest_home)}):
            install_local(str(src), link=True)

        meta = json.loads(
            (dest_home / "skills" / "my-skill" / "_install.json").read_text()
        )
        assert meta["type"] == "link"

    def test_install_local_raises_if_already_installed(self, tmp_path):
        src = _make_local_skill(tmp_path / "src")
        dest_home = tmp_path / "home"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(dest_home)}):
            install_local(str(src), link=False)
            with pytest.raises(FileExistsError, match="already installed"):
                install_local(str(src), link=False)

    def test_install_local_force_replaces(self, tmp_path):
        src = _make_local_skill(tmp_path / "src")
        dest_home = tmp_path / "home"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(dest_home)}):
            install_local(str(src), link=False)
            name = install_local(str(src), link=False, force=True)

        assert name == "my-skill"

    def test_install_local_path_not_found_raises(self, tmp_path):
        dest_home = tmp_path / "home"
        with patch.dict(os.environ, {"ZIPSA_HOME": str(dest_home)}):
            with pytest.raises(FileNotFoundError):
                install_local(str(tmp_path / "nonexistent"), link=False)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_installer.py::TestInstallLocal -v
```

Expected: FAIL — `ImportError: cannot import name 'install_local'`

- [ ] **Step 3: Add `install_local` to `launcher/zipsa/installer.py`**

Append to the file:

```python


def install_local(local_path: str, link: bool = False, force: bool = False) -> str:
    """Install a local skill by copy (default) or symlink. Returns skill name."""
    from .paths import skills_dir
    from .core.skill import Skill
    from pydantic import ValidationError

    src = Path(local_path).resolve()
    if not src.exists():
        raise FileNotFoundError(f"Path not found: {src}")

    try:
        skill = Skill.load(src)
    except ValidationError as e:
        raise ValueError(f"Install failed: invalid manifest — {e}")

    name = skill.name
    version = skill.manifest.metadata.version
    dest = skills_dir() / name

    if dest.exists() and not force:
        raise FileExistsError(
            f"Skill '{name}' is already installed. Use --force to overwrite."
        )
    if dest.exists():
        if dest.is_symlink():
            dest.unlink()
        else:
            shutil.rmtree(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)

    if link:
        dest.symlink_to(src)
        install_type = "link"
    else:
        shutil.copytree(src, dest)
        install_type = "copy"

    _write_install_json(dest, str(src), "local", version, install_type)
    return name
```

- [ ] **Step 4: Run all installer tests**

```bash
uv run pytest tests/test_installer.py -v
```

Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/installer.py launcher/tests/test_installer.py
git commit -m "feat: add install_local for --path and --link installs"
```

---

### Task 6: `zipsa install` CLI command

**Files:**
- Modify: `launcher/zipsa/cli.py`
- Modify: `launcher/tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `launcher/tests/test_cli.py`:

```python
from zipsa.cli import app, _find_run_dir


class TestInstallCommand:
    @patch("zipsa.cli.install_from_github")
    def test_install_github_source(self, mock_install):
        """install command with GitHub source calls install_from_github."""
        mock_install.return_value = "daily-progress"
        result = runner.invoke(app, ["install", "westbrookai/zipsa/skills/daily-progress"])
        assert result.exit_code == 0
        mock_install.assert_called_once_with("westbrookai/zipsa/skills/daily-progress", force=False)
        assert "daily-progress" in result.stdout

    @patch("zipsa.cli.install_from_github")
    def test_install_with_force_flag(self, mock_install):
        """install --force passes force=True."""
        mock_install.return_value = "daily-progress"
        result = runner.invoke(app, ["install", "--force", "westbrookai/zipsa/skills/daily-progress"])
        assert result.exit_code == 0
        mock_install.assert_called_once_with("westbrookai/zipsa/skills/daily-progress", force=True)

    @patch("zipsa.cli.install_local")
    def test_install_with_path_flag(self, mock_install):
        """install --path calls install_local with link=False."""
        mock_install.return_value = "my-skill"
        result = runner.invoke(app, ["install", "--path", "./my-skill"])
        assert result.exit_code == 0
        mock_install.assert_called_once_with("./my-skill", link=False, force=False)

    @patch("zipsa.cli.install_local")
    def test_install_with_link_flag(self, mock_install):
        """install --link calls install_local with link=True."""
        mock_install.return_value = "my-skill"
        result = runner.invoke(app, ["install", "--link", "./my-skill"])
        assert result.exit_code == 0
        mock_install.assert_called_once_with("./my-skill", link=True, force=False)

    @patch("zipsa.cli.install_from_github")
    def test_install_file_exists_error_exits_nonzero(self, mock_install):
        """install exits 1 when skill already installed."""
        mock_install.side_effect = FileExistsError("already installed")
        result = runner.invoke(app, ["install", "westbrookai/zipsa/skills/daily-progress"])
        assert result.exit_code == 1
        assert "already installed" in result.stderr

    @patch("zipsa.cli.install_from_github")
    def test_install_file_not_found_exits_nonzero(self, mock_install):
        """install exits 1 when repo not found."""
        mock_install.side_effect = FileNotFoundError("not found")
        result = runner.invoke(app, ["install", "westbrookai/zipsa/skills/daily-progress"])
        assert result.exit_code == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_cli.py::TestInstallCommand -v
```

Expected: FAIL — `AttributeError: module 'zipsa.cli' has no attribute 'install_from_github'`

- [ ] **Step 3: Add `install` command to `launcher/zipsa/cli.py`**

Add import at the top (after existing imports):

```python
from .installer import install_from_github, install_local
```

Add the command before the `connect` command:

```python
@app.command()
def install(
    source: Annotated[
        Optional[str],
        typer.Argument(help="GitHub source: user/repo[/subpath][@ref]"),
    ] = None,
    path: Annotated[
        Optional[str],
        typer.Option("--path", help="Install local skill by copy"),
    ] = None,
    link: Annotated[
        Optional[str],
        typer.Option("--link", help="Install local skill by symlink"),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite if already installed"),
    ] = False,
):
    """Install a skill from GitHub or a local directory."""
    try:
        if path:
            name = install_local(path, link=False, force=force)
            typer.echo(f"✓ Installed {name}")
        elif link:
            name = install_local(link, link=True, force=force)
            typer.echo(f"✓ Installed {name} (linked)")
        elif source:
            name = install_from_github(source, force=force)
            typer.echo(f"✓ Installed {name}")
        else:
            typer.echo("Error: provide a source, --path, or --link", err=True)
            raise typer.Exit(1)
    except FileExistsError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_cli.py::TestInstallCommand -v
```

Expected: all passing.

- [ ] **Step 5: Run full suite**

```bash
uv run pytest -q
```

Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add launcher/zipsa/cli.py launcher/tests/test_cli.py
git commit -m "feat: add zipsa install command"
```

---

### Task 7: `zipsa uninstall` command

**Files:**
- Modify: `launcher/zipsa/cli.py`
- Modify: `launcher/tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `launcher/tests/test_cli.py`:

```python
class TestUninstallCommand:
    def test_uninstall_removes_skill_dir(self, tmp_path):
        """uninstall removes ~/.zipsa/skills/<name>/."""
        skill_dir = tmp_path / "skills" / "daily-progress"
        skill_dir.mkdir(parents=True)
        (skill_dir / "_install.json").write_text('{"type": "github"}')

        with patch("zipsa.cli.installed_skill_dir", return_value=skill_dir):
            result = runner.invoke(app, ["uninstall", "daily-progress"])

        assert result.exit_code == 0
        assert not skill_dir.exists()
        assert "daily-progress" in result.stdout

    def test_uninstall_removes_symlink_only_for_linked_skills(self, tmp_path):
        """uninstall for linked skill removes symlink, not original."""
        original = tmp_path / "original"
        original.mkdir()
        link_path = tmp_path / "skills" / "my-skill"
        link_path.parent.mkdir(parents=True)
        link_path.symlink_to(original)

        with patch("zipsa.cli.installed_skill_dir", return_value=link_path):
            result = runner.invoke(app, ["uninstall", "my-skill"])

        assert result.exit_code == 0
        assert not link_path.exists()
        assert original.exists()

    def test_uninstall_not_installed_exits_nonzero(self, tmp_path):
        """uninstall exits 1 when skill is not installed."""
        non_existent = tmp_path / "skills" / "ghost"
        with patch("zipsa.cli.installed_skill_dir", return_value=non_existent):
            result = runner.invoke(app, ["uninstall", "ghost"])
        assert result.exit_code == 1
        assert "not installed" in result.stderr
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_cli.py::TestUninstallCommand -v
```

Expected: FAIL — `no command 'uninstall'`

- [ ] **Step 3: Add import and `uninstall` command to `launcher/zipsa/cli.py`**

Add to imports:

```python
from .paths import skill_runs_dir, installed_skill_dir
```

Add the command:

```python
@app.command()
def uninstall(
    name: Annotated[
        str,
        typer.Argument(help="Installed skill name"),
    ],
):
    """Uninstall a skill (preserves run history)."""
    dest = installed_skill_dir(name)
    if not dest.exists():
        typer.echo(f"Error: Skill '{name}' is not installed.", err=True)
        raise typer.Exit(1)

    if dest.is_symlink():
        dest.unlink()
    else:
        import shutil
        shutil.rmtree(dest)

    typer.echo(f"✓ Uninstalled {name}")
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_cli.py::TestUninstallCommand -v
```

Expected: all passing.

- [ ] **Step 5: Run full suite**

```bash
uv run pytest -q
```

Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add launcher/zipsa/cli.py launcher/tests/test_cli.py
git commit -m "feat: add zipsa uninstall command"
```

---

### Task 8: Name-based skill resolution (breaking change)

Replace `skill_dir: str` with `name: str` in `run`, `validate`, `view`, `connect`. All use `resolve_skill(name)` internally.

**Files:**
- Modify: `launcher/zipsa/cli.py`
- Modify: `launcher/tests/test_cli.py`

- [ ] **Step 1: Add `SkillNotInstalledError` not-found tests for each command**

Add to `launcher/tests/test_cli.py` (new tests, don't remove existing ones):

```python
class TestNameResolution:
    """Verify all commands reject unknown skill names with exit code 1."""

    @patch("zipsa.cli.resolve_skill")
    def test_run_exits_when_not_installed(self, mock_resolve):
        from zipsa.paths import SkillNotInstalledError
        mock_resolve.side_effect = SkillNotInstalledError("Skill 'ghost' not found.")
        result = runner.invoke(app, ["run", "ghost", "hello"])
        assert result.exit_code == 1
        assert "ghost" in result.stderr

    @patch("zipsa.cli.resolve_skill")
    def test_validate_exits_when_not_installed(self, mock_resolve):
        from zipsa.paths import SkillNotInstalledError
        mock_resolve.side_effect = SkillNotInstalledError("Skill 'ghost' not found.")
        result = runner.invoke(app, ["validate", "ghost"])
        assert result.exit_code == 1

    @patch("zipsa.cli.resolve_skill")
    def test_view_exits_when_not_installed(self, mock_resolve):
        from zipsa.paths import SkillNotInstalledError
        mock_resolve.side_effect = SkillNotInstalledError("Skill 'ghost' not found.")
        result = runner.invoke(app, ["view", "ghost"])
        assert result.exit_code == 1

    @patch("zipsa.cli.resolve_skill")
    def test_connect_exits_when_not_installed(self, mock_resolve):
        from zipsa.paths import SkillNotInstalledError
        mock_resolve.side_effect = SkillNotInstalledError("Skill 'ghost' not found.")
        result = runner.invoke(app, ["connect", "ghost"])
        assert result.exit_code == 1
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
uv run pytest tests/test_cli.py::TestNameResolution -v
```

Expected: FAIL — `resolve_skill` not imported in cli.py yet.

- [ ] **Step 3: Update `launcher/zipsa/cli.py` imports**

Change the existing paths import line from:
```python
from .paths import skill_runs_dir
```
to:
```python
from .paths import skill_runs_dir, installed_skill_dir, resolve_skill, SkillNotInstalledError
```

- [ ] **Step 4: Replace the `run` command in `launcher/zipsa/cli.py`**

Replace the entire `run` function (from `@app.command()` through its closing `except` block) with:

```python
@app.command()
def run(
    name: Annotated[
        str,
        typer.Argument(help="Installed skill name"),
    ],
    user_input: Annotated[
        Optional[str],
        typer.Argument(help="User input/query for the skill"),
    ] = None,
    runtime: Annotated[
        str,
        typer.Option("--runtime", "-r", help="Runtime to use (claude, codex, gemini)"),
    ] = "claude",
    image: Annotated[
        str,
        typer.Option("--image", "-i", help="Docker image to use"),
    ] = "ghcr.io/westbrookai/zipsa-runtime:latest",
    env: Annotated[
        Optional[list[str]],
        typer.Option("--env", "-e", help="Environment variables (KEY=value)"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print command without executing"),
    ] = False,
    shell: Annotated[
        bool,
        typer.Option("--shell", help="Start interactive bash shell instead of running skill"),
    ] = False,
    mcp_debug: Annotated[
        bool,
        typer.Option("--mcp-debug", help="Write MCP debug logs to runs/<timestamp>/mcp-debug.log"),
    ] = False,
    docker_opt: Annotated[
        Optional[list[str]],
        typer.Option("--docker-opt", help="Extra docker run options (e.g. --docker-opt='-p 56535:56535')"),
    ] = None,
    output_mode: Annotated[
        OutputMode,
        typer.Option("--output-mode", help="Output format: pretty (default), answer, json"),
    ] = OutputMode.pretty,
):
    """Execute a skill with the specified runtime."""
    try:
        skill = Skill.load(resolve_skill(name))
        typer.echo(f"Loaded skill: {skill.name}", err=True)

        if not shell and not user_input:
            typer.echo("Error: user_input is required unless --shell is specified", err=True)
            raise typer.Exit(1)

        env_dict = {}
        if env:
            for pair in env:
                if "=" not in pair:
                    typer.echo(f"Error: Invalid env format '{pair}' (use KEY=value)", err=True)
                    raise typer.Exit(1)
                key, value = pair.split("=", 1)
                env_dict[key] = value

        executor = DockerExecutor(runtime=runtime, image=image)
        output = executor.run(
            skill, user_input or "", env=env_dict,
            dry_run=dry_run, shell=shell, mcp_debug=mcp_debug,
            extra_docker_opts=docker_opt,
        )

        if output is None:
            return

        render(output, output_mode)

    except SkillNotInstalledError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except ValidationError as e:
        typer.echo(f"Error: Invalid manifest - {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
```

- [ ] **Step 5: Replace the `validate` command**

```python
@app.command()
def validate(
    name: Annotated[
        str,
        typer.Argument(help="Installed skill name"),
    ],
):
    """Validate a skill manifest."""
    try:
        skill = Skill.load(resolve_skill(name))
        typer.echo(f"✓ Skill '{skill.name}' is valid")
        typer.echo(f"  Version: {skill.manifest.metadata.version}")
        typer.echo(f"  Purpose: {skill.manifest.spec.purpose}")
        typer.echo(f"  MCP Servers: {len(skill.manifest.spec.mcp)}")
        typer.echo(f"  Tools: {len(skill.manifest.spec.tools.builtin)}")
    except SkillNotInstalledError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except ValidationError as e:
        typer.echo("✗ Validation failed:", err=True)
        for error in e.errors():
            loc = " -> ".join(str(l) for l in error["loc"])
            typer.echo(f"  {loc}: {error['msg']}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
```

- [ ] **Step 6: Replace the `view` command**

```python
@app.command()
def view(
    name: Annotated[
        str,
        typer.Argument(help="Installed skill name"),
    ],
    run_id: Annotated[
        Optional[str],
        typer.Argument(help="Run ID prefix to replay (default: latest run)"),
    ] = None,
    output_mode: Annotated[
        OutputMode,
        typer.Option("--output-mode", help="Output format: pretty (default), answer, json"),
    ] = OutputMode.pretty,
):
    """Replay the output of a past skill run."""
    try:
        skill = Skill.load(resolve_skill(name))
        runs_dir = skill_runs_dir(skill.name, skill.manifest.metadata.version)
        run_dir = _find_run_dir(runs_dir, run_id)
    except SkillNotInstalledError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    output_jsonl = run_dir / "output.jsonl"
    if not output_jsonl.exists():
        typer.echo(f"Run '{run_dir.name}' has no output.jsonl", err=True)
        raise typer.Exit(1)

    def events():
        with open(output_jsonl) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        typer.echo(f"Warning: skipped malformed line in {output_jsonl}", err=True)

    render(events(), output_mode)
```

- [ ] **Step 7: Replace the `connect` command**

```python
@app.command()
def connect(
    name: Annotated[
        str,
        typer.Argument(help="Installed skill name"),
    ],
    server_name: Annotated[
        Optional[str],
        typer.Argument(help="MCP server name to authorize (default: all OAuth servers)"),
    ] = None,
):
    """Pre-authorize OAuth credentials for a skill's HTTP MCP servers."""
    try:
        skill = Skill.load(resolve_skill(name))

        oauth_servers = [
            s for s in skill.manifest.spec.mcp
            if s.type == "http" and getattr(s, "auth", None) and s.auth.type == "oauth2"
        ]

        if server_name:
            oauth_servers = [s for s in oauth_servers if s.name == server_name]
            if not oauth_servers:
                typer.echo(
                    f"Error: Server '{server_name}' not found or is not an OAuth2 server",
                    err=True,
                )
                raise typer.Exit(1)

        if not oauth_servers:
            typer.echo("No OAuth servers found in skill")
            return

        manager = OAuthManager()
        for server in oauth_servers:
            typer.echo(f"Authorizing {server.name}...")
            manager.ensure_credentials(server.name, server.url)
            typer.echo(f"✓ {server.name}: authorized")

    except SkillNotInstalledError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
```

- [ ] **Step 8: Update existing CLI tests that pass paths to pass names**

In `launcher/tests/test_cli.py`, all existing tests that currently invoke commands with a path string (e.g. `"skills/daily-progress"`) must be updated to pass a skill name and mock `zipsa.cli.resolve_skill`.

For every test in `TestRunCommand`, `TestValidateCommand`, `TestViewCommand`, `TestConnectCommand` that calls `runner.invoke(app, ["run", "skills/daily-progress", ...])`:

1. Add `@patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill/path"))` decorator (before existing `@patch("zipsa.cli.Skill")`)
2. Add `mock_resolve` as the first positional mock argument after `self`
3. Change `"skills/daily-progress"` → `"daily-progress"` in the `runner.invoke` call

Example — before:
```python
@patch("zipsa.cli.DockerExecutor")
@patch("zipsa.cli.Skill")
def test_run_basic(self, mock_skill_cls, mock_executor_cls):
    mock_skill_cls.load.return_value = mock_skill
    result = runner.invoke(app, ["run", "skills/daily-progress", "hello"])
```

After:
```python
@patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
@patch("zipsa.cli.DockerExecutor")
@patch("zipsa.cli.Skill")
def test_run_basic(self, mock_skill_cls, mock_executor_cls, mock_resolve):
    mock_skill_cls.load.return_value = mock_skill
    result = runner.invoke(app, ["run", "daily-progress", "hello"])
```

Apply this pattern to all affected tests.

- [ ] **Step 9: Run all tests**

```bash
uv run pytest -q
```

Expected: all passing.

- [ ] **Step 10: Commit**

```bash
git add launcher/zipsa/cli.py launcher/tests/test_cli.py
git commit -m "feat: switch all commands to name-based skill resolution"
```

---

### Task 9: `zipsa list` (installed + stats) and `zipsa discover` (renamed)

**Files:**
- Modify: `launcher/zipsa/cli.py`
- Modify: `launcher/tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `launcher/tests/test_cli.py`:

```python
class TestListCommand:
    def test_list_shows_installed_skills(self, tmp_path):
        """list shows skills from ZIPSA_HOME/skills/."""
        import yaml
        skill_dir = tmp_path / "skills" / "daily-progress"
        skill_dir.mkdir(parents=True)
        (skill_dir / "manifest.yaml").write_text(yaml.dump({
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "daily-progress", "version": "0.1.0"},
            "spec": {"purpose": "Test", "instructions": "./SKILL.md",
                     "mcp": [], "tools": {"builtin": [], "mcp": []}},
        }))
        (skill_dir / "SKILL.md").write_text("# Test")
        (skill_dir / "_install.json").write_text(json.dumps({
            "source": "github:westbrookai/zipsa/skills/daily-progress",
            "ref": "main", "commit_sha": "abc123", "version": "0.1.0",
            "type": "github", "installed_at": "2026-05-11T00:00:00+00:00",
        }))

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "daily-progress" in result.stdout
        assert "0.1.0" in result.stdout

    def test_list_empty_when_no_skills_installed(self, tmp_path):
        """list reports no installed skills."""
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "No installed skills" in result.stdout

    def test_list_shows_linked_label_for_link_type(self, tmp_path):
        """list shows (linked) for link-type installs."""
        import yaml
        skill_dir = tmp_path / "skills" / "hello-world"
        skill_dir.mkdir(parents=True)
        (skill_dir / "manifest.yaml").write_text(yaml.dump({
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "hello-world", "version": "0.1.0"},
            "spec": {"purpose": "Hi", "instructions": "./SKILL.md",
                     "mcp": [], "tools": {"builtin": [], "mcp": []}},
        }))
        (skill_dir / "SKILL.md").write_text("# Hi")
        (skill_dir / "_install.json").write_text(json.dumps({
            "source": "/some/local/path", "ref": "local",
            "version": "0.1.0", "type": "link",
            "installed_at": "2026-05-11T00:00:00+00:00",
        }))

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "linked" in result.stdout.lower()


class TestDiscoverCommand:
    def test_discover_lists_skills_in_directory(self, tmp_path):
        """discover scans a directory for skill manifests."""
        import yaml
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "manifest.yaml").write_text(yaml.dump({
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "my-skill", "version": "0.1.0"},
            "spec": {"purpose": "Test", "instructions": "./SKILL.md",
                     "mcp": [], "tools": {"builtin": [], "mcp": []}},
        }))
        (skill_dir / "SKILL.md").write_text("# Test")

        result = runner.invoke(app, ["discover", str(tmp_path)])
        assert result.exit_code == 0
        assert "my-skill" in result.stdout

    def test_discover_no_skills_found(self, tmp_path):
        result = runner.invoke(app, ["discover", str(tmp_path)])
        assert result.exit_code == 0
        assert "No skills found" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_cli.py::TestListCommand tests/test_cli.py::TestDiscoverCommand -v
```

Expected: FAIL — no `list` / `discover` command matching new behavior.

- [ ] **Step 3: Replace `list_skills` with `discover` + new `list` in `launcher/zipsa/cli.py`**

Remove the existing `list_skills` command and replace with:

```python
@app.command(name="discover")
def discover(
    skills_dir: Annotated[
        str,
        typer.Argument(help="Directory containing skills"),
    ] = ".",
):
    """Scan a directory and list skills found (by manifest.yaml)."""
    try:
        skills_path = Path(skills_dir)
        if not skills_path.exists():
            typer.echo(f"Error: Directory '{skills_dir}' not found", err=True)
            raise typer.Exit(1)
        if not skills_path.is_dir():
            typer.echo(f"Error: '{skills_dir}' is not a directory", err=True)
            raise typer.Exit(1)

        found = []
        for item in skills_path.iterdir():
            if not item.is_dir():
                continue
            if not (item / "manifest.yaml").exists():
                continue
            try:
                skill = Skill.load(item)
                found.append({"name": skill.name, "version": skill.manifest.metadata.version,
                              "purpose": skill.manifest.spec.purpose, "path": item})
            except Exception:
                continue

        if not found:
            typer.echo("No skills found")
            return

        typer.echo(f"Found {len(found)} skill(s):\n")
        for s in found:
            typer.echo(f"  {s['name']} (v{s['version']})")
            typer.echo(f"    {s['purpose']}")
            typer.echo(f"    Path: {s['path']}")
            typer.echo()

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command(name="list")
def list_installed():
    """List installed skills with run statistics."""
    import json as _json
    from datetime import datetime, timezone, timedelta
    from .paths import skills_dir as _skills_dir, zipsa_home

    sd = _skills_dir()
    if not sd.exists():
        typer.echo("No installed skills.")
        return

    installed = []
    for item in sorted(sd.iterdir()):
        if not item.is_dir() and not item.is_symlink():
            continue
        manifest_path = (item / "manifest.yaml") if not item.is_symlink() else (item / "manifest.yaml")
        if not manifest_path.exists():
            continue
        try:
            skill = Skill.load(item)
        except Exception:
            continue

        install_json = item / "_install.json"
        install_meta = {}
        if install_json.exists():
            try:
                install_meta = _json.loads(install_json.read_text())
            except Exception:
                pass

        # Compute run stats
        total_runs = 0
        successful_runs = 0
        latest_ts: Optional[datetime] = None
        home = zipsa_home()
        for run_data_dir in home.iterdir():
            if not run_data_dir.is_dir():
                continue
            if not run_data_dir.name.startswith(f"{skill.name}@"):
                continue
            runs_dir = run_data_dir / "runs"
            if not runs_dir.exists():
                continue
            for run_dir in runs_dir.iterdir():
                if not run_dir.is_dir():
                    continue
                meta_file = run_dir / "metadata.json"
                if not meta_file.exists():
                    continue
                try:
                    meta = _json.loads(meta_file.read_text())
                except Exception:
                    continue
                total_runs += 1
                if not meta.get("is_error", True):
                    successful_runs += 1
                try:
                    ts = datetime.strptime(run_dir.name[:19], "%Y-%m-%d_%H%M%S").replace(tzinfo=timezone.utc)
                    if latest_ts is None or ts > latest_ts:
                        latest_ts = ts
                except ValueError:
                    pass

        installed.append({
            "skill": skill,
            "meta": install_meta,
            "total_runs": total_runs,
            "successful_runs": successful_runs,
            "latest_ts": latest_ts,
            "is_link": item.is_symlink(),
            "link_target": str(item.resolve()) if item.is_symlink() else None,
        })

    if not installed:
        typer.echo("No installed skills.")
        return

    typer.echo(f"Installed skills ({len(installed)}):\n")
    now = datetime.now(timezone.utc)

    for entry in installed:
        skill = entry["skill"]
        meta = entry["meta"]
        label = " (linked)" if entry["is_link"] else ""
        typer.echo(f"  {skill.name} @ {skill.manifest.metadata.version}{label}")

        # Run stats
        if entry["total_runs"] == 0:
            typer.echo("    Last run: never")
        else:
            success_pct = int(entry["successful_runs"] / entry["total_runs"] * 100)
            last_run = _fmt_relative(entry["latest_ts"], now)
            typer.echo(
                f"    Last run: {last_run} · {entry['total_runs']} runs · {success_pct}% success"
            )

        # Source
        if entry["is_link"]:
            typer.echo(f"    Linked from: {entry['link_target']}")
        elif meta.get("source"):
            ref = meta.get("ref", "")
            source_display = meta["source"]
            if ref and ref not in source_display:
                source_display += f"@{ref}"
            typer.echo(f"    Source: {source_display}")

        typer.echo()


def _fmt_relative(ts: datetime, now: datetime) -> str:
    delta = int((now - ts).total_seconds())
    if delta < 60:
        return "just now"
    if delta < 3600:
        m = delta // 60
        return f"{m} minute{'s' if m > 1 else ''} ago"
    if delta < 86400:
        h = delta // 3600
        return f"{h} hour{'s' if h > 1 else ''} ago"
    if delta < 86400 * 2:
        return "yesterday"
    return f"{delta // 86400} days ago"
```

- [ ] **Step 4: Run all tests**

```bash
uv run pytest -q
```

Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/cli.py launcher/tests/test_cli.py
git commit -m "feat: add zipsa list (installed stats) and zipsa discover (renamed)"
```

---

## Done

After all 9 tasks:

```bash
uv run pytest --cov=zipsa -q
```

All tests pass. Verify manually:

```bash
zipsa install westbrookai/zipsa/skills/daily-progress
zipsa list
zipsa run daily-progress "test"
zipsa uninstall daily-progress
zipsa discover ./skills
```
