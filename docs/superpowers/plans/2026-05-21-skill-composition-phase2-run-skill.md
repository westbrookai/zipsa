# Skill Composition Phase 2 — `mcp__zipsa__run_skill` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parent orchestrator skills can invoke child skills via `mcp__zipsa__run_skill(name, args) -> {status, exit_code, skill, version, run_id, summary}`. The child's HITL prompts route through the parent's terminal (parent's HitlServer is reused), skill-scoped tools (`ask_once`, `recall`, `remember`, `forget`, `list_memory`) correctly resolve to the calling skill's memory file (not parent's), and `get_artifact` lets the parent read the child's outputs.

**Architecture:** Subprocess model — `RunSkillHandler` calls `subprocess.run(["uv", "run", "zipsa", "run", <child>, <args>])`. Child launcher detects `ZIPSA_PARENT_MCP_URL` env var, skips spawning its own HitlServer, and points its container's MCP config at the parent's URL using a child-specific Bearer token. Parent's HitlServer maintains a `_caller_map: dict[token, (skill, version)]` set by `RunSkillHandler` when spawning the child. A Starlette middleware reads the request token, looks up the caller, and stores it in a contextvar that tool handlers read to scope their behavior. Cycle/depth detection via `ZIPSA_CALL_TRACE` + `ZIPSA_CALL_DEPTH` env vars, evaluated at child launcher startup.

**Tech Stack:** Python 3.12+, FastMCP, Starlette ASGI middleware, contextvars, subprocess, Pydantic.

---

## File Structure

- **New files:**
  - `launcher/zipsa/core/run_skill_handler.py` — `RunSkillHandler` class (subprocess wrapper + summary parser)
  - `launcher/zipsa/core/caller_context.py` — contextvar + Starlette middleware for caller routing
  - `launcher/tests/test_run_skill_handler.py` — unit tests
  - `launcher/tests/test_caller_context.py` — middleware tests
  - `launcher/tests/fixtures/skills/test-parent/` — fixture parent skill declaring `children: [hello-world]`
  - `docs/superpowers/plans/2026-05-21-skill-composition-phase2-run-skill.md` — this plan
- **Modified:**
  - `launcher/zipsa/core/models.py` — `SkillSpec.children: list[str] = []`
  - `launcher/zipsa/core/hitl_runner.py` — register `run_skill` tool, attach caller-context middleware, expose token map
  - `launcher/zipsa/core/hitl_mcp.py` — handlers (ask_once / recall / remember / forget / list_memory) read caller from contextvar, look up per-skill MemoryStore lazily
  - `launcher/zipsa/core/executor.py` — child-launcher path: detect `ZIPSA_PARENT_MCP_URL`, skip own HitlServer, override `.claude.json` MCP config. Auto-allow gains `mcp__zipsa__run_skill` when `spec.children` non-empty
  - `launcher/zipsa/cli.py` — cycle/depth env-var check at startup
  - `launcher/zipsa/system-prompts/runtime-contract.md` — document `run_skill`

---

## Task 1: Manifest `spec.children` field

**Files:**
- Modify: `launcher/zipsa/core/models.py`
- Test: `launcher/tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
class TestChildrenField:
    def test_children_default_empty(self):
        from zipsa.core.models import SkillSpec
        spec = SkillSpec.model_validate({
            "purpose": "x", "instructions": "./SKILL.md",
        })
        assert spec.children == []

    def test_children_accepts_list(self):
        from zipsa.core.models import SkillSpec
        spec = SkillSpec.model_validate({
            "purpose": "x", "instructions": "./SKILL.md",
            "children": ["agenthud-report", "x-post"],
        })
        assert spec.children == ["agenthud-report", "x-post"]

    def test_children_rejects_non_string(self):
        from zipsa.core.models import SkillSpec
        with pytest.raises(ValidationError):
            SkillSpec.model_validate({
                "purpose": "x", "instructions": "./SKILL.md",
                "children": [123],
            })
```

- [ ] **Step 2: Run tests, confirm fail**

```bash
cd launcher && uv run pytest tests/test_models.py::TestChildrenField -v
```

Expected: 3 fails (field doesn't exist).

- [ ] **Step 3: Add field**

In `launcher/zipsa/core/models.py`, in `SkillSpec`:

```python
    children: list[str] = Field(
        default_factory=list,
        description="Names of child skills this skill may invoke via mcp__zipsa__run_skill.",
    )
```

- [ ] **Step 4: Run tests, confirm pass + full suite**

```bash
cd launcher && uv run pytest tests/test_models.py::TestChildrenField -v
cd launcher && uv run pytest 2>&1 | tail -3
```

Expected: 3 pass + 642 total.

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/core/models.py launcher/tests/test_models.py
git commit -m "feat(models): add spec.children manifest field

List of child skill names this skill may invoke via run_skill.
Default empty (atomic skills). Phase 2 will use this list to gate
the auto-allow of mcp__zipsa__run_skill and to validate that a
called child was declared up-front."
```

---

## Task 2: Caller context — contextvar + middleware

**Files:**
- Create: `launcher/zipsa/core/caller_context.py`
- Test: `launcher/tests/test_caller_context.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for caller-context routing: tokens map to (skill, version),
middleware reads them from the request and stashes in a contextvar
that tool handlers read."""

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from zipsa.core.caller_context import (
    CallerInfo,
    CallerContextMiddleware,
    current_caller,
)


class TestCallerContext:
    def test_middleware_resolves_token_to_caller(self):
        token_map = {"tok-alice": CallerInfo(skill="alice", version="1.0.0")}

        async def endpoint(request):
            caller = current_caller.get()
            return JSONResponse({"skill": caller.skill, "version": caller.version})

        app = Starlette(routes=[Route("/", endpoint)])
        app.add_middleware(CallerContextMiddleware, token_map=token_map)
        client = TestClient(app)
        r = client.get("/", headers={"Authorization": "Bearer tok-alice"})
        assert r.json() == {"skill": "alice", "version": "1.0.0"}

    def test_unknown_token_returns_401(self):
        async def endpoint(request):
            return JSONResponse({"should_not_reach": True})

        app = Starlette(routes=[Route("/", endpoint)])
        app.add_middleware(CallerContextMiddleware, token_map={})
        client = TestClient(app)
        r = client.get("/", headers={"Authorization": "Bearer mystery"})
        assert r.status_code == 401
        assert r.json() == {"error": "unauthorized"}

    def test_missing_auth_header_returns_401(self):
        async def endpoint(request):
            return JSONResponse({"should_not_reach": True})

        app = Starlette(routes=[Route("/", endpoint)])
        app.add_middleware(CallerContextMiddleware, token_map={})
        client = TestClient(app)
        r = client.get("/")
        assert r.status_code == 401

    def test_contextvar_isolates_concurrent_requests(self):
        """Each request must see its own caller — no leakage between
        concurrent calls. Critical when parent + child both make MCP
        calls (theoretically concurrent through ASGI)."""
        token_map = {
            "tok-a": CallerInfo(skill="alice", version="1.0.0"),
            "tok-b": CallerInfo(skill="bob", version="2.0.0"),
        }

        async def endpoint(request):
            caller = current_caller.get()
            return JSONResponse({"skill": caller.skill})

        app = Starlette(routes=[Route("/", endpoint)])
        app.add_middleware(CallerContextMiddleware, token_map=token_map)
        client = TestClient(app)
        r_a = client.get("/", headers={"Authorization": "Bearer tok-a"})
        r_b = client.get("/", headers={"Authorization": "Bearer tok-b"})
        assert r_a.json()["skill"] == "alice"
        assert r_b.json()["skill"] == "bob"
```

- [ ] **Step 2: Run, fail**

```bash
cd launcher && uv run pytest tests/test_caller_context.py -v
```

Expected: import errors (module not found).

- [ ] **Step 3: Implement**

Create `launcher/zipsa/core/caller_context.py`:

```python
"""Caller-context routing for the HitlServer.

A child skill invoked via run_skill talks to the parent's HitlServer
using a child-specific Bearer token. The server's token map
({token: CallerInfo}) is set by RunSkillHandler when it spawns the
child. This middleware reads the token from each incoming request,
looks up the caller, and stashes it in a contextvar so tool handlers
can route skill-scoped operations (memory, ask_once) to the right
skill's files without the parent and child stepping on each other.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware


@dataclass(frozen=True)
class CallerInfo:
    skill: str
    version: str


current_caller: ContextVar[Optional[CallerInfo]] = ContextVar(
    "current_caller", default=None,
)


class CallerContextMiddleware(BaseHTTPMiddleware):
    """Combined auth + caller-resolution middleware.

    Extracts the Bearer token from each request, resolves it to a
    CallerInfo via the provided token map, and stores it in the
    `current_caller` contextvar for the duration of the request.

    Unknown or missing tokens → 401. There's no "anonymous" case — every
    request must come from a registered caller. This replaces the
    separate _BearerAuthMiddleware: one middleware handles both auth
    and routing instead of two.
    """

    def __init__(self, app, token_map: dict[str, CallerInfo]) -> None:
        super().__init__(app)
        self._token_map = token_map

    async def dispatch(self, request, call_next):
        from starlette.responses import JSONResponse
        auth = request.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
        caller = self._token_map.get(token)
        if caller is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        reset = current_caller.set(caller)
        try:
            return await call_next(request)
        finally:
            current_caller.reset(reset)
```

**Adjust T2 tests accordingly**: the "unknown token" and "missing auth" tests should expect 401, not a None caller. The "known token resolves" test stays the same.

- [ ] **Step 4: Pass + full suite**

```bash
cd launcher && uv run pytest tests/test_caller_context.py -v
cd launcher && uv run pytest 2>&1 | tail -3
```

Expected: 4 pass, full suite green.

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/core/caller_context.py launcher/tests/test_caller_context.py
git commit -m "feat(mcp): caller-context middleware for token-based scope routing

Bearer token-per-caller + contextvar. When the parent's HitlServer
hosts requests from multiple skills (parent agent + child agent via
run_skill), each request's token resolves to a CallerInfo and is
stashed in current_caller so tool handlers can route skill-scoped
operations (ask_once, recall, remember) to the correct skill's
memory file."
```

---

## Task 3: HitlServer wires caller-context + skill-scoped tool routing

**Files:**
- Modify: `launcher/zipsa/core/hitl_runner.py`, `launcher/zipsa/core/hitl_mcp.py`
- Test: `launcher/tests/test_hitl_runner.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_hitl_runner.py`:

```python
class TestPerCallerMemoryRouting:
    """When two different callers (parent skill, child skill) invoke
    memory tools on the same HitlServer, each must read/write its own
    skill's memory file — never the other's."""

    def test_ask_once_routes_per_caller(self, tmp_path, monkeypatch):
        """Caller A's ask_once write must land in A's memory file;
        Caller B reads back the cached value from B's file (which is
        empty), so B prompts the user again."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        # Start server with two registered callers
        from zipsa.core.hitl_runner import HitlServer
        from zipsa.core.hitl_mcp import HitlIO
        from zipsa.core.caller_context import CallerInfo
        import io, threading

        in_a = io.StringIO("Alice\n")
        out_a = io.StringIO()
        io_a = HitlIO(stdin=in_a, stdout=out_a,
                      stdout_lock=threading.Lock(), is_interactive=True)
        server = HitlServer(io_a)
        server.register_caller("tok-alice", CallerInfo("skill-a", "1.0.0"))
        server.register_caller("tok-bob", CallerInfo("skill-b", "1.0.0"))
        server.start()
        try:
            # Alice asks_once → answer cached to skill-a's memory
            url, headers_a = _mcp_session(server, token="tok-alice")
            _call_tool(url, headers_a, "ask_once",
                       {"key": "name", "prompt": "what's your name?"})
            # Bob reads recall → should be None (his memory is empty)
            url, headers_b = _mcp_session(server, token="tok-bob")
            result = _call_tool(url, headers_b, "recall", {"key": "name"})
            assert result == {"value": None}
        finally:
            server.stop()
```

Where `_mcp_session` and `_call_tool` are helpers (extend existing or create). The point: same server, two tokens → two memory scopes.

- [ ] **Step 2: Fail**

Expected: NameError on `server.register_caller` or AttributeError.

- [ ] **Step 3: Implement**

In `launcher/zipsa/core/hitl_runner.py`:

a) Add to `HitlServer.__init__`:

```python
        self._token_map: dict[str, "CallerInfo"] = {}
        # Lazy per-skill memory stores, keyed by skill name.
        self._skill_stores_by_caller: dict[str, "MemoryStore"] = {}
```

b) New `register_caller` method:

```python
    def register_caller(self, token: str, caller: "CallerInfo") -> None:
        """Authorize `token` as belonging to a specific skill+version.

        Called by RunSkillHandler when spawning a child, and by the
        launcher itself when registering its own primary skill at start.
        """
        self._token_map[token] = caller
```

c) Pre-register the launcher's own primary skill so existing top-level runs continue working:

```python
    def __init__(
        self,
        io_: HitlIO,
        skill_store: "MemoryStore | None" = None,
        global_store: "MemoryStore | None" = None,
        primary_caller: "CallerInfo | None" = None,
    ) -> None:
        ...
        if primary_caller is not None:
            # The launcher's own token (self.token) maps to primary_caller
            # once start() runs. start() does the actual registration since
            # the token is generated there.
            self._primary_caller = primary_caller
        else:
            self._primary_caller = None
```

In `start()`, after the token is generated:

```python
        if self._primary_caller is not None:
            self._token_map[self.token] = self._primary_caller
```

d) Replace the existing `_BearerAuthMiddleware` with `CallerContextMiddleware` (it both auths and resolves caller). Update the middleware to also reject unknown tokens (preserving auth):

```python
class CallerContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token_map: dict[str, CallerInfo]) -> None:
        super().__init__(app)
        self._token_map = token_map

    async def dispatch(self, request, call_next):
        auth = request.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
        caller = self._token_map.get(token)
        if caller is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        reset = current_caller.set(caller)
        try:
            return await call_next(request)
        finally:
            current_caller.reset(reset)
```

(This consolidates auth + caller resolution. Update `caller_context.py` to match — the test from T2 that asserts unknown token → caller is None will need updating to expect 401 instead. Adjust T2 tests to test the consolidated middleware: unknown token → 401, missing auth → 401, known token → caller resolved.)

e) Update tool handlers in `hitl_runner.py` to look up per-caller memory store from contextvar. Skill memory handlers (`recall`, `remember`, `forget`, `list_memory`, `ask_once`) currently use `self._skill_store` from server init. Change to:

```python
        from .caller_context import current_caller

        def _store_for_caller(scope: str) -> "MemoryStore":
            caller = current_caller.get()
            if caller is None:
                raise RuntimeError("caller_unknown")  # shouldn't happen — middleware rejects
            if scope == "global":
                return self._global_store
            # Skill scope — lazy per-caller store
            key = caller.skill
            if key not in self._skill_stores_by_caller:
                self._skill_stores_by_caller[key] = MemoryStore(
                    paths.resolve_skill_memory_path(caller.skill)
                )
            return self._skill_stores_by_caller[key]
```

Rewrite handlers to use `_store_for_caller(scope)` instead of `self._skill_store` / `self._global_store` directly.

Note: this means the `skill_store` parameter to `HitlServer.__init__` becomes mostly vestigial — kept for backward-compat with tests that need to inject. Used only as the initial store for the `primary_caller`'s skill name (pre-populate `_skill_stores_by_caller[primary_caller.skill] = skill_store` so existing tests pass).

- [ ] **Step 4: Adapt existing tests**

Existing tests in `test_hitl_runner.py` that construct `HitlServer(io_, skill_store=..., global_store=...)` will fail because:
- Middleware now requires the token to be registered before requests
- Skill-memory handlers now read from contextvar via the middleware

Affected test classes (grep `class Test` in test_hitl_runner.py):
- `TestPortAllocation` — likely unaffected, just inspects server.port
- `TestToolsCallable` — calls ask/confirm/choose — needs `primary_caller=CallerInfo("test","0")` injected
- `TestMemoryToolsWired` — calls recall/remember — needs primary_caller and the memory store keyed correctly
- `TestAskOnceWired` — same
- `TestGetArtifactMCP` — doesn't use skill memory, just artifacts; should be fine

Update pattern (one-line change per test):
```python
# Before
server = HitlServer(io_, skill_store=ms, global_store=gms)
# After
server = HitlServer(io_, skill_store=ms, global_store=gms,
                    primary_caller=CallerInfo("test", "0"))
```

Run after each class adaptation to keep deltas small.

- [ ] **Step 5: Pass + full suite**

```bash
cd launcher && uv run pytest tests/test_hitl_runner.py -v 2>&1 | tail -20
cd launcher && uv run pytest 2>&1 | tail -3
```

Expected: full green.

- [ ] **Step 6: Commit**

```bash
git add launcher/zipsa/core/hitl_runner.py launcher/zipsa/core/caller_context.py launcher/tests/test_hitl_runner.py launcher/tests/test_caller_context.py
git commit -m "feat(mcp): per-caller skill-memory routing via caller-context

HitlServer now multiplexes by Bearer token: each token maps to a
CallerInfo(skill, version), and skill-memory tools (ask_once, recall,
remember, forget, list_memory) resolve the memory file from the
contextvar set by CallerContextMiddleware. Same server can host
parent and child skills without cross-contamination.

Top-level runs unchanged: launcher calls register_caller with its
own token+(skill, version) at start; existing single-skill flow
hits the same code path with token_map size 1."
```

---

## Task 4: Launcher cycle/depth detection from env

**Files:**
- Modify: `launcher/zipsa/cli.py`
- Test: `launcher/tests/test_cli.py`

- [ ] **Step 1: Write failing tests**

```python
class TestCallTraceCycleDetection:
    def test_cycle_rejected(self, monkeypatch, capsys):
        from zipsa.cli import _check_call_trace
        monkeypatch.setenv("ZIPSA_CALL_TRACE", "morning-ritual,daily-bip-tweet")
        with pytest.raises(SystemExit) as exc:
            _check_call_trace(skill_name="daily-bip-tweet")
        out = capsys.readouterr().err
        assert "skill_cycle_detected" in out

    def test_depth_cap_rejected(self, monkeypatch, capsys):
        from zipsa.cli import _check_call_trace
        monkeypatch.setenv("ZIPSA_CALL_DEPTH", "5")
        with pytest.raises(SystemExit) as exc:
            _check_call_trace(skill_name="any")
        out = capsys.readouterr().err
        assert "skill_depth_exceeded" in out

    def test_no_env_passes(self, monkeypatch):
        from zipsa.cli import _check_call_trace
        monkeypatch.delenv("ZIPSA_CALL_TRACE", raising=False)
        monkeypatch.delenv("ZIPSA_CALL_DEPTH", raising=False)
        _check_call_trace(skill_name="weather")  # no raise
```

- [ ] **Step 2: Fail**

```bash
cd launcher && uv run pytest tests/test_cli.py::TestCallTraceCycleDetection -v
```

- [ ] **Step 3: Implement**

In `launcher/zipsa/cli.py`, add:

```python
_MAX_CALL_DEPTH = 5


def _check_call_trace(skill_name: str) -> None:
    """Reject runs that would cycle or exceed depth cap.

    Called early in the `run` command. Reads ZIPSA_CALL_TRACE and
    ZIPSA_CALL_DEPTH set by a parent's RunSkillHandler.
    """
    trace = [s for s in os.environ.get("ZIPSA_CALL_TRACE", "").split(",") if s]
    if skill_name in trace:
        click.echo(
            f"Error: skill_cycle_detected — '{skill_name}' is already in "
            f"the call chain ({' → '.join(trace)} → {skill_name})",
            err=True,
        )
        raise SystemExit(2)
    depth = int(os.environ.get("ZIPSA_CALL_DEPTH", "0"))
    if depth >= _MAX_CALL_DEPTH:
        click.echo(
            f"Error: skill_depth_exceeded — call depth {depth} ≥ cap "
            f"{_MAX_CALL_DEPTH} (chain: {' → '.join(trace)})",
            err=True,
        )
        raise SystemExit(2)
```

Call from the `run` command before any other work.

- [ ] **Step 4: Pass + full suite**

```bash
cd launcher && uv run pytest tests/test_cli.py::TestCallTraceCycleDetection -v
cd launcher && uv run pytest 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/cli.py launcher/tests/test_cli.py
git commit -m "feat(cli): cycle/depth detection via ZIPSA_CALL_TRACE/DEPTH env

A parent skill invoking a child via run_skill passes its own call
trace as ZIPSA_CALL_TRACE=parent,grandparent. Child launcher checks
on startup: if its name is already in the trace → exit 2 with
skill_cycle_detected. Hard depth cap at 5 to bound resource use
even if the trace is unique."
```

---

## Task 5: Child launcher uses parent's MCP server

**Files:**
- Modify: `launcher/zipsa/core/executor.py`
- Test: `launcher/tests/test_executor.py`

- [ ] **Step 1: Write failing test**

```python
class TestParentMCPDelegation:
    def test_parent_mcp_env_skips_own_hitlserver(self, tmp_path, monkeypatch):
        """When ZIPSA_PARENT_MCP_URL is set, DockerExecutor must NOT
        start its own HitlServer; instead it builds the container's
        .claude.json to point at the parent's URL+token."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        monkeypatch.setenv("ZIPSA_PARENT_MCP_URL", "http://host.docker.internal:7777/mcp")
        monkeypatch.setenv("ZIPSA_PARENT_MCP_TOKEN", "parent-tok")

        from zipsa.core.executor import DockerExecutor
        from zipsa.core.skill import Skill

        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)
        executor = DockerExecutor(runtime="claude", image="x")

        # The .claude.json builder should produce a config pointing at
        # the parent URL with the parent token, NOT a fresh localhost port.
        claude_json_path = skill.build_claude_json(
            output_dir=tmp_path,
            mcp_url_override="http://host.docker.internal:7777/mcp",
            mcp_token_override="parent-tok",
        )
        import json
        cfg = json.loads(claude_json_path.read_text())
        # Inspect MCP config — should reference parent URL
        zipsa_mcp = cfg["mcpServers"]["zipsa"]
        assert zipsa_mcp["url"] == "http://host.docker.internal:7777/mcp"
        assert zipsa_mcp["headers"]["Authorization"] == "Bearer parent-tok"
```

- [ ] **Step 2: Fail**

Expected: TypeError (unknown kwargs) or assertion failure.

- [ ] **Step 3: Implement**

In `launcher/zipsa/core/skill.py` (where `build_claude_json` lives):

a) Add `mcp_url_override` and `mcp_token_override` kwargs.
b) When overrides provided, skip generating localhost MCP server config and use the overrides.

In `launcher/zipsa/core/executor.py`:

a) At start of `run()` (or wherever HitlServer is spawned), check `ZIPSA_PARENT_MCP_URL`:

```python
parent_mcp_url = os.environ.get("ZIPSA_PARENT_MCP_URL")
parent_mcp_token = os.environ.get("ZIPSA_PARENT_MCP_TOKEN")

if parent_mcp_url and parent_mcp_token:
    # We're a child — reuse parent's HitlServer
    self._hitl_server = None
    mcp_url = parent_mcp_url
    mcp_token = parent_mcp_token
else:
    # Top-level run — spawn own server
    self._hitl_server = HitlServer(...)
    self._hitl_server.start()
    mcp_url = f"http://host.docker.internal:{self._hitl_server.port}/mcp"
    mcp_token = self._hitl_server.token
```

b) Pass mcp_url and mcp_token to `build_claude_json`.

- [ ] **Step 4: Pass + full suite**

```bash
cd launcher && uv run pytest tests/test_executor.py::TestParentMCPDelegation -v
cd launcher && uv run pytest 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/core/executor.py launcher/zipsa/core/skill.py launcher/tests/test_executor.py
git commit -m "feat(executor): child launcher reuses parent's HitlServer

When ZIPSA_PARENT_MCP_URL + ZIPSA_PARENT_MCP_TOKEN are set, the
child launcher does NOT spawn its own HitlServer. Instead it points
its container's .claude.json MCP config at the parent's URL with
the parent-supplied token. The parent's HitlServer has already
registered (token → CallerInfo(child_skill, child_version)) so
tool handlers route correctly via current_caller contextvar.

Top-level runs (no env vars) work as before."
```

---

## Task 6: RunSkillHandler + MCP tool registration

**Files:**
- Create: `launcher/zipsa/core/run_skill_handler.py`
- Modify: `launcher/zipsa/core/hitl_runner.py`, `launcher/zipsa/core/executor.py` (auto-allow)
- Test: `launcher/tests/test_run_skill_handler.py`, `launcher/tests/test_hitl_runner.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for RunSkillHandler: subprocess wrapper that runs a child
skill via `uv run zipsa run`, parses summary.json, returns
{status, exit_code, skill, version, run_id, summary}."""

import json
import pytest
import secrets
import subprocess
from unittest.mock import MagicMock, patch

from zipsa.core.run_skill_handler import RunSkillHandler
from zipsa.core.caller_context import CallerInfo


class TestRunSkillHandler:
    def test_rejects_child_not_in_caller_children(self, tmp_path, monkeypatch):
        """If parent's spec.children doesn't include the requested
        child, return failed with skill_not_in_children."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        server = MagicMock(port=12345, token="parent-tok")
        h = RunSkillHandler(server=server)
        # Register parent caller via fake current_caller contextvar
        from zipsa.core.caller_context import current_caller
        current_caller.set(CallerInfo("parent", "1.0.0"))
        # Parent's spec.children fetched via skill loader — use a stub
        def fake_resolve_caller_children(caller):
            return ["alpha", "beta"]
        h._resolve_caller_children = fake_resolve_caller_children
        result = h.run(name="gamma", args="")
        assert result["status"] == "failed"
        assert "skill_not_in_children" in result["error"]["code"]

    def test_spawns_subprocess_with_propagated_env(self, tmp_path, monkeypatch):
        """When child is permitted, RunSkillHandler must spawn it
        with ZIPSA_PARENT_MCP_URL, ZIPSA_PARENT_MCP_TOKEN, and
        ZIPSA_CALL_TRACE/DEPTH set."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        server = MagicMock(port=12345, token="parent-tok")
        h = RunSkillHandler(server=server)
        from zipsa.core.caller_context import current_caller
        current_caller.set(CallerInfo("parent", "1.0.0"))
        h._resolve_caller_children = lambda c: ["alpha"]
        # Fake summary.json the subprocess "writes"
        fake_run_dir = tmp_path / "alpha@0.1.0" / "runs" / "2026-05-21_000000_000"
        fake_run_dir.mkdir(parents=True)
        (fake_run_dir / "summary.json").write_text(json.dumps({
            "schema_version": 1, "status": "ok", "exit_code": 0,
            "skill": "alpha", "version": "0.1.0",
            "started_at": "x", "finished_at": "y",
            "duration_seconds": 1.0, "cost_usd": 0.01, "turns": 1,
            "phases": [],
        }))
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
            # patch the run_id discovery: have it return our fake dir
            h._find_latest_run_dir = lambda name: fake_run_dir
            result = h.run(name="alpha", args="hello")

        call_args = mock_run.call_args
        env = call_args.kwargs["env"]
        assert env["ZIPSA_PARENT_MCP_URL"] == "http://host.docker.internal:12345/mcp"
        assert env["ZIPSA_PARENT_MCP_TOKEN"]  # some token issued
        assert "parent" in env["ZIPSA_CALL_TRACE"]
        assert env["ZIPSA_CALL_DEPTH"] == "1"  # depth bumped from default 0
        assert call_args.kwargs["stdin"] == subprocess.DEVNULL or call_args.kwargs.get("stdin") == subprocess.DEVNULL

        # Result shape
        assert result["status"] == "ok"
        assert result["skill"] == "alpha"
        assert result["version"] == "0.1.0"
        assert result["run_id"] == "2026-05-21_000000_000"
        assert "summary" in result

    def test_dict_args_serialized_as_json(self, tmp_path, monkeypatch):
        """Allow callers to pass args as a dict; we JSON-encode for the
        subprocess so child skill sees it as a structured user_query."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        server = MagicMock(port=12345, token="parent-tok")
        h = RunSkillHandler(server=server)
        from zipsa.core.caller_context import current_caller
        current_caller.set(CallerInfo("parent", "1.0.0"))
        h._resolve_caller_children = lambda c: ["alpha"]
        fake_run_dir = tmp_path / "alpha@0.1.0" / "runs" / "r1"
        fake_run_dir.mkdir(parents=True)
        (fake_run_dir / "summary.json").write_text(json.dumps({
            "schema_version": 1, "status": "ok", "exit_code": 0,
            "skill": "alpha", "version": "0.1.0",
            "started_at": "x", "finished_at": "y",
            "duration_seconds": 0, "cost_usd": 0, "turns": 1, "phases": [],
        }))
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
            h._find_latest_run_dir = lambda name: fake_run_dir
            h.run(name="alpha", args={"date": "2026-05-21"})

        cmd = mock_run.call_args.args[0]
        # The arg passed to uv run zipsa run should be JSON
        assert cmd[:-1] == ["uv", "run", "zipsa", "run", "alpha"]
        assert json.loads(cmd[-1]) == {"date": "2026-05-21"}

    def test_child_exit_nonzero_returns_failed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        server = MagicMock(port=12345, token="parent-tok")
        h = RunSkillHandler(server=server)
        from zipsa.core.caller_context import current_caller
        current_caller.set(CallerInfo("parent", "1.0.0"))
        h._resolve_caller_children = lambda c: ["alpha"]
        fake_run_dir = tmp_path / "alpha@0.1.0" / "runs" / "r1"
        fake_run_dir.mkdir(parents=True)
        (fake_run_dir / "summary.json").write_text(json.dumps({
            "schema_version": 1, "status": "failed", "exit_code": 1,
            "skill": "alpha", "version": "0.1.0",
            "started_at": "x", "finished_at": "y",
            "duration_seconds": 0, "cost_usd": 0, "turns": 1, "phases": [],
            "error": {"code": "agent_error", "message": "x"},
        }))
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout=b"", stderr=b"")
            h._find_latest_run_dir = lambda name: fake_run_dir
            result = h.run(name="alpha", args="")
        assert result["status"] == "failed"
        assert result["exit_code"] == 1
```

- [ ] **Step 2: Fail**

Expected: ImportError.

- [ ] **Step 3: Implement**

Create `launcher/zipsa/core/run_skill_handler.py`:

```python
"""RunSkillHandler — subprocess wrapper that runs a child skill via
`uv run zipsa run`, then reads the child's summary.json and returns
{status, exit_code, skill, version, run_id, summary} for the calling
MCP tool to surface to the parent agent."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .. import paths as zipsa_paths
from .caller_context import CallerInfo, current_caller

if TYPE_CHECKING:
    from .hitl_runner import HitlServer


class RunSkillHandler:
    def __init__(self, server: "HitlServer") -> None:
        self._server = server

    def run(self, *, name: str, args: str = "") -> dict:
        """Spawn child skill subprocess. Return summary dict."""
        caller = current_caller.get()
        if caller is None:
            return self._fail("caller_unknown", "no caller context")

        # 1. Gate: child must be in caller's spec.children
        permitted = self._resolve_caller_children(caller)
        if name not in permitted:
            return self._fail(
                "skill_not_in_children",
                f"'{caller.skill}' did not declare '{name}' in spec.children",
            )

        # 2. Mint child token, register with server
        child_token = secrets.token_urlsafe(32)
        # Child's actual version isn't known until launcher loads its
        # manifest — register with placeholder, update from summary.json
        self._server.register_caller(child_token, CallerInfo(skill=name, version="*"))

        # 3. Build env
        env = self._build_child_env(caller, child_token)

        # 4. Build cmd
        cmd = ["uv", "run", "zipsa", "run", name, args]

        # 5. Spawn. stdin=DEVNULL: child LAUNCHER doesn't need stdin
        # (HITL goes through parent's HitlServer, not the child launcher
        # process). capture_output: keep parent's terminal clean — child's
        # verbose event stream goes into stdout/stderr buffers, discarded.
        # The child's run_dir + summary.json is the audit trail.
        # Timeout default 600s (10min); override via ZIPSA_RUN_SKILL_TIMEOUT.
        timeout_s = int(os.environ.get("ZIPSA_RUN_SKILL_TIMEOUT", "600"))
        try:
            result = subprocess.run(
                cmd, env=env, stdin=subprocess.DEVNULL,
                capture_output=True, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as e:
            return self._fail("child_timeout", str(e))

        # 6. Read summary.json
        run_dir = self._find_latest_run_dir(name)
        if run_dir is None:
            return self._fail(
                "summary_not_found",
                f"child returned but no run dir found under {zipsa_paths.skill_runs_dir(name, '*')}",
            )
        try:
            summary = json.loads((run_dir / "summary.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError) as e:
            return self._fail("summary_unreadable", str(e))

        # 7. Update token registration with actual version
        if "version" in summary:
            self._server.register_caller(
                child_token,
                CallerInfo(skill=name, version=summary["version"]),
            )

        return {
            "status": summary.get("status", "ok"),
            "exit_code": result.returncode,
            "skill": summary.get("skill", name),
            "version": summary.get("version", "*"),
            "run_id": run_dir.name,
            "summary": summary,
        }

    def _build_child_env(self, caller: CallerInfo, child_token: str) -> dict[str, str]:
        env = os.environ.copy()
        env["ZIPSA_PARENT_MCP_URL"] = f"http://host.docker.internal:{self._server.port}/mcp"
        env["ZIPSA_PARENT_MCP_TOKEN"] = child_token
        # Extend call trace
        existing_trace = env.get("ZIPSA_CALL_TRACE", "")
        existing_list = [s for s in existing_trace.split(",") if s]
        env["ZIPSA_CALL_TRACE"] = ",".join(existing_list + [caller.skill])
        # Bump depth
        existing_depth = int(env.get("ZIPSA_CALL_DEPTH", "0"))
        env["ZIPSA_CALL_DEPTH"] = str(existing_depth + 1)
        return env

    def _resolve_caller_children(self, caller: CallerInfo) -> list[str]:
        """Load caller's manifest, return spec.children. Cached if costly."""
        from .skill import Skill
        skill = Skill.load_installed(caller.skill, caller.version)
        return list(skill.manifest.spec.children)

    def _find_latest_run_dir(self, name: str) -> Optional[Path]:
        """Locate the most recently created run dir for this child name
        across all installed versions."""
        # Look under ~/.zipsa/<name>@*/runs/*
        home = zipsa_paths.zipsa_home()
        candidates = sorted(home.glob(f"{name}@*/runs/*"), key=lambda p: p.stat().st_mtime)
        return candidates[-1] if candidates else None

    @staticmethod
    def _fail(code: str, message: str) -> dict:
        return {
            "status": "failed",
            "exit_code": -1,
            "skill": None,
            "version": None,
            "run_id": None,
            "summary": None,
            "error": {"code": code, "message": message},
        }
```

In `hitl_runner.py` `start()`, after artifact handler registration, add:

```python
        from .run_skill_handler import RunSkillHandler
        run_skill_h = RunSkillHandler(server=self)

        @mcp.tool()
        def run_skill(name: str, args: str = "") -> dict:
            """Invoke a child skill declared in this skill's spec.children.

            Returns {status, exit_code, skill, version, run_id, summary}.
            Pair `skill`+`version`+`run_id` with `mcp__zipsa__get_artifact`
            to read the child's outputs.

            Args:
              name: child skill name (must be in this skill's spec.children)
              args: passed to child as user_query (string). If you need to
                    pass structured data, JSON-encode it yourself: the child
                    SKILL.md decides whether to parse user_query as JSON.
            """
            return run_skill_h.run(name=name, args=args)
```

In `executor.py` `_write_phase_allow_file`, add to auto-allow:

```python
        allowed_tools = list(allowed_tools) + [
            ...
            "mcp__zipsa__get_artifact",
            "mcp__zipsa__run_skill",   # NEW — gated by skill's spec.children at handler level
            "ToolSearch",
        ]
```

(Optionally: only inject `run_skill` if `caller.spec.children` non-empty. For simplicity, always allow at hook level — the handler-side `skill_not_in_children` check is the real gate.)

- [ ] **Step 4: Pass + full suite**

```bash
cd launcher && uv run pytest tests/test_run_skill_handler.py -v
cd launcher && uv run pytest tests/test_hitl_runner.py -v
cd launcher && uv run pytest 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/core/run_skill_handler.py launcher/zipsa/core/hitl_runner.py launcher/zipsa/core/executor.py launcher/tests/test_run_skill_handler.py launcher/tests/test_hitl_runner.py
git commit -m "feat(mcp): mcp__zipsa__run_skill tool

Parent orchestrator skills can now invoke child skills declared in
spec.children. RunSkillHandler spawns subprocess (uv run zipsa run),
propagates ZIPSA_PARENT_MCP_URL/TOKEN/CALL_TRACE/DEPTH env so child
launcher reuses parent's HitlServer with a child-specific token.
Reads child's summary.json, returns {status, exit_code, skill,
version, run_id, summary} — caller pairs the routing fields with
get_artifact to consume child's outputs.

stdin=DEVNULL on child → non-interactive → atomic skills that try
HITL via their own server would fail, but since they're routed
through parent's server (which DOES have stdin), HITL works
naturally."
```

---

## Task 7: Docs + e2e fixture

**Files:**
- Modify: `launcher/zipsa/system-prompts/runtime-contract.md`
- Create: `launcher/tests/fixtures/skills/test-parent/` (manifest + SKILL.md)
- Test: `launcher/tests/test_run_skill_e2e.py` (integration)

- [ ] **Step 1: Document in runtime-contract.md**

Add section `## Invoking child skills` after the Artifacts section, covering:
- `mcp__zipsa__run_skill(name, args)` signature + response shape
- Must be declared in `spec.children` to invoke
- How to chain: `result = run_skill(...); art = get_artifact(result.skill, result.version, result.run_id, "output.json")`
- Update the intent → tool table to include "invoke child skill"

Also add to the execution_context field list a `caller_chain` field if we expose it (otherwise note: the agent doesn't see the chain).

- [ ] **Step 2: Create fixture parent skill**

`launcher/tests/fixtures/skills/test-parent/manifest.yaml`:

```yaml
apiVersion: zipsa.dev/v1alpha1
kind: Skill
metadata:
  name: test-parent
  version: 0.1.0
spec:
  purpose: "Integration fixture for run_skill."
  instructions: ./SKILL.md
  children: [hello-world]
  tools:
    builtin: []
  limits:
    max_turns: 4
    max_cost_usd: 0.05
    timeout_seconds: 60
```

`SKILL.md`: instructions to call `mcp__zipsa__run_skill("hello-world")` and echo the resulting summary's `skill`+`version`+`run_id` back.

- [ ] **Step 3: Integration test (marked, off-by-default)**

```python
@pytest.mark.integration
def test_parent_calls_hello_world_via_run_skill():
    """End-to-end: parent fixture calls hello-world via run_skill,
    receives {skill: 'hello-world', version: ..., run_id: ...},
    then calls get_artifact on the routing fields and gets a real
    response. Requires Docker; mark skipped if unavailable."""
    pytest.importorskip("docker")  # or just run subprocess
    result = subprocess.run(
        ["uv", "run", "zipsa", "run", "test-parent"],
        capture_output=True, timeout=120,
    )
    assert result.returncode == 0
    # Parse summary.json
    home = Path.home() / ".zipsa"
    parent_runs = sorted((home / "test-parent@0.1.0/runs").iterdir())
    latest = parent_runs[-1]
    summary = json.loads((latest / "summary.json").read_text())
    assert summary["status"] == "ok"
```

- [ ] **Step 4: Run docs lint + ensure existing tests pass**

```bash
cd launcher && uv run pytest -m "not integration" 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/system-prompts/runtime-contract.md launcher/tests/fixtures/skills/test-parent launcher/tests/test_run_skill_e2e.py
git commit -m "docs+test: document run_skill, add fixture parent skill

Document the run_skill tool in runtime-contract.md: signature, the
{status, exit_code, skill, version, run_id, summary} response shape,
how to chain with get_artifact, and the spec.children declaration
requirement.

Add fixture parent skill that calls hello-world via run_skill and
an integration-marked test that verifies the chain end-to-end."
```

---

## Verification (end-to-end)

After all tasks:

```bash
cd /Users/neochoon/WestbrookAI/zipsa/.worktrees/feat-skill-composition-phase2/launcher
uv run pytest  # all green

# Manual e2e
uv run zipsa run test-parent  # parent → run_skill(hello-world) → success
ls ~/.zipsa/hello-world@*/runs/  # child run exists
cat ~/.zipsa/test-parent@0.1.0/runs/*/summary.json  # parent saw the child's routing fields
```

## Implementation risks (track during execution)

- **T3 is the riskiest task.** Refactoring HitlServer's memory routing from `self._skill_store` to per-caller stores changes the behavior of every existing top-level run. Backward compat hinges on `primary_caller` pre-registration. If pre-registration is missed or off-by-one, every existing skill breaks at runtime.
- **`_find_latest_run_dir` race.** If a parent fires multiple concurrent `run_skill` calls (or another run is in progress on the same skill), "latest" by mtime could pick the wrong dir. v1 accepts this; v2 could have the child write a `run_id_marker` file to a parent-known path or have RunSkillHandler create the run_dir before spawning.
- **`Skill.load_installed(name, version)`** may not exist with that exact signature. Need to use whatever the codebase's installed-skill lookup actually is. Check `launcher/zipsa/installer.py` for the registry pattern.
- **Token leak.** Per-child tokens live forever in `_token_map` until the server stops. For long-running parent processes calling many children, the map grows. v1: accept (depth-5 cap bounds it); v2: GC after subprocess.run returns.
- **MCP tool args typing.** FastMCP needs JSON-schema-compatible types. `str` works; `dict | str` would force union types. Plan settled on `str` only — caller JSON-encodes if structured.

## Out of scope

- ask_once/recall/remember from a child whose memory file doesn't yet exist — handler creates it lazily, no special logic
- Streaming child events to parent — child's run_dir + summary.json is the audit trail; parent doesn't see child progress live
- Per-child cost cap from parent (`run_skill(... max_cost=X)`) — BACKLOG; child uses its own limits
- Hot-reload of caller_map during a run — token map is built up only on registration, never removed mid-run
- Linux UID 1000 / docker bind-mount writability — same Phase 1 followup
- BPF-style sandboxing of child subprocess — child inherits parent's network + filesystem; the only isolation is Docker for the agent
