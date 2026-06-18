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
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import IO

from . import exec_runner
from . import paths as zipsa_paths
# reuse create's container mcp-config shape + interactivity check
from .create import _is_interactive, build_mcp_config
from .core.hitl_mcp import HitlIO
from .core.run_server import RunServer
from .core.run_script_handler import RunScriptHandler

_CONTAINER_MCP_CONFIG = "/tmp/zipsa-run-mcp.json"


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


def build_run_argv(
    *, image: str, skill_root: Path, mcp_config_host: Path,
    prompt: str, env_file: Path | None,
    extra_mounts: "list[tuple[Path, str]] | None" = None,
) -> list[str]:
    # Docker bind mounts require absolute host paths — resolve here so the
    # function is safe to call with a relative skill_root in isolation.
    skill_root = skill_root.resolve()
    argv = ["docker", "run", "--rm"]
    if env_file is not None:
        argv += ["--env-file", str(env_file)]
    if platform.system() == "Linux":
        argv += ["--add-host", "host.docker.internal:host-gateway"]
    argv += [
        "-v", f"{skill_root}:{skill_root}:ro",
        "-v", f"{mcp_config_host}:{_CONTAINER_MCP_CONFIG}:ro",
    ]
    for host, container in extra_mounts or []:
        argv += ["-v", f"{host}:{container}:ro"]
    argv += [
        "-w", str(skill_root),
        image,
        "claude", "-p", prompt,
        "--mcp-config", _CONTAINER_MCP_CONFIG,
        "--strict-mcp-config",
        "--permission-mode", "bypassPermissions",
    ]
    return argv


def _tee_stream(src: "IO[bytes]", live: "IO[str]", sink: list[bytes]) -> None:
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


def _print_run_dry_run(argv: list[str], mcp_config_host: Path) -> None:
    """Print the orchestrator (claude) container command + mcp-config path,
    mirroring the legacy `_print_dry_run` shape: the full command on one
    line, then one arg per line so it stays scannable. The mcp-config path
    is echoed so the user can inspect the generated config."""
    print("=== DRY RUN (run, exec-format) ===")
    print(f"MCP config: {mcp_config_host}")
    print()
    print("Orchestrator command:")
    print(" ".join(str(a) for a in argv))
    for i, arg in enumerate(argv):
        print(f"  [{i:2d}] {arg}")


def run_skill_llm(
    skill_root: Path, user_input: str, *,
    image: str, env_file: Path | None = None,
    extra_mounts: "list[tuple[Path, str]] | None" = None,
    stdout: "IO[str] | None" = None,
    stderr: "IO[str] | None" = None,
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
    path are printed and the function returns 0 WITHOUT starting the
    RunServer (no bound port), spawning the container, or writing a run
    record. The mcp-config is built with a placeholder port/token since no
    server is running — it only documents the config shape.
    """
    if stdout is None:
        stdout = sys.stdout
    if stderr is None:
        stderr = sys.stderr
    if env_file is None:
        env_file = zipsa_paths.global_env_file()
    skill_root = skill_root.resolve()

    if dry_run:
        # Spawn nothing and bind no port: don't start the RunServer. The
        # mcp-config still needs a port/token to render — use placeholders
        # (the real values are only known once the server binds). The config
        # is written so the printed path points at an inspectable file. A
        # FIXED path (overwritten each run) — not a unique mkstemp — so dry
        # runs never accumulate orphan config files.
        cfg_dir = zipsa_paths.zipsa_home() / "run"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        mcp_config_host = cfg_dir / "dry-run.mcp.json"
        mcp_config_host.write_text(json.dumps(build_mcp_config(0, "<token>")))
        argv = build_run_argv(
            image=image, skill_root=skill_root, mcp_config_host=mcp_config_host,
            prompt=build_run_prompt(skill_root, user_input),
            env_file=env_file if env_file.exists() else None,
        )
        _print_run_dry_run(argv, mcp_config_host)
        return 0

    run_dir = exec_runner.new_run_dir(skill_root.name)
    try:
        (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    hitl_io = HitlIO(
        stdin=sys.stdin, stdout=sys.stdout,
        stdout_lock=threading.Lock(), is_interactive=_is_interactive(sys.stdin),
    )
    # extra_mounts (skill creds, etc.) go to the SCRIPT's exec sub-container via
    # RunScriptHandler.default_mounts — NOT into the run-time (claude) container.
    # The claude container only needs Claude auth (env_file); mounting skill creds
    # there would be incorrect and a security concern.
    handler = RunScriptHandler(
        docker_image=image, skill_root=skill_root, default_mounts=extra_mounts,
    )
    server = RunServer(hitl_io, handler)
    server.start()
    mcp_config_host: Path | None = None
    try:
        mcp_config = build_mcp_config(server.port, server.token)
        cfg_dir = zipsa_paths.zipsa_home() / "run"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        fd, cfg_path = tempfile.mkstemp(prefix="run-", suffix=".mcp.json", dir=cfg_dir)
        os.close(fd)  # mkstemp opens the file; we only need the path
        mcp_config_host = Path(cfg_path)
        mcp_config_host.write_text(json.dumps(mcp_config))
        argv = build_run_argv(
            image=image, skill_root=skill_root, mcp_config_host=mcp_config_host,
            prompt=build_run_prompt(skill_root, user_input),
            env_file=env_file if env_file.exists() else None,
            # Skill creds are NOT passed here — the claude container gets only
            # Claude auth. Mounts reach the script via RunScriptHandler above.
        )
        started = time.monotonic()
        # stdin stays DEVNULL — the relay FIFO feeds the MCP HITL tools, not
        # claude's stdin. stdout/stderr are PIPEd so we can tee them.
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
    finally:
        server.stop()
        # The mcp-config carries the bearer token — don't leave it lying
        # around once the session ends. (One file per run would otherwise
        # accumulate under ~/.zipsa/run/.)
        if mcp_config_host is not None:
            mcp_config_host.unlink(missing_ok=True)
