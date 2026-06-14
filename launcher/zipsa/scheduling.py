"""Host-side scheduling for zipsa skills — run `zipsa exec` on a cron.

Scheduling is the OS's job (a skill stays schedule-agnostic). The CLI
interface (`zipsa schedule add/list/remove`) is OS-agnostic; the backend
is per-OS and pluggable. v1 ships the macOS launchd backend — it runs a
missed daily job after wake and is the supported macOS mechanism (cron
on modern macOS is deprecated + skips jobs during sleep). crontab
(Linux) and schtasks (Windows) slot in behind the same interface later.

cron syntax is the user-facing schedule format (the lingua franca);
each backend translates it. v1 supports the practical subset: minute &
hour as int / `*` / `*/N`, day-of-week as int / `*` / range / list;
day-of-month and month must be `*`.
"""

from __future__ import annotations

import platform
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


def resolve_zipsa_command() -> list[str]:
    """The `zipsa` invocation to bake into a scheduled job.

    Must be an ABSOLUTE program — launchd (and cron) do not resolve
    ProgramArguments[0] via PATH. Prefer the absolute path of a `zipsa`
    on PATH (brew / pip / the active venv's console script); fall back
    to this interpreter's `-m zipsa` (also absolute) for dev runs.
    """
    found = shutil.which("zipsa")
    if found:
        return [found]
    return [sys.executable, "-m", "zipsa"]


class CronError(Exception):
    """The cron expression is invalid or uses an unsupported feature."""


class SchedulerUnavailable(Exception):
    """No scheduling backend for this OS yet."""


@dataclass(frozen=True)
class ScheduledJob:
    label: str
    command: list[str]


def _parse_field(field: str, lo: int, hi: int) -> "list[int] | None":
    """Parse one cron field. Returns None for `*` (any), else the sorted
    list of explicit values. Supports int, `*`, `*/N`, `a-b`, `a,b,c`."""
    if field == "*":
        return None
    if field.startswith("*/"):
        step = int(field[2:])
        if step <= 0:
            raise CronError(f"bad step: {field}")
        return list(range(lo, hi + 1, step))
    values: list[int] = []
    for part in field.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            values.extend(range(int(a), int(b) + 1))
        else:
            values.append(int(part))
    for v in values:
        if not (lo <= v <= hi):
            raise CronError(f"value {v} out of range {lo}-{hi}")
    return sorted(dict.fromkeys(values))


def cron_to_launchd_intervals(expr: str) -> list[dict]:
    """Translate a cron expression to launchd StartCalendarInterval
    dicts (one dict = one fire time; multiple = an array). Raises
    CronError on unsupported forms (day-of-month / month other than `*`).
    """
    fields = expr.split()
    if len(fields) != 5:
        raise CronError(
            f"expected 5 cron fields (min hour dom month dow), got {len(fields)}"
        )
    minute_f, hour_f, dom_f, month_f, dow_f = fields

    if dom_f != "*" or month_f != "*":
        raise CronError(
            "day-of-month and month must be '*' in this version "
            "(supported: minute, hour, day-of-week)"
        )

    try:
        minutes = _parse_field(minute_f, 0, 59)
        hours = _parse_field(hour_f, 0, 23)
        weekdays = _parse_field(dow_f, 0, 7)
    except ValueError as e:
        raise CronError(f"invalid cron field: {e}") from e

    # Build the cartesian product across the constrained fields. A field
    # left as `*` (None) just omits its key (launchd treats a missing key
    # as "any"), unless every field is None (which cron would mean "every
    # minute" — launchd can't express that as an interval).
    minute_vals = minutes if minutes is not None else [None]
    hour_vals = hours if hours is not None else [None]
    weekday_vals = weekdays if weekdays is not None else [None]

    if minutes is None and hours is None and weekdays is None:
        raise CronError("every-minute schedules are not supported")

    intervals: list[dict] = []
    for wd in weekday_vals:
        for h in hour_vals:
            for m in minute_vals:
                d: dict = {}
                if m is not None:
                    d["Minute"] = m
                if h is not None:
                    d["Hour"] = h
                if wd is not None:
                    d["Weekday"] = wd
                intervals.append(d)
    return intervals


def build_exec_command(
    *,
    zipsa: list[str],
    skill_path: Path,
    mounts: list[str],
    query: "str | None",
) -> list[str]:
    """Build the `zipsa exec ...` argv a schedule fires."""
    cmd = [*zipsa, "exec", str(skill_path)]
    if query:
        cmd.append(query)
    for m in mounts:
        cmd += ["--mount", m]
    return cmd


class LaunchdScheduler:
    """macOS launchd backend. One LaunchAgent plist per scheduled job,
    labeled com.zipsa.<label>; the plist dir is the source of truth."""

    _PREFIX = "com.zipsa."

    def __init__(self, agents_dir: "Path | None" = None) -> None:
        self._dir = agents_dir or (Path.home() / "Library" / "LaunchAgents")

    def _plist_path(self, label: str) -> Path:
        return self._dir / f"{self._PREFIX}{label}.plist"

    def _unique_label(self, base: str) -> str:
        """Return `base`, or `base-2`/`-3`/… if a job already uses it.

        The user never names a schedule — it's derived from the skill;
        scheduling the same skill again just gets the next number."""
        if not self._plist_path(base).exists():
            return base
        n = 2
        while self._plist_path(f"{base}-{n}").exists():
            n += 1
        return f"{base}-{n}"

    def add(self, *, label: str, cron: str, command: list[str]) -> str:
        """Register a job. `label` is the desired base (the skill name);
        the actual label (deduped) is returned. Raises CronError on a
        bad cron before touching the filesystem."""
        interval = cron_to_launchd_intervals(cron)
        label = self._unique_label(label)
        full_label = f"{self._PREFIX}{label}"
        log_dir = Path.home() / ".zipsa" / "schedule-logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        import os
        plist = {
            "Label": full_label,
            "ProgramArguments": command,
            "StartCalendarInterval": interval[0] if len(interval) == 1 else interval,
            "RunAtLoad": False,
            # launchd runs with a minimal env; bake the current PATH so
            # docker + zipsa resolve when the job fires.
            "EnvironmentVariables": {"PATH": os.environ.get("PATH", "")},
            "StandardOutPath": str(log_dir / f"{label}.out.log"),
            "StandardErrorPath": str(log_dir / f"{label}.err.log"),
        }
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._plist_path(label)
        path.write_bytes(plistlib.dumps(plist))

        # Reload: unload any prior version, then load.
        subprocess.run(["launchctl", "unload", str(path)],
                       capture_output=True)
        subprocess.run(["launchctl", "load", str(path)],
                       capture_output=True)
        return label

    def list(self) -> list[ScheduledJob]:
        jobs: list[ScheduledJob] = []
        if not self._dir.is_dir():
            return jobs
        for p in sorted(self._dir.glob(f"{self._PREFIX}*.plist")):
            data = plistlib.loads(p.read_bytes())
            label = data.get("Label", p.stem)[len(self._PREFIX):]
            jobs.append(ScheduledJob(
                label=label, command=list(data.get("ProgramArguments", [])),
            ))
        return jobs

    def remove(self, label: str) -> bool:
        path = self._plist_path(label)
        if not path.exists():
            return False
        subprocess.run(["launchctl", "unload", str(path)],
                       capture_output=True)
        path.unlink()
        return True


def get_scheduler():
    """Return the scheduler backend for this OS, or raise
    SchedulerUnavailable with a clear message."""
    system = platform.system()
    if system == "Darwin":
        return LaunchdScheduler()
    raise SchedulerUnavailable(
        f"no zipsa schedule backend for {system} yet "
        "(macOS launchd is implemented; crontab/schtasks are follow-ups)"
    )
