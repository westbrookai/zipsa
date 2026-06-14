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
- `.md` files are LLM phases: the runner reads the file host-side,
  assembles a prompt (md + ctx/prev + output contract), and pipes it
  to `claude -p` — the file is never executed in the container. Only
  LLM phase containers get --env-file (Claude auth); code phases stay
  secret-free.
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
from datetime import datetime
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
    if ext not in RUNNERS:
        raise ExecRunnerError(
            f"{phase_path.name}: no runner for .{ext} "
            f"(supported: .md, {', '.join('.' + e for e in sorted(RUNNERS))})"
        )
    return RUNNERS[ext]


# claude CLI invocation for .md (LLM) phases. --max-turns 1 because
# LLM phases are pure reasoning — no tools, no loop.
_LLM_COMMAND = ["claude", "-p", "--max-turns", "1"]


def _build_llm_prompt(md_text: str, *, ctx: dict, prev: dict) -> str:
    """Assemble the prompt for an LLM phase.

    The .md file is the phase author's instruction; the runner appends
    the input payload and the output contract (same last-JSON-object
    rule the code phases follow) so the model's reply parses like any
    other phase's stdout.
    """
    payload = json.dumps({"ctx": ctx, "prev": prev}, ensure_ascii=False)
    return (
        f"{md_text}\n"
        "---\n"
        "Input (ctx = run context, prev = previous phase's result):\n"
        f"{payload}\n"
        "---\n"
        "Rules:\n"
        "- You have no tools. Reason from the input only.\n"
        "- The last line of your reply MUST be a single-line JSON "
        "object — it is parsed as this phase's result.\n"
        "- Any lines before it are treated as logs.\n"
    )


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


def _host_timezone() -> str | None:
    """Return the host's IANA timezone name (e.g. Australia/Sydney),
    or None when it can't be determined.

    Read from the /etc/localtime symlink — present on macOS and most
    Linux distros, and the zoneinfo name is the path suffix.
    """
    try:
        target = os.readlink("/etc/localtime")
    except OSError:
        return None
    if "zoneinfo/" not in target:
        return None
    return target.split("zoneinfo/", 1)[1]


def _build_docker_argv(
    phase_path: Path,
    *,
    skill_root: Path,
    out_dir: Path,
    image: str,
    command: list[str] | None = None,
    env_file: Path | None = None,
    extra_mounts: list[tuple[Path, str]] | None = None,
) -> list[str]:
    """Build the minimal `docker run` command for one phase.

    Pure function — unit-testable without Docker. `-i` pipes the stdin
    payload through; no `-t` (stdout must stay clean for parsing).

    `command` overrides the default <runner> <phase-file> invocation
    (used by LLM phases, whose prompt arrives via stdin instead of a
    file argument). `env_file` adds --env-file — only LLM phases get
    it (Claude auth); code phases stay secret-free.

    `extra_mounts` are (host_path, container_path) pairs mounted
    read-only — usually the same path on both sides so tools that
    embed host paths in their data keep working (agenthud resolving
    session cwd → .git); a different container path covers cases like
    session logs that must land at the container user's home.

    The host's timezone is injected as TZ so date arithmetic inside
    the container ("yesterday") means the user's yesterday, not UTC's.
    """
    if command is None:
        command = [
            *_runner_for(phase_path),
            f"{_CONTAINER_SKILL_DIR}/zipsa-dist/{phase_path.name}",
        ]
    argv = [
        "docker", "run",
        "--rm",
        "-i",
        "--name", f"zipsa-exec-{skill_root.name}-{os.getpid()}",
    ]
    tz = _host_timezone()
    if tz is not None:
        argv += ["-e", f"TZ={tz}"]
    if env_file is not None:
        argv += ["--env-file", str(env_file)]
    argv += [
        "-v", f"{skill_root.resolve()}:{_CONTAINER_SKILL_DIR}:ro",
        "-v", f"{out_dir}:{_CONTAINER_OUT_DIR}",
    ]
    for host_path, container_path in extra_mounts or []:
        argv += ["-v", f"{host_path}:{container_path}:ro"]
    argv += [
        image,
        *command,
    ]
    return argv


def run_phase(
    phase_path: Path,
    *,
    skill_name: str,
    user_query: str = "",
    out_dir: Path | None = None,
    skill_root: Path | None = None,
    docker_image: str | None = None,
    prev: dict | None = None,
    extra_mounts: list[tuple[Path, str]] | None = None,
    timeout_seconds: int = 600,
) -> ExecResult:
    """Execute one phase file and return its outcome.

    `docker_image=None` runs the phase directly on the host (local
    mode); otherwise it runs inside the given runtime image with the
    skill mounted read-only and `out_dir` mounted writable at /out.

    `out_dir` defaults to a fresh temp directory (reported in
    ExecResult.out_dir). `skill_root` is required for docker mode.
    `prev` is the previous phase's result dict ({} for the first
    phase) — delivered to the phase as the stdin payload's "prev" key.
    `extra_mounts` are host paths mounted ro at the same container
    path (docker mode; ignored under local where the host is visible
    anyway).

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

    is_llm = phase_path.suffix == ".md"

    if docker_image is not None:
        if skill_root is None:
            raise ExecRunnerError("skill_root is required for docker mode")
        for host_path, _container_path in extra_mounts or []:
            if not host_path.exists():
                raise ExecRunnerError(
                    f"mount path does not exist: {host_path}"
                )
        _ensure_image(docker_image)
        mode = "docker"
        ctx_out_dir = _CONTAINER_OUT_DIR
        cwd = None
        if is_llm:
            from .paths import global_env_file

            env_file = global_env_file()
            argv = _build_docker_argv(
                phase_path,
                skill_root=skill_root,
                out_dir=out_dir,
                image=docker_image,
                command=list(_LLM_COMMAND),
                env_file=env_file if env_file.exists() else None,
                extra_mounts=extra_mounts,
            )
        else:
            argv = _build_docker_argv(
                phase_path,
                skill_root=skill_root,
                out_dir=out_dir,
                image=docker_image,
                extra_mounts=extra_mounts,
            )
    else:
        mode = "local"
        ctx_out_dir = str(out_dir)
        cwd = phase_path.parent
        if is_llm:
            # Host claude CLI uses the user's own login — no env wiring.
            argv = list(_LLM_COMMAND)
        else:
            argv = [*_runner_for(phase_path), str(phase_path)]

    ctx = {
        "skill_name": skill_name,
        "user_query": user_query,
        "out_dir": ctx_out_dir,
    }
    if is_llm:
        stdin_payload = _build_llm_prompt(
            phase_path.read_text(), ctx=ctx, prev=prev or {},
        )
    else:
        stdin_payload = json.dumps({"ctx": ctx, "prev": prev or {}}) + "\n"

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


def run_phases(
    phases: list,
    *,
    skill_name: str,
    user_query: str = "",
    out_dir: Path | None = None,
    skill_root: Path | None = None,
    docker_image: str | None = None,
    extra_mounts: list[tuple[Path, str]] | None = None,
    timeout_seconds: int = 600,
) -> list[ExecResult]:
    """Run a skill's phases sequentially, chaining each result into the
    next phase's `prev`.

    `phases` is the ordered list from `phase_discovery.discover_phases`.
    All phases share one out_dir. A phase that exits non-zero stops the
    chain — the returned list ends with the failing phase's result.

    Pre-flight (before ANY phase runs): every phase must be runnable —
    sub-phases (dotted ids like `3.1`, branching) and unknown
    extensions are rejected up front so a chain never dies halfway on
    a known-bad phase.
    """
    for phase in phases:
        if len(phase.id_tuple) > 1:
            raise ExecRunnerError(
                f"{phase.path.name}: branching (sub-phases) is not yet "
                "supported — phases must have single-integer ids"
            )
        if phase.kind != "md":
            _runner_for(phase.path)  # raises on unknown ext

    if out_dir is None:
        # Allocate once here so every phase shares it (run_phase would
        # otherwise mint a fresh temp dir per phase).
        if docker_image is not None:
            from .paths import zipsa_home

            base = zipsa_home() / "exec-out"
            base.mkdir(parents=True, exist_ok=True)
            out_dir = Path(tempfile.mkdtemp(prefix=f"{skill_name}-", dir=base))
        else:
            out_dir = Path(
                tempfile.mkdtemp(prefix=f"zipsa-exec-{skill_name}-")
            )

    results: list[ExecResult] = []
    prev: dict = {}
    for phase in phases:
        outcome = run_phase(
            phase.path,
            skill_name=skill_name,
            user_query=user_query,
            out_dir=out_dir,
            skill_root=skill_root,
            docker_image=docker_image,
            prev=prev,
            extra_mounts=extra_mounts,
            timeout_seconds=timeout_seconds,
        )
        results.append(outcome)
        if outcome.exit_code != 0:
            break
        prev = outcome.result or {}

    return results


def new_run_dir(skill_name: str) -> Path:
    """Create and return a fresh run directory for a `zipsa exec` run:
    ~/.zipsa/<skill_name>/runs/<timestamp>/. Mirrors the legacy run dir
    convention (minus the @version, which exec skills don't carry)."""
    from . import paths as zipsa_paths

    ts = datetime.now().astimezone().strftime("%Y-%m-%d_%H%M%S_%f")[:23]
    run_dir = zipsa_paths.zipsa_home() / skill_name / "runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def write_run_record(
    run_dir: Path,
    summary: dict,
    results: list[ExecResult],
    *,
    phases: "list[tuple[str, str]] | None" = None,
) -> None:
    """Persist what exec already has in memory: the summary dict plus the
    per-phase stdout/stderr. Best-effort — a logging failure must not
    sink the run.

    `phases` is a list of (id, slug) parallel to `results`, used for
    `=== phase <id>.<slug> ===` markers in the logs.
    """
    try:
        (run_dir / "result.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2)
        )

        def _stream(attr: str) -> str:
            chunks = []
            for i, r in enumerate(results):
                if phases and i < len(phases):
                    pid, slug = phases[i]
                    chunks.append(f"=== phase {pid}.{slug} ===\n")
                else:
                    chunks.append(f"=== phase {i + 1} ===\n")
                chunks.append(getattr(r, attr) or "")
                if not getattr(r, attr, "").endswith("\n"):
                    chunks.append("\n")
            return "".join(chunks)

        (run_dir / "stdout.log").write_text(_stream("stdout"))
        (run_dir / "stderr.log").write_text(_stream("stderr"))
    except OSError:
        pass
