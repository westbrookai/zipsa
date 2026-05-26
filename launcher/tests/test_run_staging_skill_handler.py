"""Tests for RunStagingSkillHandler — runs a staging-dir skill so
skill-builder can iterate on its draft before install.

These tests do NOT actually spawn docker — they stub out the subprocess
call so we verify the handler's wiring (permission check, staging
lookup, env var propagation, result shape) without needing a real run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

from zipsa.core.caller_context import CallerInfo, current_caller
from zipsa.core.run_staging_skill_handler import RunStagingSkillHandler


@pytest.fixture(autouse=True)
def _clear_caller():
    """current_caller is a ContextVar — reset after every test so a
    leftover set() from one test doesn't leak into another."""
    token = current_caller.set(None)
    yield
    current_caller.reset(token)


@pytest.fixture
def zipsa_home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
    (tmp_path / "skills").mkdir()
    (tmp_path / "staging").mkdir()
    return tmp_path


def _write_skill(root: Path, name: str, *,
                 allows_staging_run: bool = False) -> Path:
    """Write a minimal skill at root/<name>/manifest.yaml + SKILL.md."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    spec = {"purpose": "Test.", "instructions": "./SKILL.md"}
    if allows_staging_run:
        spec["allows_staging_run"] = True
    (d / "manifest.yaml").write_text(yaml.safe_dump({
        "apiVersion": "zipsa.dev/v1alpha1",
        "kind": "SkillManifest",
        "metadata": {"name": name, "version": "0.1.0"},
        "spec": spec,
    }))
    (d / "SKILL.md").write_text(f"# {name}\n")
    return d


def _write_staging_run(home: Path, name: str, version: str = "0.1.0",
                       *, status: str = "ok") -> Path:
    """Write a fake summary.json under the staging skill's run dir.
    Subprocess result-reading uses skill name + version to find this."""
    run_id = "2026-05-26_120000_000000"
    run_dir = home / f"{name}@{version}" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({
        "status": status, "skill": name, "version": version,
    }))
    return run_dir


def _caller(skill_name: str) -> CallerInfo:
    return CallerInfo(skill=skill_name, version="0.1.0", depth=0, trace=())


class TestPermission:
    def test_caller_without_allows_staging_run_rejected(self, zipsa_home):
        """Only skills that declared allows_staging_run=True in their
        manifest can invoke run_staging_skill. Default is False."""
        _write_skill(zipsa_home / "skills", "regular-skill",
                     allows_staging_run=False)
        _write_skill(zipsa_home / "staging", "draft-skill")
        h = RunStagingSkillHandler(server=MagicMock())
        current_caller.set(_caller("regular-skill"))
        result = h.run(name="draft-skill", args="")
        assert result["status"] == "failed"
        assert result["error"]["code"] == "staging_run_not_allowed"

    def test_no_caller_context_rejected(self, zipsa_home):
        h = RunStagingSkillHandler(server=MagicMock())
        current_caller.set(None)
        result = h.run(name="any", args="")
        assert result["status"] == "failed"
        assert result["error"]["code"] == "caller_unknown"

    def test_caller_with_allows_staging_run_passes_permission(self, zipsa_home):
        _write_skill(zipsa_home / "skills", "builder", allows_staging_run=True)
        h = RunStagingSkillHandler(server=MagicMock())
        current_caller.set(_caller("builder"))
        result = h.run(name="missing", args="")
        # Past the permission gate — fails on staging_skill_not_found instead
        assert result["error"]["code"] != "staging_run_not_allowed"


class TestStagingLookup:
    def test_missing_staging_skill_returns_clear_error(self, zipsa_home):
        _write_skill(zipsa_home / "skills", "builder", allows_staging_run=True)
        h = RunStagingSkillHandler(server=MagicMock())
        current_caller.set(_caller("builder"))
        result = h.run(name="nonexistent", args="")
        assert result["status"] == "failed"
        assert result["error"]["code"] == "staging_skill_not_found"

    def test_staging_with_bad_manifest_returns_clear_error(self, zipsa_home):
        _write_skill(zipsa_home / "skills", "builder", allows_staging_run=True)
        bad = zipsa_home / "staging" / "broken"
        bad.mkdir()
        # No manifest.yaml → Skill.load raises
        h = RunStagingSkillHandler(server=MagicMock())
        current_caller.set(_caller("builder"))
        result = h.run(name="broken", args="")
        assert result["status"] == "failed"
        assert result["error"]["code"] in (
            "staging_skill_unloadable", "staging_skill_not_found",
        )


class TestPathSafety:
    @pytest.mark.parametrize("bad_name", ["..", "../other", "a/b", ""])
    def test_traversal_in_name_rejected(self, zipsa_home, bad_name):
        _write_skill(zipsa_home / "skills", "builder", allows_staging_run=True)
        h = RunStagingSkillHandler(server=MagicMock())
        current_caller.set(_caller("builder"))
        result = h.run(name=bad_name, args="")
        assert result["status"] == "failed"
        assert result["error"]["code"] == "staging_skill_bad_name"


class TestSubprocessSpawn:
    def test_spawn_passes_staging_path_via_env(self, zipsa_home):
        """The handler sets ZIPSA_STAGING_RUN_PATH so cli.run loads the
        staging dir instead of the installed skill of the same name."""
        _write_skill(zipsa_home / "skills", "builder", allows_staging_run=True)
        staging = _write_skill(zipsa_home / "staging", "draft")
        _write_staging_run(zipsa_home, "draft")

        captured = {}

        def fake_popen(cmd, env=None, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = env
            proc = MagicMock()
            proc.poll.side_effect = [None, 0]
            proc.returncode = 0
            proc.stderr = None
            proc.wait.return_value = 0
            return proc

        h = RunStagingSkillHandler(server=MagicMock(port=8080))
        h._server.register_caller = MagicMock()
        current_caller.set(_caller("builder"))
        with patch("zipsa.core.run_staging_skill_handler.subprocess.Popen",
                   side_effect=fake_popen):
            result = h.run(name="draft", args="hello")

        assert captured["env"]["ZIPSA_STAGING_RUN_PATH"] == str(staging.resolve())
        assert "ZIPSA_PARENT_MCP_URL" in captured["env"]
        assert "ZIPSA_PARENT_MCP_TOKEN" in captured["env"]


class TestResultShape:
    def test_success_returns_is_staging_true(self, zipsa_home):
        """The CRUCIAL field — every result is tagged is_staging:true so
        downstream (skill-builder's analysis) knows the run came from
        staging vs a regular install."""
        _write_skill(zipsa_home / "skills", "builder", allows_staging_run=True)
        _write_skill(zipsa_home / "staging", "draft")
        _write_staging_run(zipsa_home, "draft")

        def fake_popen(cmd, env=None, **kwargs):
            proc = MagicMock()
            proc.poll.side_effect = [0]
            proc.returncode = 0
            proc.stderr = None
            proc.wait.return_value = 0
            return proc

        h = RunStagingSkillHandler(server=MagicMock(port=8080))
        h._server.register_caller = MagicMock()
        current_caller.set(_caller("builder"))
        with patch("zipsa.core.run_staging_skill_handler.subprocess.Popen",
                   side_effect=fake_popen):
            result = h.run(name="draft", args="")

        assert result["status"] == "ok"
        assert result["is_staging"] is True
        assert result["skill"] == "draft"
        assert result["run_id"] is not None
        assert result["summary"]["status"] == "ok"
