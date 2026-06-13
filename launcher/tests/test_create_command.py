"""Tests for the `zipsa create` CLI command."""

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
    @patch("zipsa.create.subprocess.run")
    def test_resolves_into_under_root_and_runs(self, mock_run, tmp_path, monkeypatch):
        root = _make_repo(tmp_path / "repo")
        monkeypatch.chdir(root)
        mock_run.return_value.returncode = 0

        result = runner.invoke(
            app, ["create", "8am umbrella alert", "--into", "skills/umbrella"],
        )

        assert result.exit_code == 0, result.output
        argv = mock_run.call_args.args[0]
        assert argv[0] == "claude"
        # The target dir was created and referenced in the prompt
        assert (root / "skills" / "umbrella").is_dir()
        assert any("skills/umbrella" in a for a in argv)
        assert mock_run.call_args.kwargs["cwd"] == root

    @patch("zipsa.create.subprocess.run")
    def test_propagates_exit_code(self, mock_run, tmp_path, monkeypatch):
        root = _make_repo(tmp_path / "repo")
        monkeypatch.chdir(root)
        mock_run.return_value.returncode = 3

        result = runner.invoke(
            app, ["create", "x", "--into", "skills/foo"],
        )

        assert result.exit_code == 3

    def test_errors_outside_repo(self, tmp_path, monkeypatch):
        plain = tmp_path / "plain"
        plain.mkdir()
        monkeypatch.chdir(plain)

        result = runner.invoke(
            app, ["create", "x", "--into", "skills/foo"],
        )

        assert result.exit_code == 1
        assert "zipsa-skill-builder" in result.output

    @patch("zipsa.create.subprocess.run")
    def test_claude_not_installed_message(self, mock_run, tmp_path, monkeypatch):
        root = _make_repo(tmp_path / "repo")
        monkeypatch.chdir(root)
        mock_run.side_effect = FileNotFoundError

        result = runner.invoke(
            app, ["create", "x", "--into", "skills/foo"],
        )

        assert result.exit_code == 1
        assert "claude" in result.output.lower()
