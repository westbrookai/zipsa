"""Tests for the `zipsa create` CLI command (Step 3).

`zipsa create [intent] [--image] [--skills-dir]` — intent optional
(host prompts in English if absent). No repo dependency; promote lands
in --skills-dir (default ./skills).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from zipsa.cli import app

runner = CliRunner()


class TestCreateCommand:
    @patch("zipsa.create.run_create")
    def test_intent_and_default_skills_dir(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.return_value = 0

        result = runner.invoke(app, ["create", "8am umbrella alert"])

        assert result.exit_code == 0, result.output
        assert mock_run.call_args.args[0] == "8am umbrella alert"
        assert mock_run.call_args.kwargs["skills_dir"] == (tmp_path / "skills").resolve()

    @patch("zipsa.create.run_create")
    def test_skills_dir_option(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.return_value = 0

        runner.invoke(app, ["create", "x", "--skills-dir", "mylib"])

        assert mock_run.call_args.kwargs["skills_dir"] == (tmp_path / "mylib").resolve()

    @patch("zipsa.create.run_create")
    def test_prompts_for_intent_when_absent(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.return_value = 0

        result = runner.invoke(app, ["create"], input="a telegram weather bot\n")

        assert result.exit_code == 0, result.output
        assert "what" in result.output.lower()
        assert mock_run.call_args.args[0] == "a telegram weather bot"

    @patch("zipsa.create.run_create")
    def test_propagates_exit_code(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.return_value = 5
        assert runner.invoke(app, ["create", "x"]).exit_code == 5

    @patch("zipsa.create.run_create", side_effect=FileNotFoundError)
    def test_docker_missing_message(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["create", "x"])
        assert result.exit_code == 1
        assert "docker" in result.output.lower()

    @patch("zipsa.create.run_create")
    def test_no_into_option(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_run.return_value = 0
        result = runner.invoke(app, ["create", "x", "--into", "skills/foo"])
        assert result.exit_code != 0
