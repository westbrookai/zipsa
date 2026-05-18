# launcher/zipsa/core/limits.py
"""Mid-execution limit tracking and enforcement.

Pure bookkeeping: `update_for_event` mutates state from each event,
`check_limits` reports a `LimitBreach` if any threshold is crossed.
The executor calls both per event and stops the run gracefully when
a breach is reported (current operation finishes, no new tool call).

Time tracking subtracts HITL wait (time spent inside an
`mcp__zipsa__ask|confirm|choose|ask_once` round-trip). Cost tracking
uses the pricing table in `zipsa.core.pricing`. Turn tracking counts
`thinking` blocks (matches the existing behavior we're replacing).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal, Optional

from .pricing import estimate_cost
from .models import SkillLimits


# Tool names whose round-trip should be excluded from compute time.
_HITL_TOOL_NAMES = frozenset(
    f"mcp__zipsa__{t}" for t in ("ask", "confirm", "choose", "ask_once")
)


@dataclass
class LimitsState:
    phase_id: str
    phase_started_at: float            # monotonic, set on phase entry
    phase_compute_started_at: float    # same as phase_started_at; used as the basis for compute elapsed
    run_started_at: float              # monotonic, set on run entry (immutable across phases)

    # Counts
    phase_turns: int = 0
    phase_cost_usd: float = 0.0
    run_turns: int = 0
    run_cost_usd: float = 0.0

    # HITL pause tracking — private
    _hitl_open_at: Optional[float] = None
    _phase_hitl_paused: float = 0.0
    _run_hitl_paused: float = 0.0

    def phase_compute_elapsed(self) -> float:
        """Wall time since phase started, minus HITL pauses."""
        now = time.monotonic()
        # Include an in-progress pause if one is open
        ongoing = (now - self._hitl_open_at) if self._hitl_open_at is not None else 0.0
        return (now - self.phase_compute_started_at) - self._phase_hitl_paused - ongoing

    def run_compute_elapsed(self) -> float:
        now = time.monotonic()
        ongoing = (now - self._hitl_open_at) if self._hitl_open_at is not None else 0.0
        return (now - self.run_started_at) - self._run_hitl_paused - ongoing


def new_state(phase_id: str) -> LimitsState:
    now = time.monotonic()
    return LimitsState(
        phase_id=phase_id,
        phase_started_at=now,
        phase_compute_started_at=now,
        run_started_at=now,
    )


def update_for_event(state: LimitsState, event: dict, model: str) -> None:
    """Mutate state based on one parsed event. No limit checks here.

    `model` is the model id used for cost estimation (falls back to
    Opus inside estimate_cost if unknown).
    """
    etype = event.get("type")

    if etype == "zipsa_phase_start":
        new_phase = event.get("phase", state.phase_id)
        now = time.monotonic()
        state.phase_id = new_phase
        state.phase_started_at = now
        state.phase_compute_started_at = now
        state.phase_turns = 0
        state.phase_cost_usd = 0.0
        state._phase_hitl_paused = 0.0
        # _hitl_open_at intentionally NOT cleared — a HITL ask could (in theory)
        # span a phase boundary, but in practice it can't (the agent must emit
        # a final JSON before the phase ends, and ask/confirm blocks that).
        return

    if etype == "assistant":
        msg = event.get("message", {}) or {}
        content = msg.get("content") or []
        # Turn counter: count thinking blocks (matches pre-existing behavior)
        for block in content:
            if block.get("type") == "thinking":
                state.phase_turns += 1
                state.run_turns += 1
        # Cost: sum usage from this message
        usage = msg.get("usage")
        if isinstance(usage, dict):
            cost = estimate_cost(model, usage)
            state.phase_cost_usd += cost
            state.run_cost_usd += cost
        # HITL pause start: tool_use for an ask/confirm/choose/ask_once
        for block in content:
            if block.get("type") == "tool_use" and block.get("name") in _HITL_TOOL_NAMES:
                if state._hitl_open_at is None:
                    state._hitl_open_at = time.monotonic()
                break
        return

    if etype == "user":
        # HITL pause end: tool_result for the open ask
        msg = event.get("message", {}) or {}
        content = msg.get("content") or []
        if content and content[0].get("type") == "tool_result" and state._hitl_open_at is not None:
            now = time.monotonic()
            paused = now - state._hitl_open_at
            state._phase_hitl_paused += paused
            state._run_hitl_paused += paused
            state._hitl_open_at = None
        return


@dataclass(frozen=True)
class LimitBreach:
    scope: Literal["phase", "aggregate"]
    kind: Literal["turns", "cost", "time"]
    value: float
    limit: float
    phase: str


def check_limits(
    state: LimitsState,
    phase_limits: Optional[SkillLimits],
    aggregate_limits: Optional[SkillLimits],
) -> Optional[LimitBreach]:
    """Return the breach to report, or None if all clear.

    Phase-level breach wins over aggregate (closer to user's stated
    intent for the running phase). Within a scope, turns / cost / time
    are checked in declaration order.
    """
    # Phase scope
    if phase_limits is not None:
        b = _check_one(state, phase_limits, "phase")
        if b is not None:
            return b
    # Aggregate scope
    if aggregate_limits is not None:
        # For aggregate, swap in run-level counters by temporarily wrapping state
        return _check_one(state, aggregate_limits, "aggregate")
    return None


def _check_one(
    state: LimitsState, limits: SkillLimits, scope: Literal["phase", "aggregate"]
) -> Optional[LimitBreach]:
    turns = state.phase_turns if scope == "phase" else state.run_turns
    cost = state.phase_cost_usd if scope == "phase" else state.run_cost_usd
    elapsed = state.phase_compute_elapsed() if scope == "phase" else state.run_compute_elapsed()

    if limits.max_turns is not None and turns > limits.max_turns:
        return LimitBreach(scope=scope, kind="turns", value=float(turns), limit=float(limits.max_turns), phase=state.phase_id)
    if limits.max_cost_usd is not None and cost > limits.max_cost_usd:
        return LimitBreach(scope=scope, kind="cost", value=cost, limit=float(limits.max_cost_usd), phase=state.phase_id)
    if limits.timeout_seconds is not None and elapsed > limits.timeout_seconds:
        return LimitBreach(scope=scope, kind="time", value=elapsed, limit=float(limits.timeout_seconds), phase=state.phase_id)
    return None
