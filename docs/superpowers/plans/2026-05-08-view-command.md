# View Command and Output Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `zipsa view <skill> [run-id]` to replay past run output, and add `--output-mode [pretty|answer|json]` to both `run` and `view`.

**Architecture:** Extract existing `format_event()` from `cli.py` into a new `renderer.py` module with `OutputMode` enum and a stateful `render()` function. The `run` command gains `--output-mode` and delegates rendering to `renderer.py`. The new `view` command reads `output.jsonl` from the runs directory and feeds it through the same renderer.

**Tech Stack:** Python 3.12, Click/Typer, pytest

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `launcher/zipsa/core/renderer.py` | Create | `OutputMode` enum + `render(events, mode)` |
| `launcher/zipsa/cli.py` | Modify | Wire `--output-mode` into `run`; add `view` command; remove `format_event()` |
| `launcher/tests/test_renderer.py` | Create | Unit tests for all three output modes |
| `launcher/tests/test_cli.py` | Modify | Tests for `view` command and `--output-mode` on `run` |

---

## Task 1: `OutputMode` enum and `renderer.py` skeleton with json mode

**Files:**
- Create: `launcher/zipsa/core/renderer.py`
- Create: `launcher/tests/test_renderer.py`

- [ ] **Step 1: Write the failing test**

```python
# launcher/tests/test_renderer.py
"""Tests for output renderer."""

import json
from io import StringIO
from unittest.mock import patch
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
        lines = [l for l in out.splitlines() if l.strip()]
        assert len(lines) == len(EVENTS)
        for line, event in zip(lines, EVENTS):
            assert json.loads(line) == event
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd launcher && uv run pytest tests/test_renderer.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'zipsa.core.renderer'`

- [ ] **Step 3: Create `renderer.py` with `OutputMode` enum and `json` mode**

```python
# launcher/zipsa/core/renderer.py
"""Output renderer for skill execution events."""

import json
import sys
from enum import Enum
from typing import Iterator


class OutputMode(str, Enum):
    pretty = "pretty"
    answer = "answer"
    json = "json"


def render(events: Iterator[dict], mode: OutputMode) -> None:
    """Render an event stream to stdout according to the given mode."""
    if mode == OutputMode.json:
        for event in events:
            print(json.dumps(event, ensure_ascii=False))
        return

    turn = 0
    for event in events:
        line = _format(event, mode, turn)
        if line is not None:
            if isinstance(line, tuple):
                output, turn = line
            else:
                output = line
            print(output)


def _format(event: dict, mode: OutputMode, turn: int) -> "str | tuple[str, int] | None":
    """Format a single event. Returns (output, new_turn) or output string or None."""
    return None  # placeholder — implemented in later tasks
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_renderer.py::TestOutputModeEnum tests/test_renderer.py::TestJsonMode -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/core/renderer.py launcher/tests/test_renderer.py
git commit -m "feat: add OutputMode enum and json mode to renderer"
```

---

## Task 2: pretty mode — thinking, tool, text, and result events

**Files:**
- Modify: `launcher/zipsa/core/renderer.py`
- Modify: `launcher/tests/test_renderer.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_renderer.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_renderer.py::TestPrettyMode -v
```
Expected: all FAIL (pretty mode returns None for everything)

- [ ] **Step 3: Implement `_format()` for pretty mode**

Replace the `_format` function and `render` in `renderer.py` with:

```python
# ANSI color codes
_GRAY = "\033[90m"
_RESET = "\033[0m"


def render(events: Iterator[dict], mode: OutputMode) -> None:
    """Render an event stream to stdout according to the given mode."""
    if mode == OutputMode.json:
        for event in events:
            print(json.dumps(event, ensure_ascii=False))
        return

    turn = 0
    for event in events:
        result = _format(event, mode, turn)
        if result is None:
            continue
        if isinstance(result, tuple):
            output, turn = result
        else:
            output = result
        print(output)


def _format(event: dict, mode: OutputMode, turn: int) -> "str | tuple[str, int] | None":
    """Format a single event. Returns (output, new_turn), output string, or None to skip."""
    event_type = event.get("type")

    if event_type in ("system", "rate_limit_event"):
        return None

    if event_type == "assistant":
        message = event.get("message", {})
        content = message.get("content", [])
        if not content:
            return None
        block = content[0]
        block_type = block.get("type")

        if block_type == "thinking":
            turn += 1
            thinking = block.get("thinking", "")
            if mode == OutputMode.pretty:
                return (f"\n{_GRAY}[Turn {turn}]{_RESET}\n{_GRAY}Thinking:{_RESET} {thinking}", turn)
            return None

        elif block_type == "tool_use":
            if mode != OutputMode.pretty:
                return None
            name = block.get("name", "Unknown")
            inp = block.get("input", {})
            items = list(inp.items())[:3]
            args = "  ".join(f"{k}={str(v)[:80]}" for k, v in items)
            return f"\n{_GRAY}Tool:{_RESET} {name}\n  {args}"

        elif block_type == "text":
            text = block.get("text", "")
            if mode == OutputMode.pretty:
                turn += 1
                return (f"\n{_GRAY}[Turn {turn}]{_RESET}\n{_GRAY}Answer:{_RESET} {text}", turn)
            elif mode == OutputMode.answer:
                return text
            return None

    if event_type == "user":
        if mode != OutputMode.pretty:
            return None
        message = event.get("message", {})
        content = message.get("content", [])
        if not content or content[0].get("type") != "tool_result":
            return None
        tool_result = event.get("tool_use_result", {})
        if isinstance(tool_result, str):
            return f"{_GRAY}Result:{_RESET} {tool_result}"
        elif isinstance(tool_result, dict):
            if "result" in tool_result:
                return f"{_GRAY}Result:{_RESET} {tool_result['result']}"
            elif "matches" in tool_result:
                return f"{_GRAY}Result:{_RESET} Found {', '.join(tool_result['matches'])}"
            elif "code" in tool_result:
                code = tool_result.get("code")
                code_text = tool_result.get("codeText", "")
                return f"{_GRAY}Result:{_RESET} HTTP {code} {code_text}"
            else:
                return f"{_GRAY}Result:{_RESET} Success"
        first = content[0].get("content", "")
        if isinstance(first, str):
            return f"{_GRAY}Result:{_RESET} {first}"
        return f"{_GRAY}Result:{_RESET} Success"

    if event_type == "result":
        if mode != OutputMode.pretty:
            return None
        is_error = event.get("is_error", False)
        duration_s = event.get("duration_ms", 0) / 1000
        num_turns = event.get("num_turns", 0)
        cost = event.get("total_cost_usd", 0)
        status = "Error" if is_error else "Success"
        sep = "=" * 50
        return f"\n{sep}\n{status}\nDuration: {duration_s:.1f}s | Turns: {num_turns} | Cost: ${cost:.4f}\n{sep}"

    return None
```

- [ ] **Step 4: Run all renderer tests**

```bash
uv run pytest tests/test_renderer.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/core/renderer.py launcher/tests/test_renderer.py
git commit -m "feat: implement pretty and answer modes in renderer"
```

---

## Task 3: Wire `--output-mode` into `zipsa run` and remove `format_event()`

**Files:**
- Modify: `launcher/zipsa/cli.py`
- Modify: `launcher/tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
class TestRunOutputMode:
    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_defaults_to_pretty_mode(self, mock_skill_cls, mock_executor_cls):
        """run without --output-mode should use pretty rendering."""
        mock_skill = Mock()
        mock_skill.name = "test-skill"
        mock_skill_cls.load.return_value = mock_skill

        events = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}}
        ]
        mock_executor = Mock()
        mock_executor.run.return_value = iter(events)
        mock_executor_cls.return_value = mock_executor

        result = runner.invoke(app, ["run", "test-skill", "hello"])

        assert result.exit_code == 0
        assert "Done." in result.output
        # pretty mode adds Answer: prefix
        assert "Answer:" in result.output

    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_answer_mode_prints_only_text(self, mock_skill_cls, mock_executor_cls):
        mock_skill = Mock()
        mock_skill.name = "test-skill"
        mock_skill_cls.load.return_value = mock_skill

        events = [
            {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "hmm"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Final answer."}]}},
        ]
        mock_executor = Mock()
        mock_executor.run.return_value = iter(events)
        mock_executor_cls.return_value = mock_executor

        result = runner.invoke(app, ["run", "test-skill", "hello", "--output-mode", "answer"])

        assert result.exit_code == 0
        assert "Final answer." in result.output
        assert "Thinking" not in result.output
        assert "Turn" not in result.output

    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_json_mode_prints_raw_json(self, mock_skill_cls, mock_executor_cls):
        import json as _json
        mock_skill = Mock()
        mock_skill.name = "test-skill"
        mock_skill_cls.load.return_value = mock_skill

        event = {"type": "result", "total_cost_usd": 0.01}
        mock_executor = Mock()
        mock_executor.run.return_value = iter([event])
        mock_executor_cls.return_value = mock_executor

        result = runner.invoke(app, ["run", "test-skill", "hello", "--output-mode", "json"])

        assert result.exit_code == 0
        assert _json.loads(result.output.strip()) == event
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_cli.py::TestRunOutputMode -v
```
Expected: FAIL — `--output-mode` option doesn't exist yet

- [ ] **Step 3: Update `cli.py`**

Add import at top:
```python
from .core.renderer import OutputMode, render
```

Add `--output-mode` parameter to `run()` function signature (after `docker_opt`):
```python
output_mode: Annotated[
    OutputMode,
    typer.Option("--output-mode", help="Output format: pretty (default), answer, json"),
] = OutputMode.pretty,
```

Replace the streaming loop (currently lines 228-232):
```python
# Stream output
for event in output:
    formatted = format_event(event)
    if formatted:
        typer.echo(formatted)
```

With:
```python
# Stream output through renderer
render(output, output_mode)
```

Delete the `format_event()` function (lines 32-146) and the `_current_turn` global and `GRAY`/`RESET` constants from `cli.py` — they now live in `renderer.py`.

- [ ] **Step 4: Run all tests**

```bash
uv run pytest tests/test_cli.py tests/test_renderer.py -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/cli.py launcher/tests/test_cli.py
git commit -m "feat: add --output-mode to zipsa run, delegate rendering to renderer.py"
```

---

## Task 4: `zipsa view` — run selection logic

**Files:**
- Modify: `launcher/zipsa/cli.py` (add `_find_run_dir()` helper)
- Modify: `launcher/tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
from zipsa.cli import _find_run_dir


class TestFindRunDir:
    def test_returns_latest_run_when_no_id_given(self, tmp_path):
        runs = tmp_path / "runs"
        older = runs / "2026-05-07_100000_000000"
        newer = runs / "2026-05-08_120000_000000"
        older.mkdir(parents=True)
        newer.mkdir(parents=True)
        (older / "output.jsonl").touch()
        (newer / "output.jsonl").touch()

        result = _find_run_dir(runs)

        assert result == newer

    def test_raises_when_no_runs_exist(self, tmp_path):
        import pytest
        runs = tmp_path / "runs"
        with pytest.raises(ValueError, match="No runs found"):
            _find_run_dir(runs)

    def test_prefix_match_returns_correct_run(self, tmp_path):
        runs = tmp_path / "runs"
        run = runs / "2026-05-08_103540_691234"
        run.mkdir(parents=True)
        (run / "output.jsonl").touch()

        result = _find_run_dir(runs, run_id="2026-05-08_103540")

        assert result == run

    def test_raises_on_ambiguous_prefix(self, tmp_path):
        import pytest
        runs = tmp_path / "runs"
        (runs / "2026-05-08_103540_111111").mkdir(parents=True)
        (runs / "2026-05-08_103540_222222").mkdir(parents=True)

        with pytest.raises(ValueError, match="Ambiguous"):
            _find_run_dir(runs, run_id="2026-05-08_103540")

    def test_raises_when_prefix_matches_nothing(self, tmp_path):
        import pytest
        runs = tmp_path / "runs"
        (runs / "2026-05-08_103540_111111").mkdir(parents=True)

        with pytest.raises(ValueError, match="No run matching"):
            _find_run_dir(runs, run_id="2026-05-09")

    def test_raises_when_output_jsonl_missing(self, tmp_path):
        import pytest
        runs = tmp_path / "runs"
        run = runs / "2026-05-08_103540_111111"
        run.mkdir(parents=True)
        # no output.jsonl

        result = _find_run_dir(runs)  # selection succeeds
        assert result == run          # directory found

        # CLI layer handles missing output.jsonl separately
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_cli.py::TestFindRunDir -v
```
Expected: FAIL — `_find_run_dir` not imported

- [ ] **Step 3: Implement `_find_run_dir()` in `cli.py`**

Add before the `run` command:

```python
def _find_run_dir(runs_dir: Path, run_id: Optional[str] = None) -> Path:
    """Find a run directory under runs_dir.

    If run_id is None, returns the lexicographically latest directory.
    If run_id is given, matches it as a prefix against directory names.

    Raises ValueError on missing, ambiguous, or empty runs directory.
    """
    if not runs_dir.exists():
        raise ValueError(f"No runs found")
    dirs = sorted([d for d in runs_dir.iterdir() if d.is_dir()])
    if not dirs:
        raise ValueError(f"No runs found")

    if run_id is None:
        return dirs[-1]

    matches = [d for d in dirs if d.name.startswith(run_id)]
    if not matches:
        raise ValueError(f"No run matching '{run_id}' found")
    if len(matches) > 1:
        names = ", ".join(m.name for m in matches)
        raise ValueError(f"Ambiguous run ID '{run_id}' — matches: {names}")
    return matches[0]
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_cli.py::TestFindRunDir -v
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/cli.py launcher/tests/test_cli.py
git commit -m "feat: add _find_run_dir helper for view command run selection"
```

---

## Task 5: `zipsa view` CLI command

**Files:**
- Modify: `launcher/zipsa/cli.py`
- Modify: `launcher/tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
class TestViewCommand:
    @patch("zipsa.cli.Skill")
    def test_view_replays_latest_run(self, mock_skill_cls, tmp_path):
        """view should read output.jsonl from latest run and render it."""
        mock_skill = Mock()
        mock_skill.name = "daily-progress"
        mock_skill.manifest.metadata.version = "0.1.0"
        mock_skill_cls.load.return_value = mock_skill

        # Create a fake run directory
        run_dir = tmp_path / ".zipsa" / "daily-progress@0.1.0" / "runs" / "2026-05-08_120000_000000"
        run_dir.mkdir(parents=True)
        output_jsonl = run_dir / "output.jsonl"
        output_jsonl.write_text(
            '{"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}}\n'
        )

        with patch("pathlib.Path.home", return_value=tmp_path):
            result = runner.invoke(app, ["view", "test-skill"])

        assert result.exit_code == 0
        assert "Done." in result.output

    @patch("zipsa.cli.Skill")
    def test_view_errors_when_no_runs(self, mock_skill_cls, tmp_path):
        mock_skill = Mock()
        mock_skill.name = "daily-progress"
        mock_skill.manifest.metadata.version = "0.1.0"
        mock_skill_cls.load.return_value = mock_skill

        with patch("pathlib.Path.home", return_value=tmp_path):
            result = runner.invoke(app, ["view", "test-skill"])

        assert result.exit_code == 1
        assert "No runs found" in result.output

    @patch("zipsa.cli.Skill")
    def test_view_specific_run_by_prefix(self, mock_skill_cls, tmp_path):
        mock_skill = Mock()
        mock_skill.name = "daily-progress"
        mock_skill.manifest.metadata.version = "0.1.0"
        mock_skill_cls.load.return_value = mock_skill

        run_dir = tmp_path / ".zipsa" / "daily-progress@0.1.0" / "runs" / "2026-05-08_103540_691234"
        run_dir.mkdir(parents=True)
        (run_dir / "output.jsonl").write_text(
            '{"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello."}]}}\n'
        )

        with patch("pathlib.Path.home", return_value=tmp_path):
            result = runner.invoke(app, ["view", "test-skill", "2026-05-08_103540"])

        assert result.exit_code == 0
        assert "Hello." in result.output

    @patch("zipsa.cli.Skill")
    def test_view_errors_when_output_jsonl_missing(self, mock_skill_cls, tmp_path):
        mock_skill = Mock()
        mock_skill.name = "daily-progress"
        mock_skill.manifest.metadata.version = "0.1.0"
        mock_skill_cls.load.return_value = mock_skill

        run_dir = tmp_path / ".zipsa" / "daily-progress@0.1.0" / "runs" / "2026-05-08_120000_000000"
        run_dir.mkdir(parents=True)
        # no output.jsonl

        with patch("pathlib.Path.home", return_value=tmp_path):
            result = runner.invoke(app, ["view", "test-skill"])

        assert result.exit_code == 1
        assert "output.jsonl" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_cli.py::TestViewCommand -v
```
Expected: FAIL — `view` command doesn't exist

- [ ] **Step 3: Add `view` command to `cli.py`**

Add after the `run` command:

```python
@app.command()
def view(
    skill_dir: Annotated[
        str,
        typer.Argument(help="Path to skill directory or manifest.yaml"),
    ],
    run_id: Annotated[
        Optional[str],
        typer.Argument(help="Run ID prefix to replay (default: latest run)"),
    ] = None,
    output_mode: Annotated[
        OutputMode,
        typer.Option("--output-mode", help="Output format: pretty (default), answer, json"),
    ] = OutputMode.pretty,
):
    """Replay the output of a past skill run."""
    try:
        skill = Skill.load(skill_dir)
        runs_dir = (
            Path.home() / ".zipsa" / f"{skill.name}@{skill.manifest.metadata.version}" / "runs"
        )
        run_dir = _find_run_dir(runs_dir, run_id)
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1)

    output_jsonl = run_dir / "output.jsonl"
    if not output_jsonl.exists():
        typer.echo(f"Run '{run_dir.name}' has no output.jsonl")
        raise typer.Exit(1)

    def events():
        with open(output_jsonl) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        pass

    render(events(), output_mode)
```

- [ ] **Step 4: Run all tests**

```bash
uv run pytest --tb=short -q
```
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/cli.py launcher/tests/test_cli.py
git commit -m "feat: add zipsa view command to replay past skill runs"
```

---

## Self-Review

**Spec coverage check:**
- ✅ `OutputMode` enum with pretty/answer/json
- ✅ `renderer.py` as shared module
- ✅ `--output-mode` added to `run`
- ✅ pretty mode: thinking, tool_use, tool_result, text, result events
- ✅ answer mode: text blocks only
- ✅ json mode: raw JSONL passthrough
- ✅ `view` command with latest-run default
- ✅ `view` with run-id prefix matching
- ✅ All error cases from spec (no runs, ambiguous, missing output.jsonl)
- ✅ `format_event()` removed from cli.py (moved to renderer.py)
- ✅ Tests for all paths

**No placeholders found.**

**Type consistency:** `_find_run_dir(runs_dir: Path, ...)` used in Task 4 and Task 5 — consistent.
