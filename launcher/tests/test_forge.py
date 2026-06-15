"""Tests for zipsa.create's forge orchestration — build_forge_prompt +
run_forge.

The forge authoring agent runs headless (`claude -p`) in the runtime
container and drives INTENT.md → author → exec-debug → run-test →
promote over a path-scoped ForgeServer. Forge tools are path-scoped:
exec/run/promote take no staging_path arg (the server injects it).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

from zipsa.create import build_forge_prompt, run_forge


class TestBuildForgePrompt:
    def test_mentions_intent_exec_run_promote_and_loop(self, tmp_path):
        p = build_forge_prompt("a weather alert", tmp_path / "draft-1")
        for needle in ("INTENT.md", "mcp__zipsa__exec", "mcp__zipsa__run",
                       "mcp__zipsa__promote", "a weather alert"):
            assert needle in p


class TestRunForge:
    @patch("zipsa.create.subprocess.run")
    @patch("zipsa.create.ForgeServer")
    def test_starts_server_runs_container_stops(self, mock_srv, mock_run,
                                                tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        srv = MagicMock(); srv.port = 5000; srv.token = "t"
        mock_srv.return_value = srv
        mock_run.return_value.returncode = 0
        rc = run_forge("intent", skills_dir=tmp_path / "skills", image="img")
        assert rc == 0
        srv.start.assert_called_once(); srv.stop.assert_called_once()
        argv = mock_run.call_args.args[0]
        assert "claude" in argv
