"""Tests for the `zipsa create` CLI command (Step 3).

`zipsa create [intent]` — intent optional. If absent, the host prompts
in English and reads stdin (seed only). No --into (naming happens at
promote, inside the session). Delegates to create.run_create.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from zipsa.cli import app

runner = CliRunner()


def _make_repo(root: Path) -> Path:
    sb = root / ".claude" / "skills" / "zipsa-skill-builder"
    sb.mkdir(parents=True)
    (sb / "SKILL.md").write_text("# zipsa-skill-builder\n")
    (root / "skills").mkdir()
    (root / "skills" / "AUTHORING.md").write_text("# authoring\n")
    return root


class TestCreateCommand:
    @patch("zipsa.create.run_create")
    def test_intent_arg_passed_through(self, mock_run, tmp_path, monkeypatch):
        root = _make_repo(tmp_path / "repo")
        monkeypatch.chdir(root)
        mock_run.return_value = 0

        result = runner.invoke(app, ["create", "8am umbrella alert"])

        assert result.exit_code == 0, result.output
        assert mock_run.call_args.args[0] == "8am umbrella alert"
        assert mock_run.call_args.kwargs["repo_root"] == root

    @patch("zipsa.create.run_create")
    def test_prompts_for_intent_when_absent(self, mock_run, tmp_path, monkeypatch):
        root = _make_repo(tmp_path / "repo")
        monkeypatch.chdir(root)
        mock_run.return_value = 0

        result = runner.invoke(app, ["create"], input="a telegram weather bot\n")

        assert result.exit_code == 0, result.output
        # English prompt shown to the user
        assert "what" in result.output.lower()
        # the typed seed reached run_create
        assert mock_run.call_args.args[0] == "a telegram weather bot"

    @patch("zipsa.create.run_create")
    def test_propagates_exit_code(self, mock_run, tmp_path, monkeypatch):
        root = _make_repo(tmp_path / "repo")
        monkeypatch.chdir(root)
        mock_run.return_value = 5

        result = runner.invoke(app, ["create", "x"])
        assert result.exit_code == 5

    def test_errors_outside_repo(self, tmp_path, monkeypatch):
        plain = tmp_path / "plain"
        plain.mkdir()
        monkeypatch.chdir(plain)

        result = runner.invoke(app, ["create", "x"])
        assert result.exit_code == 1
        assert "zipsa-skill-builder" in result.output

    @patch("zipsa.create.run_create", side_effect=FileNotFoundError)
    def test_docker_missing_message(self, mock_run, tmp_path, monkeypatch):
        root = _make_repo(tmp_path / "repo")
        monkeypatch.chdir(root)

        result = runner.invoke(app, ["create", "x"])
        assert result.exit_code == 1
        assert "docker" in result.output.lower()

    @patch("zipsa.create.run_create")
    def test_no_into_option(self, mock_run, tmp_path, monkeypatch):
        """--into was removed; passing it is an error."""
        root = _make_repo(tmp_path / "repo")
        monkeypatch.chdir(root)
        mock_run.return_value = 0

        result = runner.invoke(app, ["create", "x", "--into", "skills/foo"])
        assert result.exit_code != 0
