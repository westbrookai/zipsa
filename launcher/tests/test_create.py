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
    build_docker_argv,
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


class TestBuildDockerArgv:
    def test_command_shape(self, tmp_path):
        staging = tmp_path / ".zipsa" / "staging" / "abc"
        staging.mkdir(parents=True)
        mcpcfg = tmp_path / "mcp.json"
        mcpcfg.write_text("{}")

        argv = build_docker_argv(
            image="img:test",
            staging_path=staging,
            mcp_config_host=mcpcfg,
            prompt="do the thing",
            env_file=None,
        )

        assert argv[0:2] == ["docker", "run"]
        assert "--rm" in argv
        assert "-i" not in argv and "-t" not in argv
        assert f"{staging}:{staging}:rw" in argv
        assert any(a.startswith(f"{mcpcfg}:") and a.endswith(":ro") for a in argv)
        # no repo mount
        assert not any(":ro" in a and "skills" in a and str(staging) not in a
                       for a in argv if a.endswith(":ro"))
        assert "img:test" in argv
        ci = argv.index("claude")
        assert argv[ci + 1] == "-p"
        assert argv[ci + 2] == "do the thing"
        assert "--strict-mcp-config" in argv
        assert "bypassPermissions" in argv
        # workdir = staging (no repo)
        w = argv.index("-w")
        assert argv[w + 1] == str(staging)

    def test_env_file_added_when_present(self, tmp_path):
        staging = tmp_path / "s"; staging.mkdir()
        mcpcfg = tmp_path / "mcp.json"; mcpcfg.write_text("{}")
        env_file = tmp_path / ".env"; env_file.write_text("X=1\n")

        argv = build_docker_argv(
            image="i", staging_path=staging, mcp_config_host=mcpcfg,
            prompt="p", env_file=env_file,
        )
        i = argv.index("--env-file")
        assert argv[i + 1] == str(env_file)


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
