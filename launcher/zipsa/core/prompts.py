"""System prompt + per-phase user message builders.

Both functions are pure renderers — they read the skill manifest +
the template files in `launcher/zipsa/system-prompts/` and return
strings. No I/O beyond template reads, no class state.

The runtime-contract.md content is injected verbatim into every
system prompt (so the agent sees the contract guaranteeing what the
runtime will and will not do). The user message is rendered once per
phase and includes execution_context (date, run_id, allowed_tools,
previous_phase_output, skill_state, ...).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .skill import Skill

# Placeholder POSIX locales that don't carry language preference info —
# `C` is the historical "C / POSIX" default; `POSIX` is its alias. Both
# should fall back to the launcher's default language rather than be
# treated as their literal first letters.
_POSIX_PLACEHOLDER_LOCALES = frozenset({"c", "posix"})
# Default for fresh users on systems where no locale is set, or where
# the locale is a placeholder like `C`. English chosen because the
# tool's own docs and most skill source code are in English — safer
# bet for a stranger than guessing.
_DEFAULT_LANGUAGE = "en"
# Strict shape: 2-3 lowercase ASCII letters. Catches typos and garbage
# (e.g. `LANG=zh_CN_garbage`) instead of passing them downstream.
_LANGUAGE_CODE_RE = re.compile(r"^[a-z]{2,3}$")


def _detect_user_language() -> str:
    """Return a 2-3 letter ISO language code from POSIX locale env vars.

    Reads `LC_ALL` first (the POSIX override), then `LANG`. Strips the
    region and encoding suffix:
      `ko_KR.UTF-8`  → `ko`
      `en_US.UTF-8`  → `en`
      `ja`           → `ja`
      `C` / `POSIX`  → fallback (`en`)
      missing/empty  → fallback (`en`)
      garbage shape  → fallback (`en`)

    The detected value lands in `execution_context.user_language` so
    the agent can localize all user-facing strings. Users on hosts with
    a misleading $LANG can override later via the global skill memory
    (ask_once / remember with scope="global", key="user_language").
    """
    raw = (os.environ.get("LC_ALL") or os.environ.get("LANG") or "").strip()
    if not raw:
        return _DEFAULT_LANGUAGE
    # ko_KR.UTF-8 → ko_KR → ko
    code = raw.split(".", 1)[0].split("_", 1)[0].lower()
    if code in _POSIX_PLACEHOLDER_LOCALES:
        return _DEFAULT_LANGUAGE
    if not _LANGUAGE_CODE_RE.match(code):
        return _DEFAULT_LANGUAGE
    return code

CONTAINER_WORKSPACE = "/home/agent/workspace"


def build_system_prompt(skill: Skill) -> str:
    """Render the system prompt for `skill`.

    Combines runtime-contract.md (verbatim) + a per-skill body
    (purpose + instructions + optional MCP server-path section) via
    system-prompt-template.md.
    """
    prompts_dir = Path(__file__).parent.parent / "system-prompts"
    contract = (prompts_dir / "runtime-contract.md").read_text(encoding="utf-8")
    template = (prompts_dir / "system-prompt-template.md").read_text(encoding="utf-8")

    mcp_paths_section = ""
    mounted_servers = [
        s for s in skill.manifest.spec.mcp
        if s.type == "stdio" and s.mount
    ]
    if mounted_servers:
        lines = ["# MCP Server Paths"]
        for server in mounted_servers:
            lines.append(f"- {server.name}: {CONTAINER_WORKSPACE}/{server.name}")
        mcp_paths_section = "\n".join(lines) + "\n\n"

    skill_body = f"""You are the {skill.name} agent (v{skill.manifest.metadata.version}).

# Purpose
{skill.manifest.spec.purpose}

# Instructions
{skill.instructions}

{mcp_paths_section}# Behavior rules
- Single-task focused: only do what your purpose describes
- Be concise: no preamble, just answer
- Decline gracefully for off-topic requests
"""

    meta = skill.manifest.metadata
    return template.format(
        contract=contract,
        skill_name=meta.name,
        skill_version=meta.version,
        skill_body=skill_body,
    )


def build_user_message(
    skill: Skill,
    phase_id: str,
    phase_goal: str,
    phase_allowed_tools: str,
    previous_phase_output: Optional[str],
    skill_state: dict,
    user_query: str,
    run_id: str = "unknown",
) -> str:
    """Render the per-phase user message including execution_context."""
    from tzlocal import get_localzone

    prompts_dir = Path(__file__).parent.parent / "system-prompts"
    template = (prompts_dir / "user-message-template.md").read_text(encoding="utf-8")

    now = datetime.now().astimezone()
    tz_offset = now.strftime("%z")
    tz_offset_fmt = f"UTC{tz_offset[:3]}:{tz_offset[3:]}"
    tz_iana = str(get_localzone())

    config_json = json.dumps(skill.manifest.spec.config, ensure_ascii=False)
    state_json = json.dumps(skill_state, ensure_ascii=False)
    prev_output = json.dumps(previous_phase_output, ensure_ascii=False)

    return template.format(
        date=now.strftime("%Y-%m-%d"),
        time=now.strftime("%H:%M:%S"),
        timezone=f"{now.strftime('%Z')} ({tz_offset_fmt})",
        tz_iana=tz_iana,
        user_language=_detect_user_language(),
        run_id=run_id,
        phase_id=phase_id,
        phase_goal=phase_goal,
        allowed_tools=phase_allowed_tools,
        previous_phase_output=prev_output,
        skill_state=state_json,
        user_query=user_query,
        config=config_json,
    )
