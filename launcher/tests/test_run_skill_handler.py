"""Tests for RunSkillHandler: subprocess wrapper that invokes a child
skill via uv run zipsa run, parses summary.json, returns the routing
fields the parent needs to chain get_artifact."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zipsa.core.run_skill_handler import RunSkillHandler
from zipsa.core.caller_context import CallerInfo, current_caller


def _build_handler(server_mock, children: list[str]):
    """Helper: build RunSkillHandler with a stubbed caller-children resolver."""
    h = RunSkillHandler(server=server_mock)
    h._resolve_caller_children = lambda c: children
    return h


def _make_summary(run_dir: Path, status: str = "ok", skill: str = "alpha",
                   version: str = "0.1.0", exit_code: int = 0,
                   error: dict | None = None) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    s = {
        "schema_version": 1, "status": status, "exit_code": exit_code,
        "skill": skill, "version": version,
        "started_at": "2026-05-21T00:00:00+10:00",
        "finished_at": "2026-05-21T00:00:01+10:00",
        "duration_seconds": 1.0, "cost_usd": 0.01,
        "turns": 1, "phases": [],
    }
    if error is not None:
        s["error"] = error
    p = run_dir / "summary.json"
    p.write_text(json.dumps(s))
    return p


class TestRunSkillHandler:
    def test_rejects_child_not_in_caller_children(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        server = MagicMock(port=12345, token="parent-tok")
        h = _build_handler(server, children=["alpha", "beta"])
        current_caller.set(CallerInfo("parent", "1.0.0"))
        try:
            result = h.run(name="gamma", args="")
            assert result["status"] == "failed"
            assert result["error"]["code"] == "skill_not_in_children"
        finally:
            current_caller.set(None)

    def test_no_caller_context_returns_failed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        server = MagicMock(port=12345, token="parent-tok")
        h = _build_handler(server, children=["alpha"])
        current_caller.set(None)
        result = h.run(name="alpha", args="")
        assert result["status"] == "failed"
        assert result["error"]["code"] == "caller_unknown"

    def test_spawns_subprocess_with_propagated_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        server = MagicMock(port=12345, token="parent-tok")
        h = _build_handler(server, children=["alpha"])
        current_caller.set(CallerInfo("parent", "1.0.0"))
        try:
            run_dir = tmp_path / "alpha@0.1.0" / "runs" / "2026-05-21_000000_000"
            _make_summary(run_dir)
            with patch("subprocess.Popen") as mock_run:
                proc = MagicMock()
                proc.poll.return_value = 0
                proc.returncode = 0
                proc.communicate.return_value = (b"", b"")
                mock_run.return_value = proc
                h._find_latest_run_dir = lambda name: run_dir
                result = h.run(name="alpha", args="hello")

            call_kwargs = mock_run.call_args.kwargs
            env = call_kwargs["env"]
            assert env["ZIPSA_PARENT_MCP_URL"] == "http://host.docker.internal:12345/mcp"
            assert env["ZIPSA_PARENT_MCP_TOKEN"]
            assert "parent" in env["ZIPSA_CALL_TRACE"]
            assert env["ZIPSA_CALL_DEPTH"] == "1"
            assert call_kwargs["stdin"] == subprocess.DEVNULL
            assert call_kwargs["stdout"] == subprocess.DEVNULL
            assert call_kwargs["stderr"] == subprocess.PIPE

            # The child token should have been registered on the server
            server.register_caller.assert_called()
            first_call = server.register_caller.call_args_list[0]
            child_token = first_call.args[0]
            assert env["ZIPSA_PARENT_MCP_TOKEN"] == child_token

            # Result shape
            assert result["status"] == "ok"
            assert result["skill"] == "alpha"
            assert result["version"] == "0.1.0"
            assert result["run_id"] == "2026-05-21_000000_000"
            assert "summary" in result
        finally:
            current_caller.set(None)

    def test_call_trace_extended_when_already_present(self, tmp_path, monkeypatch):
        """When ZIPSA_CALL_TRACE is already set (we ourselves are a child),
        extend it rather than overwriting."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        monkeypatch.setenv("ZIPSA_CALL_TRACE", "grandparent")
        monkeypatch.setenv("ZIPSA_CALL_DEPTH", "2")
        server = MagicMock(port=12345, token="parent-tok")
        h = _build_handler(server, children=["alpha"])
        current_caller.set(CallerInfo("parent", "1.0.0"))
        try:
            run_dir = tmp_path / "alpha@0.1.0" / "runs" / "r1"
            _make_summary(run_dir)
            with patch("subprocess.Popen") as mock_run:
                proc = MagicMock()
                proc.poll.return_value = 0
                proc.returncode = 0
                proc.communicate.return_value = (b"", b"")
                mock_run.return_value = proc
                h._find_latest_run_dir = lambda name: run_dir
                h.run(name="alpha", args="")
            env = mock_run.call_args.kwargs["env"]
            assert env["ZIPSA_CALL_TRACE"] == "grandparent,parent"
            assert env["ZIPSA_CALL_DEPTH"] == "3"
        finally:
            current_caller.set(None)

    def test_child_failed_status_propagates(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        server = MagicMock(port=12345, token="parent-tok")
        h = _build_handler(server, children=["alpha"])
        current_caller.set(CallerInfo("parent", "1.0.0"))
        try:
            run_dir = tmp_path / "alpha@0.1.0" / "runs" / "r1"
            _make_summary(run_dir, status="failed", exit_code=1,
                          error={"code": "agent_error", "message": "x"})
            with patch("subprocess.Popen") as mock_run:
                proc = MagicMock()
                proc.poll.return_value = 1
                proc.returncode = 1
                proc.communicate.return_value = (b"", b"")
                mock_run.return_value = proc
                h._find_latest_run_dir = lambda name: run_dir
                result = h.run(name="alpha", args="")
            assert result["status"] == "failed"
            assert result["exit_code"] == 1
            assert result["summary"]["error"]["code"] == "agent_error"
        finally:
            current_caller.set(None)

    def test_summary_missing_returns_failed(self, tmp_path, monkeypatch):
        """If the subprocess completes but no summary.json is found,
        surface a clean error."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        server = MagicMock(port=12345, token="parent-tok")
        h = _build_handler(server, children=["alpha"])
        current_caller.set(CallerInfo("parent", "1.0.0"))
        try:
            with patch("subprocess.Popen") as mock_run:
                proc = MagicMock()
                proc.poll.return_value = 0
                proc.returncode = 0
                proc.communicate.return_value = (b"", b"")
                mock_run.return_value = proc
                h._find_latest_run_dir = lambda name: None
                result = h.run(name="alpha", args="")
            assert result["status"] == "failed"
            assert result["error"]["code"] == "summary_not_found"
        finally:
            current_caller.set(None)

    def test_child_timeout_returns_failed(self, tmp_path, monkeypatch):
        """Timeout is enforced by our own poll loop (Popen-based) so we
        can simulate it by having poll() always return None and shrinking
        the timeout env var to ~0."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        monkeypatch.setenv("ZIPSA_RUN_SKILL_TIMEOUT", "0")
        server = MagicMock(port=12345, token="parent-tok")
        h = _build_handler(server, children=["alpha"])
        current_caller.set(CallerInfo("parent", "1.0.0"))
        try:
            with patch("subprocess.Popen") as mock_run:
                proc = MagicMock()
                # Never finishes: poll always None until killed
                proc.poll.return_value = None
                proc.returncode = -9
                proc.wait.return_value = -9
                proc.communicate.return_value = (b"", b"")
                mock_run.return_value = proc
                result = h.run(name="alpha", args="")
            assert result["status"] == "failed"
            assert result["error"]["code"] == "child_timeout"
        finally:
            current_caller.set(None)
