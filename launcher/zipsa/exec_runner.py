"""Phase 0 deterministic phase runner.

Runs a single phase file as a subprocess — by default inside the zipsa
runtime Docker container, or directly on the host in local mode. The
language is picked by file extension. The phase receives
`{"ctx": {...}}` as one JSON line on stdin and reports its result as
the last JSON-object line on stdout (earlier lines are logs).

Design decisions:
- Docker by default: production behavior (isolation, reproducible env)
  is the default; `--local` is the authoring-speed escape hatch. The
  phase contract is identical in both modes.
- stdin/stdout JSON instead of env vars: language-independent, no
  env-size limits, trivially testable.
- Extension dispatch table instead of shebang sniffing: predictable
  cross-platform, no chmod required on phase files.
- The last stdout line must parse as a JSON *object* to count as the
  result; arrays and bare values are treated as logs. A phase that
  prints no JSON object yields `result=None` (still a success if exit
  code is 0).
- exec builds its own minimal docker argv — this is NOT
  DockerExecutor's LLM-centric run path (no hooks, no .claude.json,
  no env-file).

Gotchas:
- `.md` files are LLM phases — refused here with a distinct error so
  callers can explain "exec doesn't run LLM phases" rather than
  "unknown extension".
- The runtime image runs as user `agent` (uid 1000). On Linux hosts a
  mounted /out owned by another uid may not be writable; macOS Docker
  Desktop handles this transparently (primary dev platform).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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

_CONTAINER_SKILL_DIR = "/skill"
_CONTAINER_OUT_DIR = "/out"


@dataclass(frozen=True)
class ExecResult:
    """Outcome of one phase execution."""

    skill_name: str
    mode: str  # "docker" or "local"
    result: dict | None
    exit_code: int
    duration_ms: int
    out_dir: str
    stdout: str
    stderr: str


class ExecRunnerError(Exception):
    """Raised when the phase cannot be started at all (missing file,
    unsupported extension, docker unavailable). Phase *failures*
    (non-zero exit) are not errors — they come back in
    ExecResult.exit_code.
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


def _runner_for(phase_path: Path) -> list[str]:
    ext = phase_path.suffix.lstrip(".")
    if ext == "md":
        raise ExecRunnerError(
            f"{phase_path.name}: LLM phases (.md) are not supported by "
            "deterministic exec — code phases only"
        )
    if ext not in RUNNERS:
        raise ExecRunnerError(
            f"{phase_path.name}: no runner for .{ext} "
            f"(supported: {', '.join('.' + e for e in sorted(RUNNERS))})"
        )
    return RUNNERS[ext]


def _ensure_image(image: str) -> None:
    """Pull the image up-front if it isn't local.

    `docker run` would pull implicitly, but its progress would be
    swallowed by our capture_output and the user would stare at
    silence for minutes. An explicit pull inherits the terminal's
    stderr so progress is visible.
    """
    try:
        inspect = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise ExecRunnerError(
            "docker not found — is Docker installed and running? "
            "(or use --local)"
        ) from e

    if inspect.returncode == 0:
        return

    if "Cannot connect to the Docker daemon" in inspect.stderr:
        raise ExecRunnerError(
            "Docker daemon is not running — start Docker Desktop "
            "(or use --local)"
        )

    print(
        f"Pulling {image} (not found locally — this can take a few "
        "minutes)...",
        file=sys.stderr,
    )
    pull = subprocess.run(["docker", "pull", image])
    if pull.returncode != 0:
        raise ExecRunnerError(
            f"docker pull {image} failed (exit {pull.returncode})"
        )


def _build_docker_argv(
    phase_path: Path,
    *,
    skill_root: Path,
    out_dir: Path,
    image: str,
) -> list[str]:
    """Build the minimal `docker run` command for one phase.

    Pure function — unit-testable without Docker. `-i` pipes the ctx
    JSON through stdin; no `-t` (stdout must stay clean for parsing).
    """
    container_phase = (
        f"{_CONTAINER_SKILL_DIR}/zipsa-dist/{phase_path.name}"
    )
    return [
        "docker", "run",
        "--rm",
        "-i",
        "--name", f"zipsa-exec-{skill_root.name}-{os.getpid()}",
        "-v", f"{skill_root.resolve()}:{_CONTAINER_SKILL_DIR}:ro",
        "-v", f"{out_dir}:{_CONTAINER_OUT_DIR}",
        image,
        *_runner_for(phase_path),
        container_phase,
    ]


def run_phase(
    phase_path: Path,
    *,
    skill_name: str,
    user_query: str = "",
    out_dir: Path | None = None,
    skill_root: Path | None = None,
    docker_image: str | None = None,
    timeout_seconds: int = 600,
) -> ExecResult:
    """Execute one phase file and return its outcome.

    `docker_image=None` runs the phase directly on the host (local
    mode); otherwise it runs inside the given runtime image with the
    skill mounted read-only and `out_dir` mounted writable at /out.

    `out_dir` defaults to a fresh temp directory (reported in
    ExecResult.out_dir). `skill_root` is required for docker mode.

    Raises ExecRunnerError if the phase can't be started (file missing,
    extension unsupported, docker unavailable). A phase that starts but
    exits non-zero is reported in ExecResult, not raised.
    """
    # Resolve before building argv: the subprocess runs with cwd set to
    # the phase's directory, which would break a relative phase path.
    phase_path = phase_path.resolve()
    if not phase_path.is_file():
        raise ExecRunnerError(f"phase file not found: {phase_path}")

    if out_dir is None:
        if docker_image is not None:
            # System temp (/var/folders on macOS) is typically NOT in
            # Docker Desktop's file-sharing list — it would mount empty
            # and artifacts would be silently lost. ~/.zipsa sits under
            # /Users, which IS shared.
            from .paths import zipsa_home

            base = zipsa_home() / "exec-out"
            base.mkdir(parents=True, exist_ok=True)
            out_dir = Path(tempfile.mkdtemp(prefix=f"{skill_name}-", dir=base))
        else:
            out_dir = Path(tempfile.mkdtemp(prefix=f"zipsa-exec-{skill_name}-"))

    if docker_image is not None:
        if skill_root is None:
            raise ExecRunnerError("skill_root is required for docker mode")
        _ensure_image(docker_image)
        mode = "docker"
        argv = _build_docker_argv(
            phase_path,
            skill_root=skill_root,
            out_dir=out_dir,
            image=docker_image,
        )
        ctx_out_dir = _CONTAINER_OUT_DIR
        cwd = None
    else:
        mode = "local"
        argv = [*_runner_for(phase_path), str(phase_path)]
        ctx_out_dir = str(out_dir)
        cwd = phase_path.parent

    ctx = {
        "skill_name": skill_name,
        "user_query": user_query,
        "out_dir": ctx_out_dir,
    }
    stdin_payload = json.dumps({"ctx": ctx}) + "\n"

    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            input=stdin_payload,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=cwd,
        )
    except FileNotFoundError as e:
        if mode == "docker":
            raise ExecRunnerError(
                "docker not found — is Docker installed and running? "
                "(or use --local)"
            ) from e
        raise ExecRunnerError(
            f"runner command not found: {argv[0]} — is it on PATH?"
        ) from e
    duration_ms = int((time.monotonic() - started) * 1000)

    result = _parse_result(proc.stdout) if proc.returncode == 0 else None

    return ExecResult(
        skill_name=skill_name,
        mode=mode,
        result=result,
        exit_code=proc.returncode,
        duration_ms=duration_ms,
        out_dir=str(out_dir),
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
