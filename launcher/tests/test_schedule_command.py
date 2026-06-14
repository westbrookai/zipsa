"""Tests for the `zipsa schedule` CLI (add / list / remove)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from zipsa.cli import app
from zipsa.scheduling import ScheduledJob

runner = CliRunner()


class TestScheduleAdd:
    @patch("zipsa.cli.get_scheduler")
    def test_add_derives_label_from_skill_name(self, mock_get, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        skill = tmp_path / "skills" / "umbrella"
        skill.mkdir(parents=True)
        sched = MagicMock()
        sched.add.return_value = "umbrella"
        mock_get.return_value = sched

        result = runner.invoke(app, [
            "schedule", "add", "skills/umbrella",
            "--cron", "0 8 * * *",
            "--mount", "~/.zipsa/credentials/telegram.json:/mnt/creds/telegram.json",
        ])

        assert result.exit_code == 0, result.output
        kwargs = sched.add.call_args.kwargs
        # label derived from the skill dir name — user never typed it
        assert kwargs["label"] == "umbrella"
        assert kwargs["cron"] == "0 8 * * *"
        cmd = kwargs["command"]
        assert cmd[1] == "exec"
        assert str(skill.resolve()) in cmd
        assert "--mount" in cmd
        assert "umbrella" in result.output

    @patch("zipsa.cli.get_scheduler")
    def test_add_reports_auto_numbered_label(self, mock_get, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "skills" / "umbrella").mkdir(parents=True)
        sched = MagicMock()
        sched.add.return_value = "umbrella-2"   # already one registered
        mock_get.return_value = sched

        result = runner.invoke(app, [
            "schedule", "add", "skills/umbrella", "--cron", "0 8 * * 6",
        ])
        assert result.exit_code == 0, result.output
        assert "umbrella-2" in result.output

    @patch("zipsa.cli.get_scheduler")
    def test_add_rejects_bad_cron(self, mock_get, tmp_path, monkeypatch):
        from zipsa.scheduling import CronError
        monkeypatch.chdir(tmp_path)
        (tmp_path / "s").mkdir()
        sched = MagicMock()
        sched.add.side_effect = CronError("day-of-month must be *")
        mock_get.return_value = sched

        result = runner.invoke(app, [
            "schedule", "add", "s", "--cron", "0 8 1 * *",
        ])
        assert result.exit_code == 1
        assert "cron" in result.output.lower() or "day-of-month" in result.output


class TestScheduleList:
    @patch("zipsa.cli.get_scheduler")
    def test_list(self, mock_get):
        sched = MagicMock()
        sched.list.return_value = [
            ScheduledJob(label="umbrella", command=["zipsa", "exec", "/x"],
                         schedule="daily 08:00"),
        ]
        mock_get.return_value = sched

        result = runner.invoke(app, ["schedule", "list"])
        assert result.exit_code == 0
        assert "umbrella" in result.output
        assert "daily 08:00" in result.output  # WHEN it runs is shown


class TestScheduleRemove:
    @patch("zipsa.cli.get_scheduler")
    def test_remove(self, mock_get):
        sched = MagicMock()
        sched.remove.return_value = True
        mock_get.return_value = sched

        result = runner.invoke(app, ["schedule", "remove", "umbrella"])
        assert result.exit_code == 0
        sched.remove.assert_called_once_with("umbrella")

    @patch("zipsa.cli.get_scheduler")
    def test_remove_missing(self, mock_get):
        sched = MagicMock()
        sched.remove.return_value = False
        mock_get.return_value = sched

        result = runner.invoke(app, ["schedule", "remove", "nope"])
        assert result.exit_code == 1
        assert "no" in result.output.lower() or "not" in result.output.lower()


class TestScheduleUnavailable:
    @patch("zipsa.cli.get_scheduler")
    def test_backend_unavailable_message(self, mock_get, tmp_path, monkeypatch):
        from zipsa.scheduling import SchedulerUnavailable
        monkeypatch.chdir(tmp_path)
        (tmp_path / "s").mkdir()
        mock_get.side_effect = SchedulerUnavailable("no backend for Windows yet")

        result = runner.invoke(app, [
            "schedule", "add", "s", "--cron", "0 8 * * *",
        ])
        assert result.exit_code == 1
        assert "backend" in result.output.lower()
