# Zipsa Memory (KV) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four memory tools (`recall` / `remember` / `forget` / `list_memory`) to the existing zipsa MCP server, with two scopes (per-skill / global), JSON-file backed, always available to every skill.

**Architecture:** A small `MemoryStore` class reads/writes a JSON dict to a per-store file path. The existing `HitlServer` constructs two stores (skill + global) per run and registers four new MCP tools that route by `scope` parameter. The executor adds the file paths during HitlServer setup and includes the four new tool names in the default phase allow list. No manifest changes, no runtime image changes.

**Tech Stack:** Python 3.10+, stdlib `json` + `pathlib`, existing zipsa launcher, existing `mcp` SDK / FastMCP wiring.

**Spec:** [docs/superpowers/specs/2026-05-18-zipsa-memory-design.md](../specs/2026-05-18-zipsa-memory-design.md)

---

## File structure

**New files**

| Path | Responsibility |
|---|---|
| `launcher/zipsa/core/memory_store.py` | `MemoryStore` class — JSON-file backed KV |
| `launcher/tests/test_memory_store.py` | Unit tests for `MemoryStore` (no MCP, no server) |

**Modified files**

| Path | Change |
|---|---|
| `launcher/zipsa/core/hitl_mcp.py` | Add `RecallHandler`, `RememberHandler`, `ForgetHandler`, `ListMemoryHandler` |
| `launcher/zipsa/core/hitl_runner.py` | `HitlServer.__init__` accepts skill/global stores; `start()` registers 4 new MCP tools |
| `launcher/zipsa/core/executor.py` | Construct two `MemoryStore`s, pass to `HitlServer`; include four tool names in default allow list (both `_write_phase_allow_file` and `_write_default_phase_allow_file`) |
| `launcher/zipsa/system-prompts/runtime-contract.md` | Add "Memory" section after "Asking the user" |
| `launcher/tests/test_hitl_mcp.py` | Tests for the four handlers (mock stores) |
| `launcher/tests/test_hitl_runner.py` | Integration test: HitlServer with stores, end-to-end MCP tool call |
| `launcher/tests/test_executor.py` | Tests for memory paths + allow list extension |

---

## Task 1: Worktree + baseline

**Files:**
- None modified (setup only)

- [ ] **Step 1: Create worktree from main**

```bash
cd /Users/neochoon/WestbrookAI/zipsa
git fetch origin
git worktree add .worktrees/feat-memory -b feat/memory
cd .worktrees/feat-memory/launcher
uv venv -q
uv pip install -e ".[dev]" 2>&1 | tail -1
```

Expected: `+ zipsa==0.1.5 ...`

- [ ] **Step 2: Confirm baseline tests pass**

```bash
uv run pytest -q 2>&1 | tail -3
```

Expected: passing count (note the number; subsequent tasks add ~15-20 tests).

- [ ] **Step 3: No commit needed** — worktree creation isn't a code change.

---

## Task 2: MemoryStore class

**Files:**
- Create: `launcher/zipsa/core/memory_store.py`
- Create: `launcher/tests/test_memory_store.py`

- [ ] **Step 1: Write failing tests**

Create `launcher/tests/test_memory_store.py`:

```python
"""Tests for MemoryStore — JSON file-backed KV store."""

import json
import stat
from pathlib import Path

import pytest

from zipsa.core.memory_store import MemoryStore


class TestMemoryStore:
    def test_get_missing_returns_none(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        assert store.get("missing") is None

    def test_set_then_get_roundtrip_string(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        store.set("name", "Westbrook")
        assert store.get("name") == "Westbrook"

    def test_set_then_get_roundtrip_int(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        store.set("n", 42)
        assert store.get("n") == 42

    def test_set_then_get_roundtrip_list(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        store.set("items", ["a", "b", "c"])
        assert store.get("items") == ["a", "b", "c"]

    def test_set_then_get_roundtrip_dict(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        store.set("conf", {"workspace": "X", "db": "Y"})
        assert store.get("conf") == {"workspace": "X", "db": "Y"}

    def test_set_creates_file_with_0600_perms(self, tmp_path):
        path = tmp_path / "memory.json"
        store = MemoryStore(path)
        store.set("k", "v")
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    def test_set_creates_parent_dir_if_missing(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "memory.json"
        store = MemoryStore(path)
        store.set("k", "v")
        assert path.exists()

    def test_set_overwrites_existing_key(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        store.set("k", "v1")
        store.set("k", "v2")
        assert store.get("k") == "v2"

    def test_delete_existing_returns_true(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        store.set("k", "v")
        assert store.delete("k") is True
        assert store.get("k") is None

    def test_delete_missing_returns_false(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        assert store.delete("never_existed") is False

    def test_keys_empty_when_file_missing(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        assert store.keys() == []

    def test_keys_returns_stored_keys(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        store.set("a", 1)
        store.set("b", 2)
        assert sorted(store.keys()) == ["a", "b"]

    def test_two_stores_share_file_see_same_data(self, tmp_path):
        """Reading on each get means file changes are picked up live."""
        path = tmp_path / "memory.json"
        store1 = MemoryStore(path)
        store2 = MemoryStore(path)
        store1.set("k", "from-1")
        assert store2.get("k") == "from-1"

    def test_get_with_corrupt_json_raises(self, tmp_path):
        path = tmp_path / "memory.json"
        path.write_text("not json {")
        store = MemoryStore(path)
        with pytest.raises(json.JSONDecodeError):
            store.get("k")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_memory_store.py -q 2>&1 | tail -5
```

Expected: `ModuleNotFoundError: No module named 'zipsa.core.memory_store'`.

- [ ] **Step 3: Implement `MemoryStore`**

Create `launcher/zipsa/core/memory_store.py`:

```python
"""JSON file-backed key/value store.

Reads the file on every get/keys so concurrent writers (e.g. another
process or another launcher run) are picked up live without cache
invalidation logic. Writes do a full read-modify-write to the same
file. File is created with 0600 permissions and the parent directory
is created on first write.

This is intentionally minimal: no schema validation, no TTL, no audit
log. Values must be JSON-serializable (str, int, float, bool, None,
list, dict). v1 makes no concurrency guarantee for simultaneous writers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class MemoryStore:
    """JSON dict on disk. One file per store."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        with open(self._path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self._path.chmod(0o600)

    def get(self, key: str) -> Any | None:
        return self._read().get(key)

    def set(self, key: str, value: Any) -> None:
        data = self._read()
        data[key] = value
        self._write(data)

    def delete(self, key: str) -> bool:
        data = self._read()
        if key not in data:
            return False
        del data[key]
        self._write(data)
        return True

    def keys(self) -> list[str]:
        return list(self._read().keys())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_memory_store.py -q 2>&1 | tail -3
```

Expected: `14 passed`.

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/core/memory_store.py launcher/tests/test_memory_store.py
git commit -m "feat(memory): add MemoryStore — JSON file-backed KV"
```

---

## Task 3: Memory tool handlers

**Files:**
- Modify: `launcher/zipsa/core/hitl_mcp.py`
- Modify: `launcher/tests/test_hitl_mcp.py`

- [ ] **Step 1: Add failing tests**

Append to `launcher/tests/test_hitl_mcp.py`:

```python
from zipsa.core.memory_store import MemoryStore
from zipsa.core.hitl_mcp import (
    RecallHandler, RememberHandler, ForgetHandler, ListMemoryHandler,
)


def _store_pair(tmp_path):
    return (
        MemoryStore(tmp_path / "skill.json"),
        MemoryStore(tmp_path / "global.json"),
    )


class TestRecall:
    def test_skill_scope_default(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        skill.set("k", "skill-value")
        global_.set("k", "global-value")
        h = RecallHandler(skill, global_)
        assert h.run(key="k") == "skill-value"

    def test_global_scope_explicit(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        global_.set("lang", "ko")
        h = RecallHandler(skill, global_)
        assert h.run(key="lang", scope="global") == "ko"

    def test_missing_returns_none(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        h = RecallHandler(skill, global_)
        assert h.run(key="absent") is None

    def test_invalid_scope_raises(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        h = RecallHandler(skill, global_)
        with pytest.raises(ValueError, match="scope"):
            h.run(key="k", scope="bogus")


class TestRemember:
    def test_skill_scope_default(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        h = RememberHandler(skill, global_)
        h.run(key="k", value="v")
        assert skill.get("k") == "v"
        assert global_.get("k") is None

    def test_global_scope(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        h = RememberHandler(skill, global_)
        h.run(key="lang", value="ko", scope="global")
        assert global_.get("lang") == "ko"
        assert skill.get("lang") is None

    def test_invalid_scope_raises(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        h = RememberHandler(skill, global_)
        with pytest.raises(ValueError, match="scope"):
            h.run(key="k", value="v", scope="bogus")


class TestForget:
    def test_removes_existing_returns_true(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        skill.set("k", "v")
        h = ForgetHandler(skill, global_)
        assert h.run(key="k") is True
        assert skill.get("k") is None

    def test_missing_returns_false(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        h = ForgetHandler(skill, global_)
        assert h.run(key="never") is False

    def test_global_scope(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        global_.set("k", "v")
        h = ForgetHandler(skill, global_)
        assert h.run(key="k", scope="global") is True
        assert global_.get("k") is None

    def test_invalid_scope_raises(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        h = ForgetHandler(skill, global_)
        with pytest.raises(ValueError, match="scope"):
            h.run(key="k", scope="bogus")


class TestListMemory:
    def test_skill_scope_default(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        skill.set("a", 1)
        skill.set("b", 2)
        global_.set("g", 3)
        h = ListMemoryHandler(skill, global_)
        assert sorted(h.run()) == ["a", "b"]

    def test_global_scope(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        global_.set("x", 1)
        global_.set("y", 2)
        h = ListMemoryHandler(skill, global_)
        assert sorted(h.run(scope="global")) == ["x", "y"]

    def test_invalid_scope_raises(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        h = ListMemoryHandler(skill, global_)
        with pytest.raises(ValueError, match="scope"):
            h.run(scope="bogus")
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/test_hitl_mcp.py::TestRecall tests/test_hitl_mcp.py::TestRemember tests/test_hitl_mcp.py::TestForget tests/test_hitl_mcp.py::TestListMemory -q 2>&1 | tail -5
```

Expected: collection error (`cannot import name 'RecallHandler' ...`).

- [ ] **Step 3: Implement the four handlers**

Append to `launcher/zipsa/core/hitl_mcp.py`:

```python
from typing import Any

from .memory_store import MemoryStore


_VALID_SCOPES = ("skill", "global")


def _pick_store(scope: str, skill: MemoryStore, global_: MemoryStore) -> MemoryStore:
    if scope == "skill":
        return skill
    if scope == "global":
        return global_
    raise ValueError(f"scope must be one of {_VALID_SCOPES!r}, got {scope!r}")


class RecallHandler:
    def __init__(self, skill_store: MemoryStore, global_store: MemoryStore) -> None:
        self._skill = skill_store
        self._global = global_store

    def run(self, key: str, scope: str = "skill") -> Any | None:
        return _pick_store(scope, self._skill, self._global).get(key)


class RememberHandler:
    def __init__(self, skill_store: MemoryStore, global_store: MemoryStore) -> None:
        self._skill = skill_store
        self._global = global_store

    def run(self, key: str, value: Any, scope: str = "skill") -> None:
        _pick_store(scope, self._skill, self._global).set(key, value)


class ForgetHandler:
    def __init__(self, skill_store: MemoryStore, global_store: MemoryStore) -> None:
        self._skill = skill_store
        self._global = global_store

    def run(self, key: str, scope: str = "skill") -> bool:
        return _pick_store(scope, self._skill, self._global).delete(key)


class ListMemoryHandler:
    def __init__(self, skill_store: MemoryStore, global_store: MemoryStore) -> None:
        self._skill = skill_store
        self._global = global_store

    def run(self, scope: str = "skill") -> list[str]:
        return _pick_store(scope, self._skill, self._global).keys()
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/test_hitl_mcp.py -q 2>&1 | tail -3
```

Expected: all tests pass (existing HITL tests + the new memory tests).

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/core/hitl_mcp.py launcher/tests/test_hitl_mcp.py
git commit -m "feat(memory): add Recall/Remember/Forget/ListMemory handlers"
```

---

## Task 4: Wire memory tools into HitlServer

**Files:**
- Modify: `launcher/zipsa/core/hitl_runner.py`
- Modify: `launcher/tests/test_hitl_runner.py`

- [ ] **Step 1: Add failing test**

Append to `launcher/tests/test_hitl_runner.py`:

```python
class TestMemoryToolsWired:
    """End-to-end: HitlServer exposes memory tools over HTTP MCP."""

    def test_remember_then_recall_via_http(self, tmp_path):
        import io as _io
        import httpx
        import json
        from zipsa.core.memory_store import MemoryStore

        io_ = HitlIO(
            stdin=_io.StringIO(""),
            stdout=_io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        skill = MemoryStore(tmp_path / "skill.json")
        global_ = MemoryStore(tmp_path / "global.json")
        server = HitlServer(io_, skill_store=skill, global_store=global_)
        server.start()
        try:
            url = f"http://127.0.0.1:{server.port}/mcp"
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {server.token}",
            }
            # initialize
            init = {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26",
                           "capabilities": {},
                           "clientInfo": {"name": "test", "version": "0"}},
            }
            r = httpx.post(url, json=init, headers=headers, timeout=5.0)
            assert r.status_code == 200
            session_id = r.headers["mcp-session-id"]
            session_headers = {**headers, "mcp-session-id": session_id}

            httpx.post(url, json={
                "jsonrpc": "2.0", "method": "notifications/initialized",
            }, headers=session_headers, timeout=5.0)

            # remember
            call = {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "remember",
                           "arguments": {"key": "workspace", "value": "WBrk HQ"}},
            }
            r = httpx.post(url, json=call, headers=session_headers, timeout=5.0)
            assert r.status_code == 200
            # recall
            call = {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "recall", "arguments": {"key": "workspace"}},
            }
            r = httpx.post(url, json=call, headers=session_headers, timeout=5.0)
            assert r.status_code == 200
            body = r.text
            if "data:" in body:
                for line in body.splitlines():
                    if line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                        break
            else:
                data = r.json()
            text = data["result"]["content"][0]["text"]
            # MCP serializes returned strings as JSON in text content
            assert "WBrk HQ" in text
        finally:
            server.stop()
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest tests/test_hitl_runner.py::TestMemoryToolsWired -q 2>&1 | tail -8
```

Expected: TypeError or signature mismatch (`HitlServer.__init__()` got an unexpected keyword argument `skill_store`).

- [ ] **Step 3: Update `HitlServer` signature + tool registration**

In `launcher/zipsa/core/hitl_runner.py`, modify `HitlServer.__init__` to accept the two stores, and `start()` to register the four new tools.

Change `__init__`:

```python
class HitlServer:
    """HTTP MCP server (FastMCP) bound to 127.0.0.1:<random-port>."""

    def __init__(
        self,
        io_: HitlIO,
        skill_store: "MemoryStore | None" = None,
        global_store: "MemoryStore | None" = None,
    ) -> None:
        self._io = io_
        self._skill_store = skill_store
        self._global_store = global_store
        self.port: int = 0
        self.token: str = ""
        self._thread: Optional[threading.Thread] = None
        self._uvicorn_server: Optional[uvicorn.Server] = None
```

Inside `start()`, right after the existing Ask/Confirm/Choose tools are registered (and before `app = mcp.streamable_http_app()`), add:

```python
        from .hitl_mcp import (
            RecallHandler, RememberHandler, ForgetHandler, ListMemoryHandler,
        )

        if self._skill_store is not None and self._global_store is not None:
            recall_h = RecallHandler(self._skill_store, self._global_store)
            remember_h = RememberHandler(self._skill_store, self._global_store)
            forget_h = ForgetHandler(self._skill_store, self._global_store)
            list_h = ListMemoryHandler(self._skill_store, self._global_store)

            @mcp.tool()
            def recall(key: str, scope: str = "skill") -> str | None:
                """Read a value previously stored via remember.

                Returns null if the key is not set in the given scope.
                Scope: "skill" (default, per-skill private) or "global"
                (shared across all skills).
                """
                value = recall_h.run(key=key, scope=scope)
                # Normalize non-string values to JSON for MCP text content
                if value is None or isinstance(value, str):
                    return value
                import json as _json
                return _json.dumps(value, ensure_ascii=False)

            @mcp.tool()
            def remember(key: str, value: str, scope: str = "skill") -> None:
                """Store a value for future runs of this (or any) skill.

                Scope: "skill" (default, per-skill private) or "global"
                (shared across all skills).
                """
                remember_h.run(key=key, value=value, scope=scope)

            @mcp.tool()
            def forget(key: str, scope: str = "skill") -> bool:
                """Delete a stored value. Returns true if removed, false if missing."""
                return forget_h.run(key=key, scope=scope)

            @mcp.tool()
            def list_memory(scope: str = "skill") -> list[str]:
                """List keys in the chosen scope."""
                return list_h.run(scope=scope)
```

Add the missing import at the top of `hitl_runner.py`:

```python
from .memory_store import MemoryStore  # noqa: F401  (for type hints in __init__)
```

- [ ] **Step 4: Run to verify pass**

```bash
uv run pytest tests/test_hitl_runner.py -q 2>&1 | tail -3
```

Expected: all hitl_runner tests pass, including the new `TestMemoryToolsWired`.

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/core/hitl_runner.py launcher/tests/test_hitl_runner.py
git commit -m "feat(memory): wire recall/remember/forget/list_memory into HitlServer"
```

---

## Task 5: Executor integration

**Files:**
- Modify: `launcher/zipsa/core/executor.py`
- Modify: `launcher/tests/test_executor.py`

- [ ] **Step 1: Add failing tests**

Append to `launcher/tests/test_executor.py`:

```python
class TestMemoryIntegration:
    """Executor wires per-skill and global MemoryStores into HitlServer and
    adds the four memory tools to the default phase allow list."""

    def test_default_allow_list_contains_memory_tools(self, tmp_path):
        import json
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)
        executor._write_default_phase_allow_file(tmp_path, skill)
        data = json.loads((tmp_path / "phase-allow.json").read_text())
        assert "mcp__zipsa__recall" in data["allowed_tools"]
        assert "mcp__zipsa__remember" in data["allowed_tools"]
        assert "mcp__zipsa__forget" in data["allowed_tools"]
        assert "mcp__zipsa__list_memory" in data["allowed_tools"]

    def test_phase_allow_file_appends_memory_tools(self, tmp_path):
        import json
        executor = DockerExecutor()
        executor._write_phase_allow_file(tmp_path, "precheck", ["WebFetch"])
        data = json.loads((tmp_path / "phase-allow.json").read_text())
        # Existing tool stays, memory tools added
        assert "WebFetch" in data["allowed_tools"]
        assert "mcp__zipsa__recall" in data["allowed_tools"]
        assert "mcp__zipsa__remember" in data["allowed_tools"]
        assert "mcp__zipsa__forget" in data["allowed_tools"]
        assert "mcp__zipsa__list_memory" in data["allowed_tools"]
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest tests/test_executor.py::TestMemoryIntegration -q 2>&1 | tail -5
```

Expected: 2 failed (memory tool names not in allow list).

- [ ] **Step 3: Update default allow list**

Find `_write_default_phase_allow_file` in `launcher/zipsa/core/executor.py`. It currently has a line like:

```python
        tools.extend(["mcp__zipsa__ask", "mcp__zipsa__confirm", "mcp__zipsa__choose"])
```

Replace with:

```python
        tools.extend([
            "mcp__zipsa__ask", "mcp__zipsa__confirm", "mcp__zipsa__choose",
            "mcp__zipsa__recall", "mcp__zipsa__remember",
            "mcp__zipsa__forget", "mcp__zipsa__list_memory",
        ])
```

Find `_write_phase_allow_file`. It has a line like:

```python
        allowed_tools = list(allowed_tools) + [
            "mcp__zipsa__ask", "mcp__zipsa__confirm", "mcp__zipsa__choose",
        ]
```

Replace with:

```python
        allowed_tools = list(allowed_tools) + [
            "mcp__zipsa__ask", "mcp__zipsa__confirm", "mcp__zipsa__choose",
            "mcp__zipsa__recall", "mcp__zipsa__remember",
            "mcp__zipsa__forget", "mcp__zipsa__list_memory",
        ]
```

- [ ] **Step 4: Wire MemoryStores into HitlServer instantiation**

Search `executor.py` for `HitlServer(` constructor calls. There should be one (or a couple) inside `_execute_with_hitl` or similar. At the top of that method, before constructing `HitlServer`, compute the two paths:

```python
            from .memory_store import MemoryStore
            skill_memory_path = (
                zipsa_paths.skill_data_dir(skill.name, skill.manifest.metadata.version)
                / "memory" / "skill-mem.json"
            )
            global_memory_path = zipsa_paths.zipsa_home() / "memory" / "global-mem.json"
            skill_store = MemoryStore(skill_memory_path)
            global_store = MemoryStore(global_memory_path)
```

Then pass both to `HitlServer(...)`:

```python
            hitl_server = HitlServer(
                hitl_io,
                skill_store=skill_store,
                global_store=global_store,
            )
```

- [ ] **Step 5: Run to verify**

```bash
uv run pytest tests/test_executor.py -q 2>&1 | tail -3
```

Expected: all executor tests pass (the two new + all existing).

- [ ] **Step 6: Full suite sanity check**

```bash
uv run pytest -q 2>&1 | tail -3
```

Expected: all pass with no regressions.

- [ ] **Step 7: Commit**

```bash
git add launcher/zipsa/core/executor.py launcher/tests/test_executor.py
git commit -m "feat(executor): wire MemoryStores into HitlServer, allow memory tools"
```

---

## Task 6: runtime-contract.md Memory section

**Files:**
- Modify: `launcher/zipsa/system-prompts/runtime-contract.md`

- [ ] **Step 1: Locate insertion point**

```bash
grep -n "^## " launcher/zipsa/system-prompts/runtime-contract.md
```

The new section goes right after `## Asking the user` (and before `## State management`).

- [ ] **Step 2: Insert new section**

Add this block after the `## Asking the user` block and before `## State management`:

```markdown
## Memory

You have a persistent key/value store with two scopes, always
available (no need to declare):

- `mcp__zipsa__recall({key, scope?: "skill"|"global"})` → value | null
- `mcp__zipsa__remember({key, value, scope?: "skill"|"global"})` → void
- `mcp__zipsa__forget({key, scope?})` → bool
- `mcp__zipsa__list_memory({scope?})` → list[string]

Default scope is `"skill"` — visible only to this skill. Use
`"global"` only for facts that apply to the user across all skills
(e.g. preferred language, name).

When you would otherwise ask the user the same thing repeatedly
(workspace name, db name, default values), follow this pattern:

1. `mcp__zipsa__recall({key})` first
2. If null → `mcp__zipsa__ask` the user
3. `mcp__zipsa__remember({key, value: answer})`
4. Proceed

Keep keys descriptive and stable across runs (e.g.
`notion_workspace`, not `ws1`). Values must be JSON-serializable
(string / number / list / object).
```

- [ ] **Step 3: Commit**

```bash
git add launcher/zipsa/system-prompts/runtime-contract.md
git commit -m "docs(contract): add Memory section for recall/remember/forget/list_memory"
```

---

## Task 7: End-to-end manual verification + PR

**Files:** none modified

- [ ] **Step 1: Run hello-world to confirm no regressions**

```bash
cd /Users/neochoon/WestbrookAI/zipsa/.worktrees/feat-memory/launcher
uv run zipsa run hello-world "say hi" -i ghcr.io/westbrookai/zipsa-runtime:0.4.6 2>&1 | tail -10
```

Expected: standard hello-world success output. New memory tools are
available but unused — no behavior change.

- [ ] **Step 2: Manual smoke test of memory via shell mode**

```bash
uv run zipsa run hello-world "say hi" --shell -i ghcr.io/westbrookai/zipsa-runtime:0.4.6
```

Inside the container shell:

```bash
# Hit the MCP server directly with curl to verify recall/remember work
TOKEN=$(grep ZIPSA_HITL_TOKEN /home/agent/.env-like-file 2>/dev/null || echo "see env")
# (If hard to capture token in shell, skip this step — the next step
#  is the real verification via an actual skill run.)
exit
```

If the shell-mode probe is awkward, defer verification to Step 3.

- [ ] **Step 3: Verify memory files appear after a real run**

```bash
ls ~/.zipsa/hello-world@0.1.0/memory/skill-mem.json 2>&1 || echo "no per-skill memory file (expected — agent didn't write)"
ls ~/.zipsa/memory/global-mem.json 2>&1 || echo "no global memory file (expected)"
```

Expected: both files absent (no agent has written yet). The
infrastructure is wired but unused — correct for v1.

- [ ] **Step 4: Full test suite as final check**

```bash
uv run pytest -q 2>&1 | tail -3
```

Expected: all pass.

- [ ] **Step 5: Push branch**

```bash
git push -u origin feat/memory
```

- [ ] **Step 6: Open PR**

```bash
gh pr create --base main --head feat/memory \
  --title "feat: per-skill and global KV memory (recall/remember/forget/list_memory)" \
  --body "$(cat <<'EOF'
## Summary
Adds four memory tools to the existing zipsa MCP server, always
available to every skill:

- \`mcp__zipsa__recall({key, scope?})\` → value | null
- \`mcp__zipsa__remember({key, value, scope?})\` → void
- \`mcp__zipsa__forget({key, scope?})\` → bool
- \`mcp__zipsa__list_memory({scope?})\` → list[string]

\`scope\` is \"skill\" (default, per-skill private) or \"global\"
(shared across all skills).

## Storage
- per-skill: \`~/.zipsa/<skill>@<version>/memory/skill-mem.json\` (rw, 0600)
- global: \`~/.zipsa/memory/global-mem.json\` (rw, 0600)

JSON dict files, created on first write. Values must be
JSON-serializable.

## Spec & plan
- Spec: \`docs/superpowers/specs/2026-05-18-zipsa-memory-design.md\`
- Plan: \`docs/superpowers/plans/2026-05-18-zipsa-memory.md\`

## Changes
- New: \`launcher/zipsa/core/memory_store.py\` (\`MemoryStore\` class)
- Modified: \`hitl_mcp.py\` (4 handler classes), \`hitl_runner.py\`
  (wires stores into FastMCP), \`executor.py\` (paths + default
  allow-list), \`runtime-contract.md\` (Memory section)
- No manifest changes. No runtime image changes.

## Test plan
- [x] \`uv run pytest\` — passing
- [x] hello-world run end-to-end (no regression)
- [ ] daily-progress / weather still pass on reference runs

## Follow-up (out of scope)
- \`daily-progress\` migration: move hardcoded workspace_name /
  db_name / timezone from manifest \`config\` to recall+ask+remember.
  Separate PR.
EOF
)"
```

Return the PR URL.

---

## Self-review

**Spec coverage check:**

| Spec section | Implemented in |
|---|---|
| `MemoryStore` JSON file backend | Task 2 |
| 4 handler classes (recall/remember/forget/list) | Task 3 |
| Scope routing (skill vs global) | Task 3 (`_pick_store`) |
| `HitlServer` wires stores + FastMCP tool registration | Task 4 |
| Per-skill file path under `~/.zipsa/<skill>@<ver>/memory/skill-mem.json` | Task 5 |
| Global file path under `~/.zipsa/memory/global-mem.json` | Task 5 |
| Default allow list extension (both write functions) | Task 5 |
| `runtime-contract.md` Memory section | Task 6 |
| End-to-end verification | Task 7 |
| 0600 perms, parent dir auto-creation | Task 2 (impl + test) |
| Live re-read on every get (no stale cache) | Task 2 (test_two_stores_share_file) |
| Corrupt JSON raises | Task 2 (test_get_with_corrupt_json_raises) |
| Invalid scope raises ValueError | Task 3 (all 4 handler tests) |
| Per-skill scope isolation | Task 3 (TestRemember.test_skill_scope_default verifies write goes to skill only) |

All spec items have tasks.

**Type consistency:**

- `MemoryStore(path: Path)` consistent across tasks 2-5.
- `RecallHandler(skill_store, global_store)`, `RememberHandler(skill_store, global_store, value: Any)` etc. consistent.
- `HitlServer.__init__(io_, skill_store=None, global_store=None)` consistent across task 4 (definition) and task 5 (caller).
- Tool names (`mcp__zipsa__recall` etc.) consistent across tasks 5, 6, 7.

No mismatches.

**Placeholder check:** All steps contain full code or exact commands. No "TBD" / "similar to" / "add validation" patterns.
