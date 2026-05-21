"""Resume a failed multi-phase run.

Eligibility check + state load for the `zipsa run` flow. Pure helpers
+ one dataclass. No I/O side effects beyond reading prior run dirs and
the interactive prompt (in this module's CLI helpers, added in T3).

Spec: docs/superpowers/specs/2026-05-21-resume-failed-run-design.md
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import paths as zipsa_paths


@dataclass(frozen=True)
class ResumeCandidate:
    """A prior run that satisfies every resume eligibility check.

    Returned by find_resumable_run when resume is possible; None
    otherwise. Callers use this to render the interactive prompt and,
    if accepted, pass failed_phase_index to the executor as resume_from.
    """
    skill: str
    version: str
    run_id: str
    run_dir: Path
    original_args: str
    failed_phase_index: int
    failed_phase_id: str
    failed_phase_status: str  # "failed" or "limits_exceeded"
    failed_phase_error_code: Optional[str]
    failed_phase_error_message: Optional[str]
    last_successful_phase_index: int
    last_successful_phase_id: str
    next_phase_input: object  # the loaded next_phase_input from state.json
    user_facing_summary: Optional[str]
    started_at: str  # ISO timestamp from summary.json


_RESUMABLE_STATUSES = frozenset({"failed", "limits_exceeded"})


def find_resumable_run(
    *,
    skill: str,
    current_version: str,
    current_args: str,
    current_phase_count: int,
) -> Optional[ResumeCandidate]:
    """Inspect the most recent prior run for `skill` and return a
    ResumeCandidate iff every spec eligibility condition is met.

    See spec §"Resume Eligibility" for the rules. Any failed check
    returns None silently — the caller treats None as "no resume,
    fresh start, no prompt".
    """
    if current_phase_count < 2:
        return None  # single-shot skills not resumable

    install_data_dir = zipsa_paths.skill_data_dir(skill, current_version)
    runs_dir = install_data_dir / "runs"
    if not runs_dir.exists():
        return None

    # Sort reverse-lex by name; the timestamp format is
    # YYYY-MM-DD_HHMMSS_µµµµµµ which is lex-monotone.
    candidates = sorted(
        (p for p in runs_dir.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    if not candidates:
        return None

    run_dir = candidates[0]
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        return None

    try:
        summary = json.loads(summary_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    # Eligibility checks
    if summary.get("status") not in _RESUMABLE_STATUSES:
        return None
    if summary.get("version") != current_version:
        return None
    if summary.get("user_input", "") != current_args:
        return None

    phases = summary.get("phases", [])
    if not phases:
        return None

    # Find the failed phase: walk from the end to find first non-ok.
    failed_idx: Optional[int] = None
    for i in range(len(phases) - 1, -1, -1):
        if phases[i].get("status") != "ok":
            failed_idx = i
            break
    if failed_idx is None or failed_idx == 0:
        # All ok, or the very first phase failed — no prior successful
        # phase to load state from. Fresh start.
        return None

    last_ok_idx = failed_idx - 1
    last_ok_id = phases[last_ok_idx]["id"]
    state_path = run_dir / "phases" / f"{last_ok_idx}-{last_ok_id}" / "state.json"
    if not state_path.exists():
        return None  # kill-in-the-window — treat as fresh start
    try:
        state = json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    failed_phase = phases[failed_idx]
    err = (summary.get("error") or {})

    return ResumeCandidate(
        skill=skill,
        version=current_version,
        run_id=run_dir.name,
        run_dir=run_dir,
        original_args=summary.get("user_input", ""),
        failed_phase_index=failed_idx,
        failed_phase_id=failed_phase["id"],
        failed_phase_status=failed_phase.get("status", "failed"),
        failed_phase_error_code=err.get("code"),
        failed_phase_error_message=err.get("message"),
        last_successful_phase_index=last_ok_idx,
        last_successful_phase_id=last_ok_id,
        next_phase_input=state.get("next_phase_input"),
        user_facing_summary=state.get("user_facing_summary"),
        started_at=summary.get("started_at", ""),
    )
