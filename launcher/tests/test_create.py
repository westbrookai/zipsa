"""Tests for zipsa.create — headless containerized authoring (Step 3).

The authoring agent runs headless (`claude -p`) in the runtime
container and drives author → test → promote over MCP. The workflow +
contract are bundled with the launcher and inlined into the prompt, so
create needs nothing from any repo. Only mounts: staging (rw) + the
mcp-config (ro).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zipsa.create import (
    build_create_prompt,
    build_mcp_config,
    run_create,
)


class TestBuildCreatePrompt:
    def test_inlines_bundled_workflow_and_contract(self, tmp_path):
        staging = tmp_path / ".zipsa" / "staging" / "abc"
        prompt = build_create_prompt("umbrella alert at 8am", staging)

        assert "umbrella alert at 8am" in prompt
        assert str(staging) in prompt
        # Bundled workflow + contract are inlined (markers from each)
        assert "WORKFLOW" in prompt and "CONTRACT" in prompt
        assert "skill-builder workflow" in prompt          # skill-builder.md
        assert "Phase contract" in prompt                  # AUTHORING.md
        assert "mcp__zipsa__exec" in prompt
        assert "mcp__zipsa__promote" in prompt
        assert "feasibility" in prompt.lower()
        assert "prerequisite" in prompt.lower()

    def test_no_repo_paths_referenced(self, tmp_path):
        """The prompt must not tell the agent to read repo files — those
        won't exist once skills live in a registry."""
        prompt = build_create_prompt("x", tmp_path / "s")
        assert ".claude/skills" not in prompt
        assert "skills/AUTHORING.md" not in prompt


class TestBuildMcpConfig:
    def test_points_at_host_server_with_token(self):
        cfg = build_mcp_config(port=54321, token="tok-xyz")
        zipsa = cfg["mcpServers"]["zipsa"]
        assert zipsa["type"] == "http"
        assert "host.docker.internal:54321/mcp" in zipsa["url"]
        assert "tok-xyz" in zipsa["headersHelper"]

    def test_generous_tool_timeout(self):
        cfg = build_mcp_config(port=1, token="t")
        assert cfg["mcpServers"]["zipsa"]["timeout"] >= 300_000

    def test_hitl_timeout_constant_meets_human_latency_bound(self):
        """_MCP_TOOL_TIMEOUT_MS must be >= 10_800_000 (3 h) to survive a
        relayed or away-operator forge session without timing out ask/confirm/
        choose before the human responds."""
        from zipsa.create import _MCP_TOOL_TIMEOUT_MS
        assert _MCP_TOOL_TIMEOUT_MS >= 10_800_000

    def test_mcp_config_propagates_timeout_constant(self):
        """build_mcp_config must embed _MCP_TOOL_TIMEOUT_MS verbatim so the
        container claude's tool calls respect the human-latency bound."""
        from zipsa.create import _MCP_TOOL_TIMEOUT_MS
        cfg = build_mcp_config(port=1, token="t")
        assert cfg["mcpServers"]["zipsa"]["timeout"] == _MCP_TOOL_TIMEOUT_MS


class TestRunCreate:
    @patch("zipsa.create.run_forge")
    def test_delegates_to_run_forge(self, mock_forge, tmp_path):
        mock_forge.return_value = 0
        rc = run_create("make a thing", skills_dir=tmp_path / "skills",
                        image="img:test")
        assert rc == 0
        mock_forge.assert_called_once()
        # same intent + skills_dir + image forwarded
        assert mock_forge.call_args.args[0] == "make a thing"
        assert mock_forge.call_args.kwargs["skills_dir"] == tmp_path / "skills"
        assert mock_forge.call_args.kwargs["image"] == "img:test"

    @patch("zipsa.create.run_forge")
    def test_propagates_exit_code(self, mock_forge, tmp_path):
        mock_forge.return_value = 7
        assert run_create("x", skills_dir=tmp_path / "s", image="i") == 7


class TestIsInteractive:
    def test_tty(self):
        from zipsa.create import _is_interactive
        class _T:
            def isatty(self): return True
        assert _is_interactive(_T()) is True

    def test_non_tty(self, monkeypatch):
        from zipsa.create import _is_interactive
        monkeypatch.delenv("ZIPSA_FORCE_INTERACTIVE", raising=False)
        class _P:
            def isatty(self): return False
        assert _is_interactive(_P()) is False

    def test_force_env(self, monkeypatch):
        from zipsa.create import _is_interactive
        monkeypatch.setenv("ZIPSA_FORCE_INTERACTIVE", "1")
        class _P:
            def isatty(self): return False
        assert _is_interactive(_P()) is True


class TestRunForgeDryRun:
    """`run_forge(dry_run=True)` prints the would-run command + mcp-config
    path and returns 0 WITHOUT starting a ForgeServer (no bound port),
    spawning the container, or leaving an orphan staging dir / config (#175)."""

    @patch("zipsa.create.subprocess.run")
    @patch("zipsa.create.ForgeServer")
    def test_dry_run_spawns_nothing(self, mock_forge_cls, mock_run, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        srv = MagicMock(); srv.port = 5; srv.token = "t"
        mock_forge_cls.return_value = srv

        from zipsa.create import run_forge
        rc = run_forge("make a thing", skills_dir=tmp_path / "skills", image="img", dry_run=True)

        assert rc == 0
        mock_run.assert_not_called()
        srv.start.assert_not_called()

    @patch("zipsa.create.subprocess.run")
    @patch("zipsa.create.ForgeServer")
    def test_dry_run_leaves_no_orphan_staging_or_config(
        self, mock_forge_cls, mock_run, tmp_path, monkeypatch
    ):
        home = tmp_path / "home"
        monkeypatch.setenv("ZIPSA_HOME", str(home))
        srv = MagicMock(); srv.port = 5; srv.token = "t"
        mock_forge_cls.return_value = srv

        from zipsa.create import run_forge
        run_forge("x", skills_dir=tmp_path / "skills", image="img", dry_run=True)
        run_forge("x", skills_dir=tmp_path / "skills", image="img", dry_run=True)

        staging = home / "staging"
        drafts = list(staging.glob("draft-*")) if staging.exists() else []
        # the placeholder dir is never created on disk
        assert all(not d.is_dir() for d in drafts)
        cfgs = list(staging.glob("*.mcp.json")) if staging.exists() else []
        assert len(cfgs) <= 1
