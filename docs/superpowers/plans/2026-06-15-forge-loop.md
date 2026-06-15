# Forge Loop (`zipsa forge`) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve `zipsa create` into `zipsa forge` — an iterative authoring loop that nests the run-time as its test step (draft → `exec` single scripts / `run` the whole skill via the real LLM run-time → refine → `promote`), with `INTENT.md` as a first-class artifact.

**Architecture:** Reuse sub-project 1's run-time (`RunScriptHandler`, `run_skill_llm`). The forge session has ONE staging dir; a `ForgeServer` (sibling of `RunServer`/`CreateServer`) exposes **path-scoped** tools — `exec`(one script), `run`(full run-time test), `promote`(finalize) — plus HITL. The authoring agent writes `INTENT.md` + `SKILL.md` + `scripts/`, tests with exec/run, and promotes last. Spec: `docs/superpowers/specs/2026-06-15-skill-runtime-forge-redesign-design.md`.

**Tech Stack:** Python 3.12, uv, pytest, Typer, FastMCP/uvicorn, Docker.

---

## Pinned design decisions

1. **`exec` + `run` both** (user-chosen). `exec(script, args, prev)` → `RunScriptHandler` (one script, fast debug). `run(args, mounts)` → `run_skill_llm` (full run-time: a nested LLM follows SKILL.md). The old create `exec`=`run_phases` (fixed pipeline) is dropped — it doesn't match the Forge model.
2. **Path-scoped tools.** The `ForgeServer` is constructed with the session's `staging_path`; tools take NO `staging_path` arg (agent calls `exec(script=…)`, `run(args=…)`, `promote(name=…)`). Cleaner than create's pass-the-path style and matches `RunServer`.
3. **`create` → `forge` rename, `create` kept as a thin deprecated alias** (the relay workflow + memory reference `zipsa create`; don't hard-break).
4. **Run-time gains `--mount` support** (Task 1) so cred-using skills (wahroonga-class) are testable via `run`, and `zipsa run --mount` works generally. Deferred from sub-project 1.
5. **`INTENT.md` authored into staging** by the agent (first-class artifact); `promote` moves the whole dir so it travels with the skill.

## File structure

- Modify `launcher/zipsa/exec_runner.py` — `run_phase` already takes `extra_mounts`; no change. (Confirm.)
- Modify `launcher/zipsa/run_llm.py` — `build_run_argv` + `run_skill_llm` gain `extra_mounts`.
- Modify `launcher/zipsa/core/run_script_handler.py` — `RunScriptHandler` gains optional `extra_mounts` (forwarded to `run_phase`) + a `mounts` arg on `run()`.
- Create `launcher/zipsa/core/run_skill_handler.py` — `RunSkillHandler(image, skill_root).run(args, mounts)` → wraps `run_skill_llm` (the `run` tool's body).
- Create `launcher/zipsa/core/forge_server.py` — `ForgeServer`: path-scoped exec + run + promote + HITL.
- Modify `launcher/zipsa/create.py` → forge: add `build_forge_prompt`, `run_forge`; keep `run_create` as a deprecated wrapper.
- Modify `launcher/zipsa/cli.py` — `zipsa forge` command; `zipsa create` alias.
- Modify `launcher/zipsa/authoring/skill-builder.md` — new loop, `run` tool, INTENT.md.
- Tests: extend `test_run_llm.py`, `test_run_script_handler.py`; new `test_run_skill_handler.py`, `test_forge_server.py`, `test_forge.py`; keep `test_create*.py` green via the alias.

---

## Task 1: Run-time `--mount` support (RunScriptHandler, run_llm, exec_runner)

**Files:**
- Modify: `launcher/zipsa/run_llm.py`, `launcher/zipsa/core/run_script_handler.py`
- Test: `launcher/tests/test_run_llm.py`, `launcher/tests/test_run_script_handler.py`

`run_phase` already accepts `extra_mounts: list[tuple[Path, str]]`. Thread it through.

- [ ] **Step 1: Failing tests**

```python
# add to launcher/tests/test_run_script_handler.py
def test_forwards_mounts_to_run_phase(tmp_path, monkeypatch):
    from zipsa.core.run_script_handler import RunScriptHandler
    import zipsa.core.run_script_handler as mod
    root = tmp_path / "s"; (root / "zipsa-dist").mkdir(parents=True)
    (root / "zipsa-dist" / "1.do.py").write_text(
        "import json,sys; print(json.dumps({'ok': True}))\n")
    (root / "SKILL.md").write_text("# s\n")
    captured = {}
    def fake_run_phase(path, **kw):
        captured.update(kw)
        from zipsa.exec_runner import ExecResult
        return ExecResult(skill_name="s", mode="local", result={"ok": True},
                          exit_code=0, duration_ms=1, out_dir="/tmp",
                          stdout="", stderr="")
    monkeypatch.setattr(mod, "run_phase", fake_run_phase)
    h = RunScriptHandler(docker_image="img", skill_root=root)
    h.run(script="1.do", mounts=[("/host/creds.json", "/mnt/creds.json")])
    assert captured["extra_mounts"] == [("/host/creds.json", "/mnt/creds.json")]
```

```python
# add to launcher/tests/test_run_llm.py  (in TestBuildRunArgv)
def test_extra_mounts_added_ro(self, tmp_path):
    root = _skill(tmp_path)
    argv = build_run_argv(
        image="img", skill_root=root, mcp_config_host=tmp_path / "m.json",
        prompt="P", env_file=None,
        extra_mounts=[(Path("/host/c.json"), "/mnt/c.json")],
    )
    assert "/host/c.json:/mnt/c.json:ro" in argv
```

- [ ] **Step 2: Run, confirm fail** (`extra_mounts` not accepted).

- [ ] **Step 3: Implement.**

`run_script_handler.py` — add `extra_mounts` to `run()` (accept `mounts` list of `(host, container)` tuples), forward as `extra_mounts`:
```python
    def run(self, *, script: str, args: str = "", prev: "dict | None" = None,
            mounts: "list[tuple[str, str]] | None" = None) -> dict:
        path = self._resolve(script)
        if path is None:
            return self._fail("script_not_found", f"no such script: {script}")
        outcome = run_phase(
            path, skill_name=self._root.name, user_query=args,
            skill_root=self._root, docker_image=self._image, prev=prev or {},
            extra_mounts=[(Path(h), c) for h, c in (mounts or [])],
        )
        ...  # unchanged result dict
```
(add `from pathlib import Path` if not present.)

`run_llm.py` `build_run_argv` — add `extra_mounts: list[tuple[Path, str]] | None = None`, and after the skill mount:
```python
    for host, container in extra_mounts or []:
        argv += ["-v", f"{host}:{container}:ro"]
```
`run_skill_llm` — add `extra_mounts: list[tuple[Path, str]] | None = None` param and pass it to `build_run_argv(..., extra_mounts=extra_mounts)`.

- [ ] **Step 4: Run tests, confirm pass.** Then `uv run --extra dev pytest tests/test_run_llm.py tests/test_run_script_handler.py -q`.

- [ ] **Step 5: Commit** `feat(launcher): run-time --mount support (RunScriptHandler, run_llm)`

---

## Task 2: `RunSkillHandler` — the `run` tool body

**Files:**
- Create: `launcher/zipsa/core/run_skill_handler.py`
- Test: `launcher/tests/test_run_skill_handler.py`

- [ ] **Step 1: Failing test**

```python
from pathlib import Path
from unittest.mock import patch
from zipsa.core.run_skill_handler import RunSkillHandler


class TestRunSkillHandler:
    @patch("zipsa.core.run_skill_handler.run_skill_llm")
    def test_runs_skill_and_shapes_result(self, mock_run, tmp_path):
        mock_run.return_value = 0
        h = RunSkillHandler(image="img", skill_root=tmp_path)
        out = h.run(args="hi", mounts=[("/h/c.json", "/mnt/c.json")])
        assert out["status"] == "ok"
        assert out["exit_code"] == 0
        # forwarded to run_skill_llm
        _, kwargs = mock_run.call_args
        assert kwargs["image"] == "img"
        assert kwargs["extra_mounts"] == [(Path("/h/c.json"), "/mnt/c.json")]

    @patch("zipsa.core.run_skill_handler.run_skill_llm")
    def test_nonzero_exit_is_failed(self, mock_run, tmp_path):
        mock_run.return_value = 1
        h = RunSkillHandler(image="img", skill_root=tmp_path)
        assert h.run()["status"] == "failed"
```

- [ ] **Step 2: Run, confirm fail** (module missing).

- [ ] **Step 3: Implement** `launcher/zipsa/core/run_skill_handler.py`:
```python
"""The forge `run` tool: test the draft via the real run-time (an LLM
following SKILL.md). Wraps run_skill_llm, scoped to the staging skill."""
from __future__ import annotations

from pathlib import Path

from ..run_llm import run_skill_llm


class RunSkillHandler:
    def __init__(self, *, image: str, skill_root: Path) -> None:
        self._image = image
        self._root = Path(skill_root)

    def run(self, *, args: str = "",
            mounts: "list[tuple[str, str]] | None" = None) -> dict:
        rc = run_skill_llm(
            self._root, args, image=self._image,
            extra_mounts=[(Path(h), c) for h, c in (mounts or [])],
        )
        return {"status": "ok" if rc == 0 else "failed", "exit_code": rc}
```

- [ ] **Step 4: Run tests, confirm pass.**
- [ ] **Step 5: Commit** `feat(launcher): RunSkillHandler — forge run tool (full run-time test)`

---

## Task 3: `ForgeServer` — path-scoped exec + run + promote + HITL

**Files:**
- Create: `launcher/zipsa/core/forge_server.py`
- Test: `launcher/tests/test_forge_server.py`

Mirror `RunServer` (`launcher/zipsa/core/run_server.py`) for the FastMCP/uvicorn/threading boilerplate (the listen-wait loop already uses the `with socket(...)` + `time.sleep` form — copy it). Construct with the three injected handlers; register path-scoped tools.

- [ ] **Step 1: Failing test**

```python
import io, threading, socket
from unittest.mock import MagicMock
from zipsa.core.hitl_mcp import HitlIO
from zipsa.core.forge_server import ForgeServer


def _io():
    return HitlIO(stdin=io.StringIO(""), stdout=io.StringIO(),
                  stdout_lock=threading.Lock(), is_interactive=False)


class TestForgeServer:
    def test_start_stop_and_tools(self):
        s = ForgeServer(_io(), exec_handler=MagicMock(),
                        run_handler=MagicMock(), promote_handler=MagicMock())
        s.start()
        try:
            assert s.port > 0 and s.token
            socket.create_connection(("127.0.0.1", s.port), timeout=2).close()
            tools = set(s.tool_names())
            assert {"exec", "run", "promote", "ask", "confirm", "choose"} == tools
        finally:
            s.stop()

    def test_promote_tool_passes_only_name(self):
        promote = MagicMock(); promote.run.return_value = {"status": "ok"}
        s = ForgeServer(_io(), exec_handler=MagicMock(),
                        run_handler=MagicMock(), promote_handler=promote,
                        staging_path="/x/staging/draft-1")
        # the promote tool injects the staging path the server was built with
        s._promote_impl(name="weather")  # test hook calling the tool body
        promote.run.assert_called_once_with(
            staging_path="/x/staging/draft-1", name="weather")
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Implement** `forge_server.py`. Constructor:
`ForgeServer(hitl_io, *, exec_handler, run_handler, promote_handler, staging_path="", caller=None)`. Copy `RunServer.start/stop` boilerplate. Register tools (expose `_promote_impl`/`_exec_impl`/`_run_impl` as methods the tools call, so tests can hit them directly):
```python
        @mcp.tool(name="exec")
        def exec_script(script: str, args: str = "",
                        prev: dict | None = None,
                        mounts: list[tuple[str, str]] | None = None) -> dict:
            """Run ONE of the draft's scripts (fast debug)."""
            return exec_handler.run(script=script, args=args, prev=prev,
                                    mounts=mounts)

        @mcp.tool(name="run")
        def run_skill(args: str = "",
                      mounts: list[tuple[str, str]] | None = None) -> dict:
            """Test the WHOLE draft through the real run-time (an LLM
            following SKILL.md, calling scripts) — the user's real
            experience. Use after exec-debugging the scripts."""
            return run_handler.run(args=args, mounts=mounts)

        @mcp.tool(name="promote")
        def promote(name: str) -> dict:
            """Finalize: validate kebab-case name, move the draft into
            skills/<name>/. Decide the name LAST, once the user is happy."""
            return promote_handler.run(staging_path=self._staging, name=name)
```
plus ask/confirm/choose copied from RunServer. Set `self._tool_names = ["exec","run","promote","ask","confirm","choose"]`. Add `self._staging = staging_path`. Add `_promote_impl = lambda name: promote_handler.run(...)` style test hooks OR make the test call the registered functions; simplest: store closures as methods. (Pick whichever keeps the test above green.)

- [ ] **Step 4: Run tests, confirm pass.** Then `uv run --extra dev pytest tests/test_forge_server.py tests/test_run_server.py -q`.
- [ ] **Step 5: Commit** `feat(launcher): ForgeServer — path-scoped exec+run+promote+HITL`

---

## Task 4: forge orchestration + prompt + INTENT.md

**Files:**
- Modify: `launcher/zipsa/create.py` (add `build_forge_prompt`, `run_forge`; keep `run_create` as alias)
- Test: `launcher/tests/test_forge.py`

- [ ] **Step 1: Failing tests**

```python
from pathlib import Path
from unittest.mock import patch, MagicMock
from zipsa.create import build_forge_prompt, run_forge


class TestBuildForgePrompt:
    def test_mentions_intent_exec_run_promote_and_loop(self, tmp_path):
        p = build_forge_prompt("a weather alert", tmp_path / "draft-1")
        for needle in ("INTENT.md", "mcp__zipsa__exec", "mcp__zipsa__run",
                       "mcp__zipsa__promote", "a weather alert"):
            assert needle in p


class TestRunForge:
    @patch("zipsa.create.subprocess.run")
    @patch("zipsa.create.ForgeServer")
    def test_starts_server_runs_container_stops(self, mock_srv, mock_run,
                                                tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        srv = MagicMock(); srv.port = 5000; srv.token = "t"
        mock_srv.return_value = srv
        mock_run.return_value.returncode = 0
        rc = run_forge("intent", skills_dir=tmp_path / "skills", image="img")
        assert rc == 0
        srv.start.assert_called_once(); srv.stop.assert_called_once()
        argv = mock_run.call_args.args[0]
        assert "claude" in argv
```

- [ ] **Step 2: Run, confirm fail.**

- [ ] **Step 3: Implement** in `create.py`:
- `build_forge_prompt(intent, staging_path)` — like `build_create_prompt` but: instruct to write `INTENT.md` first (capture the user's requirements as the why/acceptance), then SKILL.md + scripts; test scripts with `mcp__zipsa__exec(script=…)`, test the whole skill with `mcp__zipsa__run(args=…)`; iterate until the user AND you are satisfied; `mcp__zipsa__promote(name=…)` last. Inline `_bundled("skill-builder.md")` + `_bundled("AUTHORING.md")`.
- `run_forge(intent, *, skills_dir, image, env_file=None)` — like `run_create` but builds a `ForgeServer` with `RunScriptHandler(docker_image=image, skill_root=staging_path)` (exec), `RunSkillHandler(image=image, skill_root=staging_path)` (run), `PromoteSkillHandler(dest_root=skills_dir)` (promote), `staging_path=staging_path`; uses `build_forge_prompt`. The docker argv reuses `build_docker_argv` unchanged (staging mounted rw + mcp-config).
- `run_create(...)` — keep as a thin deprecated wrapper: `return run_forge(...)`.

- [ ] **Step 4: Run tests, confirm pass.** Then `uv run --extra dev pytest tests/test_forge.py tests/test_create.py -q` (create tests still green via wrapper).
- [ ] **Step 5: Commit** `feat(launcher): run_forge orchestration + INTENT.md-aware prompt`

---

## Task 5: CLI `zipsa forge` (+ `create` alias)

**Files:**
- Modify: `launcher/zipsa/cli.py`
- Test: `launcher/tests/test_cli.py`

- [ ] **Step 1: Failing test**

```python
class TestForgeCommand:
    @patch("zipsa.cli.run_forge")
    def test_forge_invokes_run_forge(self, mock_forge, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_forge.return_value = 0
        res = runner.invoke(app, ["forge", "a weather alert"])
        assert res.exit_code == 0, res.output
        assert mock_forge.call_args.args[0] == "a weather alert"

    @patch("zipsa.cli.run_forge")
    def test_create_alias_still_works(self, mock_forge, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_forge.return_value = 0
        res = runner.invoke(app, ["create", "x"])
        assert res.exit_code == 0, res.output
        assert mock_forge.called
```

- [ ] **Step 2: Run, confirm fail** (`forge` command + `run_forge` import absent).

- [ ] **Step 3: Implement.** Import `from .create import run_forge`. Add `@app.command(name="forge")` `forge_skill(...)` (same args/body as `create_skill` but calling `run_forge`; prompt text "What would you like to forge?"). Repoint the existing `create` command to call `run_forge` (keep it, mark "(alias of forge)" in help). Both catch `FileNotFoundError` (docker missing) the same way.

- [ ] **Step 4: Run tests, confirm pass.** Then full `uv run --extra dev pytest tests/test_cli.py -q`.
- [ ] **Step 5: Commit** `feat(launcher): zipsa forge command (create kept as alias)`

---

## Task 6: Authoring workflow doc — forge loop, run tool, INTENT.md

**Files:**
- Modify: `launcher/zipsa/authoring/skill-builder.md`

- [ ] **Step 1:** Update `skill-builder.md` so the inlined workflow matches the forge loop:
  - Tool list: add `mcp__zipsa__run` (full run-time test) alongside `exec` (script debug); keep ask/confirm/choose + promote.
  - Step "Clarify the intent" → also: **write `INTENT.md`** capturing the agreed requirements (the why / acceptance criteria) before drafting.
  - Test step: `exec` to debug individual scripts; **`run` to test the whole skill as the user will experience it** (LLM follows SKILL.md). Iterate until the user AND you are satisfied.
  - Keep "name is last → promote".
- [ ] **Step 2:** Verify the create/forge prompt-assembly tests still pass (they assert the bundled doc is inlined): `uv run --extra dev pytest tests/test_create.py tests/test_forge.py -q`.
- [ ] **Step 3: Commit** `docs(authoring): forge loop — run tool, INTENT.md, iterate-to-satisfied`

---

## Task 7: E2E — forge a real skill (manual, observed, with the user)

- [ ] **Step 1:** `uvr --project=launcher zipsa forge "…"` (driven via the create-HITL relay — see memory `reference_create_hitl_relay`). Relay the agent's HITL to the user.
- [ ] **Step 2:** Confirm the agent: writes `INTENT.md`; drafts SKILL.md + scripts; `exec`-debugs a script; `run`-tests the whole skill (nested run-time LLM); iterates; `promote`s last.
- [ ] **Step 3:** Run the promoted skill via `zipsa run skills/<name> "…"` and confirm it behaves.
- [ ] **Step 4:** Full suite `uv run --extra dev pytest`. Clean up any throwaway skill.

---

## Self-Review

**Spec coverage:** forge loop = create→forge + nested run-time test (Tasks 3–5 ✓); exec+run both (Tasks 1–3 ✓, decision 1); path-scoped tools (Task 3, decision 2); INTENT.md first-class (Tasks 4,6 ✓); run-time --mount so cred skills are testable (Task 1 ✓); create alias (Tasks 4,5, decision 3). Deferred (separate sub-projects): INTENT import/extraction for skills lacking it; retiring `zipsa exec`; legacy-skill migration.

**Placeholder scan:** none — code steps carry real code; Task 7 is manual E2E by nature.

**Type consistency:** `RunScriptHandler.run(script, args, prev, mounts)` ↔ ForgeServer `exec` tool; `RunSkillHandler.run(args, mounts)` ↔ ForgeServer `run` tool; `run_skill_llm(..., extra_mounts=)` ↔ `build_run_argv(..., extra_mounts=)` ↔ RunScriptHandler `extra_mounts`. `run_forge`/`build_forge_prompt`/`ForgeServer` signatures consistent across Tasks 3–5.

**Known follow-ups (flag, not fix here):** ForgeServer duplicates the FastMCP boilerplate shared by RunServer/CreateServer (shared-base refactor later — now 3 copies, the strongest signal yet to extract one). Nested HITL during `run` (the run-time's HITL reads the same stdin as forge's) is sequential-safe because the forge agent is blocked awaiting the `run` result; note as a gotcha. `CreateServer` becomes dead once `run_create` is a wrapper that builds `ForgeServer` — remove `create_server.py` + its tests in a follow-up cleanup once forge is proven.
