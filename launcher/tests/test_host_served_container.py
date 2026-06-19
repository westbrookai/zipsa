"""Tests for the shared host-served container core (#175)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from zipsa.host_served_container import (
    build_host_served_argv,
    build_mcp_config,
    _CONTAINER_MCP_CONFIG,
)


class TestBuildHostServedArgv:
    def test_ro_mount_and_mcp_wiring(self, tmp_path):
        wd = tmp_path / "skill"
        wd.mkdir()
        argv = build_host_served_argv(
            image="img", work_dir=wd, mode="ro",
            mcp_config_host=tmp_path / "m.json", prompt="P", env_file=None,
        )
        assert argv[:3] == ["docker", "run", "--rm"]
        assert f"{wd}:{wd}:ro" in argv
        assert f"{tmp_path / 'm.json'}:{_CONTAINER_MCP_CONFIG}:ro" in argv
        assert ["claude", "-p", "P"] == argv[argv.index("claude"):argv.index("claude") + 3]
        assert "--mcp-config" in argv and _CONTAINER_MCP_CONFIG in argv
        assert "--strict-mcp-config" in argv
        assert argv[-2:] == ["--permission-mode", "bypassPermissions"]
        assert argv[argv.index("-w") + 1] == str(wd)

    def test_rw_mount(self, tmp_path):
        wd = tmp_path / "staging"
        wd.mkdir()
        argv = build_host_served_argv(
            image="img", work_dir=wd, mode="rw",
            mcp_config_host=tmp_path / "m.json", prompt="P", env_file=None,
        )
        assert f"{wd}:{wd}:rw" in argv

    def test_env_file_added_when_given(self, tmp_path):
        wd = tmp_path / "skill"; wd.mkdir()
        ef = tmp_path / ".env"; ef.write_text("X=1\n")
        argv = build_host_served_argv(
            image="img", work_dir=wd, mode="ro",
            mcp_config_host=tmp_path / "m.json", prompt="P", env_file=ef,
        )
        assert "--env-file" in argv
        assert str(ef) in argv

    def test_extra_mounts_added_ro(self, tmp_path):
        wd = tmp_path / "skill"; wd.mkdir()
        creds = tmp_path / "creds"
        argv = build_host_served_argv(
            image="img", work_dir=wd, mode="ro",
            mcp_config_host=tmp_path / "m.json", prompt="P", env_file=None,
            extra_mounts=[(creds, "/run/creds")],
        )
        assert f"{creds}:/run/creds:ro" in argv

    @patch("zipsa.host_served_container.platform.system", return_value="Linux")
    def test_linux_adds_host_gateway(self, _mock_sys, tmp_path):
        wd = tmp_path / "skill"; wd.mkdir()
        argv = build_host_served_argv(
            image="img", work_dir=wd, mode="ro",
            mcp_config_host=tmp_path / "m.json", prompt="P", env_file=None,
        )
        assert "--add-host" in argv
        assert "host.docker.internal:host-gateway" in argv


class TestBuildMcpConfig:
    def test_embeds_port_and_token(self):
        cfg = build_mcp_config(51111, "tok")
        srv = cfg["mcpServers"]["zipsa"]
        assert "51111" in srv["url"]
        assert "tok" in srv["headersHelper"]


from unittest.mock import MagicMock


class TestRunHostServedContainer:
    def _common(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        ef = tmp_path / ".env"  # absent → env_file None branch
        return ef

    def test_dry_run_spawns_nothing_and_no_server(self, tmp_path, monkeypatch, capsys):
        from zipsa.host_served_container import run_host_served_container
        ef = self._common(tmp_path, monkeypatch)
        server_factory = MagicMock()
        execute = MagicMock()

        rc = run_host_served_container(
            image="img", env_file=ef,
            work_dir_factory=lambda dry: tmp_path / "wd",
            mode="rw", extra_mounts=None,
            server_factory=server_factory,
            prompt_factory=lambda wd: "PROMPT",
            execute=execute,
            mcp_subdir="staging",
            dry_run=True,
        )

        assert rc == 0
        server_factory.assert_not_called()
        execute.assert_not_called()
        out = capsys.readouterr().out
        assert "docker run" in out
        assert "PROMPT" in out
        assert ".mcp.json" in out
        # un-created work dir; single fixed config
        assert not (tmp_path / "wd").exists()
        cfgs = list((tmp_path / "home" / "staging").glob("*.mcp.json"))
        assert [c.name for c in cfgs] == ["dry-run.mcp.json"]

    def test_dry_run_config_is_single_fixed_file(self, tmp_path, monkeypatch):
        from zipsa.host_served_container import run_host_served_container
        ef = self._common(tmp_path, monkeypatch)
        for _ in range(2):
            run_host_served_container(
                image="img", env_file=ef,
                work_dir_factory=lambda dry: tmp_path / "wd",
                mode="ro", extra_mounts=None,
                server_factory=MagicMock(), prompt_factory=lambda wd: "P",
                execute=MagicMock(), mcp_subdir="run", dry_run=True,
            )
        cfgs = list((tmp_path / "home" / "run").glob("*.mcp.json"))
        assert len(cfgs) == 1

    def test_real_path_starts_stops_server_and_runs_execute(self, tmp_path, monkeypatch):
        from zipsa.host_served_container import run_host_served_container
        ef = self._common(tmp_path, monkeypatch)
        srv = MagicMock(); srv.port = 51120; srv.token = "tok"
        captured = {}
        def execute(argv):
            captured["argv"] = argv
            return 7

        rc = run_host_served_container(
            image="img", env_file=ef,
            work_dir_factory=lambda dry: tmp_path / "wd",
            mode="ro", extra_mounts=None,
            server_factory=lambda wd: srv,
            prompt_factory=lambda wd: "P",
            execute=execute, mcp_subdir="run", dry_run=False,
        )

        assert rc == 7
        srv.start.assert_called_once()
        srv.stop.assert_called_once()
        assert captured["argv"][:2] == ["docker", "run"]

    def test_real_path_stops_server_even_when_execute_raises(self, tmp_path, monkeypatch):
        from zipsa.host_served_container import run_host_served_container
        ef = self._common(tmp_path, monkeypatch)
        srv = MagicMock(); srv.port = 1; srv.token = "t"
        def boom(argv):
            raise RuntimeError("x")

        import pytest
        with pytest.raises(RuntimeError):
            run_host_served_container(
                image="img", env_file=ef,
                work_dir_factory=lambda dry: tmp_path / "wd",
                mode="ro", extra_mounts=None,
                server_factory=lambda wd: srv,
                prompt_factory=lambda wd: "P",
                execute=boom, mcp_subdir="run", dry_run=False,
            )
        srv.stop.assert_called_once()

    def test_real_path_unlinks_bearer_token_config(self, tmp_path, monkeypatch):
        from zipsa.host_served_container import run_host_served_container
        ef = self._common(tmp_path, monkeypatch)
        srv = MagicMock(); srv.port = 9; srv.token = "secret"

        run_host_served_container(
            image="img", env_file=ef,
            work_dir_factory=lambda dry: tmp_path / "wd",
            mode="ro", extra_mounts=None,
            server_factory=lambda wd: srv,
            prompt_factory=lambda wd: "P",
            execute=lambda argv: 0, mcp_subdir="run", dry_run=False,
        )
        # the bearer-token config must NOT persist after a real run
        assert not list((tmp_path / "home" / "run").glob("run-*.mcp.json"))
