"""Tests for zipsa.create — interactive containerized authoring (Step 3).

`zipsa create` spawns the authoring claude INSIDE the pinned runtime
container (version-consistent), mounts a staging dir rw + the repo ro,
points it at a host CreateServer (exec + promote tools), and runs it
interactively. zipsa never enters the container; the host orchestrates
test runs via the exec tool.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zipsa.create import (
    build_create_prompt,
    build_docker_argv,
    build_mcp_config,
    find_repo_root,
    run_create,
)


def _make_repo(root: Path) -> Path:
    sb = root / ".claude" / "skills" / "zipsa-skill-builder"
    sb.mkdir(parents=True)
    (sb / "SKILL.md").write_text("# zipsa-skill-builder\n")
    (root / "skills").mkdir()
    (root / "skills" / "AUTHORING.md").write_text("# authoring\n")
    return root


class TestFindRepoRoot:
    def test_finds_from_subdir(self, tmp_path):
        root = _make_repo(tmp_path / "repo")
        deep = root / "a" / "b"
        deep.mkdir(parents=True)
        assert find_repo_root(deep) == root

    def test_none_when_absent(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert find_repo_root(plain) is None


class TestBuildCreatePrompt:
    def test_carries_intent_staging_and_tools(self, tmp_path):
        staging = tmp_path / ".zipsa" / "staging" / "abc"
        prompt = build_create_prompt("umbrella alert at 8am", staging)
        assert "umbrella alert at 8am" in prompt
        assert str(staging) in prompt
        assert "zipsa-skill-builder" in prompt
        assert "AUTHORING.md" in prompt
        assert "mcp__zipsa__exec" in prompt
        assert "mcp__zipsa__promote" in prompt


class TestBuildMcpConfig:
    def test_points_at_host_server_with_token(self):
        cfg = build_mcp_config(port=54321, token="tok-xyz")
        zipsa = cfg["mcpServers"]["zipsa"]
        assert zipsa["type"] == "http"
        assert "host.docker.internal:54321/mcp" in zipsa["url"]
        assert "tok-xyz" in zipsa["headersHelper"]

    def test_generous_tool_timeout(self):
        """HITL/exec tools block minutes; the per-server timeout must be
        well above Claude Code's ~60s default."""
        cfg = build_mcp_config(port=1, token="t")
        assert cfg["mcpServers"]["zipsa"]["timeout"] >= 300_000


class TestBuildDockerArgv:
    def test_command_shape(self, tmp_path):
        staging = tmp_path / ".zipsa" / "staging" / "abc"
        staging.mkdir(parents=True)
        repo = tmp_path / "repo"
        repo.mkdir()
        mcpcfg = tmp_path / "mcp.json"
        mcpcfg.write_text("{}")

        argv = build_docker_argv(
            image="img:test",
            staging_path=staging,
            repo_root=repo,
            mcp_config_host=mcpcfg,
            prompt="do the thing",
            env_file=None,
        )

        assert argv[0:2] == ["docker", "run"]
        assert "--rm" in argv
        # headless: no stdin into the container (host stdin is the HITL
        # reader's); no TTY. stdout still streams via the subprocess.
        assert "-i" not in argv
        assert "-t" not in argv
        assert f"{staging}:{staging}:rw" in argv
        assert f"{repo}:{repo}:ro" in argv
        assert any(a.startswith(f"{mcpcfg}:") and a.endswith(":ro") for a in argv)
        assert "img:test" in argv
        # headless claude: claude -p "<prompt>"
        ci = argv.index("claude")
        assert argv[ci + 1] == "-p"
        assert argv[ci + 2] == "do the thing"
        assert "--mcp-config" in argv
        assert "--strict-mcp-config" in argv
        assert "bypassPermissions" in argv
        w = argv.index("-w")
        assert argv[w + 1] == str(repo)

    def test_env_file_added_when_present(self, tmp_path):
        staging = tmp_path / ".zipsa" / "staging" / "abc"
        staging.mkdir(parents=True)
        repo = tmp_path / "repo"
        repo.mkdir()
        mcpcfg = tmp_path / "mcp.json"
        mcpcfg.write_text("{}")
        env_file = tmp_path / ".env"
        env_file.write_text("CLAUDE_CODE_OAUTH_TOKEN=t\n")

        argv = build_docker_argv(
            image="i", staging_path=staging, repo_root=repo,
            mcp_config_host=mcpcfg, prompt="p", env_file=env_file,
        )

        i = argv.index("--env-file")
        assert argv[i + 1] == str(env_file)


class TestRunCreate:
    @patch("zipsa.create.subprocess.run")
    @patch("zipsa.create.CreateServer")
    def test_lifecycle_and_staging(self, mock_server_cls, mock_run, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        repo = _make_repo(tmp_path / "repo")
        server = MagicMock()
        server.port = 55555
        server.token = "tok"
        mock_server_cls.return_value = server
        mock_run.return_value.returncode = 0

        rc = run_create("make a thing", repo_root=repo, image="img:test")

        assert rc == 0
        server.start.assert_called_once()
        server.stop.assert_called_once()
        staging_root = tmp_path / "home" / "staging"
        made = [p for p in staging_root.iterdir() if p.is_dir()]
        assert len(made) == 1
        argv = mock_run.call_args.args[0]
        assert argv[0] == "docker"
        assert "img:test" in argv

    @patch("zipsa.create.subprocess.run")
    @patch("zipsa.create.CreateServer")
    def test_propagates_exit_code(self, mock_server_cls, mock_run, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        repo = _make_repo(tmp_path / "repo")
        server = MagicMock(); server.port = 1; server.token = "t"
        mock_server_cls.return_value = server
        mock_run.return_value.returncode = 7

        rc = run_create("x", repo_root=repo, image="i")
        assert rc == 7

    @patch("zipsa.create.subprocess.run")
    @patch("zipsa.create.CreateServer")
    def test_server_stopped_even_on_docker_error(self, mock_server_cls, mock_run, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        repo = _make_repo(tmp_path / "repo")
        server = MagicMock(); server.port = 1; server.token = "t"
        mock_server_cls.return_value = server
        mock_run.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            run_create("x", repo_root=repo, image="i")
        server.stop.assert_called_once()


class TestIsInteractive:
    def test_tty_is_interactive(self):
        from zipsa.create import _is_interactive

        class _TTY:
            def isatty(self): return True
        assert _is_interactive(_TTY()) is True

    def test_non_tty_not_interactive(self, monkeypatch):
        from zipsa.create import _is_interactive
        monkeypatch.delenv("ZIPSA_FORCE_INTERACTIVE", raising=False)

        class _Pipe:
            def isatty(self): return False
        assert _is_interactive(_Pipe()) is False

    def test_force_interactive_env_overrides(self, monkeypatch):
        from zipsa.create import _is_interactive
        monkeypatch.setenv("ZIPSA_FORCE_INTERACTIVE", "1")

        class _Pipe:
            def isatty(self): return False
        assert _is_interactive(_Pipe()) is True
