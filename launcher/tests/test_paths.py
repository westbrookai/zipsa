"""Tests for zipsa.paths — centralized path resolution."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

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


class TestZipsaHome:
    def test_default_is_dotzip_under_home(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZIPSA_HOME", None)
            assert zipsa_home() == Path.home() / ".zipsa"

    def test_env_var_overrides_default(self, tmp_path):
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            assert zipsa_home() == tmp_path


class TestSkillPaths:
    def test_skill_data_dir(self, tmp_path):
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            result = skill_data_dir("my-skill", "1.2.3")
            assert result == tmp_path / "my-skill@1.2.3"

    def test_skill_runs_dir(self, tmp_path):
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            result = skill_runs_dir("my-skill", "1.2.3")
            assert result == tmp_path / "my-skill@1.2.3" / "runs"

    def test_skill_env_file(self, tmp_path):
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            result = skill_env_file("my-skill", "1.2.3")
            assert result == tmp_path / "my-skill@1.2.3" / ".env"


class TestGlobalPaths:
    def test_global_env_file(self, tmp_path):
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            assert global_env_file() == tmp_path / ".env"

    def test_credentials_dir(self, tmp_path):
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            assert credentials_dir() == tmp_path / "credentials"


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


class TestSkillRequiresFile:
    def test_returns_path_inside_skill_data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.paths import skill_requires_file
        p = skill_requires_file("daily-progress", "0.4.0")
        assert p == tmp_path / "daily-progress@0.4.0" / "requires.yaml"


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


class TestSkillMemoryFile:
    def test_returns_path_under_memory_dir_no_version(self, tmp_path, monkeypatch):
        """Per-skill memory lives at ~/.zipsa/memory/<skill>/skill-mem.json —
        cross-version by design so user-set values persist across upgrades."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.paths import skill_memory_file
        p = skill_memory_file("daily-progress")
        assert p == tmp_path / "memory" / "daily-progress" / "skill-mem.json"

    def test_sibling_of_global_mem(self, tmp_path, monkeypatch):
        """Per-skill and global memory share the ~/.zipsa/memory/ tree."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.paths import skill_memory_file, zipsa_home
        p = skill_memory_file("bip-daily-x")
        assert p.parent.parent == zipsa_home() / "memory"


class TestLatestLegacySkillMemory:
    def test_none_when_no_legacy_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.paths import latest_legacy_skill_memory
        assert latest_legacy_skill_memory("daily-progress") is None

    def test_none_when_home_dir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "nope"))
        from zipsa.paths import latest_legacy_skill_memory
        assert latest_legacy_skill_memory("daily-progress") is None

    def test_finds_single_legacy_version(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        legacy = tmp_path / "daily-progress@0.4.0" / "memory" / "skill-mem.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"notion_workspace": "Work"}')
        from zipsa.paths import latest_legacy_skill_memory
        result = latest_legacy_skill_memory("daily-progress")
        assert result == legacy

    def test_picks_most_recent_when_multiple(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        for ver, val in [("0.3.0", "old"), ("0.4.10", "newest"), ("0.4.9", "older")]:
            f = tmp_path / f"daily-progress@{ver}" / "memory" / "skill-mem.json"
            f.parent.mkdir(parents=True)
            f.write_text(f'{{"v": "{val}"}}')
        from zipsa.paths import latest_legacy_skill_memory
        result = latest_legacy_skill_memory("daily-progress")
        # Semver-aware: 0.4.10 > 0.4.9 > 0.3.0
        assert result == tmp_path / "daily-progress@0.4.10" / "memory" / "skill-mem.json"

    def test_ignores_versions_without_memory_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        # @0.5.0 has memory dir but no file → skipped
        (tmp_path / "daily-progress@0.5.0" / "memory").mkdir(parents=True)
        # @0.4.0 has the file
        legacy = tmp_path / "daily-progress@0.4.0" / "memory" / "skill-mem.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{}')
        from zipsa.paths import latest_legacy_skill_memory
        result = latest_legacy_skill_memory("daily-progress")
        assert result == legacy

    def test_ignores_other_skills(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        # bip-daily-x has memory → must not be returned for daily-progress
        other = tmp_path / "bip-daily-x@0.1.0" / "memory" / "skill-mem.json"
        other.parent.mkdir(parents=True)
        other.write_text('{}')
        from zipsa.paths import latest_legacy_skill_memory
        assert latest_legacy_skill_memory("daily-progress") is None
        # but it does find its own
        assert latest_legacy_skill_memory("bip-daily-x") == other


class TestResolveSkillMemoryPath:
    def test_returns_new_path_when_already_present(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        # Pre-create the new path
        new = tmp_path / "memory" / "daily-progress" / "skill-mem.json"
        new.parent.mkdir(parents=True)
        new.write_text('{"already": "here"}')
        # Also have a legacy file — must not be used
        legacy = tmp_path / "daily-progress@0.4.0" / "memory" / "skill-mem.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"stale": "ignore me"}')

        from zipsa.paths import resolve_skill_memory_path
        result = resolve_skill_memory_path("daily-progress")
        assert result == new
        assert '"already": "here"' in result.read_text()

    def test_migrates_legacy_when_new_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        legacy = tmp_path / "daily-progress@0.5.0" / "memory" / "skill-mem.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"notion_workspace": "Work"}')

        from zipsa.paths import resolve_skill_memory_path, skill_memory_file
        result = resolve_skill_memory_path("daily-progress")
        assert result == skill_memory_file("daily-progress")
        assert result.exists()
        # Content migrated
        assert '"notion_workspace": "Work"' in result.read_text()
        # Legacy left in place (safety)
        assert legacy.exists()

    def test_returns_path_when_no_legacy_no_new(self, tmp_path, monkeypatch):
        """Fresh install: returns the new path (which doesn't exist yet).
        The caller (MemoryStore) handles write-on-first-use."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.paths import resolve_skill_memory_path, skill_memory_file
        result = resolve_skill_memory_path("new-skill")
        assert result == skill_memory_file("new-skill")
        assert not result.exists()

    def test_migration_picks_latest_legacy(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        for ver, val in [("0.3.0", "old"), ("0.5.1", "newest"), ("0.5.0", "older")]:
            f = tmp_path / f"daily-progress@{ver}" / "memory" / "skill-mem.json"
            f.parent.mkdir(parents=True)
            f.write_text(f'{{"v": "{val}"}}')

        from zipsa.paths import resolve_skill_memory_path
        result = resolve_skill_memory_path("daily-progress")
        assert '"v": "newest"' in result.read_text()
