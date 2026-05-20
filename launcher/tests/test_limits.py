# launcher/tests/test_limits.py
"""Limits module tests — pure unit, no Docker."""

from dataclasses import asdict
from typing import Optional
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


def _assistant_event(model: str, usage: dict, has_thinking: bool = True, msg_id: Optional[str] = None):
    """Shape matches what runtime.parse_output produces."""
    content = []
    if has_thinking:
        content.append({"type": "thinking", "thinking": "..."})
    msg = {"model": model, "content": content, "usage": usage}
    if msg_id is not None:
        msg["id"] = msg_id
    return {"type": "assistant", "message": msg}


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

    def test_per_message_model_overrides_fallback(self):
        """If the assistant message has a `model` field (Claude Code always
        provides one), that's what gets billed — use it for cost, ignore
        the static fallback. Catches the hello-world bug where manifests
        without explicit model: were being charged at Opus rates
        regardless of the model that actually ran."""
        s = new_state("p")
        # Manifest says Opus (fallback); actual message ran on Haiku.
        usage = {"input_tokens": 1_000_000}
        ev = _assistant_event("claude-haiku-4-5-20251001", usage)
        update_for_event(s, ev, "claude-opus-4-7")  # fallback = Opus
        # Cost MUST match Haiku ($0.80), not Opus ($15.00).
        assert s.phase_cost_usd == pytest.approx(0.80)

    def test_fallback_model_used_when_message_omits_model(self):
        """If the message doesn't carry a model (rare), fall back to the
        static arg so cost tracking doesn't silently zero out."""
        s = new_state("p")
        usage = {"input_tokens": 1_000_000}
        # Build an event without a model field
        ev = {
            "type": "assistant",
            "message": {"content": [{"type": "thinking", "thinking": "."}], "usage": usage},
        }
        update_for_event(s, ev, "claude-haiku-4-5-20251001")
        assert s.phase_cost_usd == pytest.approx(0.80)  # Haiku from fallback

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

    def test_duplicate_message_id_counts_cost_once(self):
        """Real Claude Code stream-json splits one assistant message into
        multiple events (thinking, text, tool_use, tool_use, ...) all
        carrying the SAME usage object. Without dedupe the cost would
        inflate by the content-block count."""
        s = new_state("p")
        usage = {"input_tokens": 1_000_000}
        # Same msg_id, 4 events (simulating thinking + text + 2 tool_use blocks)
        for _ in range(4):
            update_for_event(s, _assistant_event(PRICING_MODEL, usage, msg_id="msg_abc"), PRICING_MODEL)
        # Cost counted ONCE not 4×: $0.80, not $3.20
        assert s.phase_cost_usd == pytest.approx(0.80)
        assert s.run_cost_usd == pytest.approx(0.80)

    def test_different_message_ids_count_separately(self):
        """Distinct assistant messages (different ids) each contribute cost."""
        s = new_state("p")
        usage = {"input_tokens": 1_000_000}
        update_for_event(s, _assistant_event(PRICING_MODEL, usage, msg_id="msg_a"), PRICING_MODEL)
        update_for_event(s, _assistant_event(PRICING_MODEL, usage, msg_id="msg_b"), PRICING_MODEL)
        # Two separate messages: 2 × $0.80
        assert s.phase_cost_usd == pytest.approx(1.60)

    def test_phase_start_clears_message_id_dedupe(self):
        """A message id from phase 1 must not block counting in phase 2
        (defensive: ids shouldn't collide across claude --print invocations,
        but reset for cleanliness)."""
        s = new_state("p1")
        usage = {"input_tokens": 1_000_000}
        update_for_event(s, _assistant_event(PRICING_MODEL, usage, msg_id="msg_a"), PRICING_MODEL)
        update_for_event(s, _phase_start("p2"), PRICING_MODEL)
        # Same id in new phase counts again
        update_for_event(s, _assistant_event(PRICING_MODEL, usage, msg_id="msg_a"), PRICING_MODEL)
        assert s.phase_cost_usd == pytest.approx(0.80)  # phase reset, counted once in p2
        assert s.run_cost_usd == pytest.approx(1.60)    # run accumulates both

    def test_missing_message_id_falls_back_to_counting_every_event(self):
        """Defensive: if msg has no id (shouldn't happen for real Claude
        events but might for malformed input or tests), keep the old
        behavior of counting every event so cost tracking doesn't silently
        zero out."""
        s = new_state("p")
        usage = {"input_tokens": 1_000_000}
        for _ in range(3):
            update_for_event(s, _assistant_event(PRICING_MODEL, usage, msg_id=None), PRICING_MODEL)
        # No id → counted 3 times
        assert s.phase_cost_usd == pytest.approx(2.40)

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
