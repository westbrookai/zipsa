"""Per-phase persistence for resume support.

Each successful phase writes its full skill envelope (the JSON the
agent emits — `{status, phase, result, state_updates,
next_phase_input, user_facing_summary}`) to
`<run_dir>/phases/<idx>-<id>/state.json`. A subsequent resumed run
reads the prior run's state.json files to populate `previous_output`
for the resumed phase AND to roll forward cost/turns into the new
summary (see executor.run's build_summary call site for the chain
aggregation).

A failed / out_of_scope / limits_exceeded phase writes nothing —
the absence of state.json is the "this phase did not complete
cleanly" signal that find_resumable_run keys on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def write_phase_state(phase_dir: Optional[Path], envelope: dict) -> None:
    """Persist the phase's full skill envelope to state.json.

    Called after a phase completes with status="ok" so a future
    `zipsa run` invocation can resume from the next phase using the
    persisted `next_phase_input`. No-op when phase_dir is None
    (dry-run, shell, or single-shot path where multi-phase per-phase
    dirs aren't created).
    """
    if phase_dir is None:
        return
    path = phase_dir / "state.json"
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2))


def load_resume_state(run_dir: Path, resume_from: int) -> object:
    """Read the next_phase_input from the phase BEFORE resume_from.

    Used by `_execute_phases` when resume_from is set: the previous
    phase's state.json is the source of truth for what the resumed
    phase should see as previous_output.

    Phase dirs are named `<idx>-<phase_id>`; we scan the phases/
    directory for the one starting with `f"{resume_from-1}-"`.

    Raises FileNotFoundError if the prior phase's state.json is missing.
    """
    prev_idx = resume_from - 1
    phases_dir = run_dir / "phases"
    if phases_dir.exists():
        for d in sorted(phases_dir.iterdir()):
            if d.name.startswith(f"{prev_idx}-"):
                state_path = d / "state.json"
                if state_path.exists():
                    return json.loads(state_path.read_text()).get(
                        "next_phase_input",
                    )
                break
    raise FileNotFoundError(
        f"state.json for phase {prev_idx} not found under {phases_dir}"
    )
