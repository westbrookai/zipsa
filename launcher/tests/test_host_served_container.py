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
