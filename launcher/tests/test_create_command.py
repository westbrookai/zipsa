"""Tests for the `zipsa create` and `zipsa forge` CLI commands.

`zipsa create [intent] [--image] [--skills-dir]` — intent optional
(host prompts in English if absent). No repo dependency; promote lands
in --skills-dir.

Default --skills-dir is now repo-aware (default_forge_skills_dir()), not
cwd-relative. Both `create` and `forge` delegate to `run_forge`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from zipsa.cli import app

runner = CliRunner()


class TestCreateCommand:
    @patch("zipsa.cli.run_forge")
    @patch("zipsa.cli.default_forge_skills_dir")
    def test_no_skills_dir_uses_default_forge_skills_dir(
        self, mock_default, mock_run, tmp_path
    ):
        """create with no --skills-dir calls run_forge with default_forge_skills_dir()."""
        expected = tmp_path / "skills"
        mock_default.return_value = expected
        mock_run.return_value = 0

        result = runner.invoke(app, ["create", "8am umbrella alert"])

        assert result.exit_code == 0, result.output
        assert mock_run.call_args.args[0] == "8am umbrella alert"
        assert mock_run.call_args.kwargs["skills_dir"] == expected
        mock_default.assert_called_once()

    @patch("zipsa.cli.run_forge")
    @patch("zipsa.cli.default_forge_skills_dir")
    def test_explicit_skills_dir_resolves_without_calling_default(
        self, mock_default, mock_run, tmp_path, monkeypatch
    ):
        """create with --skills-dir resolves that path; default helper NOT consulted."""
        monkeypatch.chdir(tmp_path)
        mock_run.return_value = 0

        runner.invoke(app, ["create", "x", "--skills-dir", str(tmp_path / "mylib")])

        assert mock_run.call_args.kwargs["skills_dir"] == (tmp_path / "mylib").resolve()
        mock_default.assert_not_called()

    @patch("zipsa.cli.run_forge")
    @patch("zipsa.cli.default_forge_skills_dir")
    def test_prompts_for_intent_when_absent(self, mock_default, mock_run, tmp_path):
        mock_default.return_value = tmp_path / "skills"
        mock_run.return_value = 0

        result = runner.invoke(app, ["create"], input="a telegram weather bot\n")

        assert result.exit_code == 0, result.output
        assert "what" in result.output.lower()
        assert mock_run.call_args.args[0] == "a telegram weather bot"

    @patch("zipsa.cli.run_forge")
    @patch("zipsa.cli.default_forge_skills_dir")
    def test_propagates_exit_code(self, mock_default, mock_run, tmp_path):
        mock_default.return_value = tmp_path / "skills"
        mock_run.return_value = 5
        assert runner.invoke(app, ["create", "x"]).exit_code == 5

    @patch("zipsa.cli.run_forge", side_effect=FileNotFoundError)
    @patch("zipsa.cli.default_forge_skills_dir")
    def test_docker_missing_message(self, mock_default, mock_run, tmp_path):
        mock_default.return_value = tmp_path / "skills"
        result = runner.invoke(app, ["create", "x"])
        assert result.exit_code == 1
        assert "docker" in result.output.lower()

    @patch("zipsa.cli.run_forge")
    def test_no_into_option(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.return_value = 0
        result = runner.invoke(app, ["create", "x", "--into", "skills/foo"])
        assert result.exit_code != 0


class TestForgeCommand:
    @patch("zipsa.cli.run_forge")
    @patch("zipsa.cli.default_forge_skills_dir")
    def test_no_skills_dir_uses_default_forge_skills_dir(
        self, mock_default, mock_run, tmp_path
    ):
        """forge with no --skills-dir calls run_forge with default_forge_skills_dir()."""
        expected = tmp_path / "skills"
        mock_default.return_value = expected
        mock_run.return_value = 0

        result = runner.invoke(app, ["forge", "build a daily digest"])

        assert result.exit_code == 0, result.output
        assert mock_run.call_args.args[0] == "build a daily digest"
        assert mock_run.call_args.kwargs["skills_dir"] == expected
        mock_default.assert_called_once()

    @patch("zipsa.cli.run_forge")
    @patch("zipsa.cli.default_forge_skills_dir")
    def test_explicit_skills_dir_resolves_without_calling_default(
        self, mock_default, mock_run, tmp_path, monkeypatch
    ):
        """forge with --skills-dir resolves that path; default helper NOT consulted."""
        monkeypatch.chdir(tmp_path)
        mock_run.return_value = 0
        explicit = tmp_path / "custom-skills"

        runner.invoke(app, ["forge", "x", "--skills-dir", str(explicit)])

        assert mock_run.call_args.kwargs["skills_dir"] == explicit.resolve()
        mock_default.assert_not_called()

    @patch("zipsa.cli.default_forge_skills_dir")
    @patch("zipsa.create.subprocess.run")
    @patch("zipsa.create.ForgeServer")
    def test_forge_dry_run_prints_and_runs_nothing(
        self, mock_forge_cls, mock_run, mock_default, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        mock_default.return_value = tmp_path / "skills"
        srv = MagicMock(); srv.port = 5; srv.token = "t"
        mock_forge_cls.return_value = srv

        result = runner.invoke(app, ["forge", "make a thing", "--dry-run"])

        assert result.exit_code == 0, result.output
        mock_run.assert_not_called()
        srv.start.assert_not_called()
        assert "docker run" in result.output
