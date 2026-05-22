# `ask_once` `default` Parameter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional `default` argument to the `mcp__zipsa__ask_once` MCP tool so empty interactive input and non-interactive runs resolve to the default instead of caching an empty string or raising `HITL_UNATTENDED`.

**Architecture:** All behavior lives inside the existing `ask_once` tool closure in `hitl_runner.py`; the `AskHandler` I/O class is untouched. Tests drive the tool end-to-end over HTTP MCP using the in-memory `HitlIO` harness already established in `tests/test_hitl_runner.py`.

**Tech Stack:** Python 3.12, pytest, uv, FastMCP (streamable HTTP), httpx (test client).

**Spec:** `docs/superpowers/specs/2026-05-22-ask-once-default-design.md`

---

## File Structure

- Modify: `launcher/zipsa/core/hitl_runner.py` — `ask_once` tool closure (~line 267). Add `default` param + branching.
- Modify: `launcher/tests/test_hitl_runner.py` — add `TestAskOnceDefault` class (6 tests).
- Modify: `launcher/zipsa/system-prompts/runtime-contract.md` — document `default?` in the intent→tool mapping + a guideline line.
- Modify: `BACKLOG.md` — remove the resolved "`ask_once` should accept a `default` parameter" entry.

All commands below run from the `launcher/` directory unless noted.

---

### Task 1: Add `default` parameter and behavior to `ask_once`

**Files:**
- Modify: `launcher/zipsa/core/hitl_runner.py:267` (the `ask_once` closure)
- Test: `launcher/tests/test_hitl_runner.py` (new `TestAskOnceDefault` class, appended after `TestAskOnceWired`)

- [ ] **Step 1: Write the failing tests**

Append this class to `launcher/tests/test_hitl_runner.py`. It reuses the
same end-to-end HTTP-MCP pattern as `TestAskOnceWired` / `TestGetArtifactMCP`.
`threading` and `HitlIO` are already imported at the top of the file.

```python
class TestAskOnceDefault:
    """ask_once `default` resolves empty input and unattended runs."""

    def _make_server(self, tmp_path, stdin_text, interactive):
        import io as _io
        from zipsa.core.memory_store import MemoryStore
        from zipsa.core.caller_context import CallerInfo
        io_ = HitlIO(
            stdin=_io.StringIO(stdin_text),
            stdout=_io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=interactive,
        )
        skill = MemoryStore(tmp_path / "skill.json")
        global_ = MemoryStore(tmp_path / "global.json")
        server = HitlServer(io_, skill_store=skill, global_store=global_,
                            primary_caller=CallerInfo("test", "0"))
        return server, skill

    def _session(self, server):
        import httpx
        url = f"http://127.0.0.1:{server.port}/mcp"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {server.token}",
        }
        init = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}},
        }
        r = httpx.post(url, json=init, headers=headers, timeout=5.0)
        assert r.status_code == 200
        sid = r.headers["mcp-session-id"]
        sh = {**headers, "mcp-session-id": sid}
        httpx.post(url, json={"jsonrpc": "2.0",
                              "method": "notifications/initialized"},
                   headers=sh, timeout=5.0)
        return url, sh

    def _call(self, url, sh, arguments, req_id=2):
        import httpx
        call = {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                "params": {"name": "ask_once", "arguments": arguments}}
        r = httpx.post(url, json=call, headers=sh, timeout=5.0)
        assert r.status_code == 200
        return r

    def _text(self, r):
        import json
        body = r.text
        if "data:" in body:
            for line in body.splitlines():
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    break
        else:
            data = r.json()
        return data["result"]["content"][0]["text"]

    def test_empty_input_uses_default(self, tmp_path):
        server, skill = self._make_server(tmp_path, "\n", interactive=True)
        server.start()
        try:
            url, sh = self._session(server)
            r = self._call(url, sh, {"key": "db", "prompt": "DB?",
                                     "default": "zipsa-daily-log"})
            assert "zipsa-daily-log" in self._text(r)
            assert skill.get("db") == "zipsa-daily-log"
        finally:
            server.stop()

    def test_empty_input_no_default_stores_empty(self, tmp_path):
        server, skill = self._make_server(tmp_path, "\n", interactive=True)
        server.start()
        try:
            url, sh = self._session(server)
            self._call(url, sh, {"key": "db", "prompt": "DB?"})
            assert skill.get("db") == ""   # documents current behavior
        finally:
            server.stop()

    def test_nonempty_input_ignores_default(self, tmp_path):
        server, skill = self._make_server(tmp_path, "my-db\n", interactive=True)
        server.start()
        try:
            url, sh = self._session(server)
            r = self._call(url, sh, {"key": "db", "prompt": "DB?",
                                     "default": "zipsa-daily-log"})
            assert "my-db" in self._text(r)
            assert skill.get("db") == "my-db"
        finally:
            server.stop()

    def test_cache_hit_ignores_default(self, tmp_path):
        server, skill = self._make_server(tmp_path, "", interactive=True)
        skill.set("db", "cached-db")
        server.start()
        try:
            url, sh = self._session(server)
            r = self._call(url, sh, {"key": "db", "prompt": "DB?",
                                     "default": "zipsa-daily-log"})
            assert "cached-db" in self._text(r)
        finally:
            server.stop()

    def test_noninteractive_uses_default(self, tmp_path):
        server, skill = self._make_server(tmp_path, "", interactive=False)
        server.start()
        try:
            url, sh = self._session(server)
            r = self._call(url, sh, {"key": "db", "prompt": "DB?",
                                     "default": "zipsa-daily-log"})
            assert "zipsa-daily-log" in self._text(r)
            assert skill.get("db") == "zipsa-daily-log"
        finally:
            server.stop()

    def test_noninteractive_no_default_raises_unattended(self, tmp_path):
        server, skill = self._make_server(tmp_path, "", interactive=False)
        server.start()
        try:
            url, sh = self._session(server)
            r = self._call(url, sh, {"key": "db", "prompt": "DB?"})
            assert "HITL_UNATTENDED" in r.text
        finally:
            server.stop()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_hitl_runner.py::TestAskOnceDefault -v`
Expected: `test_empty_input_uses_default`, `test_nonempty_input_ignores_default`,
and `test_noninteractive_uses_default` FAIL (the `default` argument is rejected
or ignored by the current signature). `test_empty_input_no_default_stores_empty`,
`test_cache_hit_ignores_default`, and `test_noninteractive_no_default_raises_unattended`
may already PASS (they exercise current behavior) — that is fine; they are
regression guards.

Note: if passing an unknown `default` argument raises a hard MCP error rather
than being ignored, the three new-behavior tests still fail — that is the
expected red state.

- [ ] **Step 3: Implement the behavior**

In `launcher/zipsa/core/hitl_runner.py`, replace the `ask_once` closure body
(currently around line 267) with this version. Add the `default` parameter and
the empty-input / unattended branching; keep the `@mcp.tool()` and `@_logged`
decorators exactly as they are.

```python
        @mcp.tool()
        @_logged
        def ask_once(
            key: str,
            prompt: str,
            scope: str = "skill",
            default: str | None = None,
        ) -> str:
            """Ask the user a question and cache the answer permanently.

            If the key already has a value (in the chosen scope), returns
            that value without prompting. Otherwise asks the user, stores
            the answer, and returns it. The "cached config" pattern in one
            call — no risk of forgetting to remember.

            If `default` is given: an empty answer (the user just hits
            Enter) resolves to `default`, and in a non-interactive run the
            question resolves to `default` instead of failing. Pass the
            value you mention in the prompt as `default` rather than
            relying on the agent inferring that empty input means "use the
            default".

            Use this for values that, once given, should never be asked
            again (workspace name, default city, preferred language).

            For one-off questions whose answers should NOT be stored
            (current date, "are you sure?"), use the bare `ask` tool.
            """
            store = _store_for_scope(scope)
            cached = store.get(key)
            if cached is not None:
                return cached if isinstance(cached, str) else str(cached)
            try:
                answer = ask_h.run(prompt=prompt)
            except HitlUnattended as e:
                if default is None:
                    raise RuntimeError(f"HITL_UNATTENDED: {e}") from e
                answer = default
            else:
                if answer == "" and default is not None:
                    answer = default
            store.set(key, answer)
            return answer
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_hitl_runner.py::TestAskOnceDefault -v`
Expected: all 6 PASS.

If the `test_noninteractive_no_default_raises_unattended` assertion does not
match (the error envelope serializes differently than expected), inspect the
raw response with `print(r.text)` and adjust the assertion to match where
`HITL_UNATTENDED` actually appears — do not change the implementation for it.

- [ ] **Step 5: Run the full launcher suite (regression check)**

Run: `uv run pytest -q`
Expected: all tests pass, including the existing `TestAskOnceWired` and
`TestMemoryToolsWired`.

- [ ] **Step 6: Commit**

```bash
git add launcher/zipsa/core/hitl_runner.py launcher/tests/test_hitl_runner.py
git commit -m "feat: add default parameter to ask_once (BACKLOG)

Empty interactive input and non-interactive runs now resolve to the
provided default instead of caching an empty string or raising
HITL_UNATTENDED. Cache hits and explicit answers ignore the default.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: Document `default` in the runtime contract and clear the BACKLOG entry

**Files:**
- Modify: `launcher/zipsa/system-prompts/runtime-contract.md` (intent→tool mapping table ~line 191, guidelines ~line 196)
- Modify: `BACKLOG.md` (remove resolved entry)

- [ ] **Step 1: Update the intent→tool mapping row**

In `launcher/zipsa/system-prompts/runtime-contract.md`, find the row:

```
| "ask once" / "remember" / "default" / "cache across runs" / "set up the first time" | `mcp__zipsa__ask_once({key, prompt, scope?})` |
```

Replace the tool signature with `default?`:

```
| "ask once" / "remember" / "default" / "cache across runs" / "set up the first time" | `mcp__zipsa__ask_once({key, prompt, scope?, default?})` |
```

- [ ] **Step 2: Add a guideline line about `default`**

Immediately after the existing paragraph that ends "... preferred
language, name)." (the scope explanation around line 199), add:

```markdown

If the prompt mentions a default value, pass it as `default` — don't
rely on the agent inferring that empty input means the default. With a
`default` set, the question is also answerable in non-interactive runs
(it resolves to the default instead of failing).
```

- [ ] **Step 3: Remove the resolved BACKLOG entry**

In `BACKLOG.md`, delete the entire section beginning with the heading
`## `ask_once` should accept a `default` parameter (2026-05-18)` up to
(but not including) the next `---` separator's following heading — i.e.
remove that section and its trailing `---` so the file stays well-formed.

- [ ] **Step 4: Sanity-check the docs render**

Run: `git diff --stat`
Expected: `runtime-contract.md` and `BACKLOG.md` show as modified, with the
BACKLOG entry removed and the contract row/guideline updated. No code files in
this diff.

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/system-prompts/runtime-contract.md BACKLOG.md
git commit -m "docs: document ask_once default param; clear BACKLOG entry

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Decisions 1–7 + resolved-behavior table → Task 1 (6 tests cover every row of
  the behavior table; implementation matches the spec's code block verbatim). ✓
- "Out of scope (YAGNI)": no `default` on bare `ask`, no prompt auto-echo —
  the plan touches neither `ask` nor `AskHandler`. ✓
- Documentation section → Task 2 Steps 1–2. ✓
- Rollout / BACKLOG removal → Task 2 Step 3 + Task 1 commit tagged `(BACKLOG)`. ✓

**Placeholder scan:** No TBD/TODO; every code and command step shows concrete
content. The one conditional (Step 4 fallback for the error-envelope assertion)
gives a concrete debugging action, not a vague instruction. ✓

**Type consistency:** `default: str | None = None` used consistently in the
signature, docstring, and tests. Store accessors `store.get` / `store.set`
match existing usage. Test helper names (`_make_server`, `_session`, `_call`,
`_text`) are self-consistent within the new class. ✓
