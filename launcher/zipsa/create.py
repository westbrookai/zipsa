"""`zipsa forge` — interactive containerized skill authoring loop.

The forge agent runs headless (`claude -p`) INSIDE the pinned runtime
container (so it's the same claude version skills run on) and reaches
back to a host MCP server for everything — exactly the mechanism the
`zipsa run` path uses, with these extra tools:

- `mcp__zipsa__ask/confirm/choose` — converse with the host user (HITL,
  routed to the host terminal). This is how it clarifies + iterates;
  there is no interactive TTY into the container.
- `mcp__zipsa__exec(script, ...)` — the host runs ONE of the draft's
  scripts (docker, fresh runtime container) and returns the result.
- `mcp__zipsa__run(args, ...)` — the host runs the WHOLE draft through
  the real run-time (an LLM following SKILL.md) — the user's real
  experience.
- `mcp__zipsa__promote(name)` — the host names + moves the draft into
  the repo's skills/<name>/.

The exec/run/promote tools are PATH-SCOPED by the ForgeServer (it
injects the staging path), so the agent never passes a staging path.

zipsa never enters the forge container — it only makes HTTP MCP calls
to the host ForgeServer. The host spawns per-test containers, so
there's no docker-in-docker and no zipsa baked into the image (the
runtime image and zipsa version evolve independently).

The skill name is decided LAST (via promote); until then the draft
lives in a temp staging dir and the repo is untouched.

`run_create` remains as a deprecated alias of `run_forge` for backward
compatibility, so `zipsa create` + the relay workflow (and the docs
that reference `zipsa create`) still work.

Gotchas:
- Staging is mounted into the container at its own host path, so files
  the agent writes are host-valid as-is.
- The forge container's claude gets CLAUDE auth via --env-file
  ~/.zipsa/.env (same token the per-test containers use).
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

from .core.forge_server import ForgeServer
from .core.promote_skill_handler import PromoteSkillHandler
from .core.run_script_handler import RunScriptHandler

# RunDraftHandler is imported lazily inside run_forge: it pulls in run_llm,
# which imports back from this module (_is_interactive, build_mcp_config),
# so a top-level import here forms a circular import.

# The authoring contract + workflow ship WITH the launcher (not with any
# skill), so `zipsa create` works regardless of where skills live (repo
# today, registry later). Inlined into the agent's prompt — no repo mount.
_AUTHORING_DIR = Path(__file__).parent / "authoring"
_CONTAINER_MCP_CONFIG = "/tmp/zipsa-create-mcp.json"


def _bundled(name: str) -> str:
    return (_AUTHORING_DIR / name).read_text()


def build_create_prompt(intent: str, staging_path: Path) -> str:
    """The prompt handed to the in-container authoring claude.

    The workflow + contract are bundled with the launcher and inlined
    here, so the agent needs nothing from any repo — just this prompt,
    the MCP tools, and the staging dir.
    """
    return (
        "You are authoring a new zipsa skill, with the user, via the zipsa\n"
        "MCP tools. Follow the WORKFLOW and honor the CONTRACT below.\n\n"
        f"User's rough intent: {intent}\n"
        f"Staging directory (write the skill files here): {staging_path}\n"
        f"Test with mcp__zipsa__exec(staging_path=\"{staging_path}\", ...);\n"
        f"finalize with mcp__zipsa__promote(staging_path=\"{staging_path}\",\n"
        "name=...). The name is decided LAST, after the user is happy.\n\n"
        "===== WORKFLOW =====\n"
        f"{_bundled('skill-builder.md')}\n"
        "===== CONTRACT (AUTHORING guide) =====\n"
        f"{_bundled('AUTHORING.md')}\n"
    )


def build_forge_prompt(intent: str, staging_path: Path) -> str:
    """The prompt handed to the in-container forge authoring claude.

    Like build_create_prompt, but drives the full forge loop: capture
    requirements in INTENT.md, draft, exec-debug individual scripts, run
    the WHOLE draft through the real run-time, iterate, and promote LAST.

    Forge tools are PATH-SCOPED — the server injects the staging path, so
    the agent calls exec/run/promote WITHOUT a staging_path arg (unlike
    create). The staging dir is still where the agent writes files.
    """
    return (
        "You are authoring (forging) a new zipsa skill, with the user, via\n"
        "the zipsa MCP tools. Follow the WORKFLOW and honor the CONTRACT below.\n\n"
        f"User's rough intent: {intent}\n"
        f"Staging directory (write the skill files here): {staging_path}\n\n"
        "===== FORGE LOOP =====\n"
        "1. Agree the requirements with the user, then write INTENT.md in the\n"
        "   staging dir capturing the why and the acceptance criteria.\n"
        "2. Draft SKILL.md + the scripts.\n"
        "3. Debug individual scripts fast with\n"
        '   mcp__zipsa__exec(script="...", args="...").\n'
        "4. Test the WHOLE skill through the real run-time (an LLM following\n"
        '   SKILL.md, calling scripts) with mcp__zipsa__run(args="...").\n'
        "5. Iterate on 2-4 until BOTH the user AND you are satisfied.\n"
        "6. Finalize LAST with mcp__zipsa__promote(name=\"...\"). The name is\n"
        "   decided last, after the user is happy.\n"
        "Note: exec/run/promote are path-scoped — do NOT pass a staging_path\n"
        "argument; the host already knows the draft location.\n\n"
        "===== WORKFLOW =====\n"
        f"{_bundled('skill-builder.md')}\n"
        "===== CONTRACT (AUTHORING guide) =====\n"
        f"{_bundled('AUTHORING.md')}\n"
    )


# Per-call timeout (ms) for the create MCP server's tools. ask/confirm/
# choose block on the human; exec runs a full per-phase container test.
# Claude Code's default (~60s first-byte) is far too short — verified that
# a generous per-server timeout lets a multi-minute tool call survive.
_MCP_TOOL_TIMEOUT_MS = 600_000


def build_mcp_config(port: int, token: str) -> dict:
    """The --mcp-config the container claude uses to reach the host
    ForgeServer. Container → host via host.docker.internal; token
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

    The only mounts are the staging dir (rw, where the skill is written)
    and the mcp-config (ro). No repo mount — the workflow + contract are
    inlined into the prompt, so create needs nothing from any repo.
    """
    argv = ["docker", "run", "--rm"]
    if env_file is not None:
        argv += ["--env-file", str(env_file)]
    if platform.system() == "Linux":
        argv += ["--add-host", "host.docker.internal:host-gateway"]
    argv += [
        "-v", f"{staging_path}:{staging_path}:rw",
        "-v", f"{mcp_config_host}:{_CONTAINER_MCP_CONFIG}:ro",
        "-w", str(staging_path),
        image,
        "claude", "-p", prompt,
        "--mcp-config", _CONTAINER_MCP_CONFIG,
        "--strict-mcp-config",
        "--permission-mode", "bypassPermissions",
    ]
    return argv


def run_forge(
    intent: str,
    *,
    skills_dir: Path,
    image: str,
    env_file: Path | None = None,
) -> int:
    """Forge a skill via a headless container session. Returns the
    container claude's exit code.

    Starts a host ForgeServer (ask/confirm/choose + path-scoped exec +
    run + promote), spins up a fresh staging dir, writes the mcp-config,
    runs the headless forge container, and tears the server down
    afterwards. `skills_dir` is where promote lands the finished skill.

    Unlike create's exec=run_phases model, forge gives the agent two
    test tools: exec (one script, fast debug) via RunScriptHandler and
    run (the whole draft through the real run-time) via RunDraftHandler.
    Both are scoped to the staging dir by the ForgeServer, so the agent
    never names a staging path.
    """
    import sys
    import threading

    from . import paths as zipsa_paths
    from .core.hitl_mcp import HitlIO
    from .core.run_draft_handler import RunDraftHandler

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
    server = ForgeServer(
        hitl_io,
        exec_handler=RunScriptHandler(docker_image=image, skill_root=staging_path),
        run_handler=RunDraftHandler(image=image, skill_root=staging_path),
        promote_handler=PromoteSkillHandler(dest_root=skills_dir),
        staging_path=str(staging_path),
    )
    server.start()
    try:
        mcp_config = build_mcp_config(server.port, server.token)
        mcp_config_host = staging_root / f"{staging_path.name}.mcp.json"
        mcp_config_host.write_text(json.dumps(mcp_config))

        argv = build_docker_argv(
            image=image,
            staging_path=staging_path,
            mcp_config_host=mcp_config_host,
            prompt=build_forge_prompt(intent, staging_path),
            env_file=env_file if env_file.exists() else None,
        )
        # stdin=DEVNULL: the host terminal's stdin belongs exclusively to
        # the HITL reader (ForgeServer's ask/confirm/choose), not the
        # container. stdout/stderr inherit so the agent's progress shows.
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL)
        return proc.returncode
    finally:
        server.stop()


def run_create(
    intent: str,
    *,
    skills_dir: Path,
    image: str,
    env_file: Path | None = None,
) -> int:
    """Deprecated alias of run_forge, kept for library/external callers
    after the create→forge rename. (The `zipsa create` CLI command calls
    run_forge directly; it does not route through this wrapper.)"""
    return run_forge(intent, skills_dir=skills_dir, image=image, env_file=env_file)
