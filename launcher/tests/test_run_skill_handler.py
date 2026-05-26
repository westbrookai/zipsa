"""Tests for RunSkillHandler: subprocess wrapper that invokes a child
skill via uv run zipsa run, parses summary.json, returns the routing
fields the parent needs to chain get_artifact."""

import io
import json
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zipsa.core.hitl_mcp import HitlIO
from zipsa.core.run_skill_handler import RunSkillHandler
from zipsa.core.caller_context import CallerInfo, current_caller


def _build_handler(server_mock, children: list[str]):
    """Helper: build RunSkillHandler with a stubbed caller-children resolver."""
    h = RunSkillHandler(server=server_mock)
    h._resolve_caller_children = lambda c: children
    return h


def _install_child_skill(
    zipsa_home: Path,
    name: str,
    requires_block: str = "",
    version: str = "0.1.0",
) -> Path:
    """Drop a minimal child-skill manifest into ZIPSA_HOME/skills/<name>/
    so RunSkillHandler can load it before spawning the subprocess.
    `requires_block` is the inline YAML for `spec.requires:` (or "" for none).
    """
    skill_dir = zipsa_home / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    manifest = f"""apiVersion: zipsa.dev/v1alpha1
kind: SkillManifest
metadata:
  name: {name}
  version: {version}
  author: test
  description: |
    Child skill fixture for requires-resolution tests.
spec:
  purpose: |
    Tiny child skill used only by RunSkillHandler tests.
  instructions: ./SKILL.md
{requires_block}
  tools:
    builtin: []
  limits:
    max_turns: 1
    max_cost_usd: 0.01
    timeout_seconds: 5
"""
    (skill_dir / "manifest.yaml").write_text(manifest)
    (skill_dir / "SKILL.md").write_text(f"# {name}\nNo-op test fixture.\n")
    return skill_dir


def _make_hitl_io(stdin_text: str = "", is_interactive: bool = True) -> HitlIO:
    """HitlIO wired to StringIO so tests can drive prompts without sockets."""
    stdin = io.StringIO(stdin_text)
    stdout = io.StringIO()
    return HitlIO(
        stdin=stdin, stdout=stdout,
        stdout_lock=threading.Lock(),
        is_interactive=is_interactive,
    )


def _make_summary(run_dir: Path, status: str = "ok", skill: str = "alpha",
                   version: str = "0.1.0", exit_code: int = 0,
                   error: dict | None = None) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    s = {
        "schema_version": 1, "status": status, "exit_code": exit_code,
        "skill": skill, "version": version,
        "started_at": "2026-05-21T00:00:00+10:00",
        "finished_at": "2026-05-21T00:00:01+10:00",
        "duration_seconds": 1.0, "cost_usd": 0.01,
        "turns": 1, "phases": [],
    }
    if error is not None:
        s["error"] = error
    p = run_dir / "summary.json"
    p.write_text(json.dumps(s))
    return p


class TestRunSkillHandler:
    def test_rejects_child_not_in_caller_children(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        server = MagicMock(port=12345, token="parent-tok")
        h = _build_handler(server, children=["alpha", "beta"])
        current_caller.set(CallerInfo("parent", "1.0.0"))
        try:
            result = h.run(name="gamma", args="")
            assert result["status"] == "failed"
            assert result["error"]["code"] == "skill_not_in_children"
        finally:
            current_caller.set(None)

    def test_no_caller_context_returns_failed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        server = MagicMock(port=12345, token="parent-tok")
        h = _build_handler(server, children=["alpha"])
        current_caller.set(None)
        result = h.run(name="alpha", args="")
        assert result["status"] == "failed"
        assert result["error"]["code"] == "caller_unknown"

    def test_spawns_subprocess_with_propagated_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_child_skill(tmp_path, "alpha")
        server = MagicMock(port=12345, token="parent-tok")
        server._io = _make_hitl_io()
        h = _build_handler(server, children=["alpha"])
        current_caller.set(CallerInfo("parent", "1.0.0"))
        try:
            run_dir = tmp_path / "alpha@0.1.0" / "runs" / "2026-05-21_000000_000"
            _make_summary(run_dir)
            with patch("subprocess.Popen") as mock_run:
                proc = MagicMock()
                proc.poll.return_value = 0
                proc.returncode = 0
                proc.communicate.return_value = (b"", b"")
                mock_run.return_value = proc
                h._find_latest_run_dir = lambda name: run_dir
                result = h.run(name="alpha", args="hello")

            call_kwargs = mock_run.call_args.kwargs
            env = call_kwargs["env"]
            assert env["ZIPSA_PARENT_MCP_URL"] == "http://host.docker.internal:12345/mcp"
            assert env["ZIPSA_PARENT_MCP_TOKEN"]
            assert "parent" in env["ZIPSA_CALL_TRACE"]
            assert env["ZIPSA_CALL_DEPTH"] == "1"
            assert call_kwargs["stdin"] == subprocess.DEVNULL
            assert call_kwargs["stdout"] == subprocess.DEVNULL
            assert call_kwargs["stderr"] == subprocess.PIPE

            # The child token should have been registered on the server
            server.register_caller.assert_called()
            first_call = server.register_caller.call_args_list[0]
            child_token = first_call.args[0]
            assert env["ZIPSA_PARENT_MCP_TOKEN"] == child_token

            # Result shape
            assert result["status"] == "ok"
            assert result["skill"] == "alpha"
            assert result["version"] == "0.1.0"
            assert result["run_id"] == "2026-05-21_000000_000"
            assert "summary" in result
        finally:
            current_caller.set(None)

    def test_call_trace_extended_when_already_present(self, tmp_path, monkeypatch):
        """When ZIPSA_CALL_TRACE is already set (we ourselves are a child),
        extend it rather than overwriting."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        monkeypatch.setenv("ZIPSA_CALL_TRACE", "grandparent")
        monkeypatch.setenv("ZIPSA_CALL_DEPTH", "2")
        _install_child_skill(tmp_path, "alpha")
        server = MagicMock(port=12345, token="parent-tok")
        server._io = _make_hitl_io()
        h = _build_handler(server, children=["alpha"])
        current_caller.set(CallerInfo("parent", "1.0.0"))
        try:
            run_dir = tmp_path / "alpha@0.1.0" / "runs" / "r1"
            _make_summary(run_dir)
            with patch("subprocess.Popen") as mock_run:
                proc = MagicMock()
                proc.poll.return_value = 0
                proc.returncode = 0
                proc.communicate.return_value = (b"", b"")
                mock_run.return_value = proc
                h._find_latest_run_dir = lambda name: run_dir
                h.run(name="alpha", args="")
            env = mock_run.call_args.kwargs["env"]
            assert env["ZIPSA_CALL_TRACE"] == "grandparent,parent"
            assert env["ZIPSA_CALL_DEPTH"] == "3"
        finally:
            current_caller.set(None)

    def test_child_failed_status_propagates(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_child_skill(tmp_path, "alpha")
        server = MagicMock(port=12345, token="parent-tok")
        server._io = _make_hitl_io()
        h = _build_handler(server, children=["alpha"])
        current_caller.set(CallerInfo("parent", "1.0.0"))
        try:
            run_dir = tmp_path / "alpha@0.1.0" / "runs" / "r1"
            _make_summary(run_dir, status="failed", exit_code=1,
                          error={"code": "agent_error", "message": "x"})
            with patch("subprocess.Popen") as mock_run:
                proc = MagicMock()
                proc.poll.return_value = 1
                proc.returncode = 1
                proc.communicate.return_value = (b"", b"")
                mock_run.return_value = proc
                h._find_latest_run_dir = lambda name: run_dir
                result = h.run(name="alpha", args="")
            assert result["status"] == "failed"
            assert result["exit_code"] == 1
            assert result["summary"]["error"]["code"] == "agent_error"
        finally:
            current_caller.set(None)

    def test_summary_missing_returns_failed(self, tmp_path, monkeypatch):
        """If the subprocess completes but no summary.json is found,
        surface a clean error."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_child_skill(tmp_path, "alpha")
        server = MagicMock(port=12345, token="parent-tok")
        server._io = _make_hitl_io()
        h = _build_handler(server, children=["alpha"])
        current_caller.set(CallerInfo("parent", "1.0.0"))
        try:
            with patch("subprocess.Popen") as mock_run:
                proc = MagicMock()
                proc.poll.return_value = 0
                proc.returncode = 0
                proc.communicate.return_value = (b"", b"")
                mock_run.return_value = proc
                h._find_latest_run_dir = lambda name: None
                result = h.run(name="alpha", args="")
            assert result["status"] == "failed"
            assert result["error"]["code"] == "summary_not_found"
        finally:
            current_caller.set(None)

    def test_cycle_rejected_in_process(self, tmp_path, monkeypatch):
        """Cycle caps must fire BEFORE spawning the child subprocess —
        env-var-based check in cli.py wouldn't trigger because every
        run_skill call goes through the same parent HitlServer process
        whose os.environ never accumulates new DEPTH/TRACE."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        server = MagicMock(port=12345, token="parent-tok")
        h = _build_handler(server, children=["loop"])
        # Caller already has "loop" in its trace
        current_caller.set(CallerInfo(
            "loop", "1.0.0", depth=2, trace=("a", "loop"),
        ))
        try:
            result = h.run(name="loop", args="")
            assert result["status"] == "failed"
            assert result["exit_code"] == 2
            assert result["error"]["code"] == "skill_cycle_detected"
            # Subprocess must NOT have been spawned
            server.register_caller.assert_not_called()
        finally:
            current_caller.set(None)

    def test_depth_cap_rejected_in_process(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        server = MagicMock(port=12345, token="parent-tok")
        h = _build_handler(server, children=["lvl5"])
        # Caller at depth 4 → calling lvl5 would push to 5, hitting cap
        current_caller.set(CallerInfo(
            "lvl4", "1.0.0", depth=4,
            trace=("lvl0", "lvl1", "lvl2", "lvl3"),
        ))
        try:
            result = h.run(name="lvl5", args="")
            assert result["status"] == "failed"
            assert result["exit_code"] == 2
            assert result["error"]["code"] == "skill_depth_exceeded"
            server.register_caller.assert_not_called()
        finally:
            current_caller.set(None)

    def test_child_token_registered_with_extended_chain(self, tmp_path, monkeypatch):
        """When spawning a child, the registered CallerInfo must carry
        depth+1 and trace+(caller,) so the child's OWN run_skill calls
        see an accurate chain."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_child_skill(tmp_path, "alpha")
        server = MagicMock(port=12345, token="parent-tok")
        server._io = _make_hitl_io()
        h = _build_handler(server, children=["alpha"])
        current_caller.set(CallerInfo(
            "parent", "1.0.0", depth=2, trace=("gp", "p"),
        ))
        try:
            run_dir = tmp_path / "alpha@0.1.0" / "runs" / "r1"
            _make_summary(run_dir)
            with patch("subprocess.Popen") as mock_run:
                proc = MagicMock()
                proc.poll.return_value = 0
                proc.returncode = 0
                proc.communicate.return_value = (b"", b"")
                mock_run.return_value = proc
                h._find_latest_run_dir = lambda name: run_dir
                h.run(name="alpha", args="")

            # First register_caller call (the one before subprocess
            # spawn) should pass the extended chain.
            first_call = server.register_caller.call_args_list[0]
            registered_info = first_call.args[1]
            assert registered_info.skill == "alpha"
            assert registered_info.depth == 3  # 2 + 1
            assert registered_info.trace == ("gp", "p", "parent")
        finally:
            current_caller.set(None)

    def test_max_call_depth_matches_cli(self):
        """Handler's _MAX_CALL_DEPTH must match cli._MAX_CALL_DEPTH so
        both enforcement points (in-process here + env-var check in cli
        for direct child invocations) agree on the cap."""
        from zipsa.cli import _MAX_CALL_DEPTH as cli_max
        from zipsa.core.run_skill_handler import _MAX_CALL_DEPTH as handler_max
        assert cli_max == handler_max

    def test_child_with_no_requires_proceeds(self, tmp_path, monkeypatch):
        """Baseline: child has no spec.requires → no resolution attempted,
        subprocess spawned normally. Guards against the new code path
        adding a regression when there's nothing to resolve."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_child_skill(tmp_path, "alpha")
        server = MagicMock(port=12345, token="parent-tok")
        server._io = _make_hitl_io()
        h = _build_handler(server, children=["alpha"])
        current_caller.set(CallerInfo("parent", "1.0.0"))
        try:
            run_dir = tmp_path / "alpha@0.1.0" / "runs" / "r1"
            _make_summary(run_dir)
            with patch("subprocess.Popen") as mock_run:
                proc = MagicMock()
                proc.poll.return_value = 0
                proc.returncode = 0
                proc.communicate.return_value = (b"", b"")
                mock_run.return_value = proc
                h._find_latest_run_dir = lambda name: run_dir
                result = h.run(name="alpha", args="")
            assert result["status"] == "ok"
            mock_run.assert_called_once()
        finally:
            current_caller.set(None)

    def test_child_not_installed_returns_clean_error(self, tmp_path, monkeypatch):
        """If the child name passed children-allowlist check but is not
        actually installed (no ~/.zipsa/skills/<name>/ dir), fail with
        `child_not_installed` BEFORE attempting subprocess spawn. This is
        a guard so the new requires-resolution path can rely on
        installed_skill_dir existing."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        # Note: NOT calling _install_child_skill here
        server = MagicMock(port=12345, token="parent-tok")
        server._io = _make_hitl_io()
        h = _build_handler(server, children=["alpha"])
        current_caller.set(CallerInfo("parent", "1.0.0"))
        try:
            with patch("subprocess.Popen") as mock_run:
                result = h.run(name="alpha", args="")
            assert result["status"] == "failed"
            assert result["error"]["code"] == "child_not_installed"
            mock_run.assert_not_called()
        finally:
            current_caller.set(None)

    def test_child_with_existing_requires_yaml_skips_prompt(
        self, tmp_path, monkeypatch
    ):
        """If the child already has a valid requires.yaml, no prompt fires —
        subprocess spawns immediately. Verifies we don't unconditionally
        re-prompt on every nested invocation."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        # Real existing directories so list[directory] validation passes
        proj1 = tmp_path / "projects" / "p1"
        proj1.mkdir(parents=True)
        _install_child_skill(
            tmp_path, "alpha",
            requires_block=(
                "  requires:\n"
                "    project_roots:\n"
                "      type: list[directory]\n"
                "      prompt: |\n"
                "        Paths?\n"
            ),
        )
        # Pre-populate requires.yaml
        from zipsa.core.requires import save_requires
        from zipsa.paths import skill_requires_file
        req_file = skill_requires_file("alpha", "0.1.0")
        save_requires(req_file, {"project_roots": [str(proj1)]})

        # stdin EMPTY: a prompt firing would EOF immediately and fail the test
        server = MagicMock(port=12345, token="parent-tok")
        server._io = _make_hitl_io(stdin_text="", is_interactive=True)
        h = _build_handler(server, children=["alpha"])
        current_caller.set(CallerInfo("parent", "1.0.0"))
        try:
            run_dir = tmp_path / "alpha@0.1.0" / "runs" / "r1"
            _make_summary(run_dir)
            with patch("subprocess.Popen") as mock_run:
                proc = MagicMock()
                proc.poll.return_value = 0
                proc.returncode = 0
                proc.communicate.return_value = (b"", b"")
                mock_run.return_value = proc
                h._find_latest_run_dir = lambda name: run_dir
                result = h.run(name="alpha", args="")
            assert result["status"] == "ok"
            mock_run.assert_called_once()
            # stdin must remain unread
            assert server._io.stdin.read() == ""
        finally:
            current_caller.set(None)

    def test_child_with_unset_requires_prompts_via_parent_stdin(
        self, tmp_path, monkeypatch
    ):
        """The point of the fix. Parent is interactive (real stdin/stdout
        via its HitlIO), child has spec.requires but no requires.yaml.
        run_skill must prompt the user via the PARENT's stdin (the only
        real terminal in the call chain), save the child's requires.yaml,
        then spawn the subprocess — which can now read the file without
        needing its own TTY."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        proj1 = tmp_path / "projects" / "p1"
        proj1.mkdir(parents=True)
        _install_child_skill(
            tmp_path, "alpha",
            requires_block=(
                "  requires:\n"
                "    project_roots:\n"
                "      type: list[directory]\n"
                "      prompt: |\n"
                "        Paths? (one per line, empty line to finish)\n"
            ),
        )
        # stdin feeds one path then a blank line to terminate the list
        stdin_text = f"{proj1}\n\n"
        server = MagicMock(port=12345, token="parent-tok")
        server._io = _make_hitl_io(
            stdin_text=stdin_text, is_interactive=True,
        )
        h = _build_handler(server, children=["alpha"])
        current_caller.set(CallerInfo("parent", "1.0.0"))
        try:
            run_dir = tmp_path / "alpha@0.1.0" / "runs" / "r1"
            _make_summary(run_dir)
            with patch("subprocess.Popen") as mock_run:
                proc = MagicMock()
                proc.poll.return_value = 0
                proc.returncode = 0
                proc.communicate.return_value = (b"", b"")
                mock_run.return_value = proc
                h._find_latest_run_dir = lambda name: run_dir
                result = h.run(name="alpha", args="")

            # Resolution must have persisted child's requires.yaml
            from zipsa.paths import skill_requires_file
            from zipsa.core.requires import load_requires
            saved = load_requires(skill_requires_file("alpha", "0.1.0"))
            assert saved == {"project_roots": [str(proj1)]}

            # Subprocess spawned AFTER resolution succeeded
            assert result["status"] == "ok"
            mock_run.assert_called_once()
        finally:
            current_caller.set(None)

    def test_child_with_unset_requires_fails_when_parent_not_interactive(
        self, tmp_path, monkeypatch
    ):
        """If the parent itself is non-interactive (e.g., cron, redirected
        stdin), we cannot prompt the user. Fail with a clear MCP error
        code so the orchestrator agent surfaces a usable message instead
        of swallowing an opaque subprocess exit 4."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_child_skill(
            tmp_path, "alpha",
            requires_block=(
                "  requires:\n"
                "    project_roots:\n"
                "      type: list[directory]\n"
                "      prompt: |\n"
                "        Paths?\n"
            ),
        )
        server = MagicMock(port=12345, token="parent-tok")
        server._io = _make_hitl_io(stdin_text="", is_interactive=False)
        h = _build_handler(server, children=["alpha"])
        current_caller.set(CallerInfo("parent", "1.0.0"))
        try:
            with patch("subprocess.Popen") as mock_run:
                result = h.run(name="alpha", args="")
            assert result["status"] == "failed"
            assert result["error"]["code"] == "child_requires_unset"
            assert "project_roots" in result["error"]["message"]
            mock_run.assert_not_called()
        finally:
            current_caller.set(None)

    def test_child_timeout_returns_failed(self, tmp_path, monkeypatch):
        """Timeout is enforced by our own poll loop (Popen-based) so we
        can simulate it by having poll() always return None and shrinking
        the timeout env var to ~0."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        monkeypatch.setenv("ZIPSA_RUN_SKILL_TIMEOUT", "0")
        _install_child_skill(tmp_path, "alpha")
        server = MagicMock(port=12345, token="parent-tok")
        server._io = _make_hitl_io()
        h = _build_handler(server, children=["alpha"])
        current_caller.set(CallerInfo("parent", "1.0.0"))
        try:
            with patch("subprocess.Popen") as mock_run:
                proc = MagicMock()
                # Never finishes: poll always None until killed
                proc.poll.return_value = None
                proc.returncode = -9
                proc.wait.return_value = -9
                proc.communicate.return_value = (b"", b"")
                mock_run.return_value = proc
                result = h.run(name="alpha", args="")
            assert result["status"] == "failed"
            assert result["error"]["code"] == "child_timeout"
        finally:
            current_caller.set(None)
