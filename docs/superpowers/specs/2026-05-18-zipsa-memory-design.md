# Zipsa Memory (KV) — Design

**Date:** 2026-05-18
**Status:** Draft — pending user approval
**Scope:** Add four memory tools (recall / remember / forget / list_memory) to the existing zipsa MCP server, with two scopes (per-skill, global), JSON-file backed, always available to every skill.

## Context

Skills currently have no way to persist user-supplied facts (e.g. Notion
workspace name, db name, timezone) across runs. The result is hardcoding
in manifests (e.g. `daily-progress` config) or asking the user the same
question every run.

With HITL (PR #15) shipped, agents can ask the user, but answers are
forgotten when the container exits. This spec adds the matching half:
persistent memory so an answer asked once is reused on subsequent runs.

## Goals

- Persist arbitrary key/value pairs per skill (default) and globally
  (cross-skill).
- Always available — every skill gets the tools by default, like HITL.
- Zero new manifest fields. Zero skill-author work.
- No external services, no extra binaries in the runtime image, no
  network calls.
- Implementation budget: 1-2 days.

## Non-goals

- **Semantic search** (find by similarity). Exact key lookup only. If
  the user wants this later, an opt-in mem0 integration can layer on
  top without changing the KV tools.
- **Knowledge graph** (entities, relations, observations). The
  knowledge-graph MCP server was considered and rejected as overspec
  for current scale.
- **mem0 / Letta / Zep integration**. Optional in a follow-up.
- **Schema validation** of values. Values are arbitrary JSON.
- **Encryption at rest**. Stored as plaintext JSON in the user's home
  directory (same trust level as existing state.json, .env, OAuth
  tokens in `~/.zipsa/credentials/`).
- **Concurrent-write conflict resolution**. Single-user, single-run
  assumption.

## Architecture

The existing zipsa MCP server (built for HITL) gains four new tools:

```
┌─ Host ────────────────────────────────────────────────────────┐
│  ~/.zipsa/<skill>@<ver>/memory/skill-mem.json  (per-skill, rw)│
│  ~/.zipsa/memory/global-mem.json               (global, rw)   │
│                                                               │
│  Launcher (existing HitlServer): adds memory handlers         │
│   - recall(key, scope="skill")                                │
│   - remember(key, value, scope="skill")                       │
│   - forget(key, scope="skill")                                │
│   - list_memory(scope="skill")                                │
└───────────────────────────────────────────────────────────────┘
              ↑
              │ MCP over HTTP (host.docker.internal)
              │ + Bearer token (existing)
              │
┌─ Container ───────────────────────────────────────────────────┐
│  claude (MCP client)                                          │
│   - mcp__zipsa__ask / confirm / choose         (existing HITL)│
│   - mcp__zipsa__recall / remember / forget / list_memory (NEW)│
└───────────────────────────────────────────────────────────────┘
```

Same transport (TCP + Bearer), same per-run lifecycle, same
PreToolUse hook allowance list. We are extending the zipsa MCP
server, not adding a new one.

## Components

### 1. Memory store (`launcher/zipsa/core/memory_store.py`, new)

A small file-backed dict-of-dicts with a path → in-memory cache. One
instance per (scope, skill) pair.

```python
class MemoryStore:
    """JSON file-backed key/value store.

    Reads the file on each .get/.set (no in-memory cache — keeps
    behavior predictable when other processes / launcher reruns
    update the same file). File created with {} if missing.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any) -> None: ...
    def delete(self, key: str) -> None: ...
    def keys(self) -> list[str]: ...
```

Values are arbitrary JSON-serializable (str, int, list, dict, …). File
created with `0600` permissions. Parent directory created on first
write.

### 2. Memory tool handlers (`launcher/zipsa/core/hitl_mcp.py`, modified)

Add four handler classes alongside the existing Ask/Confirm/Choose:

```python
class RecallHandler:
    def __init__(self, skill_store: MemoryStore, global_store: MemoryStore): ...
    def run(self, key: str, scope: str = "skill") -> str | None: ...

class RememberHandler:
    def __init__(self, skill_store, global_store): ...
    def run(self, key: str, value: str, scope: str = "skill") -> None: ...

class ForgetHandler:
    def __init__(self, skill_store, global_store): ...
    def run(self, key: str, scope: str = "skill") -> bool: ...

class ListMemoryHandler:
    def __init__(self, skill_store, global_store): ...
    def run(self, scope: str = "skill") -> list[str]: ...
```

`scope` is validated to be `"skill"` or `"global"` — anything else
returns an error.

### 3. HitlServer wiring (`launcher/zipsa/core/hitl_runner.py`, modified)

`HitlServer.__init__` accepts two extra parameters: `skill_store_path`
and `global_store_path`. Inside `start()`, after the ask/confirm/choose
tools are registered, four more `@mcp.tool()` decorated functions are
registered that delegate to the four handlers.

```python
@mcp.tool()
def recall(key: str, scope: str = "skill") -> str | None: ...

@mcp.tool()
def remember(key: str, value: str, scope: str = "skill") -> None: ...

# etc.
```

### 4. Executor integration (`launcher/zipsa/core/executor.py`, modified)

Where the executor currently creates the `HitlServer`, it also computes
the two paths:

```python
skill_memory_path = zipsa_paths.skill_data_dir(skill.name, version) / "memory" / "skill-mem.json"
global_memory_path = zipsa_home() / "memory" / "global.json"
hitl_server = HitlServer(hitl_io,
                         skill_store=MemoryStore(skill_memory_path),
                         global_store=MemoryStore(global_memory_path))
```

And the default allow list (used by `_write_phase_allow_file` and
`_write_default_phase_allow_file`) gains four entries:

```python
"mcp__zipsa__recall",
"mcp__zipsa__remember",
"mcp__zipsa__forget",
"mcp__zipsa__list_memory",
```

### 5. runtime-contract.md (modified)

Insert a "Memory" section after "Asking the user":

```markdown
## Memory

You have a persistent key/value store with two scopes, always
available:

- `mcp__zipsa__recall({key, scope?: "skill"|"global"})` → value | null
- `mcp__zipsa__remember({key, value, scope?: "skill"|"global"})` → void
- `mcp__zipsa__forget({key, scope?})` → bool
- `mcp__zipsa__list_memory({scope?})` → list[string]

Default scope is `"skill"` — visible only to this skill.
Use `"global"` only for facts that apply to the user across all
skills (e.g. preferred language, name).

When you would otherwise ask the user the same thing repeatedly
(workspace name, db name, default values), follow this pattern:

1. `mcp__zipsa__recall({key})` first
2. If null → `mcp__zipsa__ask` the user
3. Store the answer with `mcp__zipsa__remember({key, value})`
4. Proceed

Keep keys descriptive and stable across runs (e.g. `notion_workspace`,
not `ws1`). Values must be JSON-serializable (string / number / list /
object).
```

## Data flow — concrete example

`daily-progress` precheck phase asks for the Notion workspace name on
first run, and reuses it on every subsequent run.

1. Phase starts.
2. Agent: `mcp__zipsa__recall({key: "notion_workspace"})`
   - File `~/.zipsa/daily-progress@0.3.1/memory/skill-mem.json` doesn't exist yet.
   - MemoryStore returns `None`.
3. Agent: `mcp__zipsa__ask({prompt: "어느 Notion workspace를 사용?"})`
4. User types `Westbrook AI HQ`.
5. Agent: `mcp__zipsa__remember({key: "notion_workspace", value: "Westbrook AI HQ"})`
   - File created at `~/.zipsa/daily-progress@0.3.1/memory/skill-mem.json`
     with `{"notion_workspace": "Westbrook AI HQ"}`.
6. Agent proceeds with `notion-search` etc.

Next day's run:
1. Phase starts.
2. Agent: `mcp__zipsa__recall({key: "notion_workspace"})` → `"Westbrook AI HQ"`.
3. Agent proceeds immediately.

## File layout

```
~/.zipsa/
├── memory/
│   └── global-mem.json                      (global, all skills)
├── daily-progress@0.3.1/
│   ├── .env
│   ├── .claude.json
│   ├── settings.json
│   ├── state.json                           (existing — launcher-managed)
│   ├── memory/
│   │   └── skill-mem.json                   (NEW — agent-managed)
│   └── runs/...
└── ...
```

The `*-mem.json` files are agent-managed memory. They live inside a
`memory/` subdirectory so the name carries its identity even when a
file is copied or moved, and so future memory-related siblings (backups,
indexes, audit logs) have a natural home.

`memory/skill-mem.json` and `state.json` are deliberately separate files:

- `state.json`: launcher-managed, set via the skill output's
  `state_updates` JSON field. Mutated only by the launcher.
- `memory/skill-mem.json`: agent-managed, mutated only by the MCP tools.

Same data domain, different mutation paths. Keeping them split
prevents race conditions and conceptual confusion.

## Concurrency

- File reads on every recall — predictable, no cache invalidation
  worries.
- File writes are read-modify-write within a single process (the
  launcher); MCP tool calls are serialized through the existing HITL
  thread / lock. No multi-process write concurrency expected (one
  zipsa run at a time per skill).
- Two simultaneous `zipsa run <same-skill>` invocations: undefined.
  v1 doesn't lock the file. Documented as known limitation.

## Tests

### Unit (no MCP, mock files)

- `MemoryStore.get` returns `None` when key absent
- `MemoryStore.set` then `.get` round-trip preserves types
  (str / int / list / dict)
- `MemoryStore.set` creates the file with 0600 if missing
- `MemoryStore.set` creates parent dir if missing
- `MemoryStore.delete` removes a key; returns `False` if key absent
- `MemoryStore.keys()` returns the current keys
- Handlers route to the correct store based on `scope`
- Invalid `scope` raises `ValueError`

### Integration (real MCP HTTP)

- End-to-end: connect to HitlServer with both scopes,
  remember → recall → list → forget cycle.
- Memory persists across HitlServer restart (same path).
- Per-skill scopes are isolated: writes to skill A's store invisible
  to skill B's store.

## Error handling

- Corrupt JSON in memory file: server returns a structured error
  on the failing tool call; agent receives `isError: true`. The file
  itself is left untouched (not auto-rewritten with empty `{}` —
  that would silently destroy data).
- Permission denied (read or write): same — return error to agent.
- Disk full on write: error returned, agent can retry.

In all error cases the rest of the run continues — memory failure
shouldn't abort an in-flight skill.

## Backwards compatibility

- No manifest changes.
- No runtime image changes.
- Existing skills continue to work unchanged. They get the new tools
  in the default allow list but only use them if the agent chooses to.
- The four new tool names (`mcp__zipsa__recall`, `remember`, `forget`,
  `list_memory`) are added to `_write_default_phase_allow_file` and
  `_write_phase_allow_file` alongside the existing HITL tools.

## Implementation budget

| Chunk | Days |
|---|---|
| `memory_store.py` (`MemoryStore`) + tests | 0.5 |
| Handlers in `hitl_mcp.py` + tests | 0.5 |
| Wiring in `hitl_runner.py` + integration tests | 0.25 |
| Executor: paths + allow list + tests | 0.25 |
| runtime-contract.md "Memory" section | 0.25 |
| End-to-end manual verification (daily-progress dry run) | 0.25 |

**Total: 2 days focused work.**

## Success criteria

- A new skill that calls `recall → ask → remember` resolves a value
  once and reuses it on every subsequent run without re-prompting.
- The daily-progress skill (after a separate follow-up that adopts
  this pattern) stops asking for workspace_name / db_name after the
  first run.
- Existing skills (weather, hello-world, daily-progress-0.3.1)
  continue to pass their reference runs.
- Test suite count grows by ~15-20 (the new tests above) with zero
  regressions in existing tests.

## Out of scope (deferred follow-ups)

- **daily-progress migration** — moves config (workspace_name,
  db_name, timezone) from manifest hardcoding to memory recall.
  Separate PR after this one.
- **mem0 integration** — optional opt-in for semantic memory. Not in
  v1; can be added as an *additional* MCP server later without
  changing the KV tools.
- **Memory inspection CLI** — `zipsa memory list <skill>`,
  `zipsa memory get <skill> <key>`, `zipsa memory rm <skill> <key>`
  for the user to introspect / edit / reset memory from the host.
  Nice-to-have; not required for v1 functionality.
- **TTL / expiry**.
- **History / audit log** (who set what when).
- **Encryption at rest**.
