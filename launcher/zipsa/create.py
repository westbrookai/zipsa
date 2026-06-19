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

Gotchas:
- Staging is mounted into the container at its own host path, so files
  the agent writes are host-valid as-is.
- The forge container's claude gets CLAUDE auth via --env-file
  ~/.zipsa/.env (same token the per-test containers use).
- Runs claude with bypassed permissions inside the container (same
  trust as the user authoring in their own repo).
"""

from __future__ import annotations

import os
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
from .host_served_container import (  # noqa: E402
    build_mcp_config,  # noqa: F401  (re-exported for back-compat)
    run_host_served_container,
)

# RunDraftHandler is imported lazily inside run_forge: it pulls in run_llm,
# which imports back from this module (_is_interactive, build_mcp_config),
# so a top-level import here forms a circular import.

# The authoring contract + workflow ship WITH the launcher (not with any
# skill), so `zipsa create` works regardless of where skills live (repo
# today, registry later). Inlined into the agent's prompt — no repo mount.
_AUTHORING_DIR = Path(__file__).parent / "authoring"


def _bundled(name: str) -> str:
    return (_AUTHORING_DIR / name).read_text()


def build_forge_prompt(intent: str, staging_path: Path) -> str:
    """The prompt handed to the in-container forge authoring claude.

    Drives the full forge loop: clarify intent, check feasibility &
    gather prerequisites, draft (writing INTENT.md once scope is
    settled), exec-debug individual scripts, run the WHOLE draft through
    the real run-time, iterate, and promote LAST.

    Forge tools are PATH-SCOPED — the server injects the staging path, so
    the agent calls exec/run/promote WITHOUT a staging_path arg. The
    staging dir is still where the agent writes files.
    """
    return (
        "You are authoring (forging) a new zipsa skill, with the user, via\n"
        "the zipsa MCP tools.\n\n"
        f"User's rough intent: {intent}\n"
        f"Staging directory (write the skill files here): {staging_path}\n"
        "Note: exec/run/promote are path-scoped — do NOT pass a staging_path\n"
        "argument; the host already knows the draft location.\n\n"
        "Follow the WORKFLOW below step by step, and honor the CONTRACT after it.\n\n"
        "===== WORKFLOW =====\n"
        f"{_bundled('skill-builder.md')}\n"
        "===== CONTRACT (AUTHORING guide) =====\n"
        f"{_bundled('AUTHORING.md')}\n"
    )


def run_forge(
    intent: str,
    *,
    skills_dir: Path,
    image: str,
    env_file: Path | None = None,
    dry_run: bool = False,
) -> int:
    """Forge a skill via a host-served headless container session. Returns
    the container claude's exit code.

    With `dry_run=True` the would-run command + mcp-config path are printed
    and 0 is returned WITHOUT starting the ForgeServer (no bound port),
    spawning the container, or creating a staging dir.
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

    def _work_dir(dry: bool) -> Path:
        if dry:
            # Placeholder — NOT created on disk; the printed mount is plausible
            # but a dry run leaves no orphan staging dir.
            return staging_root / "draft-DRYRUN"
        return Path(tempfile.mkdtemp(prefix="draft-", dir=staging_root))

    def _server(work_dir: Path):
        hitl_io = HitlIO(
            stdin=sys.stdin, stdout=sys.stdout,
            stdout_lock=threading.Lock(), is_interactive=_is_interactive(sys.stdin),
        )
        return ForgeServer(
            hitl_io,
            exec_handler=RunScriptHandler(docker_image=image, skill_root=work_dir),
            run_handler=RunDraftHandler(image=image, skill_root=work_dir),
            promote_handler=PromoteSkillHandler(dest_root=skills_dir),
            staging_path=str(work_dir),
        )

    def _execute(argv: list[str]) -> int:
        # stdin DEVNULL: the host terminal's stdin belongs to the HITL reader,
        # not the container. stdout/stderr inherit so progress shows.
        return subprocess.run(argv, stdin=subprocess.DEVNULL).returncode

    return run_host_served_container(
        image=image,
        env_file=env_file,
        work_dir_factory=_work_dir,
        mode="rw",
        extra_mounts=None,
        server_factory=_server,
        prompt_factory=lambda wd: build_forge_prompt(intent, wd),
        execute=_execute,
        mcp_subdir="staging",
        dry_run=dry_run,
    )
