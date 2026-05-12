"""Tests for output renderer."""

import json
from zipsa.core.renderer import OutputMode, render


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
