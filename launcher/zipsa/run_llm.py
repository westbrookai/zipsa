"""Run-time orchestration: execute a skill as an LLM following SKILL.md.

Mirror of create.py for the run path — headless claude in a container,
SKILL.md as the instruction, a host MCP server exposing exec + HITL.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from . import paths as zipsa_paths
# reuse create's container mcp-config shape + interactivity check
from .create import _is_interactive, build_mcp_config
from .core.hitl_mcp import HitlIO
from .core.run_server import RunServer
from .core.run_script_handler import RunScriptHandler

_CONTAINER_MCP_CONFIG = "/tmp/zipsa-run-mcp.json"


def build_run_prompt(skill_root: Path, user_input: str) -> str:
    skill_md = (skill_root / "SKILL.md").read_text()
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


def run_skill_llm(
    skill_root: Path, user_input: str, *,
    image: str, env_file: Path | None = None,
    extra_mounts: "list[tuple[Path, str]] | None" = None,
) -> int:
    """Execute a skill as an LLM following SKILL.md, calling scripts via
    the host RunServer's exec tool. Returns the container claude's exit code."""
    if env_file is None:
        env_file = zipsa_paths.global_env_file()
    skill_root = skill_root.resolve()

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
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL)
        return proc.returncode
    finally:
        server.stop()
        # The mcp-config carries the bearer token — don't leave it lying
        # around once the session ends. (One file per run would otherwise
        # accumulate under ~/.zipsa/run/.)
        if mcp_config_host is not None:
            mcp_config_host.unlink(missing_ok=True)
