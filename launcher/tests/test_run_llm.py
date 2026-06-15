from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zipsa.run_llm import build_run_prompt, build_run_argv


def _skill(tmp_path):
    root = tmp_path / "weather"
    (root / "zipsa-dist").mkdir(parents=True)
    (root / "SKILL.md").write_text("# weather\nFetch then report. Call exec.\n")
    return root


class TestBuildRunPrompt:
    def test_includes_skill_md_and_run_protocol(self, tmp_path):
        root = _skill(tmp_path)
        p = build_run_prompt(root, user_input="Sydney")
        assert "Fetch then report" in p          # SKILL.md inlined
        assert "mcp__zipsa__exec" in p            # how to call scripts
        assert "Sydney" in p                      # the user input

    def test_prepends_intent_when_present(self, tmp_path):
        root = _skill(tmp_path)
        (root / "INTENT.md").write_text("Tell me if I need an umbrella.\n")
        p = build_run_prompt(root, user_input="")
        assert "umbrella" in p
        assert "Intent" in p


class TestBuildRunArgv:
    def test_mounts_skill_ro_and_wires_mcp(self, tmp_path):
        root = _skill(tmp_path)
        argv = build_run_argv(
            image="img", skill_root=root,
            mcp_config_host=tmp_path / "m.json", prompt="P", env_file=None,
        )
        assert argv[:3] == ["docker", "run", "--rm"]
        assert f"{root}:{root}:ro" in argv         # skill mounted read-only
        # the mcp-config file is actually mounted into the container
        assert f"{tmp_path / 'm.json'}:/tmp/zipsa-run-mcp.json:ro" in argv
        assert "--mcp-config" in argv
        assert "claude" in argv and "-p" in argv
        assert "bypassPermissions" in argv

    def test_env_file_added_when_given(self, tmp_path):
        root = _skill(tmp_path)
        ef = tmp_path / ".env"; ef.write_text("CLAUDE_CODE_OAUTH_TOKEN=t\n")
        argv = build_run_argv(
            image="img", skill_root=root,
            mcp_config_host=tmp_path / "m.json", prompt="P", env_file=ef,
        )
        i = argv.index("--env-file")
        assert argv[i + 1] == str(ef)


class TestRunSkillLlm:
    @patch("zipsa.run_llm.subprocess.run")
    @patch("zipsa.run_llm.RunServer")
    def test_starts_server_runs_container_stops_server(self, mock_server_cls, mock_run, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        root = tmp_path / "weather"; (root / "zipsa-dist").mkdir(parents=True)
        (root / "SKILL.md").write_text("# weather\n")
        srv = MagicMock(); srv.port = 51111; srv.token = "tok"
        mock_server_cls.return_value = srv
        mock_run.return_value.returncode = 0

        from zipsa.run_llm import run_skill_llm
        rc = run_skill_llm(root, "Sydney", image="img")

        assert rc == 0
        srv.start.assert_called_once()
        srv.stop.assert_called_once()                      # torn down even on success
        argv = mock_run.call_args.args[0]
        assert argv[:2] == ["docker", "run"]
        assert "claude" in argv

    @patch("zipsa.run_llm.subprocess.run", side_effect=RuntimeError("boom"))
    @patch("zipsa.run_llm.RunServer")
    def test_server_stopped_on_error(self, mock_server_cls, mock_run, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        root = tmp_path / "w"; (root / "zipsa-dist").mkdir(parents=True)
        (root / "SKILL.md").write_text("# w\n")
        srv = MagicMock(); srv.port = 51112; srv.token = "t"
        mock_server_cls.return_value = srv
        from zipsa.run_llm import run_skill_llm
        with pytest.raises(RuntimeError):
            run_skill_llm(root, "", image="img")
        srv.stop.assert_called_once()
