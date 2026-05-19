"""Per-run summary.json — the structured outcome readable by a parent
skill (or anyone with shell access).

Written to run_dir/summary.json after every run, regardless of how the
run ended (ok / business failure / limits / HITL / infra). Same shape
every time. The CLI's exit code matches the `exit_code` field (which
in turn matches `status` per the table in the design spec).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal, Any

SCHEMA_VERSION = 1

Status = Literal[
    "ok", "failed", "out_of_scope",
    "limits_exceeded", "user_declined", "infra_failed",
]


@dataclass(frozen=True)
class PhaseSummary:
    """Per-phase rollup for the summary's phases[] array."""
    id: str
    status: str       # may be any of Status; phases that ran before a
                      # failure are "ok", the failing phase is the failure status
    cost_usd: float
    turns: int


def build_summary(
    *,
    status: Status,
    exit_code: int,
    skill: str,
    version: str,
    started_at: datetime,
    finished_at: datetime,
    cost_usd: float,
    turns: int,
    phases: list[PhaseSummary],
    result: Optional[dict[str, Any]] = None,
    error: Optional[dict[str, Any]] = None,
    user_input: str = "",
    stop_reason: Optional[str] = None,
    usage: Optional[dict[str, Any]] = None,
    model_usage: Optional[dict[str, Any]] = None,
    zipsa_version: Optional[str] = None,
    runtime_image: Optional[str] = None,
    runtime_version: Optional[str] = None,
    claude_version: Optional[str] = None,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Build the summary dict in the schema documented in the design spec.

    `result` is included only when status == "ok"; `error` only otherwise.
    `user_input` is the query string the user passed to `zipsa run`.
    `stop_reason`, `usage`, `model_usage` are forwarded verbatim from
    the Claude Code SDK's final `result` event so callers don't need to
    re-scan output.jsonl for them.

    Version fields capture the exact toolchain combination that ran:
      - `zipsa_version`: the launcher's own package version
      - `runtime_image`: full image ref the user asked for (e.g.
        ghcr.io/westbrookai/zipsa-runtime:0.4.6, or a local-test tag)
      - `runtime_version`: image's baked-in ZIPSA_RUNTIME_VERSION ENV
        (may differ from runtime_image's tag when the tag is :latest)
      - `claude_version`: image's baked-in CLAUDE_CODE_VERSION ENV
      - `model`: the model Claude SDK actually ran (from the system
        init event); may differ from manifest.spec.model if SDK
        chose a different default

    Callers are responsible for the status / error semantics — this
    function does NOT enforce consistency. (The executor's status
    tracking is the source of truth.)
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "exit_code": exit_code,
        "skill": skill,
        "version": version,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": (finished_at - started_at).total_seconds(),
        "cost_usd": cost_usd,
        "turns": turns,
        "phases": [
            {"id": p.id, "status": p.status, "cost_usd": p.cost_usd, "turns": p.turns}
            for p in phases
        ],
        "result": result,
        "error": error,
        "user_input": user_input,
        "stop_reason": stop_reason,
        "usage": usage,
        "model_usage": model_usage,
        "zipsa_version": zipsa_version,
        "runtime_image": runtime_image,
        "runtime_version": runtime_version,
        "claude_version": claude_version,
        "model": model,
    }


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    """Atomically write summary.json to `path`.

    Creates parent directories as needed. Overwrites any existing file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    tmp.replace(path)
