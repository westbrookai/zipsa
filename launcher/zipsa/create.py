"""`zipsa create` — interactive containerized skill authoring (Step 3).

The authoring agent runs headless (`claude -p`) INSIDE the pinned
runtime container (so it's the same claude version skills run on) and
reaches back to a host MCP server for everything — exactly the
mechanism the legacy `zipsa run` path uses, with three extra tools:

- `mcp__zipsa__ask/confirm/choose` — converse with the host user (HITL,
  routed to the host terminal). This is how it clarifies + iterates;
  there is no interactive TTY into the container.
- `mcp__zipsa__exec(staging)` — the host runs the real `zipsa exec`
  (docker, a fresh runtime container per phase) and returns the result.
- `mcp__zipsa__promote(staging, name)` — the host names + moves the
  draft into the repo's skills/<name>/.

zipsa never enters the authoring container — it only makes HTTP MCP
calls to the host CreateServer. The host spawns per-phase containers,
so there's no docker-in-docker and no zipsa baked into the image (the
runtime image and zipsa version independently).

The skill name is decided LAST (via promote); until then the draft
lives in a temp staging dir and the repo is untouched.

Gotchas:
- Staging is mounted into the container at its own host path, so the
  path the agent passes to `exec`/`promote` is host-valid as-is.
- The authoring container's claude gets CLAUDE auth via --env-file
  ~/.zipsa/.env (same token the per-phase containers use).
- Runs claude with bypassed permissions inside the container (same
  trust as the user authoring in their own repo).
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path


def _is_interactive(stdin) -> bool:
    """True when HITL prompts can reach a human — a real TTY, or a
    non-TTY driver that opted in via ZIPSA_FORCE_INTERACTIVE=1."""
    return stdin.isatty() or os.environ.get("ZIPSA_FORCE_INTERACTIVE") == "1"

from .core.create_server import CreateServer
from .core.exec_skill_handler import ExecSkillHandler
from .core.promote_skill_handler import PromoteSkillHandler

_SKILL_REL = Path(".claude/skills/zipsa-skill-builder/SKILL.md")
_CONTAINER_MCP_CONFIG = "/tmp/zipsa-create-mcp.json"


def find_repo_root(start: Path) -> Path | None:
    """Walk up from `start` for the dir holding the zipsa-skill-builder
    skill (+ skills/AUTHORING.md). Returns None if not found."""
    start = start.resolve()
    for d in (start, *start.parents):
        if (d / _SKILL_REL).is_file():
            return d
    return None


def build_create_prompt(intent: str, staging_path: Path) -> str:
    """The first message handed to the in-container authoring claude."""
    return (
        "You are authoring a new zipsa skill, interactively, with the user.\n"
        "Read the workflow in .claude/skills/zipsa-skill-builder/SKILL.md and\n"
        "the contract in skills/AUTHORING.md first, then follow them.\n\n"
        f"User's rough intent: {intent}\n"
        f"Staging directory (write the skill here): {staging_path}\n\n"
        "Clarify the intent with the user (ask, don't guess). Author the\n"
        f"phase files into {staging_path}/zipsa-dist/ plus a short SKILL.md.\n\n"
        f"Test by calling mcp__zipsa__exec with staging_path={staging_path} —\n"
        "this runs the skill the real way (a fresh runtime container per\n"
        "phase) and returns the result. If the skill reads a credential or\n"
        "data file, pass mounts (HOST:CONTAINER strings) to exec so the file\n"
        "is available during the test. Iterate until it works and the user\n"
        "is happy.\n\n"
        "IMPORTANT: you run headless — whenever you need the user to do or\n"
        "answer something (set up a bot, paste a token, confirm a name), you\n"
        "MUST call mcp__zipsa__ask/confirm/choose to block and wait. Never\n"
        "just print a request and stop: if you stop calling tools the session\n"
        "ends and the user cannot reply.\n\n"
        "Only when the user agrees on a name, call mcp__zipsa__promote with\n"
        f"staging_path={staging_path} and the chosen kebab-case name — this\n"
        "moves the skill into the repo. The name is decided last; it can\n"
        "change freely until you call promote.\n"
    )


# Per-call timeout (ms) for the create MCP server's tools. ask/confirm/
# choose block on the human; exec runs a full per-phase container test.
# Claude Code's default (~60s first-byte) is far too short — verified that
# a generous per-server timeout lets a multi-minute tool call survive.
_MCP_TOOL_TIMEOUT_MS = 600_000


def build_mcp_config(port: int, token: str) -> dict:
    """The --mcp-config the container claude uses to reach the host
    CreateServer. Container → host via host.docker.internal; token
    embedded directly (the file is host-private and mounted ro)."""
    return {
        "mcpServers": {
            "zipsa": {
                "type": "http",
                "url": f"http://host.docker.internal:{port}/mcp",
                "headersHelper": (
                    f'echo \'{{"Authorization": "Bearer {token}"}}\''
                ),
                "timeout": _MCP_TOOL_TIMEOUT_MS,
            }
        }
    }


def build_docker_argv(
    *,
    image: str,
    staging_path: Path,
    repo_root: Path,
    mcp_config_host: Path,
    prompt: str,
    env_file: Path | None,
) -> list[str]:
    """Build the headless `docker run` for the authoring session.

    Pure function — unit-testable without docker. claude runs headless
    (`-p`) — there is no interactive TTY into the container; the agent
    converses with the host user via the ask/confirm/choose MCP tools.
    No `-i`/`-t`: the container needs no stdin (the prompt is an arg,
    the conversation is over MCP), and leaving stdin to the container
    would race the host HITL reader for the user's keystrokes. The
    container's stdout still streams back (subprocess inherits it).
    Staging is mounted rw at its own host path; the repo ro (for
    AUTHORING.md + the skill); the mcp-config ro; workdir = repo.
    """
    argv = ["docker", "run", "--rm"]
    if env_file is not None:
        argv += ["--env-file", str(env_file)]
    if platform.system() == "Linux":
        argv += ["--add-host", "host.docker.internal:host-gateway"]
    argv += [
        "-v", f"{staging_path}:{staging_path}:rw",
        "-v", f"{repo_root}:{repo_root}:ro",
        "-v", f"{mcp_config_host}:{_CONTAINER_MCP_CONFIG}:ro",
        "-w", str(repo_root),
        image,
        "claude", "-p", prompt,
        "--mcp-config", _CONTAINER_MCP_CONFIG,
        "--strict-mcp-config",
        "--permission-mode", "bypassPermissions",
    ]
    return argv


def run_create(
    intent: str,
    *,
    repo_root: Path,
    image: str,
    env_file: Path | None = None,
) -> int:
    """Author a skill via an interactive container session. Returns the
    container claude's exit code.

    Starts a host CreateServer (exec + promote), spins up a fresh
    staging dir, writes the mcp-config, runs the interactive container,
    and tears the server down afterwards.
    """
    import sys
    import threading

    from . import paths as zipsa_paths
    from .core.hitl_mcp import HitlIO

    if env_file is None:
        env_file = zipsa_paths.global_env_file()

    staging_root = zipsa_paths.zipsa_home() / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging_path = Path(tempfile.mkdtemp(prefix="draft-", dir=staging_root))

    # HITL routes the agent's ask/confirm/choose to the host terminal —
    # the conversation channel (claude runs headless in the container).
    # ZIPSA_FORCE_INTERACTIVE=1 lets a non-TTY driver (web UI, or a relay
    # feeding answers via a pipe) act as the user, same hook the executor
    # uses for the legacy run path.
    hitl_io = HitlIO(
        stdin=sys.stdin,
        stdout=sys.stdout,
        stdout_lock=threading.Lock(),
        is_interactive=_is_interactive(sys.stdin),
    )
    server = CreateServer(
        hitl_io,
        ExecSkillHandler(docker_image=image),
        PromoteSkillHandler(dest_root=repo_root / "skills"),
    )
    server.start()
    try:
        mcp_config = build_mcp_config(server.port, server.token)
        mcp_config_host = staging_root / f"{staging_path.name}.mcp.json"
        mcp_config_host.write_text(json.dumps(mcp_config))

        argv = build_docker_argv(
            image=image,
            staging_path=staging_path,
            repo_root=repo_root,
            mcp_config_host=mcp_config_host,
            prompt=build_create_prompt(intent, staging_path),
            env_file=env_file if env_file.exists() else None,
        )
        # stdin=DEVNULL: the host terminal's stdin belongs exclusively to
        # the HITL reader (CreateServer's ask/confirm/choose), not the
        # container. stdout/stderr inherit so the agent's progress shows.
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL)
        return proc.returncode
    finally:
        server.stop()
