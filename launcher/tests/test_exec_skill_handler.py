"""Tests for ExecSkillHandler — the host-side body of mcp__zipsa__exec.

The authoring container (A) calls mcp__zipsa__exec(staging_path) to
test the skill it's writing. The handler runs on the HOST: it validates
the path is under ~/.zipsa/staging, discovers phases, and runs them via
exec_runner.run_phases in docker mode (the host spawns a fresh runtime
container per phase — the real user-facing path). No translation needed
because staging is mounted into A at its own host path.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from zipsa.core.exec_skill_handler import ExecSkillHandler


def _staging_skill(home: Path, name: str, files: dict[str, str]) -> Path:
    d = home / "staging" / name / "zipsa-dist"
    d.mkdir(parents=True)
    for fname, body in files.items():
        (d / fname).write_text(body)
    return home / "staging" / name


def _fake_result(**kw):
    from zipsa.exec_runner import ExecResult

    base = dict(
        skill_name="s", mode="docker", result={"ok": True}, exit_code=0,
        duration_ms=10, out_dir="/out", stdout="", stderr="",
    )
    base.update(kw)
    return ExecResult(**base)


class TestExecSkillHandler:
    @patch("zipsa.core.exec_skill_handler.run_phases")
    def test_runs_staging_skill_and_shapes_result(
        self, mock_run_phases, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        skill = _staging_skill(tmp_path, "draft-x", {
            "1.do.py": "x", "2.report.md": "y",
        })
        mock_run_phases.return_value = [
            _fake_result(result={"a": 1}),
            _fake_result(result={"final": True}),
        ]

        handler = ExecSkillHandler(docker_image="img:test")
        out = handler.run(staging_path=str(skill))

        assert out["status"] == "ok"
        assert out["skill_name"] == "draft-x"
        assert out["result"] == {"final": True}      # last phase
        assert out["exit_code"] == 0
        assert [p["id"] for p in out["phases"]] == ["1", "2"]
        assert [p["slug"] for p in out["phases"]] == ["do", "report"]
        # run_phases called in docker mode with the right image + root
        kwargs = mock_run_phases.call_args.kwargs
        assert kwargs["docker_image"] == "img:test"
        assert kwargs["skill_root"] == skill.resolve()

    @patch("zipsa.core.exec_skill_handler.run_phases")
    def test_mounts_parsed_and_passed(self, mock_run_phases, tmp_path, monkeypatch):
        """The agent can pass --mount-style specs so a draft needing a
        credential file (telegram.json) is testable for real."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        skill = _staging_skill(tmp_path, "draft-x", {"1.do.py": "x"})
        creds = tmp_path / "creds.json"
        creds.write_text("{}")
        mock_run_phases.return_value = [_fake_result()]

        ExecSkillHandler(docker_image="img").run(
            staging_path=str(skill),
            mounts=[f"{creds}:/mnt/creds/telegram.json"],
        )

        extra = mock_run_phases.call_args.kwargs["extra_mounts"]
        assert extra == [(creds.resolve(), "/mnt/creds/telegram.json")]

    @patch("zipsa.core.exec_skill_handler.run_phases")
    def test_mount_missing_host_path_rejected(self, mock_run_phases, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        skill = _staging_skill(tmp_path, "draft-x", {"1.do.py": "x"})

        out = ExecSkillHandler(docker_image="img").run(
            staging_path=str(skill),
            mounts=[f"{tmp_path/'nope.json'}:/mnt/x"],
        )

        assert out["status"] == "failed"
        assert out["error"]["code"] == "exec_mount_not_found"
        mock_run_phases.assert_not_called()

    @patch("zipsa.core.exec_skill_handler.run_phases")
    def test_passes_args_as_user_query(self, mock_run_phases, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        skill = _staging_skill(tmp_path, "draft-x", {"1.do.py": "x"})
        mock_run_phases.return_value = [_fake_result()]

        ExecSkillHandler(docker_image="img").run(
            staging_path=str(skill), args="Seoul",
        )

        assert mock_run_phases.call_args.kwargs["user_query"] == "Seoul"

    @patch("zipsa.core.exec_skill_handler.run_phases")
    def test_failed_phase_reported_not_raised(
        self, mock_run_phases, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        skill = _staging_skill(tmp_path, "draft-x", {"1.do.py": "x"})
        mock_run_phases.return_value = [
            _fake_result(result=None, exit_code=3, stderr="boom"),
        ]

        out = ExecSkillHandler(docker_image="img").run(staging_path=str(skill))

        assert out["status"] == "failed"
        assert out["exit_code"] == 3
        assert "boom" in out["stderr"]

    def test_path_outside_staging_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        outside = tmp_path / "not-staging" / "evil"
        outside.mkdir(parents=True)

        out = ExecSkillHandler(docker_image="img").run(staging_path=str(outside))

        assert out["status"] == "failed"
        assert out["error"]["code"] == "exec_path_outside_staging"

    def test_missing_path_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        gone = tmp_path / "staging" / "gone"

        out = ExecSkillHandler(docker_image="img").run(staging_path=str(gone))

        assert out["status"] == "failed"
        assert out["error"]["code"] == "exec_staging_not_found"

    def test_no_phases_reported(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        empty = tmp_path / "staging" / "empty"
        (empty / "zipsa-dist").mkdir(parents=True)

        out = ExecSkillHandler(docker_image="img").run(staging_path=str(empty))

        assert out["status"] == "failed"
        assert out["error"]["code"] == "exec_no_phases"
