"""Phase 0 deterministic phase runner.

Runs a single phase file as a subprocess, language picked by file
extension. The phase receives `{"ctx": {...}}` as one JSON line on
stdin and reports its result as the last JSON-object line on stdout
(earlier lines are logs).

Design decisions:
- stdin/stdout JSON instead of env vars: language-independent, no
  env-size limits, trivially testable.
- Extension dispatch table instead of shebang sniffing: predictable
  cross-platform, no chmod required on phase files.
- The last stdout line must parse as a JSON *object* to count as the
  result; arrays and bare values are treated as logs. A phase that
  prints no JSON object yields `result=None` (still a success if exit
  code is 0).

Gotchas:
- `.md` files are LLM phases — refused here with a distinct error so
  callers can explain "Phase 0 doesn't run LLM phases" rather than
  "unknown extension".
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

# ext → argv prefix. The phase file path is appended as the last arg.
# Keep in sync with phase_discovery.PHASE_EXTENSIONS (minus "md").
RUNNERS: dict[str, list[str]] = {
    "py": ["python"],
    "sh": ["bash"],
    "js": ["node"],
    "ts": ["npx", "tsx"],
    "go": ["go", "run"],
}


@dataclass(frozen=True)
class ExecResult:
    """Outcome of one phase execution."""

    skill_name: str
    result: dict | None
    exit_code: int
    duration_ms: int
    stdout: str
    stderr: str


class ExecRunnerError(Exception):
    """Raised when the phase cannot be started at all (missing file,
    unsupported extension). Phase *failures* (non-zero exit) are not
    errors — they come back in ExecResult.exit_code.
    """


def _parse_result(stdout: str) -> dict | None:
    """Return the last stdout line that parses as a JSON object."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def run_phase(
    phase_path: Path,
    *,
    skill_name: str,
    user_query: str = "",
    timeout_seconds: int = 600,
) -> ExecResult:
    """Execute one phase file and return its outcome.

    Raises ExecRunnerError if the phase can't be started (file missing,
    extension unsupported, or `.md` LLM phase). A phase that starts but
    exits non-zero is reported in ExecResult, not raised.
    """
    # Resolve before building argv: the subprocess runs with cwd set to
    # the phase's directory, which would break a relative phase path.
    phase_path = phase_path.resolve()
    if not phase_path.is_file():
        raise ExecRunnerError(f"phase file not found: {phase_path}")

    ext = phase_path.suffix.lstrip(".")
    if ext == "md":
        raise ExecRunnerError(
            f"{phase_path.name}: LLM phases (.md) are not supported by "
            "deterministic exec — Phase 0 runs code phases only"
        )
    if ext not in RUNNERS:
        raise ExecRunnerError(
            f"{phase_path.name}: no runner for .{ext} "
            f"(supported: {', '.join('.' + e for e in sorted(RUNNERS))})"
        )

    ctx = {"skill_name": skill_name, "user_query": user_query}
    stdin_payload = json.dumps({"ctx": ctx}) + "\n"

    started = time.monotonic()
    proc = subprocess.run(
        [*RUNNERS[ext], str(phase_path)],
        input=stdin_payload,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        cwd=phase_path.parent,
    )
    duration_ms = int((time.monotonic() - started) * 1000)

    result = _parse_result(proc.stdout) if proc.returncode == 0 else None

    return ExecResult(
        skill_name=skill_name,
        result=result,
        exit_code=proc.returncode,
        duration_ms=duration_ms,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
