"""Run-time orchestration: execute a skill as an LLM following SKILL.md.

Mirror of create.py for the run path — headless claude in a container,
SKILL.md as the instruction, a host MCP server exposing exec + HITL.

Design decisions:
- Output is teed (not just inherited): claude's stdout/stderr are PIPEd
  and pumped by reader threads to BOTH the live terminal and a per-run
  record under ~/.zipsa/<skill>/runs/<ts>/ (result.json mode="run" +
  stdout.log/stderr.log), so an unwatched/scheduled run leaves a trace.
  Shares exec's on-disk shape via exec_runner.new_run_dir.

Gotchas:
- The run record is best-effort: an OSError writing it never changes the
  returned exit code (always the container claude's).
- claude streams UTF-8 (incl. multi-byte Korean); the tee decodes the
  live stream INCREMENTALLY (boundary-safe — a multi-byte char split
  across two pipe reads is emitted whole, no spurious U+FFFD), and writes
  the raw bytes byte-exact to the on-disk log (never decoded).
"""
from __future__ import annotations

import codecs
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO

from . import exec_runner
from . import paths as zipsa_paths
from .core.hitl_mcp import HitlIO
from .core.run_script_handler import RunScriptHandler
from .core.run_server import RunServer

# reuse create's interactivity check; mcp-config + orchestration from shared core
from .create import _is_interactive
from .host_served_container import run_host_served_container


def build_run_prompt(skill_root: Path, user_input: str) -> str:
    skill_md = (skill_root / "SKILL.md").read_text()
    # Transition window (#156): INTENT.md moves to zipsa/INTENT.md (forge
    # provenance, outside the portable Agent Skill payload). Prefer the new
    # path; fall back to the legacy skill-root location for un-migrated skills.
    intent_path = skill_root / "zipsa" / "INTENT.md"
    if not intent_path.exists():
        intent_path = skill_root / "INTENT.md"
    intent = (
        f"## Intent (why)\n{intent_path.read_text()}\n\n"
        if intent_path.exists() else ""
    )
    return (
        "You are RUNNING a zipsa skill. Follow SKILL.md (your constitution)\n"
        "to accomplish the user's request. Run the skill's scripts with\n"
        "mcp__zipsa__exec(script=\"<id-or-slug>\", args=\"...\", prev=<dict>)\n"
        "— one script per call; thread data via `prev` or /out artifacts.\n"
        "On errors, judge what to do and explain the outcome to the user.\n\n"
        f"{intent}"
        f"User request: {user_input}\n\n"
        "===== SKILL.md (constitution) =====\n"
        f"{skill_md}\n"
    )


def _tee_stream(src: IO[bytes], live: IO[str], sink: list[bytes]) -> None:
    """Pump bytes from `src` to both the live text stream and a byte buffer.

    Reads raw bytes (claude streams UTF-8, incl. multi-byte Korean). The
    LIVE stream is decoded INCREMENTALLY (codecs incremental decoder,
    errors="replace"): a multi-byte sequence split across two OS pipe reads
    is held over the boundary and emitted whole, so no U+FFFD appears just
    because a read landed mid-character. The captured bytes are kept intact
    and written byte-exact to the on-disk log (no decode at all on that
    path). Best-effort: a write to the live stream that fails is swallowed —
    logging must never sink the run.
    """
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    while True:
        chunk = src.read(4096)
        if not chunk:
            break
        sink.append(chunk)
        try:
            live.write(decoder.decode(chunk))
            live.flush()
        except (OSError, ValueError):
            pass
    # Flush any trailing partial sequence (replaced if incomplete at EOF).
    try:
        tail = decoder.decode(b"", final=True)
        if tail:
            live.write(tail)
            live.flush()
    except (OSError, ValueError):
        pass


def _write_run_record(
    run_dir: Path, *,
    skill_name: str, exit_code: int, duration_ms: int,
    user_input: str, stdout_bytes: bytes, stderr_bytes: bytes,
) -> None:
    """Persist the run-time transcript as a unified `result.json` (mode="run")
    plus stdout.log/stderr.log — same on-disk shape as exec (D2/D3).

    Best-effort: any OSError is swallowed so a logging failure never changes
    the run's exit code (mirrors exec_runner.write_run_record).
    `final_message` is best-effort (D4): omitted unless trivially available —
    stdout.log already holds the full transcript.
    """
    try:
        record = {
            "skill_name": skill_name,
            "mode": "run",
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "run_dir": str(run_dir),
            "user_input": user_input,
        }
        (run_dir / "result.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2)
        )
        (run_dir / "stdout.log").write_bytes(stdout_bytes)
        (run_dir / "stderr.log").write_bytes(stderr_bytes)
    except OSError:
        pass


def run_skill_llm(
    skill_root: Path, user_input: str, *,
    image: str, env_file: Path | None = None,
    extra_mounts: list[tuple[Path, str]] | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
    dry_run: bool = False,
) -> int:
    """Execute a skill as an LLM following SKILL.md, calling scripts via
    the host RunServer's exec tool. Returns the container claude's exit code.

    Output is teed: every chunk reaches the live terminal (`stdout`/
    `stderr`, default sys.stdout/sys.stderr) AND a per-run record under
    ~/.zipsa/<skill>/runs/<ts>/ (result.json + stdout.log + stderr.log),
    so an unwatched/scheduled run still leaves a trace. The record write
    is best-effort — an OSError there does not change the returned exit
    code (the container claude's).

    With `dry_run=True` the orchestrator container command + mcp-config
    path are printed and the function returns 0 without starting the
    RunServer, spawning the container, or writing a run record. Dry-run
    is handled by the shared `run_host_served_container` core.
    """
    if stdout is None:
        stdout = sys.stdout
    if stderr is None:
        stderr = sys.stderr
    if env_file is None:
        env_file = zipsa_paths.global_env_file()
    skill_root = skill_root.resolve()

    def _server(work_dir: Path):
        hitl_io = HitlIO(
            stdin=sys.stdin, stdout=sys.stdout,
            stdout_lock=threading.Lock(), is_interactive=_is_interactive(sys.stdin),
        )
        # extra_mounts (skill creds) go to the SCRIPT's exec sub-container via
        # RunScriptHandler.default_mounts — NOT the claude container.
        handler = RunScriptHandler(
            docker_image=image, skill_root=work_dir, default_mounts=extra_mounts,
        )
        return RunServer(hitl_io, handler)

    def _execute(argv: list[str]) -> int:
        run_dir = exec_runner.new_run_dir(skill_root.name)
        try:
            (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        started = time.monotonic()
        # stdin DEVNULL — HITL goes over MCP, not claude's stdin. stdout/stderr
        # PIPEd so we can tee them to the terminal AND the run record.
        proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        out_chunks: list[bytes] = []
        err_chunks: list[bytes] = []
        t_out = threading.Thread(
            target=_tee_stream, args=(proc.stdout, stdout, out_chunks), daemon=True,
        )
        t_err = threading.Thread(
            target=_tee_stream, args=(proc.stderr, stderr, err_chunks), daemon=True,
        )
        t_out.start()
        t_err.start()
        exit_code = proc.wait()
        t_out.join()
        t_err.join()
        duration_ms = int((time.monotonic() - started) * 1000)
        _write_run_record(
            run_dir,
            skill_name=skill_root.name,
            exit_code=exit_code,
            duration_ms=duration_ms,
            user_input=user_input,
            stdout_bytes=b"".join(out_chunks),
            stderr_bytes=b"".join(err_chunks),
        )
        return exit_code

    return run_host_served_container(
        image=image,
        env_file=env_file,
        work_dir_factory=lambda _dry: skill_root,
        mode="ro",
        extra_mounts=None,  # claude container; creds reach the script handler
        server_factory=_server,
        prompt_factory=lambda wd: build_run_prompt(wd, user_input),
        execute=_execute,
        mcp_subdir="run",
        dry_run=dry_run,
    )
