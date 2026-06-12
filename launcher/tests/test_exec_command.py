"""Tests for the `zipsa exec` CLI command (Phase 0).

`zipsa exec <path> [user_query]` runs a single-phase skill
deterministically. Docker (runtime container) is the default; --local
runs on the host. Happy-path tests use --local so they exercise real
subprocesses without needing Docker; docker-default behavior is tested
with subprocess mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

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


class TestExecLocalHappyPaths:
    def test_exec_python_skill(self, tmp_path):
        skill = _make_skill(tmp_path, "hello-py", {"1.report.py": PY_PHASE})

        result = runner.invoke(app, ["exec", str(skill), "--local"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["skill_name"] == "hello-py"
        assert payload["mode"] == "local"
        assert payload["result"]["lang"] == "python"
        assert payload["result"]["name"] == "hello-py"
        assert payload["exit_code"] == 0
        assert payload["duration_ms"] >= 0
        assert payload["out_dir"]

    def test_exec_bash_skill(self, tmp_path):
        skill = _make_skill(tmp_path, "hello-sh", {
            "1.report.sh": "#!/bin/bash\nread line\necho '{\"lang\":\"bash\"}'\n",
        })

        result = runner.invoke(app, ["exec", str(skill), "--local"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["result"] == {"lang": "bash"}

    def test_user_query_forwarded(self, tmp_path):
        skill = _make_skill(tmp_path, "echo-q", {"1.report.py": PY_PHASE})

        result = runner.invoke(
            app, ["exec", str(skill), "Sydney weather", "--local"],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["result"]["q"] == "Sydney weather"

    def test_out_flag_collects_artifacts(self, tmp_path):
        skill = _make_skill(tmp_path, "writer", {
            "1.write.py": (
                "import json, sys, pathlib\n"
                "ctx = json.loads(sys.stdin.read())['ctx']\n"
                "pathlib.Path(ctx['out_dir'], 'a.txt').write_text('hi')\n"
                "print(json.dumps({'wrote': True}))\n"
            ),
        })
        out = tmp_path / "artifacts"

        result = runner.invoke(
            app, ["exec", str(skill), "--local", "--out", str(out)],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["out_dir"] == str(out)
        assert (out / "a.txt").read_text() == "hi"


class TestExecDockerDefault:
    """Without --local the command goes through docker (mocked here)."""

    @patch("zipsa.exec_runner.subprocess.run")
    def test_default_mode_is_docker(self, mock_run, tmp_path):
        skill = _make_skill(tmp_path, "hello", {"1.report.py": PY_PHASE})
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = '{"ok": true}\n'
        mock_run.return_value.stderr = ""

        result = runner.invoke(app, ["exec", str(skill)])

        assert result.exit_code == 0, result.output
        argv = mock_run.call_args.args[0]
        assert argv[0] == "docker"
        payload = json.loads(result.output)
        assert payload["mode"] == "docker"
        assert payload["result"] == {"ok": True}

    @patch("zipsa.exec_runner.subprocess.run")
    def test_image_flag_overrides(self, mock_run, tmp_path):
        skill = _make_skill(tmp_path, "hello", {"1.report.py": PY_PHASE})
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "{}\n"
        mock_run.return_value.stderr = ""

        result = runner.invoke(
            app, ["exec", str(skill), "--image", "custom:1.2.3"],
        )

        assert result.exit_code == 0, result.output
        argv = mock_run.call_args.args[0]
        assert "custom:1.2.3" in argv

    @patch(
        "zipsa.exec_runner.subprocess.run",
        side_effect=FileNotFoundError,
    )
    def test_docker_missing_suggests_local(self, mock_run, tmp_path):
        skill = _make_skill(tmp_path, "hello", {"1.report.py": PY_PHASE})

        result = runner.invoke(app, ["exec", str(skill)])

        assert result.exit_code == 1
        assert "--local" in result.output

    @patch("zipsa.exec_runner.subprocess.run")
    def test_mount_flag_repeatable(self, mock_run, tmp_path):
        """--mount <host-path> (repeatable) mounts ro at the same
        container path."""
        skill = _make_skill(tmp_path, "hello", {"1.report.py": PY_PHASE})
        m1 = tmp_path / "claude-projects"
        m1.mkdir()
        m2 = tmp_path / "code"
        m2.mkdir()
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "{}\n"
        mock_run.return_value.stderr = ""

        result = runner.invoke(app, [
            "exec", str(skill),
            "--mount", str(m1),
            "--mount", str(m2),
        ])

        assert result.exit_code == 0, result.output
        run_call = next(
            c for c in mock_run.call_args_list
            if c.args[0][:2] == ["docker", "run"]
        )
        argv = run_call.args[0]
        assert f"{m1}:{m1}:ro" in argv
        assert f"{m2}:{m2}:ro" in argv

    @patch("zipsa.exec_runner.subprocess.run")
    def test_mount_missing_path_errors(self, mock_run, tmp_path):
        skill = _make_skill(tmp_path, "hello", {"1.report.py": PY_PHASE})

        result = runner.invoke(app, [
            "exec", str(skill),
            "--mount", str(tmp_path / "nope"),
        ])

        assert result.exit_code == 1
        assert "mount" in result.output.lower()

    @patch("zipsa.exec_runner.subprocess.run")
    def test_empty_mount_hint_on_file_not_found(self, mock_run, tmp_path):
        """Skill path outside Docker Desktop's file-sharing list mounts
        empty — the resulting 'No such file' error gets a hint."""
        skill = _make_skill(tmp_path, "hello", {"1.report.py": PY_PHASE})

        def fake_run(argv, **kwargs):
            result = type("R", (), {})()
            if argv[:3] == ["docker", "image", "inspect"]:
                result.returncode = 0
                result.stdout = "[]"
                result.stderr = ""
            else:  # docker run — phase file invisible in the container
                result.returncode = 2
                result.stdout = ""
                result.stderr = (
                    "python: can't open file '/skill/zipsa-dist/1.report.py': "
                    "[Errno 2] No such file or directory\n"
                )
            return result

        mock_run.side_effect = fake_run

        result = runner.invoke(app, ["exec", str(skill)])

        assert result.exit_code == 2
        assert "file sharing" in result.output.lower()


class TestExecErrors:
    def test_missing_skill_dir(self, tmp_path):
        result = runner.invoke(app, ["exec", str(tmp_path / "nope"), "--local"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "missing" in result.output.lower()

    def test_no_zipsa_dist(self, tmp_path):
        skill = tmp_path / "empty-skill"
        skill.mkdir()

        result = runner.invoke(app, ["exec", str(skill), "--local"])

        assert result.exit_code == 1
        assert "zipsa-dist" in result.output

    def test_sub_phase_rejected(self, tmp_path):
        """Branching (dotted sub-phase ids) is post-Phase-1."""
        skill = _make_skill(tmp_path, "branchy", {
            "1.first.py": PY_PHASE,
            "2.1.branch-a.py": PY_PHASE,
            "2.2.branch-b.py": PY_PHASE,
        })

        result = runner.invoke(app, ["exec", str(skill), "--local"])

        assert result.exit_code == 1
        assert "branching" in result.output.lower()

    @patch("zipsa.exec_runner.subprocess.run")
    def test_md_phase_runs_via_claude(self, mock_run, tmp_path):
        """Phase 2: .md phases run through claude -p."""
        skill = _make_skill(tmp_path, "llm-skill", {"1.think.md": "# think\n"})
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = '{"thought": "done"}\n'
        mock_run.return_value.stderr = ""

        result = runner.invoke(app, ["exec", str(skill), "--local"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["result"] == {"thought": "done"}
        argv = mock_run.call_args.args[0]
        assert argv[0] == "claude"

    def test_phase_failure_reported(self, tmp_path):
        skill = _make_skill(tmp_path, "boom", {
            "1.bad.py": "import sys\nsys.stderr.write('kaboom\\n')\nsys.exit(3)\n",
        })

        result = runner.invoke(app, ["exec", str(skill), "--local"])

        assert result.exit_code == 3
        assert "kaboom" in result.output

    def test_mid_chain_failure_names_phase(self, tmp_path):
        """A failure in phase 2 of 3 reports WHICH phase died."""
        skill = _make_skill(tmp_path, "chain-boom", {
            "1.ok.py": PY_PHASE,
            "2.boom.py": "import sys\nsys.stderr.write('dead\\n')\nsys.exit(7)\n",
            "3.never.py": PY_PHASE,
        })

        result = runner.invoke(app, ["exec", str(skill), "--local"])

        assert result.exit_code == 7
        assert "2.boom" in result.output
        assert "dead" in result.output


class TestExecMultiPhase:
    """Phase 1: sequential multi-phase chains."""

    def test_two_phase_chain(self, tmp_path):
        skill = _make_skill(tmp_path, "chain", {
            "1.produce.py": (
                "import json, sys\n"
                "sys.stdin.read()\n"
                "print(json.dumps({'x': 41}))\n"
            ),
            "2.consume.py": (
                "import json, sys\n"
                "data = json.loads(sys.stdin.read())\n"
                "print(json.dumps({'answer': data['prev']['x'] + 1}))\n"
            ),
        })

        result = runner.invoke(app, ["exec", str(skill), "--local"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        # result = last phase's result
        assert payload["result"] == {"answer": 42}
        # per-phase summaries
        assert [p["id"] for p in payload["phases"]] == ["1", "2"]
        assert [p["slug"] for p in payload["phases"]] == ["produce", "consume"]
        assert all(p["exit_code"] == 0 for p in payload["phases"])
        assert all(p["duration_ms"] >= 0 for p in payload["phases"])

    def test_single_phase_output_shape_unchanged(self, tmp_path):
        """A 1-phase skill still gets the phases array (len 1)."""
        skill = _make_skill(tmp_path, "solo", {"1.report.py": PY_PHASE})

        result = runner.invoke(app, ["exec", str(skill), "--local"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["result"]["lang"] == "python"
        assert len(payload["phases"]) == 1
