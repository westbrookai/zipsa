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

    def test_no_aggregate_limits_returns_none_when_phase_clean(self):
        """Skills can declare phase limits but no aggregate limits; in
        that case check_limits skips the aggregate check entirely."""
        s = new_state("p")
        s.phase_turns = 1
        s.phase_cost_usd = 0.01
        # Within phase budget, no aggregate limits at all
        assert check_limits(s, self._limits(), None) is None
