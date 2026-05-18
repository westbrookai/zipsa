# Enforce Skill Limits Mid-Execution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the broken / half-broken limit checks in `executor.py` with a single `_check_limits()` call site backed by two small new modules. After this PR, `max_turns`, `max_cost_usd`, `timeout_seconds` all enforce at both phase and aggregate scope, gracefully (current op finishes), with HITL wait excluded from timeout.

**Architecture:** Spec at `docs/superpowers/specs/2026-05-19-enforce-limits-design.md`. New `zipsa/core/pricing.py` (model→USD table + per-usage-block cost), new `zipsa/core/limits.py` (state + update_for_event + check_limits + LimitBreach dataclass). Executor's two duplicated streaming branches collapse to one that calls into the limits module per event. New `zipsa_limits_breach` event surfaces in the renderer.

**Tech Stack:** Python 3.10+ stdlib (dataclasses, time.monotonic), pytest. No new runtime dependencies.

---

## File map

| File | Role |
|---|---|
| `launcher/zipsa/core/pricing.py` (new) | `ModelPricing` dataclass + `PRICING` dict + `estimate_cost()` |
| `launcher/zipsa/core/limits.py` (new) | `LimitsState`, `update_for_event`, `check_limits`, `LimitBreach` |
| `launcher/zipsa/core/executor.py` | Collapse duplicated stream branches; call into `limits` per event; emit `zipsa_limits_breach`; drop old inline turn/cost/timeout code |
| `launcher/zipsa/core/renderer.py` | Render `zipsa_limits_breach` event |
| `launcher/tests/test_pricing.py` (new) | Pure unit tests |
| `launcher/tests/test_limits.py` (new) | Pure unit tests |
| `launcher/tests/test_executor.py` | Replace inline limit tests; add HITL-exclusion test |
| `launcher/tests/test_renderer.py` | Add breach-event render test |

---

## Commit boundaries

| Commit | What |
|---|---|
| **1** | `feat(pricing): ModelPricing + PRICING table + estimate_cost` (Task 1, pure module + tests) |
| **2** | `feat(limits): LimitsState + update_for_event + check_limits` (Task 2, pure module + tests) |
| **3** | `feat(renderer): render zipsa_limits_breach event` (Task 3, small renderer-only change with a hand-crafted breach event) |
| **4** | `feat(executor): single _check_limits call site, graceful stop on breach` (Task 4, the executor refactor — biggest commit, includes integration tests) |

The order lets tasks 1–3 land independently before the executor refactor consumes them. Bisect-friendly.

---

## Task 1: `pricing.py` — model → USD

**Files:**
- Create: `launcher/zipsa/core/pricing.py`
- Create: `launcher/tests/test_pricing.py`

- [ ] **Step 1: Write the failing test**

```python
# launcher/tests/test_pricing.py
"""Pricing module tests."""

import pytest

from zipsa.core.pricing import PRICING, ModelPricing, estimate_cost


class TestPricingTable:
    def test_haiku_present(self):
        p = PRICING["claude-haiku-4-5-20251001"]
        assert isinstance(p, ModelPricing)
        # Cheapest model — should have small per-token rates
        assert p.input < 5.0
        assert p.output < 20.0

    def test_opus_present(self):
        p = PRICING["claude-opus-4-7"]
        assert isinstance(p, ModelPricing)
        # Most expensive — should be more than Sonnet
        assert p.input > PRICING["claude-sonnet-4-6"].input

    def test_pricing_is_frozen(self):
        p = PRICING["claude-opus-4-7"]
        with pytest.raises(Exception):  # FrozenInstanceError
            p.input = 999  # type: ignore


class TestEstimateCost:
    def test_zero_usage_zero_cost(self):
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        assert estimate_cost("claude-haiku-4-5-20251001", usage) == 0.0

    def test_per_token_math_matches_table(self):
        # Haiku: $0.80 / 1M input. 1,000,000 input tokens => $0.80
        usage = {
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        cost = estimate_cost("claude-haiku-4-5-20251001", usage)
        assert cost == pytest.approx(PRICING["claude-haiku-4-5-20251001"].input)

    def test_all_four_token_kinds_summed(self):
        usage = {
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "cache_read_input_tokens": 1_000_000,
            "cache_creation_input_tokens": 1_000_000,
        }
        p = PRICING["claude-haiku-4-5-20251001"]
        expected = p.input + p.output + p.cache_read + p.cache_creation
        assert estimate_cost("claude-haiku-4-5-20251001", usage) == pytest.approx(expected)

    def test_missing_fields_treated_as_zero(self):
        # Real usage blocks sometimes omit fields with value 0
        usage = {"input_tokens": 100}
        cost = estimate_cost("claude-haiku-4-5-20251001", usage)
        # Only input counted; output/cache fields default to 0
        expected = 100 / 1_000_000 * PRICING["claude-haiku-4-5-20251001"].input
        assert cost == pytest.approx(expected)

    def test_unknown_model_falls_back_to_opus(self):
        """Unknown model => use Opus pricing (safety upper bound).
        Triggers limits EARLIER, not LATER."""
        usage = {"input_tokens": 1_000_000}
        cost_unknown = estimate_cost("does-not-exist-v9", usage)
        cost_opus = estimate_cost("claude-opus-4-7", usage)
        assert cost_unknown == cost_opus
```

- [ ] **Step 2: Run test to verify it fails**

Run from `launcher/`:
```bash
uv run pytest tests/test_pricing.py -v
```
Expected: `ModuleNotFoundError: No module named 'zipsa.core.pricing'`.

- [ ] **Step 3: Implement `pricing.py`**

```python
# launcher/zipsa/core/pricing.py
"""Per-model token pricing.

Updated manually from https://docs.anthropic.com/en/docs/about-claude/pricing.
Pricing is in USD per 1,000,000 tokens.

The estimate_cost() function multiplies token counts from a Claude
Code `usage` block by these per-million rates. The launcher uses this
to enforce `max_cost_usd` mid-execution (the SDK only reports cost on
the final `result` event, which is too late).

Unknown models fall back to the most expensive model (Opus) so a
mis-named manifest trips the budget EARLIER, not later.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    """USD per 1,000,000 tokens. All four kinds are billed separately."""
    input: float
    output: float
    cache_read: float
    cache_creation: float


# Source: https://docs.anthropic.com/en/docs/about-claude/pricing — 2026-05-19.
PRICING: dict[str, ModelPricing] = {
    "claude-opus-4-7":           ModelPricing(input=15.00, output=75.00, cache_read=1.50, cache_creation=18.75),
    "claude-sonnet-4-6":         ModelPricing(input=3.00,  output=15.00, cache_read=0.30, cache_creation=3.75),
    "claude-haiku-4-5-20251001": ModelPricing(input=0.80,  output=4.00,  cache_read=0.08, cache_creation=1.00),
}

_FALLBACK_MODEL = "claude-opus-4-7"

_USAGE_KEYS = (
    ("input_tokens", "input"),
    ("output_tokens", "output"),
    ("cache_read_input_tokens", "cache_read"),
    ("cache_creation_input_tokens", "cache_creation"),
)


def estimate_cost(model: str, usage: dict) -> float:
    """Sum the four billable token classes against the model's rates.

    Missing usage keys default to 0. Unknown model => Opus pricing.
    """
    p = PRICING.get(model) or PRICING[_FALLBACK_MODEL]
    total = 0.0
    for usage_key, attr in _USAGE_KEYS:
        n = usage.get(usage_key, 0) or 0
        rate = getattr(p, attr)
        total += n / 1_000_000 * rate
    return total
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_pricing.py -v
```
Expected: all passing.

- [ ] **Step 5: Full suite — no regressions**

```bash
uv run pytest
```
Expected: 395 baseline + N new (likely 7).

- [ ] **Step 6: Commit (boundary 1)**

```bash
git add launcher/zipsa/core/pricing.py launcher/tests/test_pricing.py
git commit -m "feat(pricing): model→USD table for mid-execution cost enforcement

Anthropic SDK only reports total_cost_usd on the final result event,
too late to enforce max_cost_usd mid-stream. This module turns the
per-message usage blocks (which DO arrive in real time) into running
cost estimates via a hardcoded pricing table. Unknown models fall
back to Opus pricing so a mis-named manifest trips the budget early,
not late."
```

---

## Task 2: `limits.py` — state + update + check

**Files:**
- Create: `launcher/zipsa/core/limits.py`
- Create: `launcher/tests/test_limits.py`

- [ ] **Step 1: Write the failing test**

```python
# launcher/tests/test_limits.py
"""Limits module tests — pure unit, no Docker."""

from dataclasses import asdict
from unittest.mock import patch

import pytest

from zipsa.core.limits import (
    LimitBreach,
    LimitsState,
    check_limits,
    new_state,
    update_for_event,
)
from zipsa.core.models import SkillLimits


PRICING_MODEL = "claude-haiku-4-5-20251001"


def _assistant_event(model: str, usage: dict, has_thinking: bool = True):
    """Shape matches what runtime.parse_output produces."""
    content = []
    if has_thinking:
        content.append({"type": "thinking", "thinking": "..."})
    return {
        "type": "assistant",
        "message": {"model": model, "content": content, "usage": usage},
    }


def _tool_use(name: str) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": name, "input": {}}]},
    }


def _tool_result() -> dict:
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": "tu_1"}]},
    }


def _phase_start(phase_id: str = "p1") -> dict:
    return {"type": "zipsa_phase_start", "phase": phase_id}


class TestNewState:
    def test_starts_zeroed(self):
        with patch("zipsa.core.limits.time.monotonic", return_value=100.0):
            s = new_state("phase-a")
        assert s.phase_id == "phase-a"
        assert s.phase_turns == 0
        assert s.run_turns == 0
        assert s.phase_cost_usd == 0.0
        assert s.run_cost_usd == 0.0
        assert s.phase_started_at == 100.0
        assert s.run_started_at == 100.0


class TestUpdateForEvent:
    def test_thinking_block_increments_both_turn_counters(self):
        s = new_state("p")
        update_for_event(s, _assistant_event(PRICING_MODEL, {"output_tokens": 1}), PRICING_MODEL)
        assert s.phase_turns == 1
        assert s.run_turns == 1

    def test_assistant_usage_adds_to_phase_and_run_cost(self):
        s = new_state("p")
        usage = {"input_tokens": 1_000_000}  # Haiku $0.80
        update_for_event(s, _assistant_event(PRICING_MODEL, usage), PRICING_MODEL)
        assert s.phase_cost_usd == pytest.approx(0.80)
        assert s.run_cost_usd == pytest.approx(0.80)

    def test_phase_start_resets_phase_counters_keeps_run(self):
        s = new_state("p1")
        update_for_event(s, _assistant_event(PRICING_MODEL, {"input_tokens": 1_000_000}), PRICING_MODEL)
        assert s.phase_cost_usd == pytest.approx(0.80)
        assert s.run_cost_usd == pytest.approx(0.80)
        update_for_event(s, _phase_start("p2"), PRICING_MODEL)
        assert s.phase_id == "p2"
        assert s.phase_turns == 0
        assert s.phase_cost_usd == 0.0
        # Run-level preserved
        assert s.run_cost_usd == pytest.approx(0.80)

    def test_hitl_pause_and_resume_excludes_compute_time(self):
        s = new_state("p")
        # t=100: phase started
        with patch("zipsa.core.limits.time.monotonic", return_value=100.0):
            s.phase_compute_started_at = 100.0
        # t=110: agent invokes ask
        with patch("zipsa.core.limits.time.monotonic", return_value=110.0):
            update_for_event(s, _tool_use("mcp__zipsa__ask"), PRICING_MODEL)
        # t=170: user responds (60s HITL wait)
        with patch("zipsa.core.limits.time.monotonic", return_value=170.0):
            update_for_event(s, _tool_result(), PRICING_MODEL)
        # phase_compute_elapsed should subtract the 60s HITL wait
        with patch("zipsa.core.limits.time.monotonic", return_value=180.0):
            elapsed = s.phase_compute_elapsed()
        # Total wall: 80s. HITL: 60s. Compute: 20s.
        assert elapsed == pytest.approx(20.0)

    def test_non_zipsa_tool_use_does_not_pause(self):
        s = new_state("p")
        with patch("zipsa.core.limits.time.monotonic", return_value=100.0):
            s.phase_compute_started_at = 100.0
        with patch("zipsa.core.limits.time.monotonic", return_value=110.0):
            update_for_event(s, _tool_use("Bash"), PRICING_MODEL)
        with patch("zipsa.core.limits.time.monotonic", return_value=120.0):
            update_for_event(s, _tool_result(), PRICING_MODEL)
        with patch("zipsa.core.limits.time.monotonic", return_value=130.0):
            elapsed = s.phase_compute_elapsed()
        # Wall 30s, no HITL pause -> compute 30s
        assert elapsed == pytest.approx(30.0)

    def test_ask_once_cached_brief_pause_still_excluded(self):
        """ask_once cache hit: tool_use and tool_result come ~ms apart."""
        s = new_state("p")
        with patch("zipsa.core.limits.time.monotonic", return_value=100.0):
            s.phase_compute_started_at = 100.0
            update_for_event(s, _tool_use("mcp__zipsa__ask_once"), PRICING_MODEL)
        with patch("zipsa.core.limits.time.monotonic", return_value=100.1):
            update_for_event(s, _tool_result(), PRICING_MODEL)
        # ~0.1s HITL excluded, otherwise everything stays computed
        with patch("zipsa.core.limits.time.monotonic", return_value=110.0):
            elapsed = s.phase_compute_elapsed()
        assert elapsed == pytest.approx(9.9, abs=0.01)


class TestCheckLimits:
    def _limits(self, **kw) -> SkillLimits:
        defaults = {"max_turns": 999, "max_cost_usd": 99.0, "timeout_seconds": 9999}
        defaults.update(kw)
        return SkillLimits(**defaults)

    def test_no_breach_returns_none(self):
        s = new_state("p")
        s.phase_turns = 1
        s.phase_cost_usd = 0.001
        # Compute time effectively 0 inside test
        assert check_limits(s, self._limits(), self._limits()) is None

    def test_phase_turn_breach(self):
        s = new_state("p")
        s.phase_turns = 5
        b = check_limits(s, self._limits(max_turns=4), self._limits())
        assert b is not None
        assert b.scope == "phase"
        assert b.kind == "turns"
        assert b.value == 5
        assert b.limit == 4

    def test_phase_cost_breach(self):
        s = new_state("p")
        s.phase_cost_usd = 0.11
        b = check_limits(s, self._limits(max_cost_usd=0.10), self._limits())
        assert b is not None
        assert b.scope == "phase"
        assert b.kind == "cost"

    def test_phase_time_breach_excludes_hitl(self):
        s = new_state("p")
        with patch("zipsa.core.limits.time.monotonic", return_value=100.0):
            s.phase_compute_started_at = 100.0
            update_for_event(s, _tool_use("mcp__zipsa__ask"), PRICING_MODEL)
        with patch("zipsa.core.limits.time.monotonic", return_value=200.0):
            update_for_event(s, _tool_result(), PRICING_MODEL)
        # Wall = 200s. HITL pause = 100s. Compute = 0s. Should NOT breach 60s timeout.
        with patch("zipsa.core.limits.time.monotonic", return_value=200.0):
            b = check_limits(s, self._limits(timeout_seconds=60), self._limits())
        assert b is None
        # Now consume 70s of compute
        with patch("zipsa.core.limits.time.monotonic", return_value=270.0):
            b = check_limits(s, self._limits(timeout_seconds=60), self._limits())
        assert b is not None
        assert b.scope == "phase"
        assert b.kind == "time"

    def test_aggregate_turn_breach(self):
        s = new_state("p")
        s.phase_turns = 1
        s.run_turns = 20
        b = check_limits(s, self._limits(max_turns=999), self._limits(max_turns=15))
        assert b is not None
        assert b.scope == "aggregate"
        assert b.kind == "turns"

    def test_aggregate_cost_breach(self):
        s = new_state("p")
        s.run_cost_usd = 0.50
        b = check_limits(s, self._limits(), self._limits(max_cost_usd=0.45))
        assert b is not None
        assert b.scope == "aggregate"
        assert b.kind == "cost"

    def test_phase_breach_wins_over_aggregate(self):
        """Both phase AND aggregate over limit -> report the phase one
        (closer to the user's intent)."""
        s = new_state("p")
        s.phase_cost_usd = 0.11
        s.run_cost_usd = 0.50
        b = check_limits(
            s,
            self._limits(max_cost_usd=0.10),
            self._limits(max_cost_usd=0.45),
        )
        assert b.scope == "phase"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_limits.py -v
```
Expected: ImportError on `zipsa.core.limits`.

- [ ] **Step 3: Implement `limits.py`**

```python
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
from dataclasses import dataclass, field
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
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_limits.py -v
```
Expected: all passing.

- [ ] **Step 5: Full suite — no regressions**

```bash
uv run pytest
```
Expected: green (395 + 7 + ~13 new = ~415).

- [ ] **Step 6: Commit (boundary 2)**

```bash
git add launcher/zipsa/core/limits.py launcher/tests/test_limits.py
git commit -m "feat(limits): per-event state machine for turns, cost, time

LimitsState tracks both phase and aggregate counters in a single
dataclass; update_for_event() mutates it from each parsed event
(thinking blocks for turns, usage blocks for cost, HITL ask/result
boundaries for time pause/resume). check_limits() returns a
LimitBreach if any threshold is crossed.

Phase-level breach wins over aggregate. Time tracking subtracts HITL
wait (mcp__zipsa__ask|confirm|choose|ask_once round-trip) so an
ask-heavy phase doesn't trip timeout from user typing time alone.

No executor integration yet — this commit ships the pure module
plus tests."
```

---

## Task 3: Renderer — show `zipsa_limits_breach` event

**Files:**
- Modify: `launcher/zipsa/core/renderer.py`
- Test: `launcher/tests/test_renderer.py`

- [ ] **Step 1: Write the failing test**

Add to `launcher/tests/test_renderer.py`:

```python
class TestLimitsBreachEvent:
    """zipsa_limits_breach renders as a clear red footer that says
    which limit (phase/aggregate × turns/cost/time) was exceeded."""

    def test_phase_cost_breach_rendered(self, capsys):
        events = [
            {
                "type": "zipsa_limits_breach",
                "scope": "phase",
                "kind": "cost",
                "value": 0.107,
                "limit": 0.10,
                "phase": "report",
            }
        ]
        render(iter(events), OutputMode.pretty)
        out = capsys.readouterr().out
        assert "Limit exceeded" in out or "limit exceeded" in out.lower()
        assert "report" in out
        assert "0.10" in out
        assert "0.107" in out or "0.11" in out

    def test_aggregate_time_breach_rendered(self, capsys):
        events = [
            {
                "type": "zipsa_limits_breach",
                "scope": "aggregate",
                "kind": "time",
                "value": 2100.5,
                "limit": 2000.0,
                "phase": "post",
            }
        ]
        render(iter(events), OutputMode.pretty)
        out = capsys.readouterr().out
        assert "aggregate" in out.lower()
        assert "time" in out.lower() or "timeout" in out.lower()

    def test_json_mode_passes_through(self, capsys):
        event = {"type": "zipsa_limits_breach", "scope": "phase", "kind": "turns",
                 "value": 5, "limit": 4, "phase": "draft"}
        render(iter([event]), OutputMode.json)
        out = capsys.readouterr().out.strip()
        import json as _json
        assert _json.loads(out) == event
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_renderer.py::TestLimitsBreachEvent -v
```
Expected: failures (event not handled, no output).

- [ ] **Step 3: Implement in `renderer.py`**

Locate the existing `if event_type == "zipsa_phase_error":` block (around line 47). Add a sibling block just before it:

```python
    if event_type == "zipsa_limits_breach":
        if mode == OutputMode.json:
            return None  # printed verbatim in json mode at the top of render()
        scope = event.get("scope", "?")
        kind = event.get("kind", "?")
        value = event.get("value", 0)
        limit = event.get("limit", 0)
        phase = event.get("phase", "?")
        if kind == "cost":
            value_s = f"${value:.4f}"
            limit_s = f"${limit:.4f}"
        elif kind == "time":
            value_s = f"{value:.1f}s"
            limit_s = f"{limit:.1f}s"
        else:  # turns
            value_s = f"{int(value)} turns"
            limit_s = f"{int(limit)} turns"
        return (
            f"\n{_RED}✗ Limit exceeded — {scope} {kind} for phase '{phase}': "
            f"{value_s} > {limit_s}{_RESET}"
        )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_renderer.py::TestLimitsBreachEvent -v
```
Expected: 3 passing.

Full suite:
```bash
uv run pytest
```
Expected: green.

- [ ] **Step 5: Commit (boundary 3)**

```bash
git add launcher/zipsa/core/renderer.py launcher/tests/test_renderer.py
git commit -m "feat(renderer): render zipsa_limits_breach event (red footer)

When the executor (next commit) emits a limits-breach event, the user
sees one clear line explaining which limit (phase/aggregate × turns
/cost/time) was crossed for which phase, with both the observed and
declared values."
```

---

## Task 4: Executor — single `_check_limits` site + graceful stop

**Files:**
- Modify: `launcher/zipsa/core/executor.py`
- Modify: `launcher/tests/test_executor.py`

This is the biggest task. It removes the duplicated turn/cost/timeout code at lines ~285–378 of executor.py, replaces it with a single per-event call into the new limits module, and emits `zipsa_limits_breach` on breach.

### Step 1: Investigate graceful-stop mechanism

Read the Claude Code Agent SDK docs / the `runtimes/claude.py` adapter to find out whether we can inject a tool-call error mid-stream that the agent will see and react to (cleanly emit a `status=failed` JSON). Two paths:

- **Path A — SDK supports injected tool errors.** When `check_limits` returns a breach, the executor synthesizes a synthetic tool_result with an error, sends it back to the SDK, lets the next turn produce a clean final JSON. Most graceful.
- **Path B — no clean injection point.** Wait for the current `assistant` message to complete (we already see it whole in the parser), then `process.terminate()`. The agent's last JSON, if any, is whatever it produced before. State_updates from the breached phase are NOT applied (existing failure semantics).

If Path A is feasible, use it. If not, Path B is acceptable. Both produce a `zipsa_limits_breach` event for the renderer.

The implementer should make this call after a 5-minute investigation; if it's still unclear, default to Path B (simpler, no surprises) and add a BACKLOG item to revisit Path A later.

### Step 2: Write the failing tests (executor integration)

Add to `launcher/tests/test_executor.py`:

```python
class TestLimitsIntegration:
    """The executor's per-event handler invokes limits.update_for_event
    and limits.check_limits, and emits zipsa_limits_breach on breach."""

    def test_phase_cost_breach_emits_event_and_stops(self, tmp_path, monkeypatch):
        """A skill that would exceed phase cost on the 2nd assistant message
        gets stopped after that 2nd message; a zipsa_limits_breach event
        is emitted; no further events stream from this run."""
        # Use a fixture skill with extremely tight max_cost_usd.
        # Mock subprocess.Popen to emit a deterministic event sequence:
        #   - assistant w/ usage that pushes cost over budget on msg 2
        #   - then more events that should NOT be yielded
        # Implementer fills in the mock pattern matching the other executor tests.
        # Assert: the run yields up through the breach event then stops.
        ...

    def test_aggregate_turns_breach_across_phases(self, tmp_path, monkeypatch):
        """Two phases, each within its own phase max_turns but together
        crossing aggregate max_turns. The 2nd phase mid-stream gets the breach."""
        ...

    def test_hitl_wait_does_not_count_toward_timeout(self, tmp_path, monkeypatch):
        """Simulate a stream: tool_use for mcp__zipsa__ask, then a 5-minute
        wall-clock gap (mocked monotonic), then tool_result, then the agent
        finishes within compute timeout. Assert NO breach."""
        ...
```

The exact mock shape mirrors the existing executor tests in this file (search for `subprocess.Popen` patches). The implementer writes them out; the assertions above are the contract.

### Step 3: Refactor `_execute_phases` in executor.py

The two streaming branches around lines ~285 and ~328 (with the inline turn/cost/timeout checks) collapse to one:

```python
from .limits import new_state, update_for_event, check_limits

# ... inside the per-phase loop ...
limits_state = new_state(phase.id)
phase_limits = phase.limits or SkillLimits()
agg_limits = skill.manifest.spec.limits or SkillLimits()
model = (skill.manifest.spec.model or {}).get("name", "claude-opus-4-7")

for line in raw_stream:
    if not line:
        continue
    if output_file:
        output_file.write(line)
    for event in self.runtime.parse_output([line]):
        update_for_event(limits_state, event, model)
        breach = check_limits(limits_state, phase_limits, agg_limits)
        if breach is not None:
            yield event
            self._stop_after_current_op(process)  # path A or B
            yield {
                "type": "zipsa_limits_breach",
                "scope": breach.scope, "kind": breach.kind,
                "value": breach.value, "limit": breach.limit,
                "phase": breach.phase,
            }
            return
        yield event
```

Delete the old inline `# Check max_turns limit` / `# Check max_cost limit` blocks. The `timeout = limits.timeout_seconds if limits else None` line at the end of `_execute_phases` (around line 365) should be REMOVED — the new module already handles timeout per-event.

### Step 4: Run tests

```bash
uv run pytest
```
Expected: all previous tests still pass; new integration tests pass.

### Step 5: Manual smoke — re-run the weather scenario

The original weather runaway was 82s / $0.24 against declared 60s / $0.10. Re-run a comparable scenario (a high-cost weather query) and verify it stops near the declared bounds.

```bash
cd launcher && uv run zipsa run weather "오늘 날씨"
# Confirm the run stops at or near $0.10 with a zipsa_limits_breach event,
# NOT at $0.24+ with a postmortem warning.
```

### Step 6: Commit (boundary 4)

```bash
git add launcher/zipsa/core/executor.py launcher/tests/test_executor.py
git commit -m "feat(executor): single _check_limits call site, graceful stop

Collapses two duplicated streaming branches into one; calls
limits.update_for_event then limits.check_limits per parsed event.
On breach: yield the event, stop the agent after the current
operation [Path A: inject tool error if SDK supports it; Path B:
process.terminate() after letting current assistant msg flush],
then emit zipsa_limits_breach.

Removes the old inline turn/cost/timeout checks. Cost now real-time
(per-message usage via pricing table) instead of post-hoc warning.
Timeout now real-time (per-event compute elapsed, HITL excluded)
instead of process.wait() after stdout drains.

Fixes the BACKLOG #1 entry. Weather runaway scenario (declared $0.10,
actually $0.24) now stops within ~1 message of the declared budget."
```

---

## Wrap-up

After all 4 commits:

- [ ] `git log --oneline ffaf34d..HEAD` — 4 task commits + 1 docs commit (spec) = 5 total.
- [ ] `uv run pytest` from `launcher/` — green.
- [ ] Manual smoke: weather skill, daily-progress skill — both stop within their declared limits.
- [ ] Push branch, open PR. Reference this plan and the spec.
- [ ] If Path B was chosen for graceful stop, add a BACKLOG item to investigate Path A later.
