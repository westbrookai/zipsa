"""Tests for the Phase 0 deterministic phase runner.

`run_phase(phase_path, skill_name=..., user_query=...)` is the kernel
that invokes a single phase file as a subprocess, hands `ctx` in on
stdin as JSON, and reads the result as the last JSON-object line on
stdout.

Two languages get real subprocess execution here (Python + Bash —
universally available). Other supported extensions are covered by
asserting the dispatch table includes them.

See `~/.claude/plans/crystalline-cooking-kahan.md` for Phase 0 design.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zipsa.core.phase_discovery import PHASE_EXTENSIONS
from zipsa.exec_runner import (
    RUNNERS,
    ExecResult,
    ExecRunnerError,
    run_phase,
)


def _write(root: Path, name: str, body: str) -> Path:
    path = root / name
    path.write_text(body)
    return path


class TestRunPhaseHappyPaths:
    """Real subprocess execution against python + bash."""

    def test_python_phase_emits_result_on_last_line(self, tmp_path):
        phase = _write(
            tmp_path,
            "1.do.py",
            (
                "import json, sys\n"
                "ctx = json.loads(sys.stdin.read())['ctx']\n"
                "print(json.dumps({'lang': 'python', 'name': ctx['skill_name']}))\n"
            ),
        )

        result = run_phase(phase, skill_name="hello-py")

        assert isinstance(result, ExecResult)
        assert result.exit_code == 0
        assert result.result == {"lang": "python", "name": "hello-py"}
        assert result.skill_name == "hello-py"

    def test_relative_phase_path(self, tmp_path, monkeypatch):
        """A relative phase path must work even though the subprocess
        runs with cwd set to the phase's directory.

        Regression: `zipsa exec ../skills/hello-world` failed because
        the relative path was passed to the interpreter verbatim while
        cwd changed underneath it.
        """
        _write(
            tmp_path,
            "1.do.py",
            (
                "import json, sys\n"
                "sys.stdin.read()\n"
                "print(json.dumps({'ok': True}))\n"
            ),
        )
        monkeypatch.chdir(tmp_path.parent)
        relative = Path(tmp_path.name) / "1.do.py"

        result = run_phase(relative, skill_name="x")

        assert result.exit_code == 0, result.stderr
        assert result.result == {"ok": True}

    def test_bash_phase(self, tmp_path):
        phase = _write(
            tmp_path,
            "1.do.sh",
            (
                "#!/bin/bash\n"
                "read line\n"
                "echo '{\"lang\":\"bash\"}'\n"
            ),
        )

        result = run_phase(phase, skill_name="hello-sh")

        assert result.exit_code == 0
        assert result.result == {"lang": "bash"}

    def test_user_query_passed_in_ctx(self, tmp_path):
        phase = _write(
            tmp_path,
            "1.do.py",
            (
                "import json, sys\n"
                "ctx = json.loads(sys.stdin.read())['ctx']\n"
                "print(json.dumps({'q': ctx['user_query']}))\n"
            ),
        )

        result = run_phase(phase, skill_name="x", user_query="Sydney weather")

        assert result.result == {"q": "Sydney weather"}

    def test_empty_user_query_default(self, tmp_path):
        phase = _write(
            tmp_path,
            "1.do.py",
            (
                "import json, sys\n"
                "ctx = json.loads(sys.stdin.read())['ctx']\n"
                "print(json.dumps({'q': ctx['user_query']}))\n"
            ),
        )

        result = run_phase(phase, skill_name="x")

        assert result.result == {"q": ""}


class TestStdoutParsing:
    """Last JSON-object line is the result. Everything else is logs."""

    def test_earlier_lines_are_treated_as_logs(self, tmp_path):
        phase = _write(
            tmp_path,
            "1.do.py",
            (
                "import json, sys\n"
                "sys.stdin.read()\n"
                "print('starting...')\n"
                "print('progress 50%')\n"
                "print(json.dumps({'status': 'OK'}))\n"
            ),
        )

        result = run_phase(phase, skill_name="x")

        assert result.result == {"status": "OK"}
        assert "starting..." in result.stdout
        assert "progress 50%" in result.stdout

    def test_trailing_whitespace_after_json_ok(self, tmp_path):
        phase = _write(
            tmp_path,
            "1.do.py",
            (
                "import json, sys\n"
                "sys.stdin.read()\n"
                "print(json.dumps({'x': 1}))\n"
                "print('  ')\n"
            ),
        )

        result = run_phase(phase, skill_name="x")

        assert result.result == {"x": 1}

    def test_no_json_at_all_returns_none(self, tmp_path):
        phase = _write(
            tmp_path,
            "1.silent.py",
            (
                "import sys\n"
                "sys.stdin.read()\n"
                "print('hello world')\n"
            ),
        )

        result = run_phase(phase, skill_name="x")

        assert result.exit_code == 0
        assert result.result is None

    def test_json_array_not_treated_as_result(self, tmp_path):
        """Contract: last line must parse as a JSON OBJECT, not array.

        Arrays get treated like text (no result parsed) rather than
        silently passed through under the wrong type.
        """
        phase = _write(
            tmp_path,
            "1.do.py",
            (
                "import json, sys\n"
                "sys.stdin.read()\n"
                "print(json.dumps([1, 2, 3]))\n"
            ),
        )

        result = run_phase(phase, skill_name="x")

        assert result.result is None

    def test_multiple_json_objects_last_one_wins(self, tmp_path):
        """If a phase emits structured logs (JSON per line), the LAST
        object is the result."""
        phase = _write(
            tmp_path,
            "1.do.py",
            (
                "import json, sys\n"
                "sys.stdin.read()\n"
                "print(json.dumps({'log': 'step1'}))\n"
                "print(json.dumps({'log': 'step2'}))\n"
                "print(json.dumps({'final': True}))\n"
            ),
        )

        result = run_phase(phase, skill_name="x")

        assert result.result == {"final": True}


class TestFailureModes:
    def test_phase_exits_nonzero(self, tmp_path):
        phase = _write(
            tmp_path,
            "1.bad.py",
            (
                "import sys\n"
                "sys.stderr.write('boom\\n')\n"
                "sys.exit(2)\n"
            ),
        )

        result = run_phase(phase, skill_name="x")

        assert result.exit_code == 2
        assert result.result is None
        assert "boom" in result.stderr

    def test_unknown_extension_raises(self, tmp_path):
        phase = _write(tmp_path, "1.do.rs", "// rust\n")

        with pytest.raises(ExecRunnerError, match="no runner"):
            run_phase(phase, skill_name="x")

    def test_md_extension_explicitly_refused(self, tmp_path):
        """LLM phases get a different error message than 'unknown'."""
        phase = _write(tmp_path, "1.do.md", "# llm\n")

        with pytest.raises(ExecRunnerError, match="LLM"):
            run_phase(phase, skill_name="x")

    def test_missing_phase_file_raises(self, tmp_path):
        phase = tmp_path / "1.gone.py"

        with pytest.raises(ExecRunnerError, match="not found"):
            run_phase(phase, skill_name="x")


class TestMetadata:
    def test_duration_ms_populated(self, tmp_path):
        phase = _write(
            tmp_path,
            "1.do.py",
            (
                "import json, sys\n"
                "sys.stdin.read()\n"
                "print(json.dumps({}))\n"
            ),
        )

        result = run_phase(phase, skill_name="x")

        assert result.duration_ms >= 0


class TestRunnersTable:
    def test_runners_covers_every_executable_extension(self):
        """Every non-`.md` ext PHASE_EXTENSIONS lists must have a runner."""
        for ext in PHASE_EXTENSIONS:
            if ext == "md":
                continue
            assert ext in RUNNERS, f"PHASE_EXTENSIONS has .{ext} but RUNNERS doesn't"

    def test_md_not_in_runners(self):
        """`.md` is an LLM phase — handled (refused) separately."""
        assert "md" not in RUNNERS
