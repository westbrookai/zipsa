"""Tests for the `zipsa exec` CLI command (Phase 0).

`zipsa exec <path> [user_query]` runs a single-phase skill
deterministically — no Docker, no LLM, no manifest. These tests run
real subprocesses (python/bash phases) through the typer CLI runner.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from zipsa.cli import app

runner = CliRunner()


def _make_skill(root: Path, name: str, files: dict[str, str]) -> Path:
    skill = root / name
    (skill / "zipsa-dist").mkdir(parents=True)
    for fname, body in files.items():
        (skill / "zipsa-dist" / fname).write_text(body)
    return skill


PY_PHASE = (
    "import json, sys\n"
    "ctx = json.loads(sys.stdin.read())['ctx']\n"
    "print(json.dumps({'lang': 'python', 'name': ctx['skill_name'],"
    " 'q': ctx['user_query']}))\n"
)


class TestExecHappyPaths:
    def test_exec_python_skill(self, tmp_path):
        skill = _make_skill(tmp_path, "hello-py", {"1.report.py": PY_PHASE})

        result = runner.invoke(app, ["exec", str(skill)])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["skill_name"] == "hello-py"
        assert payload["result"]["lang"] == "python"
        assert payload["result"]["name"] == "hello-py"
        assert payload["exit_code"] == 0
        assert payload["duration_ms"] >= 0

    def test_exec_bash_skill(self, tmp_path):
        skill = _make_skill(tmp_path, "hello-sh", {
            "1.report.sh": "#!/bin/bash\nread line\necho '{\"lang\":\"bash\"}'\n",
        })

        result = runner.invoke(app, ["exec", str(skill)])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["result"] == {"lang": "bash"}

    def test_user_query_forwarded(self, tmp_path):
        skill = _make_skill(tmp_path, "echo-q", {"1.report.py": PY_PHASE})

        result = runner.invoke(app, ["exec", str(skill), "Sydney weather"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["result"]["q"] == "Sydney weather"


class TestExecErrors:
    def test_missing_skill_dir(self, tmp_path):
        result = runner.invoke(app, ["exec", str(tmp_path / "nope")])

        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "missing" in result.output.lower()

    def test_no_zipsa_dist(self, tmp_path):
        skill = tmp_path / "empty-skill"
        skill.mkdir()

        result = runner.invoke(app, ["exec", str(skill)])

        assert result.exit_code == 1
        assert "zipsa-dist" in result.output

    def test_multi_phase_rejected(self, tmp_path):
        skill = _make_skill(tmp_path, "two-phases", {
            "1.first.py": PY_PHASE,
            "2.second.py": PY_PHASE,
        })

        result = runner.invoke(app, ["exec", str(skill)])

        assert result.exit_code == 1
        assert "exactly one phase" in result.output
        assert "2" in result.output

    def test_md_phase_rejected_with_llm_message(self, tmp_path):
        skill = _make_skill(tmp_path, "llm-skill", {"1.think.md": "# think\n"})

        result = runner.invoke(app, ["exec", str(skill)])

        assert result.exit_code == 1
        assert "LLM" in result.output

    def test_phase_failure_reported(self, tmp_path):
        skill = _make_skill(tmp_path, "boom", {
            "1.bad.py": "import sys\nsys.stderr.write('kaboom\\n')\nsys.exit(3)\n",
        })

        result = runner.invoke(app, ["exec", str(skill)])

        assert result.exit_code == 3
        assert "kaboom" in result.output
