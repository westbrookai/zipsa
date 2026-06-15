# Run-time Orchestrator (`zipsa run`, Forge model) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first sub-project of the Forge redesign — a `zipsa run` that executes a skill as *an LLM following SKILL.md, calling scripts via an MCP `exec` tool* (not a fixed deterministic pipeline).

**Architecture:** Mirror the existing `zipsa create` machinery (headless `claude -p` in a container + a host FastMCP server + `--mcp-config`), but with **SKILL.md as the LLM's instruction** and a **single-script `exec` tool + HITL** (no `promote`). The skill is mounted read-only; the LLM calls `mcp__zipsa__exec(script, args, prev)` to run one script at a time through the existing `exec_runner.run_phase` (container isolation, stdin/stdout contract, creds mounts). Spec: `docs/superpowers/specs/2026-06-15-skill-runtime-forge-redesign-design.md`.

**Tech Stack:** Python 3.12, uv, pytest, Typer, FastMCP/uvicorn (already used by `CreateServer`), Docker.

---

## Pinned design decisions (spec left these open)

1. **scripts-as-tools = one generic tool, single-script granularity.** The LLM calls `mcp__zipsa__exec(script, args="", prev=None)` to run **one** script (via `run_phase`), not the whole pipeline (`run_phases` was the deterministic-pipeline model we're replacing) and not per-script typed tools (YAGNI). The LLM is the orchestrator; it threads data by passing a prior result dict as `prev`, or via `/out` artifacts.
2. **SKILL.md reaches the LLM as the `-p` prompt**, wrapped by a thin runner preamble (mirrors `build_create_prompt`). If `INTENT.md` exists in the skill, it is prepended as `## Intent (why)` context for unhappy-path judgment; SKILL.md is the operative instruction.
3. **CLI dispatch, not replacement (this phase).** `zipsa run <name>` routes **exec-format skills** (have `SKILL.md` + `zipsa-dist/`, no `manifest.yaml`) to the new runtime; **legacy manifest skills** keep using `DockerExecutor`. Retiring legacy `run`/`exec` happens after skills migrate (later plan). Skill resolved by the existing `resolve_skill(name)`; a filesystem path is also accepted.
4. **exec tool is scoped to the run's skill root** (not `~/.zipsa/staging` like create). The LLM cannot exec outside the skill being run.

---

## File Structure

- Create `launcher/zipsa/core/run_script_handler.py` — `RunScriptHandler(docker_image, skill_root)`: resolves a script name → `zipsa-dist/<file>`, runs it via `run_phase`, returns a result dict. (Run-time analogue of `ExecSkillHandler`, but single-script + scoped to `skill_root`.)
- Create `launcher/zipsa/core/run_server.py` — `RunServer(hitl_io, exec_handler)`: a FastMCP host server registering `exec` + `ask`/`confirm`/`choose` (no `promote`). Reuses `_pick_free_port`, `_ALLOWED_HOSTS` (`hitl_runner.py`), `CallerContextMiddleware`/`CallerInfo` (`caller_context.py`), and the HITL handlers (`hitl_mcp.py`).
- Create `launcher/zipsa/run_llm.py` — `build_run_prompt(skill_root)`, `build_run_argv(...)`, `run_skill_llm(skill_root, user_input, *, image, env_file=None)`: the orchestration (mirror of `create.py`'s `build_create_prompt`/`build_docker_argv`/`run_create`). Reuses `build_mcp_config` from `create.py`.
- Modify `launcher/zipsa/cli.py` — the `run` command dispatches exec-format → `run_skill_llm`, else the existing `DockerExecutor` path.
- Tests: `launcher/tests/test_run_script_handler.py`, `launcher/tests/test_run_server.py`, `launcher/tests/test_run_llm.py`, extend `launcher/tests/test_cli.py`.

---

## Task 1: `RunScriptHandler` — run one script, scoped to the skill

**Files:**
- Create: `launcher/zipsa/core/run_script_handler.py`
- Test: `launcher/tests/test_run_script_handler.py`

- [ ] **Step 1: Write the failing test**

```python
# launcher/tests/test_run_script_handler.py
from pathlib import Path
from unittest.mock import patch
from zipsa.core.run_script_handler import RunScriptHandler


def _skill(tmp_path: Path) -> Path:
    dist = tmp_path / "s" / "zipsa-dist"
    dist.mkdir(parents=True)
    (dist / "1.fetch.py").write_text(
        "import json,sys\n"
        "p=json.loads(sys.stdin.read())\n"
        "print(json.dumps({'q': p['ctx']['user_query'], 'prev': p['prev']}))\n"
    )
    (tmp_path / "s" / "SKILL.md").write_text("# s\n")
    return tmp_path / "s"


class TestRunScriptHandler:
    def test_runs_named_script_local(self, tmp_path):
        root = _skill(tmp_path)
        h = RunScriptHandler(docker_image=None, skill_root=root)  # local mode
        out = h.run(script="1.fetch", args="hello", prev={"x": 1})
        assert out["status"] == "ok"
        assert out["result"] == {"q": "hello", "prev": {"x": 1}}
        assert out["exit_code"] == 0

    def test_unknown_script_is_error_not_crash(self, tmp_path):
        root = _skill(tmp_path)
        h = RunScriptHandler(docker_image=None, skill_root=root)
        out = h.run(script="9.nope")
        assert out["status"] == "failed"
        assert out["error"]["code"] == "script_not_found"

    def test_rejects_path_escape(self, tmp_path):
        root = _skill(tmp_path)
        h = RunScriptHandler(docker_image=None, skill_root=root)
        out = h.run(script="../../etc/passwd")
        assert out["status"] == "failed"
        assert out["error"]["code"] == "script_not_found"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd launcher && uv run pytest tests/test_run_script_handler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'zipsa.core.run_script_handler'`.

- [ ] **Step 3: Write minimal implementation**

```python
# launcher/zipsa/core/run_script_handler.py
"""Run a single skill script as a run-time tool call.

Run-time analogue of ExecSkillHandler: where create tests a whole draft
(run_phases over staging), run-time lets the orchestrating LLM invoke
ONE script at a time (run_phase), scoped to the skill being run.
"""
from __future__ import annotations

from pathlib import Path

from .phase_discovery import discover_phases
from ..exec_runner import run_phase


class RunScriptHandler:
    def __init__(self, docker_image: "str | None", skill_root: Path) -> None:
        self._image = docker_image
        self._root = skill_root.resolve()

    def _fail(self, code: str, message: str) -> dict:
        return {"status": "failed", "error": {"code": code, "message": message}}

    def _resolve(self, script: str) -> "Path | None":
        # Match against discovered phases by id, slug, "id.slug", or filename.
        try:
            phases = discover_phases(self._root)
        except Exception:
            return None
        for p in phases:
            if script in (p.id_str, p.slug, f"{p.id_str}.{p.slug}", p.path.name):
                return p.path
        return None

    def run(self, *, script: str, args: str = "", prev: "dict | None" = None) -> dict:
        path = self._resolve(script)
        if path is None:
            return self._fail("script_not_found", f"no such script: {script}")
        outcome = run_phase(
            path,
            skill_name=self._root.name,
            user_query=args,
            skill_root=self._root,
            docker_image=self._image,
            prev=prev or {},
        )
        return {
            "status": "ok" if outcome.exit_code == 0 else "failed",
            "script": f"{path.name}",
            "result": outcome.result,
            "exit_code": outcome.exit_code,
            "duration_ms": outcome.duration_ms,
            "stderr": outcome.stderr if outcome.exit_code != 0 else "",
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd launcher && uv run pytest tests/test_run_script_handler.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/core/run_script_handler.py launcher/tests/test_run_script_handler.py
git commit -m "feat(launcher): RunScriptHandler — run one skill script (run-time tool)"
```

---

## Task 2: `build_run_prompt` + `build_run_argv` (pure)

**Files:**
- Create: `launcher/zipsa/run_llm.py`
- Test: `launcher/tests/test_run_llm.py`

- [ ] **Step 1: Write the failing test**

```python
# launcher/tests/test_run_llm.py
from pathlib import Path
from zipsa.run_llm import build_run_prompt, build_run_argv


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


class TestBuildRunArgv:
    def test_mounts_skill_ro_and_wires_mcp(self, tmp_path):
        root = _skill(tmp_path)
        argv = build_run_argv(
            image="img", skill_root=root,
            mcp_config_host=tmp_path / "m.json", prompt="P", env_file=None,
        )
        assert argv[:3] == ["docker", "run", "--rm"]
        assert f"{root}:{root}:ro" in argv         # skill mounted read-only
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd launcher && uv run pytest tests/test_run_llm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'zipsa.run_llm'`.

- [ ] **Step 3: Write minimal implementation**

```python
# launcher/zipsa/run_llm.py
"""Run-time orchestration: execute a skill as an LLM following SKILL.md.

Mirror of create.py for the run path — headless claude in a container,
SKILL.md as the instruction, a host MCP server exposing exec + HITL.
"""
from __future__ import annotations

import json
import platform
import subprocess
import tempfile
from pathlib import Path

from .create import build_mcp_config  # reuse the container mcp-config shape

_CONTAINER_MCP_CONFIG = "/tmp/zipsa-run-mcp.json"


def build_run_prompt(skill_root: Path, user_input: str) -> str:
    skill_md = (skill_root / "SKILL.md").read_text()
    intent_path = skill_root / "INTENT.md"
    intent = (
        f"## Intent (why)\n{intent_path.read_text()}\n\n"
        if intent_path.exists() else ""
    )
    return (
        "You are RUNNING a zipsa skill. Follow SKILL.md (your constitution)\n"
        "to accomplish the user's request. Run the skill's scripts with\n"
        "mcp__zipsa__exec(script=\"<id-or-slug>\", args=\"...\", prev=<dict>)\n"
        "— one script per call; thread data via `prev` or /out artifacts.\n"
        "On errors, judge what to do and explain the outcome to the user.\n\n"
        f"{intent}"
        f"User request: {user_input}\n\n"
        "===== SKILL.md (constitution) =====\n"
        f"{skill_md}\n"
    )


def build_run_argv(
    *, image: str, skill_root: Path, mcp_config_host: Path,
    prompt: str, env_file: "Path | None",
) -> list[str]:
    argv = ["docker", "run", "--rm"]
    if env_file is not None:
        argv += ["--env-file", str(env_file)]
    if platform.system() == "Linux":
        argv += ["--add-host", "host.docker.internal:host-gateway"]
    argv += [
        "-v", f"{skill_root}:{skill_root}:ro",
        "-v", f"{mcp_config_host}:{_CONTAINER_MCP_CONFIG}:ro",
        "-w", str(skill_root),
        image,
        "claude", "-p", prompt,
        "--mcp-config", _CONTAINER_MCP_CONFIG,
        "--strict-mcp-config",
        "--permission-mode", "bypassPermissions",
    ]
    return argv
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd launcher && uv run pytest tests/test_run_llm.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/run_llm.py launcher/tests/test_run_llm.py
git commit -m "feat(launcher): build_run_prompt/build_run_argv for the run-time LLM"
```

---

## Task 3: `RunServer` — host MCP server (exec + HITL, no promote)

**Files:**
- Create: `launcher/zipsa/core/run_server.py`
- Test: `launcher/tests/test_run_server.py`

- [ ] **Step 1: Write the failing test**

```python
# launcher/tests/test_run_server.py
import threading, urllib.request, json
from unittest.mock import MagicMock
from zipsa.core.hitl_mcp import HitlIO
from zipsa.core.run_server import RunServer


def _io():
    import io
    return HitlIO(stdin=io.StringIO(""), stdout=io.StringIO(),
                  stdout_lock=threading.Lock(), is_interactive=False)


class TestRunServer:
    def test_start_assigns_port_token_then_stops(self):
        s = RunServer(_io(), MagicMock())
        s.start()
        try:
            assert s.port > 0 and len(s.token) > 0
            # server is listening
            import socket
            sock = socket.create_connection(("127.0.0.1", s.port), timeout=2)
            sock.close()
        finally:
            s.stop()

    def test_no_promote_tool_registered(self):
        # exec + ask/confirm/choose only — promote is create-only.
        s = RunServer(_io(), MagicMock())
        s.start()
        try:
            tools = s.tool_names()
            assert "exec" in tools
            assert {"ask", "confirm", "choose"} <= set(tools)
            assert "promote" not in tools
        finally:
            s.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd launcher && uv run pytest tests/test_run_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'zipsa.core.run_server'`.

- [ ] **Step 3: Write minimal implementation**

Model it on `CreateServer` (`launcher/zipsa/core/create_server.py:37-157`). Register `exec` bound to the `RunScriptHandler` (signature `exec(script, args="", prev=None)`), plus `ask`/`confirm`/`choose`; **do not** register `promote`. Reuse `_pick_free_port`/`_ALLOWED_HOSTS` (`hitl_runner.py`), `CallerContextMiddleware`/`CallerInfo` (`caller_context.py`), and `AskHandler`/`ConfirmHandler`/`ChooseHandler` (`hitl_mcp.py`). Add a `tool_names()` test hook returning the registered tool names.

```python
# launcher/zipsa/core/run_server.py  (skeleton — fill the FastMCP body
# exactly as CreateServer.start does, minus promote)
from __future__ import annotations
import secrets, socket, threading
from typing import Optional
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.transport_security import TransportSecuritySettings
from .caller_context import CallerContextMiddleware, CallerInfo
from .hitl_mcp import AskHandler, ChooseHandler, ConfirmHandler, HitlUnattended, HitlIO
from .hitl_runner import _pick_free_port, _ALLOWED_HOSTS


class RunServer:
    def __init__(self, hitl_io: HitlIO, exec_handler, caller: "CallerInfo | None" = None):
        self._io = hitl_io
        self._exec_handler = exec_handler
        self._caller = caller or CallerInfo("run", "run")
        self.port = 0
        self.token = ""
        self._tool_names: list[str] = []
        self._thread: Optional[threading.Thread] = None
        self._uvicorn_server: Optional[uvicorn.Server] = None

    def tool_names(self) -> list[str]:
        return list(self._tool_names)

    def start(self) -> None:
        self.port = _pick_free_port()
        self.token = secrets.token_urlsafe(32)
        mcp = FastMCP("zipsa-run", host="127.0.0.1", port=self.port,
                      stateless_http=False,
                      transport_security=TransportSecuritySettings(
                          enable_dns_rebinding_protection=True,
                          allowed_hosts=_ALLOWED_HOSTS))
        exec_handler = self._exec_handler
        ask_h, confirm_h, choose_h = AskHandler(self._io), ConfirmHandler(self._io), ChooseHandler(self._io)

        @mcp.tool(name="exec")
        def exec_script(script: str, args: str = "", prev: dict | None = None) -> dict:
            """Run ONE of this skill's scripts and return its result."""
            return exec_handler.run(script=script, args=args, prev=prev)

        @mcp.tool()
        def ask(prompt: str) -> str:
            try: return ask_h.run(prompt=prompt)
            except HitlUnattended as e: raise RuntimeError(f"HITL_UNATTENDED: {e}") from e

        @mcp.tool()
        def confirm(message: str, default: bool | None = None) -> bool:
            try: return confirm_h.run(message=message, default=default)
            except HitlUnattended as e: raise RuntimeError(f"HITL_UNATTENDED: {e}") from e

        @mcp.tool()
        def choose(prompt: str, options: list[str]) -> str:
            try: return choose_h.run(prompt=prompt, options=options)
            except HitlUnattended as e: raise RuntimeError(f"HITL_UNATTENDED: {e}") from e

        self._tool_names = ["exec", "ask", "confirm", "choose"]
        app = mcp.streamable_http_app()
        app.add_middleware(CallerContextMiddleware, token_map={self.token: self._caller})
        config = uvicorn.Config(app, host="0.0.0.0", port=self.port,
                                log_level="error", access_log=False)
        self._uvicorn_server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._uvicorn_server.run,
                                         daemon=True, name=f"run-mcp-{self.port}")
        self._thread.start()
        deadline, step, elapsed = 5.0, 0.05, 0.0
        while elapsed < deadline:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5); s.connect(("127.0.0.1", self.port)); s.close()
                return
            except OSError:
                threading.Event().wait(step); elapsed += step
        raise RuntimeError(f"RunServer failed to listen on port {self.port}")

    def stop(self) -> None:
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._uvicorn_server = self._thread = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd launcher && uv run pytest tests/test_run_server.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/core/run_server.py launcher/tests/test_run_server.py
git commit -m "feat(launcher): RunServer — host MCP (exec + HITL, no promote)"
```

---

## Task 4: `run_skill_llm` orchestration

**Files:**
- Modify: `launcher/zipsa/run_llm.py`
- Test: `launcher/tests/test_run_llm.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# append to launcher/tests/test_run_llm.py
from unittest.mock import patch, MagicMock


class TestRunSkillLlm:
    @patch("zipsa.run_llm.subprocess.run")
    @patch("zipsa.run_llm.RunServer")
    def test_starts_server_runs_container_stops_server(self, mock_server_cls, mock_run, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        root = tmp_path / "weather"; (root / "zipsa-dist").mkdir(parents=True)
        (root / "SKILL.md").write_text("# weather\n")
        srv = MagicMock(); srv.port = 51111; srv.token = "tok"
        mock_server_cls.return_value = srv
        mock_run.return_value.returncode = 0

        from zipsa.run_llm import run_skill_llm
        rc = run_skill_llm(root, "Sydney", image="img")

        assert rc == 0
        srv.start.assert_called_once()
        srv.stop.assert_called_once()                      # torn down even on success
        argv = mock_run.call_args.args[0]
        assert argv[:2] == ["docker", "run"]
        assert "claude" in argv

    @patch("zipsa.run_llm.subprocess.run", side_effect=RuntimeError("boom"))
    @patch("zipsa.run_llm.RunServer")
    def test_server_stopped_on_error(self, mock_server_cls, mock_run, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        root = tmp_path / "w"; (root / "zipsa-dist").mkdir(parents=True)
        (root / "SKILL.md").write_text("# w\n")
        srv = MagicMock(); srv.port = 51112; srv.token = "t"
        mock_server_cls.return_value = srv
        from zipsa.run_llm import run_skill_llm
        import pytest
        with pytest.raises(RuntimeError):
            run_skill_llm(root, "", image="img")
        srv.stop.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd launcher && uv run pytest tests/test_run_llm.py::TestRunSkillLlm -v`
Expected: FAIL with `ImportError: cannot import name 'run_skill_llm'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to launcher/zipsa/run_llm.py
import sys, threading
from . import paths as zipsa_paths
from .core.hitl_mcp import HitlIO
from .core.run_server import RunServer
from .core.run_script_handler import RunScriptHandler
from .create import _is_interactive


def run_skill_llm(
    skill_root: Path, user_input: str, *,
    image: str, env_file: "Path | None" = None,
) -> int:
    """Execute a skill as an LLM following SKILL.md, calling scripts via
    the host RunServer's exec tool. Returns the container claude's exit code."""
    if env_file is None:
        env_file = zipsa_paths.global_env_file()
    skill_root = skill_root.resolve()

    hitl_io = HitlIO(
        stdin=sys.stdin, stdout=sys.stdout,
        stdout_lock=threading.Lock(), is_interactive=_is_interactive(sys.stdin),
    )
    server = RunServer(hitl_io, RunScriptHandler(docker_image=image, skill_root=skill_root))
    server.start()
    try:
        mcp_config = build_mcp_config(server.port, server.token)
        cfg_dir = zipsa_paths.zipsa_home() / "run"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        mcp_config_host = Path(tempfile.mkstemp(prefix="run-", suffix=".mcp.json", dir=cfg_dir)[1])
        mcp_config_host.write_text(json.dumps(mcp_config))
        argv = build_run_argv(
            image=image, skill_root=skill_root, mcp_config_host=mcp_config_host,
            prompt=build_run_prompt(skill_root, user_input),
            env_file=env_file if env_file.exists() else None,
        )
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL)
        return proc.returncode
    finally:
        server.stop()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd launcher && uv run pytest tests/test_run_llm.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/run_llm.py launcher/tests/test_run_llm.py
git commit -m "feat(launcher): run_skill_llm — orchestrate the run-time LLM session"
```

---

## Task 5: CLI dispatch — exec-format skills use the new runtime

**Files:**
- Modify: `launcher/zipsa/cli.py` (the `run` command, ~lines 237-467)
- Test: `launcher/tests/test_cli.py` (extend `TestRunCommand`)

- [ ] **Step 1: Write the failing test**

```python
# add to launcher/tests/test_cli.py
class TestRunDispatch:
    @patch("zipsa.cli.run_skill_llm")
    def test_exec_format_skill_uses_llm_runtime(self, mock_run_llm, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        root = tmp_path / "weather"; (root / "zipsa-dist").mkdir(parents=True)
        (root / "SKILL.md").write_text("# weather\n")          # no manifest.yaml
        mock_run_llm.return_value = 0
        from zipsa.cli import app
        from typer.testing import CliRunner
        res = CliRunner().invoke(app, ["run", str(root), "Sydney"])
        assert res.exit_code == 0, res.output
        called_root, called_input = mock_run_llm.call_args.args[:2]
        assert Path(called_root).name == "weather"
        assert called_input == "Sydney"

    @patch("zipsa.cli.DockerExecutor")
    def test_legacy_manifest_skill_uses_docker_executor(self, mock_exec, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        root = tmp_path / "legacy"; root.mkdir()
        (root / "manifest.yaml").write_text("kind: Skill\n")     # legacy marker
        from zipsa.cli import app
        from typer.testing import CliRunner
        CliRunner().invoke(app, ["run", str(root)])
        assert mock_exec.called    # legacy path still taken
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd launcher && uv run pytest tests/test_cli.py::TestRunDispatch -v`
Expected: FAIL — `run_skill_llm` not imported/dispatched in `cli.py` (exec-format path falls through to the legacy executor and errors on the missing manifest).

- [ ] **Step 3: Write minimal implementation**

In `cli.py`, import `from .run_llm import run_skill_llm`. At the top of the `run` command body, resolve the skill dir (accept a path or `resolve_skill(name)`), then dispatch:

```python
def _is_exec_format(skill_dir: Path) -> bool:
    return (skill_dir / "SKILL.md").is_file() and (skill_dir / "zipsa-dist").is_dir() \
        and not (skill_dir / "manifest.yaml").exists()

# inside run(...), after resolving `skill_dir` from name/path:
if _is_exec_format(skill_dir):
    rc = run_skill_llm(skill_dir, user_input or "", image=image)
    raise typer.Exit(rc)
# else: existing DockerExecutor path unchanged
```

Resolve `skill_dir`: if `Path(name).is_dir()` use it; else `resolve_skill(name)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd launcher && uv run pytest tests/test_cli.py::TestRunDispatch -v` then the full `tests/test_cli.py`.
Expected: PASS, and existing `TestRunCommand`/`TestViewCommand` still pass.

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/cli.py launcher/tests/test_cli.py
git commit -m "feat(launcher): zipsa run dispatches exec-format skills to the LLM runtime"
```

---

## Task 6: E2E — run a real skill through the new runtime

**Files:**
- Create (test fixture, throwaway): a hand-authored skill with a real SKILL.md constitution + one script.

- [ ] **Step 1:** Build a tiny skill under a shared path (e.g. `~/zipsa-run-e2e/`): `zipsa-dist/1.echo.py` (reads stdin, prints `{"echoed": ctx.user_query}`), and `SKILL.md` instructing: "Call `mcp__zipsa__exec(script='1.echo', args=<the user request>)` and report the echoed value to the user."
- [ ] **Step 2:** Run it: `uvr --project=launcher zipsa run ~/zipsa-run-e2e "hello forge"` (docker default). Expected: the container LLM reads SKILL.md, calls `mcp__zipsa__exec`, the host runs `1.echo.py`, and the LLM reports the echoed value. Exit 0.
- [ ] **Step 3:** Inject a failure (script `sys.exit(1)` with a stderr message) and confirm the LLM surfaces a graceful, human-readable explanation (the unhappy-path value proposition).
- [ ] **Step 4:** Full suite: `cd launcher && uv run pytest`. Then clean up the throwaway skill.
- [ ] **Step 5: Commit** any fixture/notes kept (or none).

---

## Self-Review

**Spec coverage:** run-time = LLM + SKILL.md + `mcp__zipsa__exec` (Tasks 2–5 ✓); single-script generic tool (Task 1, decision 1 ✓); SKILL.md as instruction + optional INTENT (Task 2, decision 2 ✓); reuse exec runner / mcp pattern / container model (Tasks 1,3,4 ✓); HITL on demand (Task 3 ✓). **Deferred to later plans (noted in spec):** forge loop, import/extraction, retiring legacy `run`/`exec`, name-resolution polish, run-record integration for the LLM runtime, model-tier selection. Not gaps — out of this sub-project's scope.

**Placeholder scan:** none — every code step has concrete code; Task 6 is manual E2E by nature with explicit commands.

**Type consistency:** `RunScriptHandler.run(script, args, prev)` ↔ `RunServer` exec tool `exec(script, args, prev)` ↔ `run_llm` wiring — consistent. `build_run_argv`/`build_run_prompt`/`run_skill_llm` signatures match across tasks 2 and 4.

**Known follow-up (flag, not fix here):** `RunServer` duplicates ~30 lines of FastMCP/uvicorn boilerplate from `CreateServer`; a later refactor can extract a shared base. Left separate now to avoid restructuring create mid-redesign.
