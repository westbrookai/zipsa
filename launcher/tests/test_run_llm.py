import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zipsa.run_llm import build_run_prompt, build_run_argv


def _fake_proc(*, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
    """A stand-in for subprocess.Popen: byte pipes + a returncode from wait()."""
    proc = MagicMock()
    proc.stdout = io.BytesIO(stdout)
    proc.stderr = io.BytesIO(stderr)
    proc.wait.return_value = returncode
    proc.returncode = returncode
    return proc


def _skill(tmp_path):
    root = tmp_path / "weather"
    (root / "zipsa-dist").mkdir(parents=True)
    (root / "SKILL.md").write_text("# weather\nFetch then report. Call exec.\n")
    return root


class TestBuildRunPrompt:
    def test_includes_skill_md_and_run_protocol(self, tmp_path):
        root = _skill(tmp_path)
        p = build_run_prompt(root, user_input="Sydney")
        assert "Fetch then report" in p          # SKILL.md inlined
        assert "mcp__zipsa__exec" in p            # how to call scripts
        assert "Sydney" in p                      # the user input

    def test_prepends_intent_when_present(self, tmp_path):
        root = _skill(tmp_path)
        (root / "INTENT.md").write_text("Tell me if I need an umbrella.\n")
        p = build_run_prompt(root, user_input="")
        assert "umbrella" in p
        assert "Intent" in p

    def test_prefers_zipsa_intent_over_legacy(self, tmp_path):
        """New layout: zipsa/INTENT.md wins over a legacy skill-root one."""
        root = _skill(tmp_path)
        (root / "zipsa").mkdir()
        (root / "zipsa" / "INTENT.md").write_text("New-layout intent.\n")
        (root / "INTENT.md").write_text("Legacy intent.\n")
        p = build_run_prompt(root, user_input="")
        assert "New-layout intent." in p
        assert "Legacy intent." not in p

    def test_falls_back_to_legacy_intent(self, tmp_path):
        """No zipsa/INTENT.md → still reads the legacy skill-root one."""
        root = _skill(tmp_path)
        (root / "INTENT.md").write_text("Legacy only.\n")
        p = build_run_prompt(root, user_input="")
        assert "Legacy only." in p


class TestBuildRunArgv:
    def test_mounts_skill_ro_and_wires_mcp(self, tmp_path):
        root = _skill(tmp_path)
        argv = build_run_argv(
            image="img", skill_root=root,
            mcp_config_host=tmp_path / "m.json", prompt="P", env_file=None,
        )
        assert argv[:3] == ["docker", "run", "--rm"]
        assert f"{root}:{root}:ro" in argv         # skill mounted read-only
        # the mcp-config file is actually mounted into the container
        assert f"{tmp_path / 'm.json'}:/tmp/zipsa-run-mcp.json:ro" in argv
        assert "--mcp-config" in argv
        assert "claude" in argv and "-p" in argv
        assert "bypassPermissions" in argv

    def test_env_file_added_when_given(self, tmp_path):
        root = _skill(tmp_path)
        ef = tmp_path / ".env"; ef.write_text("CLAUDE_CODE_OAUTH_TOKEN=t\n")
        argv = build_run_argv(
            image="img", skill_root=root,
            mcp_config_host=tmp_path / "m.json", prompt="P", env_file=ef,
        )
        i = argv.index("--env-file")
        assert argv[i + 1] == str(ef)

    def test_extra_mounts_added_ro(self, tmp_path):
        root = _skill(tmp_path)
        argv = build_run_argv(
            image="img", skill_root=root, mcp_config_host=tmp_path / "m.json",
            prompt="P", env_file=None,
            extra_mounts=[(Path("/host/c.json"), "/mnt/c.json")],
        )
        assert "/host/c.json:/mnt/c.json:ro" in argv


class TestRunSkillLlm:
    @patch("zipsa.run_llm.subprocess.Popen")
    @patch("zipsa.run_llm.RunServer")
    def test_starts_server_runs_container_stops_server(self, mock_server_cls, mock_popen, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        root = tmp_path / "weather"; (root / "zipsa-dist").mkdir(parents=True)
        (root / "SKILL.md").write_text("# weather\n")
        srv = MagicMock(); srv.port = 51111; srv.token = "tok"
        mock_server_cls.return_value = srv
        mock_popen.return_value = _fake_proc(returncode=0)

        from zipsa.run_llm import run_skill_llm
        rc = run_skill_llm(root, "Sydney", image="img")

        assert rc == 0
        srv.start.assert_called_once()
        srv.stop.assert_called_once()                      # torn down even on success
        argv = mock_popen.call_args.args[0]
        assert argv[:2] == ["docker", "run"]
        assert "claude" in argv

    @patch("zipsa.run_llm.subprocess.Popen", side_effect=RuntimeError("boom"))
    @patch("zipsa.run_llm.RunServer")
    def test_server_stopped_on_error(self, mock_server_cls, mock_popen, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        root = tmp_path / "w"; (root / "zipsa-dist").mkdir(parents=True)
        (root / "SKILL.md").write_text("# w\n")
        srv = MagicMock(); srv.port = 51112; srv.token = "t"
        mock_server_cls.return_value = srv
        from zipsa.run_llm import run_skill_llm
        with pytest.raises(RuntimeError):
            run_skill_llm(root, "", image="img")
        srv.stop.assert_called_once()

    @patch("zipsa.run_llm.subprocess.Popen")
    @patch("zipsa.run_llm.RunServer")
    @patch("zipsa.run_llm.RunScriptHandler")
    def test_extra_mounts_go_to_script_handler_not_claude_container(
        self, mock_handler_cls, mock_server_cls, mock_popen, tmp_path, monkeypatch
    ):
        """extra_mounts must reach RunScriptHandler.default_mounts, NOT the claude argv."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        root = tmp_path / "cred-skill"; (root / "zipsa-dist").mkdir(parents=True)
        (root / "SKILL.md").write_text("# cred-skill\n")
        srv = MagicMock(); srv.port = 51113; srv.token = "tok2"
        mock_server_cls.return_value = srv
        mock_popen.return_value = _fake_proc(returncode=0)

        extra = [(Path("/a/creds.json"), "/mnt/creds.json")]

        from zipsa.run_llm import run_skill_llm
        run_skill_llm(root, "", image="img", extra_mounts=extra)

        # RunScriptHandler was constructed with the mounts as default_mounts
        _, handler_kwargs = mock_handler_cls.call_args
        assert handler_kwargs.get("default_mounts") == extra

        # The claude-container argv must NOT contain the cred path
        argv = mock_popen.call_args.args[0]
        assert "/a/creds.json" not in " ".join(str(a) for a in argv), (
            "Skill creds must not be mounted into the claude container"
        )

    @patch("zipsa.run_llm.subprocess.Popen")
    @patch("zipsa.run_llm.RunServer")
    @patch("zipsa.run_llm.RunScriptHandler")
    def test_no_extra_mounts_handler_gets_empty_default(
        self, mock_handler_cls, mock_server_cls, mock_popen, tmp_path, monkeypatch
    ):
        """When no extra_mounts, RunScriptHandler is constructed with default_mounts=None."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        root = tmp_path / "plain"; (root / "zipsa-dist").mkdir(parents=True)
        (root / "SKILL.md").write_text("# plain\n")
        srv = MagicMock(); srv.port = 51114; srv.token = "tok3"
        mock_server_cls.return_value = srv
        mock_popen.return_value = _fake_proc(returncode=0)

        from zipsa.run_llm import run_skill_llm
        run_skill_llm(root, "", image="img")

        _, handler_kwargs = mock_handler_cls.call_args
        # default_mounts should be None (falsy) when no extra_mounts supplied
        assert not handler_kwargs.get("default_mounts")


class TestRunSkillLlmLogging:
    """`zipsa run` (LLM path) persists a run record under ZIPSA_HOME so
    unwatched/scheduled runs leave a trace — matching exec's layout."""

    def _skill(self, tmp_path):
        root = tmp_path / "weather"; (root / "zipsa-dist").mkdir(parents=True)
        (root / "SKILL.md").write_text("# weather\n")
        return root

    @patch("zipsa.run_llm.subprocess.Popen")
    @patch("zipsa.run_llm.RunServer")
    def test_writes_run_record_and_logs(self, mock_server_cls, mock_popen, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setenv("ZIPSA_HOME", str(home))
        root = self._skill(tmp_path)
        srv = MagicMock(); srv.port = 51211; srv.token = "tok"
        mock_server_cls.return_value = srv
        mock_popen.return_value = _fake_proc(
            stdout=b"hello stdout\n", stderr=b"hello stderr\n", returncode=0,
        )

        from zipsa.run_llm import run_skill_llm
        rc = run_skill_llm(root, "Sydney", image="img")

        assert rc == 0
        runs = home / "weather" / "runs"
        assert runs.is_dir()
        run_dirs = list(runs.iterdir())
        assert len(run_dirs) == 1
        rd = run_dirs[0]

        saved = json.loads((rd / "result.json").read_text())
        assert saved["skill_name"] == "weather"
        assert saved["mode"] == "run"
        assert saved["exit_code"] == 0
        assert saved["user_input"] == "Sydney"
        assert saved["duration_ms"] >= 0
        assert saved["run_dir"] == str(rd)

        assert "hello stdout" in (rd / "stdout.log").read_text()
        assert "hello stderr" in (rd / "stderr.log").read_text()
        assert (rd / "artifacts").is_dir()

    @patch("zipsa.run_llm.subprocess.Popen")
    @patch("zipsa.run_llm.RunServer")
    def test_tee_writes_to_passed_stream_too(self, mock_server_cls, mock_popen, tmp_path, monkeypatch):
        """The tee preserves live UX: every chunk reaches the real stdout/stderr
        AND the on-disk logs."""
        home = tmp_path / "home"
        monkeypatch.setenv("ZIPSA_HOME", str(home))
        root = self._skill(tmp_path)
        srv = MagicMock(); srv.port = 51212; srv.token = "tok"
        mock_server_cls.return_value = srv
        mock_popen.return_value = _fake_proc(
            stdout=b"LIVE-OUT\n", stderr=b"LIVE-ERR\n", returncode=0,
        )

        out_buf = io.StringIO()
        err_buf = io.StringIO()

        from zipsa.run_llm import run_skill_llm
        run_skill_llm(root, "x", image="img", stdout=out_buf, stderr=err_buf)

        # live streams received the bytes
        assert "LIVE-OUT" in out_buf.getvalue()
        assert "LIVE-ERR" in err_buf.getvalue()
        # and so did the files
        rd = next((home / "weather" / "runs").iterdir())
        assert "LIVE-OUT" in (rd / "stdout.log").read_text()
        assert "LIVE-ERR" in (rd / "stderr.log").read_text()

    @patch("zipsa.run_llm.subprocess.Popen")
    @patch("zipsa.run_llm.RunServer")
    def test_utf8_multibyte_not_corrupted(self, mock_server_cls, mock_popen, tmp_path, monkeypatch):
        """Korean (multi-byte UTF-8) output whose bytes are returned by the
        OS pipe in fragments that split a character mid-sequence must still
        reach the LIVE terminal intact (no U+FFFD) and be byte-exact on disk.

        A plain io.BytesIO returns everything in one .read(4096), so it never
        crosses a boundary; here .read(n) yields ONE byte at a time, which
        forces every 3-byte Korean char to span multiple reads. This would
        produce U+FFFD with a per-chunk independent decode, so the test
        genuinely guards the incremental decoder.
        """
        home = tmp_path / "home"
        monkeypatch.setenv("ZIPSA_HOME", str(home))
        root = self._skill(tmp_path)
        srv = MagicMock(); srv.port = 51213; srv.token = "tok"
        mock_server_cls.return_value = srv
        text = "버스가 곧 도착합니다\n"
        raw = text.encode("utf-8")

        class _ByteAtATime:
            """A pipe-like stream whose read(n) returns at most ONE byte."""
            def __init__(self, data: bytes):
                self._data = data
                self._pos = 0

            def read(self, _n: int = -1) -> bytes:
                if self._pos >= len(self._data):
                    return b""
                b = self._data[self._pos:self._pos + 1]
                self._pos += 1
                return b

        proc = MagicMock()
        proc.stdout = _ByteAtATime(raw)
        proc.stderr = io.BytesIO(b"")
        proc.wait.return_value = 0
        proc.returncode = 0
        mock_popen.return_value = proc

        out_buf = io.StringIO()
        from zipsa.run_llm import run_skill_llm
        run_skill_llm(root, "x", image="img", stdout=out_buf)

        # (a) LIVE stream has the correct text with NO replacement chars.
        live = out_buf.getvalue()
        assert text.strip() in live
        assert "�" not in live
        # (b) on-disk log is byte-exact equal to the original encoded bytes.
        rd = next((home / "weather" / "runs").iterdir())
        assert (rd / "stdout.log").read_bytes() == raw

    @patch("zipsa.run_llm.subprocess.Popen")
    @patch("zipsa.run_llm.RunServer")
    def test_returns_container_exit_code(self, mock_server_cls, mock_popen, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setenv("ZIPSA_HOME", str(home))
        root = self._skill(tmp_path)
        srv = MagicMock(); srv.port = 51214; srv.token = "tok"
        mock_server_cls.return_value = srv
        mock_popen.return_value = _fake_proc(returncode=7)

        from zipsa.run_llm import run_skill_llm
        rc = run_skill_llm(root, "x", image="img")
        assert rc == 7
        rd = next((home / "weather" / "runs").iterdir())
        assert json.loads((rd / "result.json").read_text())["exit_code"] == 7

    @patch("zipsa.run_llm.exec_runner.new_run_dir")
    @patch("zipsa.run_llm.subprocess.Popen")
    @patch("zipsa.run_llm.RunServer")
    def test_logging_failure_does_not_change_exit_code(
        self, mock_server_cls, mock_popen, mock_new_run_dir, tmp_path, monkeypatch
    ):
        """Best-effort logging: an un-writable run dir must not sink the run."""
        home = tmp_path / "home"
        monkeypatch.setenv("ZIPSA_HOME", str(home))
        root = self._skill(tmp_path)
        srv = MagicMock(); srv.port = 51215; srv.token = "tok"
        mock_server_cls.return_value = srv
        mock_popen.return_value = _fake_proc(stdout=b"out\n", returncode=0)

        # Point the run dir at a path that cannot be written to (a file, not a dir).
        bogus = tmp_path / "not-a-dir"
        bogus.write_text("x")
        mock_new_run_dir.return_value = bogus / "run"  # mkdir/write will fail

        from zipsa.run_llm import run_skill_llm
        rc = run_skill_llm(root, "x", image="img")
        assert rc == 0  # unchanged despite logging failure
