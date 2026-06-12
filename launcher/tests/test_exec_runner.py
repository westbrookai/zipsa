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

import json
from pathlib import Path

import pytest

from unittest.mock import patch

from zipsa.core.phase_discovery import PHASE_EXTENSIONS
from zipsa.exec_runner import (
    RUNNERS,
    ExecResult,
    ExecRunnerError,
    _build_docker_argv,
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


class TestCtxOutDir:
    """ctx always carries out_dir; phases write artifacts there."""

    def test_local_mode_out_dir_is_host_path(self, tmp_path):
        phase = _write(
            tmp_path,
            "1.do.py",
            (
                "import json, sys\n"
                "ctx = json.loads(sys.stdin.read())['ctx']\n"
                "print(json.dumps({'out': ctx['out_dir']}))\n"
            ),
        )
        out = tmp_path / "my-out"
        out.mkdir()

        result = run_phase(phase, skill_name="x", out_dir=out)

        assert result.result == {"out": str(out)}
        assert result.mode == "local"
        assert result.out_dir == str(out)

    def test_artifact_written_to_out_dir(self, tmp_path):
        phase = _write(
            tmp_path,
            "1.do.py",
            (
                "import json, sys, pathlib\n"
                "ctx = json.loads(sys.stdin.read())['ctx']\n"
                "pathlib.Path(ctx['out_dir'], 'artifact.txt').write_text('hello')\n"
                "print(json.dumps({'wrote': True}))\n"
            ),
        )
        out = tmp_path / "out"
        out.mkdir()

        result = run_phase(phase, skill_name="x", out_dir=out)

        assert result.exit_code == 0
        assert (out / "artifact.txt").read_text() == "hello"

    def test_out_dir_defaults_to_temp(self, tmp_path):
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

        assert result.out_dir
        assert Path(result.out_dir).is_dir()


class TestBuildDockerArgv:
    """Pure docker-argv builder — no Docker needed."""

    def test_full_command_shape(self, tmp_path):
        skill = tmp_path / "hello-world"
        dist = skill / "zipsa-dist"
        dist.mkdir(parents=True)
        phase = dist / "1.report.py"
        phase.touch()
        out = tmp_path / "out"

        argv = _build_docker_argv(
            phase, skill_root=skill, out_dir=out, image="zipsa-runtime:test",
        )

        assert argv[0:2] == ["docker", "run"]
        assert "--rm" in argv
        assert "-i" in argv
        assert "-t" not in argv
        assert f"{skill.resolve()}:/skill:ro" in argv
        assert f"{out}:/out" in argv
        assert "zipsa-runtime:test" in argv
        # Runner command + container-side phase path at the end
        assert argv[-2:] == ["python", "/skill/zipsa-dist/1.report.py"]
        # Container name carries the skill name for debuggability
        name_idx = argv.index("--name")
        assert argv[name_idx + 1].startswith("zipsa-exec-hello-world-")

    def test_ts_phase_runner_command(self, tmp_path):
        skill = tmp_path / "s"
        dist = skill / "zipsa-dist"
        dist.mkdir(parents=True)
        phase = dist / "2.fetch.ts"
        phase.touch()

        argv = _build_docker_argv(
            phase, skill_root=skill, out_dir=tmp_path / "o", image="img",
        )

        assert argv[-3:] == ["npx", "tsx", "/skill/zipsa-dist/2.fetch.ts"]


class TestDockerMode:
    """run_phase with docker_image set — subprocess mocked."""

    def _phase(self, tmp_path):
        skill = tmp_path / "myskill"
        dist = skill / "zipsa-dist"
        dist.mkdir(parents=True)
        phase = dist / "1.do.py"
        phase.write_text("# placeholder, never actually run\n")
        return skill, phase

    @patch("zipsa.exec_runner.subprocess.run")
    def test_docker_argv_and_result(self, mock_run, tmp_path):
        skill, phase = self._phase(tmp_path)
        out = tmp_path / "out"
        out.mkdir()
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = '{"ok": true}\n'
        mock_run.return_value.stderr = ""

        result = run_phase(
            phase,
            skill_name="myskill",
            skill_root=skill,
            out_dir=out,
            docker_image="zipsa-runtime:test",
        )

        argv = mock_run.call_args.args[0]
        assert argv[0] == "docker"
        assert "zipsa-runtime:test" in argv
        assert result.mode == "docker"
        assert result.result == {"ok": True}

    @patch("zipsa.exec_runner.subprocess.run")
    def test_ctx_out_dir_is_container_path(self, mock_run, tmp_path):
        skill, phase = self._phase(tmp_path)
        out = tmp_path / "out"
        out.mkdir()
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "{}\n"
        mock_run.return_value.stderr = ""

        run_phase(
            phase,
            skill_name="myskill",
            skill_root=skill,
            out_dir=out,
            docker_image="img",
        )

        stdin_payload = json.loads(mock_run.call_args.kwargs["input"])
        assert stdin_payload["ctx"]["out_dir"] == "/out"

    @patch("zipsa.exec_runner.subprocess.run", side_effect=FileNotFoundError)
    def test_docker_binary_missing(self, mock_run, tmp_path):
        skill, phase = self._phase(tmp_path)

        with pytest.raises(ExecRunnerError, match="--local"):
            run_phase(
                phase,
                skill_name="myskill",
                skill_root=skill,
                out_dir=tmp_path,
                docker_image="img",
            )

    @patch("zipsa.exec_runner.subprocess.run")
    def test_image_present_no_pull(self, mock_run, tmp_path):
        """When `docker image inspect` succeeds, no pull happens."""
        skill, phase = self._phase(tmp_path)
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "{}\n"
        mock_run.return_value.stderr = ""

        run_phase(
            phase,
            skill_name="myskill",
            skill_root=skill,
            out_dir=tmp_path,
            docker_image="img",
        )

        invoked = [c.args[0] for c in mock_run.call_args_list]
        assert ["docker", "image", "inspect", "img"] in invoked
        assert not any(argv[:2] == ["docker", "pull"] for argv in invoked)

    @patch("zipsa.exec_runner.subprocess.run")
    def test_missing_image_pulled_with_stderr_notice(
        self, mock_run, tmp_path, capsys
    ):
        """Missing image → notice on stderr + `docker pull` (stdio
        inherited so progress is visible), then the actual run."""
        skill, phase = self._phase(tmp_path)

        def fake_run(argv, **kwargs):
            result = type("R", (), {})()
            if argv[:3] == ["docker", "image", "inspect"]:
                result.returncode = 1
                result.stdout = ""
                result.stderr = "No such image"
            elif argv[:2] == ["docker", "pull"]:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            else:  # docker run
                result.returncode = 0
                result.stdout = "{}\n"
                result.stderr = ""
            return result

        mock_run.side_effect = fake_run

        result = run_phase(
            phase,
            skill_name="myskill",
            skill_root=skill,
            out_dir=tmp_path,
            docker_image="some-image:1.0",
        )

        assert result.exit_code == 0
        invoked = [c.args[0] for c in mock_run.call_args_list]
        assert ["docker", "pull", "some-image:1.0"] in invoked
        # Pull must NOT capture output — progress goes to the terminal
        pull_call = next(
            c for c in mock_run.call_args_list
            if c.args[0][:2] == ["docker", "pull"]
        )
        assert not pull_call.kwargs.get("capture_output")
        assert "some-image:1.0" in capsys.readouterr().err

    @patch("zipsa.exec_runner.subprocess.run")
    def test_pull_failure_raises(self, mock_run, tmp_path):
        skill, phase = self._phase(tmp_path)

        def fake_run(argv, **kwargs):
            result = type("R", (), {})()
            result.stdout = ""
            result.stderr = ""
            if argv[:3] == ["docker", "image", "inspect"]:
                result.returncode = 1
            elif argv[:2] == ["docker", "pull"]:
                result.returncode = 1
            else:
                result.returncode = 0
            return result

        mock_run.side_effect = fake_run

        with pytest.raises(ExecRunnerError, match="pull"):
            run_phase(
                phase,
                skill_name="myskill",
                skill_root=skill,
                out_dir=tmp_path,
                docker_image="img",
            )

    @patch("zipsa.exec_runner.subprocess.run")
    def test_daemon_down_clear_message(self, mock_run, tmp_path):
        """docker binary present but daemon not running → clear error,
        not a confusing pull failure."""
        skill, phase = self._phase(tmp_path)

        def fake_run(argv, **kwargs):
            result = type("R", (), {})()
            result.returncode = 1
            result.stdout = ""
            result.stderr = (
                "Cannot connect to the Docker daemon at "
                "unix:///var/run/docker.sock. Is the docker daemon running?"
            )
            return result

        mock_run.side_effect = fake_run

        with pytest.raises(ExecRunnerError, match="daemon"):
            run_phase(
                phase,
                skill_name="myskill",
                skill_root=skill,
                out_dir=tmp_path,
                docker_image="img",
            )

    @patch("zipsa.exec_runner.subprocess.run")
    def test_default_out_dir_under_zipsa_home(self, mock_run, tmp_path):
        """Docker mode's default out dir must live under ~/.zipsa.

        The system temp dir (/var/folders on macOS) is typically NOT in
        Docker Desktop's file-sharing list, so a temp out dir mounts
        empty inside the container and artifacts are silently lost.
        ~/.zipsa sits under /Users, which IS shared.
        """
        from zipsa.paths import zipsa_home

        skill, phase = self._phase(tmp_path)
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "{}\n"
        mock_run.return_value.stderr = ""

        result = run_phase(
            phase,
            skill_name="myskill",
            skill_root=skill,
            docker_image="img",
        )

        assert result.out_dir.startswith(str(zipsa_home()))


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
