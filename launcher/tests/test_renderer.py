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
        "tool_use_result": {"result": "Found 3 pages"},
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
