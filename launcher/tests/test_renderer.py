"""Tests for output renderer."""

import json
from zipsa.core.renderer import OutputMode, _format, render


EVENTS = [
    {"type": "system", "subtype": "init"},
    {
        "type": "assistant",
        "message": {
            "content": [{"type": "thinking", "thinking": "Let me think about this carefully."}]
        },
    },
    {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": "mcp__notion__notion-search", "input": {"query": "zipsa"}}]
        },
    },
    {
        "type": "user",
        "message": {
            "content": [{"type": "tool_result", "tool_use_id": "tu_1"}]
        },
        "tool_use_result": {"result": "Found 3 pages"},
    },
    {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": "작업 완료했습니다."}]
        },
    },
    {
        "type": "result",
        "is_error": False,
        "duration_ms": 5000,
        "num_turns": 2,
        "total_cost_usd": 0.0123,
    },
]


class TestOutputModeEnum:
    def test_modes_exist(self):
        assert OutputMode.pretty == "pretty"
        assert OutputMode.answer == "answer"
        assert OutputMode.json == "json"


class TestJsonMode:
    def test_json_mode_prints_each_event_as_json(self, capsys):
        render(iter(EVENTS), OutputMode.json)
        out = capsys.readouterr().out
        lines = [line for line in out.splitlines() if line.strip()]
        assert len(lines) == len(EVENTS)
        for line, event in zip(lines, EVENTS):
            assert json.loads(line) == event


class TestPrettyMode:
    def test_thinking_event_printed(self, capsys):
        events = [
            {
                "type": "assistant",
                "message": {"content": [{"type": "thinking", "thinking": "Let me think."}]},
            }
        ]
        render(iter(events), OutputMode.pretty)
        out = capsys.readouterr().out
        assert "Thinking" in out
        assert "Let me think." in out

    def test_tool_use_event_printed(self, capsys):
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "mcp__notion__search", "input": {"query": "test"}}
                    ]
                },
            }
        ]
        render(iter(events), OutputMode.pretty)
        out = capsys.readouterr().out
        assert "mcp__notion__search" in out
        assert "query" in out

    def test_tool_result_event_printed(self, capsys):
        events = [
            {
                "type": "user",
                "message": {"content": [{"type": "tool_result", "tool_use_id": "tu_1"}]},
                "tool_use_result": {"result": "Found 3 pages"},
            }
        ]
        render(iter(events), OutputMode.pretty)
        out = capsys.readouterr().out
        assert "Result" in out

    def test_text_event_printed(self, capsys):
        events = [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "작업 완료했습니다."}]},
            }
        ]
        render(iter(events), OutputMode.pretty)
        out = capsys.readouterr().out
        assert "작업 완료했습니다." in out

    def test_result_summary_printed(self, capsys):
        events = [
            {
                "type": "result",
                "is_error": False,
                "duration_ms": 5000,
                "num_turns": 2,
                "total_cost_usd": 0.0123,
            }
        ]
        render(iter(events), OutputMode.pretty)
        out = capsys.readouterr().out
        assert "5.0s" in out
        assert "2" in out
        assert "0.0123" in out

    def test_result_shows_failed_when_phase_status_failed(self, capsys):
        """Even if SDK is_error=False, a phase that returned status=failed
        in its JSON contract must NOT be footed with 'Success'."""
        events = [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": '```json\n{"status": "failed", "phase": "post", "error": {"code": "x_post_failed"}}\n```'}]},
            },
            {
                "type": "result",
                "is_error": False,  # SDK call itself succeeded
                "duration_ms": 11700,
                "num_turns": 2,
                "total_cost_usd": 0.05,
            },
        ]
        render(iter(events), OutputMode.pretty)
        out = capsys.readouterr().out
        assert "Failed" in out
        assert "Success" not in out

    def test_result_shows_out_of_scope_when_phase_status_out_of_scope(self, capsys):
        events = [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": '{"status": "out_of_scope", "phase": "precheck"}'}]},
            },
            {
                "type": "result",
                "is_error": False,
                "duration_ms": 1000,
                "num_turns": 1,
                "total_cost_usd": 0.01,
            },
        ]
        render(iter(events), OutputMode.pretty)
        out = capsys.readouterr().out
        assert "Out of scope" in out
        assert "Success" not in out

    def test_result_shows_success_when_phase_status_ok(self, capsys):
        events = [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": '```json\n{"status": "ok", "phase": "post"}\n```'}]},
            },
            {
                "type": "result",
                "is_error": False,
                "duration_ms": 1000,
                "num_turns": 1,
                "total_cost_usd": 0.01,
            },
        ]
        render(iter(events), OutputMode.pretty)
        out = capsys.readouterr().out
        assert "Success" in out

    def test_phase_start_resets_phase_status_tracking(self, capsys):
        """A new phase's result shouldn't inherit the previous phase's status."""
        events = [
            # Phase 1: failed
            {"type": "zipsa_phase_start", "phase": "p1", "phase_idx": 0, "total_phases": 2, "goal": "g"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": '{"status": "failed"}'}]}},
            {"type": "result", "is_error": False, "duration_ms": 1000, "num_turns": 1, "total_cost_usd": 0.01},
            # Phase 2: ok — must not show "Failed" from phase 1's leftover state
            {"type": "zipsa_phase_start", "phase": "p2", "phase_idx": 1, "total_phases": 2, "goal": "g"},
            {"type": "result", "is_error": False, "duration_ms": 1000, "num_turns": 1, "total_cost_usd": 0.01},
        ]
        render(iter(events), OutputMode.pretty)
        out = capsys.readouterr().out
        # Both footers should appear; the second must say Success (no leftover Failed).
        assert out.count("Failed") == 1
        assert "Success" in out

    def test_system_events_skipped(self, capsys):
        events = [{"type": "system", "subtype": "init"}]
        render(iter(events), OutputMode.pretty)
        out = capsys.readouterr().out
        assert out.strip() == ""

    def test_turn_counter_increments_on_thinking(self, capsys):
        events = [
            {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "first"}]}},
            {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "second"}]}},
        ]
        render(iter(events), OutputMode.pretty)
        out = capsys.readouterr().out
        assert "[Turn 1]" in out
        assert "[Turn 2]" in out


class TestPhaseStart:
    def test_phase_header_shown_in_pretty_mode(self, capsys):
        events = [
            {
                "type": "zipsa_phase_start",
                "phase": "precheck",
                "phase_idx": 0,
                "total_phases": 4,
                "goal": "Verify everything needed to run.",
            }
        ]
        render(iter(events), OutputMode.pretty)
        out = capsys.readouterr().out
        assert "Phase 1/4" in out
        assert "precheck" in out
        assert "Verify everything needed to run." in out

    def test_phase_start_resets_turn_counter(self, capsys):
        events = [
            {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "first"}]}},
            {
                "type": "zipsa_phase_start",
                "phase": "discover",
                "phase_idx": 1,
                "total_phases": 4,
                "goal": "Find session files.",
            },
            {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "second"}]}},
        ]
        render(iter(events), OutputMode.pretty)
        out = capsys.readouterr().out
        # Both turns should be [Turn 1] (counter resets at phase boundary)
        assert out.count("[Turn 1]") == 2
        assert "[Turn 2]" not in out

    def test_phase_start_hidden_in_answer_mode(self, capsys):
        events = [
            {
                "type": "zipsa_phase_start",
                "phase": "precheck",
                "phase_idx": 0,
                "total_phases": 2,
                "goal": "Some goal.",
            }
        ]
        render(iter(events), OutputMode.answer)
        out = capsys.readouterr().out
        assert out.strip() == ""


class TestAnswerMode:
    def test_answer_mode_prints_only_text(self, capsys):
        events = [
            {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "hmm"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Final answer."}]}},
        ]
        render(iter(events), OutputMode.answer)
        out = capsys.readouterr().out
        assert "Final answer." in out
        assert "Thinking" not in out
        assert "Turn" not in out

    def test_answer_mode_skips_tool_events(self, capsys):
        events = [
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "read_file", "input": {}}]},
            },
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}},
        ]
        render(iter(events), OutputMode.answer)
        out = capsys.readouterr().out
        assert "Done." in out
        assert "read_file" not in out


class TestHitlSuppression:
    def test_mcp_zipsa_tool_use_replaced_with_marker(self):
        event = {
            "type": "assistant",
            "message": {
                "content": [{
                    "type": "tool_use",
                    "name": "mcp__zipsa__ask",
                    "input": {"prompt": "Where?"},
                }],
            },
        }
        result = _format(event, OutputMode.pretty, turn=0)
        assert "[asking user]" in (result if isinstance(result, str) else result[0])
        assert "prompt=" not in (result if isinstance(result, str) else result[0])

    def test_non_zipsa_tool_use_still_verbose(self):
        event = {
            "type": "assistant",
            "message": {
                "content": [{
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "ls"},
                }],
            },
        }
        result = _format(event, OutputMode.pretty, turn=0)
        text = result if isinstance(result, str) else result[0]
        assert "Bash" in text
        assert "command=" in text

    def _zipsa_event(self, tool, inp):
        return {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": f"mcp__zipsa__{tool}", "input": inp}],
            },
        }

    def _text(self, event):
        result = _format(event, OutputMode.pretty, turn=0)
        return result if isinstance(result, str) else result[0]

    def test_confirm_uses_asking_user_marker(self):
        text = self._text(self._zipsa_event("confirm", {"message": "OK?"}))
        assert "[asking user]" in text

    def test_choose_uses_asking_user_marker(self):
        text = self._text(self._zipsa_event("choose", {"prompt": "Pick", "options": ["a"]}))
        assert "[asking user]" in text

    def test_ask_once_shows_key(self):
        text = self._text(self._zipsa_event("ask_once", {"key": "default_city", "prompt": "?"}))
        assert "[ask_once: default_city]" in text
        # Specifically NOT the misleading "asking user" — ask_once may hit cache
        assert "asking user" not in text

    def test_recall_shows_memory_marker_with_key(self):
        text = self._text(self._zipsa_event("recall", {"key": "workspace"}))
        assert "[memory: recall workspace]" in text
        assert "asking user" not in text

    def test_remember_shows_memory_marker_with_key(self):
        text = self._text(self._zipsa_event("remember", {"key": "workspace", "value": "WBrk"}))
        assert "[memory: remember workspace]" in text
        # Value should NOT leak into the marker (could be sensitive)
        assert "WBrk" not in text

    def test_forget_shows_memory_marker_with_key(self):
        text = self._text(self._zipsa_event("forget", {"key": "stale"}))
        assert "[memory: forget stale]" in text

    def test_list_memory_shows_scope(self):
        text = self._text(self._zipsa_event("list_memory", {"scope": "global"}))
        assert "[memory: list (global)]" in text

    def test_list_memory_default_scope(self):
        text = self._text(self._zipsa_event("list_memory", {}))
        assert "[memory: list (skill)]" in text

    def test_unknown_zipsa_tool_falls_back_to_short_name(self):
        text = self._text(self._zipsa_event("new_future_tool", {"x": 1}))
        assert "[new_future_tool]" in text
