"""Tests for the container-side phase runner.

`zipsa.phase_runner.main` is the entrypoint that container-side
processes invoke as `python -m zipsa.phase_runner <phase-file>`. It
loads the user's phase module, builds `ctx` and `prev`, calls
`run(ctx, prev)`, and writes the returned dict to `state.json`.

These tests drive `main()` in-process — there's no subprocess or
container involved. That layer (R4) lives elsewhere.

See `docs/zipsa-runtime-spec-2026-06-11.md` §2.1, §2.4.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zipsa.phase_runner import (
    ENV_PREV_STATE_PATH,
    ENV_RUN_DEPTH,
    ENV_RUN_DIR,
    ENV_RUN_ID,
    ENV_SKILL_NAME,
    ENV_SKILL_VERSION,
    ENV_STATE_PATH,
    ENV_USER_QUERY,
    main,
)


def _write_phase(root: Path, name: str, body: str) -> Path:
    path = root / name
    path.write_text(body)
    return path


class TestPhaseRunnerHappyPaths:
    def test_runs_simple_phase_and_writes_state(self, tmp_path, monkeypatch):
        phase = _write_phase(
            tmp_path,
            "1.do.py",
            "def run(ctx, prev): return {'status': 'OK', 'value': 42}\n",
        )
        state = tmp_path / "state.json"
        monkeypatch.setenv(ENV_STATE_PATH, str(state))

        ret = main([str(phase)])

        assert ret == 0
        assert json.loads(state.read_text()) == {"status": "OK", "value": 42}

    def test_ctx_built_from_env(self, tmp_path, monkeypatch):
        phase = _write_phase(
            tmp_path,
            "1.do.py",
            (
                "def run(ctx, prev):\n"
                "    return {\n"
                "        'skill_name': ctx['skill_name'],\n"
                "        'version': ctx['version'],\n"
                "        'user_query': ctx['user_query'],\n"
                "        'run_id': ctx['run_id'],\n"
                "        'run_dir': ctx['run_dir'],\n"
                "        'depth': ctx['depth'],\n"
                "    }\n"
            ),
        )
        state = tmp_path / "state.json"
        monkeypatch.setenv(ENV_STATE_PATH, str(state))
        monkeypatch.setenv(ENV_SKILL_NAME, "hello-world")
        monkeypatch.setenv(ENV_SKILL_VERSION, "0.3.0")
        monkeypatch.setenv(ENV_USER_QUERY, "test query")
        monkeypatch.setenv(ENV_RUN_ID, "abc-123")
        monkeypatch.setenv(ENV_RUN_DIR, "/runs/abc-123")
        monkeypatch.setenv(ENV_RUN_DEPTH, "2")

        ret = main([str(phase)])

        assert ret == 0
        data = json.loads(state.read_text())
        assert data["skill_name"] == "hello-world"
        assert data["version"] == "0.3.0"
        assert data["user_query"] == "test query"
        assert data["run_id"] == "abc-123"
        assert data["run_dir"] == "/runs/abc-123"
        assert data["depth"] == 2

    def test_prev_read_from_previous_state(self, tmp_path, monkeypatch):
        phase = _write_phase(
            tmp_path,
            "2.use.py",
            "def run(ctx, prev): return {'echo': prev}\n",
        )
        state = tmp_path / "state.json"
        prev_state = tmp_path / "prev.json"
        prev_state.write_text(json.dumps({"city": "Sydney", "temp_c": 19}))
        monkeypatch.setenv(ENV_STATE_PATH, str(state))
        monkeypatch.setenv(ENV_PREV_STATE_PATH, str(prev_state))

        ret = main([str(phase)])

        assert ret == 0
        assert json.loads(state.read_text())["echo"] == {"city": "Sydney", "temp_c": 19}

    def test_first_phase_prev_is_empty_dict(self, tmp_path, monkeypatch):
        phase = _write_phase(
            tmp_path,
            "1.first.py",
            "def run(ctx, prev): return {'is_empty': prev == {}}\n",
        )
        state = tmp_path / "state.json"
        monkeypatch.setenv(ENV_STATE_PATH, str(state))
        monkeypatch.delenv(ENV_PREV_STATE_PATH, raising=False)

        ret = main([str(phase)])

        assert ret == 0
        assert json.loads(state.read_text())["is_empty"] is True

    def test_depth_defaults_to_zero(self, tmp_path, monkeypatch):
        phase = _write_phase(
            tmp_path,
            "1.do.py",
            "def run(ctx, prev): return {'depth': ctx['depth']}\n",
        )
        state = tmp_path / "state.json"
        monkeypatch.setenv(ENV_STATE_PATH, str(state))
        monkeypatch.delenv(ENV_RUN_DEPTH, raising=False)

        ret = main([str(phase)])

        assert ret == 0
        assert json.loads(state.read_text())["depth"] == 0


class TestPhaseRunnerErrors:
    def test_phase_run_raises_returns_traceback_on_stderr(
        self, tmp_path, monkeypatch, capsys
    ):
        phase = _write_phase(
            tmp_path,
            "1.bad.py",
            "def run(ctx, prev): raise ValueError('boom')\n",
        )
        state = tmp_path / "state.json"
        monkeypatch.setenv(ENV_STATE_PATH, str(state))

        ret = main([str(phase)])

        assert ret == 1
        err = capsys.readouterr().err
        assert "ValueError" in err
        assert "boom" in err
        assert not state.exists()

    def test_phase_import_error_returns_traceback(
        self, tmp_path, monkeypatch, capsys
    ):
        phase = _write_phase(
            tmp_path,
            "1.bad.py",
            "import nonexistent_module_xyz_123\n"
            "def run(ctx, prev): return {}\n",
        )
        state = tmp_path / "state.json"
        monkeypatch.setenv(ENV_STATE_PATH, str(state))

        ret = main([str(phase)])

        assert ret == 1
        assert "nonexistent_module_xyz_123" in capsys.readouterr().err
        assert not state.exists()

    def test_missing_run_function_errors(self, tmp_path, monkeypatch, capsys):
        phase = _write_phase(
            tmp_path,
            "1.bad.py",
            "def something_else(): pass\n",
        )
        state = tmp_path / "state.json"
        monkeypatch.setenv(ENV_STATE_PATH, str(state))

        ret = main([str(phase)])

        assert ret == 1
        assert "run" in capsys.readouterr().err.lower()
        assert not state.exists()

    def test_run_returns_non_dict_errors(self, tmp_path, monkeypatch, capsys):
        phase = _write_phase(
            tmp_path,
            "1.bad.py",
            "def run(ctx, prev): return 42\n",
        )
        state = tmp_path / "state.json"
        monkeypatch.setenv(ENV_STATE_PATH, str(state))

        ret = main([str(phase)])

        assert ret == 1
        assert "dict" in capsys.readouterr().err
        assert not state.exists()

    def test_missing_phase_file_returns_2(self, tmp_path, monkeypatch, capsys):
        nope = tmp_path / "nope.py"
        state = tmp_path / "state.json"
        monkeypatch.setenv(ENV_STATE_PATH, str(state))

        ret = main([str(nope)])

        assert ret == 2
        assert "not found" in capsys.readouterr().err.lower()

    def test_missing_state_path_env_returns_2(self, tmp_path, monkeypatch, capsys):
        phase = _write_phase(
            tmp_path,
            "1.do.py",
            "def run(ctx, prev): return {}\n",
        )
        monkeypatch.delenv(ENV_STATE_PATH, raising=False)

        ret = main([str(phase)])

        assert ret == 2
        assert ENV_STATE_PATH in capsys.readouterr().err

    def test_no_args_prints_usage_returns_2(self, monkeypatch, capsys):
        monkeypatch.setenv(ENV_STATE_PATH, "/tmp/ignored")

        ret = main([])

        assert ret == 2
        assert "usage" in capsys.readouterr().err.lower()


class TestStateSerialization:
    """Returned dicts contain assorted JSON-compatible values."""

    def test_serializes_nested_dict(self, tmp_path, monkeypatch):
        phase = _write_phase(
            tmp_path,
            "1.do.py",
            (
                "def run(ctx, prev):\n"
                "    return {'meta': {'a': 1, 'b': [1, 2, 3]}, 'flag': True}\n"
            ),
        )
        state = tmp_path / "state.json"
        monkeypatch.setenv(ENV_STATE_PATH, str(state))

        ret = main([str(phase)])

        assert ret == 0
        out = json.loads(state.read_text())
        assert out == {"meta": {"a": 1, "b": [1, 2, 3]}, "flag": True}

    def test_serializes_path_via_default_str(self, tmp_path, monkeypatch):
        """Paths and other non-JSON natives are coerced via `str()`.

        Phase authors return `Path` objects all the time; the runner
        shouldn't force them to remember to `str(path)` first.
        """
        phase = _write_phase(
            tmp_path,
            "1.do.py",
            (
                "from pathlib import Path\n"
                "def run(ctx, prev): return {'p': Path('/tmp/x')}\n"
            ),
        )
        state = tmp_path / "state.json"
        monkeypatch.setenv(ENV_STATE_PATH, str(state))

        ret = main([str(phase)])

        assert ret == 0
        assert json.loads(state.read_text())["p"] == "/tmp/x"
