"""`zipsa create` — author a skill in a separate, observable claude run.

The point of this command (vs. a person or an assistant hand-writing
zipsa-dist files inline) is separation: authoring happens in a fresh
`claude -p` process with no conversation history, the user watches its
output stream by, and the same intent reproduces the same kind of
result. The authoring instructions live in the zipsa-skill-builder
project skill + skills/AUTHORING.md, which the spawned claude reads.

Gotchas:
- Runs claude with --permission-mode bypassPermissions so the
  autonomous author/test loop doesn't halt on every Write/Bash. Same
  trust level as the user running claude themselves in their repo.
- Requires the zipsa-skill-builder skill to be present (so this works
  in the zipsa repo, not an arbitrary pip-installed location yet).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_SKILL_REL = Path(".claude/skills/zipsa-skill-builder/SKILL.md")


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE env file (blank lines and #comments ignored)."""
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def find_repo_root(start: Path) -> Path | None:
    """Walk up from `start` for the dir holding the zipsa-skill-builder
    skill (and skills/AUTHORING.md alongside it). Returns None if not
    found."""
    start = start.resolve()
    for d in (start, *start.parents):
        if (d / _SKILL_REL).is_file():
            return d
    return None


def build_create_prompt(
    intent: str,
    skill_path: Path,
    *,
    zipsa_cmd: str = "zipsa exec",
) -> str:
    """Assemble the prompt for the spawned claude.

    Points it at the workflow + contract by path (robust whether or
    not headless mode auto-discovers project skills), states the
    intent and target, and demands the self-test loop. `zipsa_cmd` is
    the exec invocation the agent should test with — overridable so
    dev setups (where `zipsa` isn't on PATH) can pass a working form.
    """
    return (
        "You are authoring a zipsa skill. Read the workflow in\n"
        ".claude/skills/zipsa-skill-builder/SKILL.md and the contract in\n"
        "skills/AUTHORING.md first, then follow them exactly.\n\n"
        f"Intent: {intent}\n"
        f"Target skill directory: {skill_path}\n\n"
        f"Author the phase files into {skill_path}/zipsa-dist/ plus a short\n"
        "SKILL.md (intent prose, for humans). Then verify with\n"
        f"`{zipsa_cmd} {skill_path} --local`: iterate until the happy path\n"
        "returns a sensible result AND a bad-input case exits non-zero with\n"
        "a clear stderr message. Do not stop until it actually runs.\n"
    )


def run_create(
    intent: str,
    skill_path: Path,
    *,
    root: Path,
    zipsa_cmd: str = "zipsa exec",
    env_file: Path | None = None,
    claude_cmd: tuple[str, ...] = ("claude",),
) -> int:
    """Spawn claude headless to author the skill. Returns its exit code.

    stdio is inherited (not captured) so the user sees the authoring
    happen — that observability is the whole reason this command
    exists. claude runs with cwd=root so it can read the skill +
    AUTHORING.md and write under skills/.

    The spawned claude is headless and needs CLAUDE_CODE_OAUTH_TOKEN in
    its environment — the host's interactive login isn't picked up. We
    merge the global env file (~/.zipsa/.env) into the subprocess env,
    the same token the container LLM phases get via --env-file. It also
    propagates to any inner `claude -p` the authoring agent triggers
    via `zipsa exec --local`.
    """
    if env_file is None:
        from .paths import global_env_file

        env_file = global_env_file()

    prompt = build_create_prompt(intent, skill_path, zipsa_cmd=zipsa_cmd)
    argv = [
        *claude_cmd,
        "-p", prompt,
        "--permission-mode", "bypassPermissions",
    ]
    env = dict(os.environ)
    env.update(_load_env_file(env_file))
    proc = subprocess.run(argv, cwd=root, env=env)
    return proc.returncode
