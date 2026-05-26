"""RunLogHandler — read a past run's output.jsonl and return a compact
per-turn summary.

Powers mcp__zipsa__read_run_log. Skill-builder calls this after
running its draft skill to analyze what the agent did turn-by-turn
("did it make the right tool choice? was the prompt unclear? could
this loop be avoided?") before asking the author what to change.

The per-event formatting is the **runtime plugin's** responsibility
(runtimes/claude.py::format_event_compact and equivalents in future
runtime plugins) — this module is thin orchestration: find the
right output.jsonl, parse each line as JSON, hand the event off to
the runtime formatter, cap the joined output. The producer side of
this codec is core/executor.py's stream-to-output_file writer.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Optional

from .. import paths as zipsa_paths


# Default total-output cap. Matches the worst-case (1.79MB raw / 62
# turns compacted to ~17KB) measured against real daily-progress logs
# with multiple-order-of-magnitude headroom.
_DEFAULT_MAX_BYTES = 100_000


def _is_unsafe_segment(value: str) -> bool:
    """Single-segment POSIX path safety check — mirrors ArtifactHandler."""
    if not value:
        return True
    if len(value) > 255:
        return True
    if "\\" in value or "\x00" in value:
        return True
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or len(p.parts) != 1:
        return True
    return False


class RunLogHandler:
    """Read output.jsonl(s) for a past run and return compact summary."""

    def read(
        self,
        *,
        skill: str,
        version: str,
        run_id: str,
        phase_id: Optional[str] = None,
        max_bytes: int = _DEFAULT_MAX_BYTES,
    ) -> dict:
        # Validate every path segment up-front. Same standard ArtifactHandler
        # uses: a `run_id` of "../../victim/runs/x" would otherwise resolve
        # to a different skill's run dir even with a containment guard.
        for field, value in (("skill", skill), ("version", version),
                              ("run_id", run_id)):
            if _is_unsafe_segment(value):
                raise RuntimeError(
                    f"RUN_LOG_BAD_NAME: {field} must be a flat path segment, got {value!r}"
                )
        if phase_id is not None and _is_unsafe_segment(phase_id):
            raise RuntimeError(
                f"RUN_LOG_BAD_NAME: phase_id must be a flat path segment, got {phase_id!r}"
            )

        run_dir = zipsa_paths.skill_runs_dir(skill, version) / run_id
        if not run_dir.exists():
            raise RuntimeError(
                f"RUN_LOG_NOT_FOUND: {skill}@{version}/runs/{run_id}"
            )

        # Defense-in-depth: resolved path must land under ZIPSA_HOME.
        try:
            run_dir.resolve(strict=False).relative_to(
                zipsa_paths.zipsa_home().resolve()
            )
        except ValueError as e:
            raise RuntimeError(
                "RUN_LOG_BAD_NAME: resolved path escapes ZIPSA_HOME"
            ) from e

        # Locate output.jsonl files. Single-phase = one file at the run
        # dir root. Multi-phase = one per phase under phases/<idx>-<id>/.
        # If phase_id is given, restrict to that phase.
        log_files = self._find_log_files(run_dir, phase_id)
        if not log_files:
            raise RuntimeError(
                f"RUN_LOG_NOT_FOUND: no output.jsonl under {run_dir}"
                + (f" (phase={phase_id})" if phase_id else "")
            )

        runtime = self._get_runtime(skill, version)

        # Collect lines from every log file in order, prefixed with a
        # phase marker when there are multiple. We collect FULL set of
        # lines first, then trim from the FRONT to fit max_bytes — the
        # tail (most recent activity) is what analysis cares about most.
        lines: list[str] = []
        total_turns = 0
        total_cost_usd = 0.0
        for log_file in log_files:
            if len(log_files) > 1:
                # Multi-phase context marker so the agent knows where the
                # boundary is.
                phase_label = log_file.parent.name  # e.g. "0-precheck"
                lines.append(f"--- phase: {phase_label} ---")
            for ev in self._iter_events(log_file):
                total_turns += 1
                if ev.get("type") == "result":
                    cost = ev.get("total_cost_usd")
                    if isinstance(cost, (int, float)):
                        total_cost_usd += float(cost)
                line = runtime.format_event_compact(ev)
                if line:
                    lines.append(line)

        joined, truncated = self._cap_from_tail(lines, max_bytes)

        return {
            "log": joined,
            "total_turns": total_turns,
            "total_cost_usd": round(total_cost_usd, 6),
            "phase_id": phase_id,
            "truncated": truncated,
        }

    def _find_log_files(
        self, run_dir: Path, phase_id: Optional[str]
    ) -> list[Path]:
        """Return the output.jsonl files for this run in chronological
        order (single-phase root first, then phases/0-*, 1-*, …)."""
        results: list[Path] = []
        root_log = run_dir / "output.jsonl"
        if root_log.exists() and phase_id is None:
            results.append(root_log)
        phases_dir = run_dir / "phases"
        if phases_dir.exists():
            for phase_dir in sorted(phases_dir.iterdir()):
                if not phase_dir.is_dir():
                    continue
                if phase_id is not None and not phase_dir.name.endswith(
                    f"-{phase_id}"
                ):
                    continue
                log = phase_dir / "output.jsonl"
                if log.exists():
                    results.append(log)
        return results

    def _iter_events(self, log_file: Path):
        with open(log_file) as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    # Malformed lines are skipped silently — output.jsonl
                    # is occasionally written mid-flush, and a tail-of-log
                    # corrupted line shouldn't blank out the analysis.
                    continue

    def _cap_from_tail(
        self, lines: list[str], max_bytes: int
    ) -> tuple[str, bool]:
        """Build joined output keeping only the most recent lines that
        fit under max_bytes. Returns (joined, truncated_flag)."""
        total = 0
        kept: list[str] = []
        for line in reversed(lines):
            line_size = len(line) + 1  # +1 for the newline join
            if total + line_size > max_bytes:
                return "\n".join(reversed(kept)), True
            kept.append(line)
            total += line_size
        return "\n".join(reversed(kept)), False

    def _get_runtime(self, skill: str, version: str):
        """Look up the runtime plugin used for this run.

        For MVP we hardcode `claude` — it's the only registered runtime
        today. When Codex/Gemini land, summary.json should record the
        runtime so we can dispatch correctly here; until then this is
        a one-line change.
        """
        from ..runtimes import get_runtime
        return get_runtime("claude")
