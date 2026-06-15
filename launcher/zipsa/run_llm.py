"""Run-time orchestration: execute a skill as an LLM following SKILL.md.

Mirror of create.py for the run path — headless claude in a container,
SKILL.md as the instruction, a host MCP server exposing exec + HITL.
"""
from __future__ import annotations

import json
import platform
import subprocess
import tempfile
from pathlib import Path

from .create import build_mcp_config  # reuse the container mcp-config shape

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
        "-w", str(skill_root),
        image,
        "claude", "-p", prompt,
        "--mcp-config", _CONTAINER_MCP_CONFIG,
        "--strict-mcp-config",
        "--permission-mode", "bypassPermissions",
    ]
    return argv
