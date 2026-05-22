"""Tool allow-list for the PreToolUse hook + Claude's --allowedTools.

Two distinct gates that BOTH must include the same names for an
always-on MCP tool (ask, recall, get_artifact, run_skill, ...) to
work end-to-end:

- `phases/.../phase-allow.json` is read by the PreToolUse hook to
  veto disallowed tool calls at execution time.
- `--allowedTools` is read by Claude Code's SDK to decide which tools
  to even expose to the model.

The always-on tools (HITL, memory, artifacts, run_skill, ToolSearch)
are appended to both surfaces. The skill's own declared tools
(spec.tools.builtin + spec.mcp[*].allowed_tools) are passed in by
the caller.

Pure functions + a module constant. No class state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .skill import Skill


# Always-on tools shared between the PreToolUse hook (phase-allow.json)
# and Claude Code's --allowedTools flag. Both surfaces must include
# these names for an always-on tool to actually work.
ALWAYS_ON_TOOLS: list[str] = [
    "mcp__zipsa__ask", "mcp__zipsa__confirm", "mcp__zipsa__choose",
    "mcp__zipsa__recall", "mcp__zipsa__remember",
    "mcp__zipsa__forget", "mcp__zipsa__list_memory",
    "mcp__zipsa__ask_once",
    "mcp__zipsa__get_artifact",
    "mcp__zipsa__run_skill",  # handler-side check gates by spec.children
    "ToolSearch",
]


def merge_always_on_tools(allowed_tools: str) -> str:
    """Merge always-on tools into a comma-separated --allowedTools string.

    Deduplicates so the output stays unique. Used by the executor when
    building the Claude Code command line.
    """
    existing = [t.strip() for t in allowed_tools.split(",") if t.strip()]
    for t in ALWAYS_ON_TOOLS:
        if t not in existing:
            existing.append(t)
    return ",".join(existing)


def write_phase_allow_file(
    output_dir: Path,
    phase_id: str,
    allowed_tools: list[str],
) -> Path:
    """Write the per-phase tool allow list consumed by the PreToolUse hook.

    The file lives next to .claude.json so it's covered by the
    /.zipsa read-only mount inside the container.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    final_tools = list(allowed_tools) + ALWAYS_ON_TOOLS
    path = output_dir / "phase-allow.json"
    path.write_text(json.dumps({"phase_id": phase_id, "allowed_tools": final_tools}))
    return path


def write_default_phase_allow_file(output_dir: Path, skill: "Skill") -> Path:
    """Write phase-allow.json for single-shot (no-phases) skills.

    The hook is mounted unconditionally, so single-shot skills also
    need an allow list — otherwise every tool call would be denied.
    Use the skill's full declared tool set (spec.tools.builtin +
    per-MCP-server allowed_tools, prefixed as mcp__<server>__<tool>).
    """
    tools = list(skill.manifest.spec.tools.builtin)
    for server in skill.manifest.spec.mcp:
        for t in server.allowed_tools:
            tools.append(f"mcp__{server.name}__{t}")
    return write_phase_allow_file(output_dir, "main", tools)
