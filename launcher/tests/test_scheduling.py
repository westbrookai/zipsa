"""Tests for zipsa scheduling — cron→launchd translation + LaunchdScheduler.

v1 backend is macOS launchd (the user's platform + the live umbrella use
case). The interface is OS-agnostic so crontab/schtasks slot in later.
"""

from __future__ import annotations

import plistlib
from pathlib import Path
from unittest.mock import patch

import pytest

from zipsa.scheduling import (
    CronError,
    LaunchdScheduler,
    ScheduledJob,
    build_exec_command,
    cron_to_launchd_intervals,
    describe_intervals,
    resolve_zipsa_command,
)


class TestDescribeIntervals:
    def test_daily_at_time(self):
        assert describe_intervals([{"Minute": 0, "Hour": 8}]) == "daily 08:00"

    def test_minute_only_every_hour(self):
        assert describe_intervals([{"Minute": 30}]) == "hourly at :30"

    def test_weekdays(self):
        out = describe_intervals(
            [{"Minute": 0, "Hour": 8, "Weekday": d} for d in range(1, 6)]
        )
        assert "08:00" in out
        assert "Mon" in out and "Fri" in out

    def test_single_weekday(self):
        out = describe_intervals([{"Minute": 0, "Hour": 9, "Weekday": 6}])
        assert "Sat" in out and "09:00" in out


class TestResolveZipsaCommand:
    """The scheduled command's program must be ABSOLUTE — launchd/cron
    do not resolve ProgramArguments[0] via PATH (bare 'zipsa' → the job
    fails to exec, e.g. launchd exit 78)."""

    def test_uses_absolute_path_when_on_path(self, monkeypatch):
        monkeypatch.setattr(
            "zipsa.scheduling.shutil.which", lambda _: "/opt/homebrew/bin/zipsa"
        )
        assert resolve_zipsa_command() == ["/opt/homebrew/bin/zipsa"]

    def test_falls_back_to_interpreter_m_zipsa(self, monkeypatch):
        monkeypatch.setattr("zipsa.scheduling.shutil.which", lambda _: None)
        cmd = resolve_zipsa_command()
        assert cmd[1:] == ["-m", "zipsa"]
        assert Path(cmd[0]).is_absolute()

    def test_never_bare_zipsa(self, monkeypatch):
        """Regression: a bare 'zipsa' baked into a launchd plist made the
        job exit 78 (could not exec). The program must be absolute."""
        monkeypatch.setattr(
            "zipsa.scheduling.shutil.which", lambda _: "/abs/zipsa"
        )
        assert resolve_zipsa_command()[0] != "zipsa"
        assert Path(resolve_zipsa_command()[0]).is_absolute()


class TestCronToLaunchd:
    def test_daily_at_time(self):
        assert cron_to_launchd_intervals("0 8 * * *") == [{"Minute": 0, "Hour": 8}]

    def test_minute_only_every_hour(self):
        assert cron_to_launchd_intervals("30 * * * *") == [{"Minute": 30}]

    def test_weekdays(self):
        out = cron_to_launchd_intervals("0 8 * * 1-5")
        assert out == [{"Minute": 0, "Hour": 8, "Weekday": d} for d in range(1, 6)]

    def test_dow_list(self):
        out = cron_to_launchd_intervals("0 9 * * 1,3,5")
        assert out == [{"Minute": 0, "Hour": 9, "Weekday": d} for d in (1, 3, 5)]

    def test_minute_step(self):
        out = cron_to_launchd_intervals("*/30 * * * *")
        assert out == [{"Minute": 0}, {"Minute": 30}]

    def test_rejects_unsupported_dom(self):
        with pytest.raises(CronError):
            cron_to_launchd_intervals("0 8 1 * *")  # day-of-month not in v1

    def test_rejects_bad_field_count(self):
        with pytest.raises(CronError):
            cron_to_launchd_intervals("0 8 * *")

    def test_rejects_out_of_range(self):
        with pytest.raises(CronError):
            cron_to_launchd_intervals("0 25 * * *")


class TestBuildExecCommand:
    def test_basic(self):
        cmd = build_exec_command(
            zipsa=["zipsa"], skill_path=Path("/repo/skills/x"),
            mounts=["~/.zipsa/creds.json:/mnt/c.json"], query="Seoul",
        )
        assert cmd[:2] == ["zipsa", "exec"]
        assert "/repo/skills/x" in cmd
        assert "Seoul" in cmd
        assert "--mount" in cmd
        assert "~/.zipsa/creds.json:/mnt/c.json" in cmd

    def test_no_query_no_mounts(self):
        cmd = build_exec_command(
            zipsa=["zipsa"], skill_path=Path("/x"), mounts=[], query=None,
        )
        assert cmd == ["zipsa", "exec", "/x"]

    def test_with_timeout(self):
        cmd = build_exec_command(
            zipsa=["zipsa"], skill_path=Path("/x"), mounts=[], query=None,
            timeout=1500,
        )
        assert "--timeout" in cmd
        assert "1500" in cmd

    def test_without_timeout_no_flag(self):
        cmd = build_exec_command(
            zipsa=["zipsa"], skill_path=Path("/x"), mounts=[], query=None,
        )
        assert "--timeout" not in cmd


class TestLaunchdScheduler:
    def _sched(self, tmp_path):
        return LaunchdScheduler(agents_dir=tmp_path / "LaunchAgents")

    @patch("zipsa.scheduling.subprocess.run")
    def test_add_writes_plist_and_loads(self, mock_run, tmp_path):
        mock_run.return_value.returncode = 0
        s = self._sched(tmp_path)

        label = s.add(
            label="umbrella",
            cron="0 8 * * *",
            command=["zipsa", "exec", "/skills/umbrella", "--mount", "x:y"],
        )

        assert label == "umbrella"
        plist = tmp_path / "LaunchAgents" / "com.zipsa.umbrella.plist"
        assert plist.exists()
        data = plistlib.loads(plist.read_bytes())
        assert data["Label"] == "com.zipsa.umbrella"
        assert data["ProgramArguments"] == [
            "zipsa", "exec", "/skills/umbrella", "--mount", "x:y",
        ]
        assert data["StartCalendarInterval"] == {"Minute": 0, "Hour": 8}
        # PATH baked in so docker/zipsa resolve under launchd's minimal env
        assert "PATH" in data["EnvironmentVariables"]
        # launchctl load called with the plist
        assert any(
            "launchctl" in c.args[0][0] and str(plist) in c.args[0]
            for c in mock_run.call_args_list
        )

    @patch("zipsa.scheduling.subprocess.run")
    def test_add_array_interval_for_weekdays(self, mock_run, tmp_path):
        mock_run.return_value.returncode = 0
        s = self._sched(tmp_path)
        s.add(label="wd", cron="0 8 * * 1-5", command=["zipsa", "exec", "/x"])
        data = plistlib.loads(
            (tmp_path / "LaunchAgents" / "com.zipsa.wd.plist").read_bytes()
        )
        assert isinstance(data["StartCalendarInterval"], list)
        assert len(data["StartCalendarInterval"]) == 5

    @patch("zipsa.scheduling.subprocess.run")
    def test_add_same_skill_twice_auto_numbers(self, mock_run, tmp_path):
        mock_run.return_value.returncode = 0
        s = self._sched(tmp_path)

        first = s.add(label="umbrella", cron="0 8 * * *", command=["zipsa", "exec", "/x"])
        second = s.add(label="umbrella", cron="0 8 * * 6", command=["zipsa", "exec", "/x"])

        assert first == "umbrella"
        assert second == "umbrella-2"
        assert (tmp_path / "LaunchAgents" / "com.zipsa.umbrella.plist").exists()
        assert (tmp_path / "LaunchAgents" / "com.zipsa.umbrella-2.plist").exists()

    @patch("zipsa.scheduling.subprocess.run")
    def test_list(self, mock_run, tmp_path):
        mock_run.return_value.returncode = 0
        s = self._sched(tmp_path)
        s.add(label="a", cron="0 8 * * *", command=["zipsa", "exec", "/a"])
        s.add(label="b", cron="30 * * * *", command=["zipsa", "exec", "/b"])

        jobs = s.list()

        labels = {j.label for j in jobs}
        assert labels == {"a", "b"}
        assert all(isinstance(j, ScheduledJob) for j in jobs)
        a = next(j for j in jobs if j.label == "a")
        assert a.command == ["zipsa", "exec", "/a"]
        # list now carries WHEN it runs
        assert a.schedule == "daily 08:00"
        b = next(j for j in jobs if j.label == "b")
        assert b.schedule == "hourly at :30"

    @patch("zipsa.scheduling.subprocess.run")
    def test_remove(self, mock_run, tmp_path):
        mock_run.return_value.returncode = 0
        s = self._sched(tmp_path)
        s.add(label="a", cron="0 8 * * *", command=["zipsa", "exec", "/a"])
        plist = tmp_path / "LaunchAgents" / "com.zipsa.a.plist"
        assert plist.exists()

        removed = s.remove("a")

        assert removed is True
        assert not plist.exists()
        # bootout/unload attempted
        assert any("launchctl" in c.args[0][0] for c in mock_run.call_args_list)

    @patch("zipsa.scheduling.subprocess.run")
    def test_remove_missing_returns_false(self, mock_run, tmp_path):
        mock_run.return_value.returncode = 0
        s = self._sched(tmp_path)
        assert s.remove("nope") is False
