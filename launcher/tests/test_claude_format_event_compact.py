"""Tests for ClaudeRuntime.format_event_compact — one-event → ~280-char
summary used by read_run_log so skill-builder can analyze prior runs.

Sits next to parse_output in the runtime plugin on purpose: both halves
of the codec (raw SDK shape ↔ compact summary) live together so a SDK
event-shape change updates both in a single edit.
"""

import pytest

from zipsa.runtimes.claude import ClaudeRuntime


@pytest.fixture
def rt() -> ClaudeRuntime:
    return ClaudeRuntime()


class TestAssistantEvents:
    def test_thinking_block_summarized(self, rt):
        ev = {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "I need to figure out what to do here."},
        ]}}
        out = rt.format_event_compact(ev)
        assert out.startswith("A: ")
        assert "💭" in out
        assert "I need to figure out" in out

    def test_tool_use_block_includes_name_and_args(self, rt):
        ev = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}},
        ]}}
        out = rt.format_event_compact(ev)
        assert out.startswith("A: ")
        assert "🔧" in out
        assert "Read" in out
        assert "file_path" in out

    def test_text_block_summarized(self, rt):
        ev = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Here is my response."},
        ]}}
        out = rt.format_event_compact(ev)
        assert out.startswith("A: ")
        assert "💬" in out
        assert "Here is my response." in out

    def test_multiple_blocks_joined(self, rt):
        ev = {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "deciding"},
            {"type": "tool_use", "name": "X", "input": {"a": 1}},
        ]}}
        out = rt.format_event_compact(ev)
        assert "💭" in out and "🔧" in out


class TestUserEvents:
    def test_tool_result_string_content(self, rt):
        ev = {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "result text here"},
        ]}}
        out = rt.format_event_compact(ev)
        assert out.startswith("U: ")
        assert "✓" in out
        assert "result text here" in out

    def test_tool_result_array_content(self, rt):
        ev = {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": [
                {"type": "text", "text": "blob 1"},
                {"type": "text", "text": "blob 2"},
            ]},
        ]}}
        out = rt.format_event_compact(ev)
        assert "blob 1" in out


class TestMetaEvents:
    def test_system_init(self, rt):
        ev = {"type": "system", "subtype": "init"}
        out = rt.format_event_compact(ev)
        assert out == "S: init"

    def test_result_includes_cost_and_turns(self, rt):
        ev = {"type": "result", "subtype": "success",
              "total_cost_usd": 0.0467, "num_turns": 5}
        out = rt.format_event_compact(ev)
        assert out.startswith("R: ")
        assert "cost=$0.0467" in out
        assert "turns=5" in out

    def test_unknown_type_returns_short_marker(self, rt):
        ev = {"type": "rate_limit_event"}
        out = rt.format_event_compact(ev)
        # Either None (skipped) or a short label — both acceptable, but
        # not allowed to crash or dump the full event.
        assert out is None or len(out) < 60


class TestCappingAndSafety:
    def test_long_thinking_is_truncated(self, rt):
        long_text = "x" * 1000
        ev = {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": long_text},
        ]}}
        out = rt.format_event_compact(ev)
        # Each block contribution capped — 280 char ballpark, not 1000.
        assert len(out) < 400

    def test_long_text_is_truncated(self, rt):
        ev = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "y" * 1000},
        ]}}
        out = rt.format_event_compact(ev)
        assert len(out) < 400

    def test_long_tool_input_is_truncated(self, rt):
        ev = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "X", "input": {"data": "z" * 1000}},
        ]}}
        out = rt.format_event_compact(ev)
        assert len(out) < 400

    def test_empty_assistant_content_no_crash(self, rt):
        ev = {"type": "assistant", "message": {"content": []}}
        out = rt.format_event_compact(ev)
        assert out is None or out == "A: "

    def test_malformed_event_doesnt_crash(self, rt):
        # Missing fields — should produce something reasonable, not raise.
        for ev in ({}, {"type": "assistant"}, {"type": "user", "message": {}}):
            try:
                rt.format_event_compact(ev)
            except Exception as e:
                pytest.fail(f"format_event_compact crashed on {ev!r}: {e}")
