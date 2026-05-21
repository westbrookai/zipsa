# Skill Composition (Atomic + Orchestrator) + Log-Mediated Data Sharing — Design

**Date:** 2026-05-21
**Status:** Approved (plan-mode 2026-05-21)
**Scope:** Refactor zipsa's skill model from "monolithic skills do everything" to "atomic skills + orchestrator skills". Add the infrastructure they need: artifact storage in run_dir + MCP `get_artifact` + MCP `run_skill`. Build first orchestrator (morning-ritual) using daily-progress / bip-daily-x worth of functionality, decomposed.

---

## Context

Morning automation (daily-progress + bip-daily-x) shipped today (2026-05-20). Both skills run agenthud independently — same call twice per morning, ~$0.10 of duplicate cost plus minutes of duplicate latency.

Trying to fix the duplication surfaced a deeper architectural smell. The current skills bundle four distinct responsibilities each:

- **Data fetching** (agenthud)
- **Presentation** (per-project Notion structure / tweet text)
- **Persistence** (Notion API / X API)
- **User UX** (`ask_once` for `voice` / `notion_workspace`, HITL review loop)

Bundling these means `voice` is locked inside bip-daily-x — a future tweet-monthly-recap couldn't share it. agenthud invocation is copy-pasted between two skills' SKILL.md. Updating agenthud means touching two files.

**Better shape** (Unix philosophy):

- **Atomic skills**: single responsibility, no user UX, no `ask_once`, pure I/O. One skill = one verb. Doesn't know about other domains. (`agenthud-report` doesn't know what Notion is; `notion-page-write` doesn't know what agenthud is.)
- **Orchestrator skills**: compose atomic skills, own all user-facing flow (`ask_once`, HITL review, deciding when to skip a sub-step).

This spec lands the foundational infrastructure (run_skill + get_artifact MCP, artifact convention in run_dir) plus the first decomposition (3 atomics + 3 orchestrators) plus the first top-level orchestrator (morning-ritual) that benefits from agenthud cache sharing.

---

## Design Principles

1. **Invocation via MCP, not bash.** Parent agent calls children via a new `mcp__zipsa__run_skill` tool. The MCP server runs on the host (already true for HITL servers), so child invocations happen on host without exposing docker socket or installing zipsa CLI inside the container.

2. **Data exchange via logs, not direct results.** Child writes structured artifacts to its `run_dir/artifacts/`. Parent reads them through MCP `get_artifact`. The existing `runs/<timestamp>/` directory becomes the source of truth for cross-skill data. If logs don't capture something needed, fix the logs — one place to change.

3. **Atomic ≠ Orchestrator (enforced).** Atomic skills cannot call `run_skill`. Manifest validation rejects an atomic that declares `spec.children`. Orchestrators are the only kind that compose.

4. **Existing skills stay.** daily-progress + bip-daily-x continue working through deprecation period. New atomic + orchestrator skills get built alongside. Migration is "build the new shape, prove it, deprecate the old."

---

## Architecture

```
┌─ Host (launcher Python process) ─────────────────────────────────┐
│                                                                   │
│  Parent docker container (orchestrator)                          │
│   │                                                               │
│   ↓ HTTP MCP                                                      │
│  HitlServer                                                       │
│   ├─ ask_once / ask / confirm / choose  (existing)               │
│   ├─ run_skill(name, args)              (NEW Phase 2)            │
│   └─ get_artifact(skill, run_id, name)  (NEW Phase 1)            │
│         │                                                         │
│         ↓ subprocess                                              │
│   uv run zipsa run <child> "<args>"                              │
│         │                                                         │
│         ↓                                                         │
│   Child docker container (atomic or orchestrator)                │
│         │ writes artifacts to /home/agent/runs/current/artifacts │
│         ↓                                                         │
│   ~/.zipsa/<child>@<ver>/runs/<timestamp>/                       │
│      ├─ summary.json                                              │
│      ├─ phases/<n>-<id>/output.jsonl                             │
│      └─ artifacts/                       (NEW Phase 1)           │
│         └─ agenthud-report.json                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Phased Implementation

### Phase 1 — Artifact convention + MCP `get_artifact`

**Goal:** A skill can save arbitrary files to its run_dir under `artifacts/`. Other processes (orchestrator agents, future tools) read them via MCP.

**Changes:**

- `launcher/zipsa/core/executor.py:1145-1300` — Container mount: `~/.zipsa/<skill>@<ver>/runs/<timestamp>/` becomes read-write at `/home/agent/runs/current/` inside the container. Skill writes to `/home/agent/runs/current/artifacts/<name>`.
- `launcher/zipsa/paths.py` — Add `skill_run_artifacts_dir(name, version, run_id) -> Path`.
- `launcher/zipsa/core/hitl_runner.py` (extend) or new `launcher/zipsa/core/artifact_mcp.py` — MCP tool `mcp__zipsa__get_artifact(skill: str, run_id: str, name: str)` returns file content (string for JSON/text; base64 for binary).

**Verification:** Modified `hello-world` fixture writes a dummy artifact; manual MCP fixture-test confirms `get_artifact("hello-world", "<run_id>", "<name>")` returns the content.

### Phase 2 — MCP `run_skill`

**Goal:** Parent agent calls children via a regular MCP tool.

**Implementation choice — subprocess vs in-process:**

| Aspect | Model 1: Subprocess (`uv run zipsa run X`) | Model 2: In-process (`DockerExecutor().run()`) |
|---|---|---|
| Code change | MCP handler: one `subprocess.run(...)` line | Refactor launcher to library-mode + state-namespace isolation |
| Per-call cost | ~500-700ms (uv + python + imports) | ~100ms (docker spawn only) |
| Memory at depth 5 | ~3.6GB (6 Python processes) | ~1.6GB (1 Python + 5 docker) |
| Failure isolation | Process boundary | Same Python — careful exception handling |
| Concurrency | Multi-process | Multi-threaded |
| Code path consistency | Identical to existing `zipsa run` path | New entry point = new bug surface |

**Choice: Model 1 (subprocess).** Morning ritual is once-daily — per-call cost is noise. Code simplicity and consistency with already-tested `zipsa run` path outweigh the memory savings. Model 2 is a deferred optimization.

Model 3 (long-lived launcher daemon) is out of scope — adds daemon lifecycle complexity for marginal gain at zipsa's call frequencies.

**Changes:**

- New MCP tool: `mcp__zipsa__run_skill(name: str, args: str = "") -> {status, exit_code, run_id, summary}`.
- `launcher/zipsa/core/run_skill_mcp.py` (new) — `subprocess.run(["uv", "run", "zipsa", "run", name, args])`. Discard stdout. Read summary.json. Return parsed dict.
- `HitlServer` registration (currently registers ask/confirm/choose/ask_once — add run_skill).
- Validation: only children declared in caller's `spec.children` are allowed. Other names rejected with `error.code = "skill_not_in_children"`.
- Cost accounting: child's cost counts against child's own limits. Parent's phase counter increments by 1 turn (the run_skill call); child's internal cost does NOT subtract from parent's budget.

**Files:** `launcher/zipsa/core/hitl_runner.py` (extend), `launcher/zipsa/core/run_skill_mcp.py` (new), `launcher/zipsa/system-prompts/runtime-contract.md` (document new tool).

**Verification:** Test fixture parent skill with `children: [hello-world]`. Run parent → MCP `run_skill("hello-world")` returns `{status: "ok", exit_code: 0, run_id: <timestamp>}`.

### Phase 3 — Atomic skills

Decompose daily-progress / bip-daily-x logic into single-responsibility atomic skills.

#### 3a. `agenthud-report` (atomic)

- **Responsibility:** Run agenthud for a date, save the structured JSON report as an artifact, finish.
- No `ask_once`, no user prompts. Pure tool.
- Phases: `precheck` (env check, no creds needed for agenthud itself) + `report` (runs vendored wrapper, saves to `artifacts/agenthud-report.json`).
- `requires.project_roots: list[directory]` (same as current daily-progress / bip-daily-x).
- Summary `result`: `{date, activity_count: int, project_count: int}` (just metadata; full data is the artifact).
- If sessions empty → `status=ok`, `result.activity_count=0`. Orchestrator branches on this.
- New skill, version 0.1.0.

#### 3b. `notion-page-write` (atomic)

- **Responsibility:** Take page data + DB reference, write to Notion.
- Input via user_query (JSON-encoded args): `{db_id, data_source_id, pages: [...]}`.
- No `ask_once`. `db_id` / `data_source_id` always passed in, never asked.
- env: existing OAuth pattern (Notion MCP server).
- Single `persist` phase.
- Summary `result`: `{pages_created: int, pages_updated: int, db_url}`.

#### 3c. `x-post` (atomic)

- **Responsibility:** Take a tweet string, post it.
- Input via user_query: the tweet text (≤ 280 chars).
- No drafting, no review, no voice.
- env: `X_API_KEY` + 3 others (existing).
- Single `post` phase (just runs the bundled `post.py` script).
- Summary `result`: `{tweet_id, tweet_url}`.

### Phase 4 — Orchestrator skills

#### 4a. `daily-notion-log` (orchestrator, replaces daily-progress)

- `spec.children: [agenthud-report, notion-page-write]`.
- `ask_once`: `notion_workspace`, `notion_db_name`.
- Phases: `precheck` (resolve Notion DB), `fetch` (run agenthud-report, get artifact), `prepare` (LLM summarizes per-project), `write` (run notion-page-write).
- Accepts optional `--agenthud-run-id=X` to skip fetch phase (cache passthrough for parent orchestrators).

#### 4b. `daily-bip-tweet` (orchestrator, replaces bip-daily-x)

- `spec.children: [agenthud-report, x-post]`.
- `ask_once`: `voice`.
- Phases: `precheck` (X creds), `fetch`, `draft` (LLM tweet from agenthud data + voice), `review` (HITL feedback loop), `post` (run x-post).
- Accepts optional `--agenthud-run-id=X` to skip fetch.

#### 4c. `morning-ritual` (orchestrator, top-level)

- **Recommended shape (B: nested orchestrators with cache-hint passing):**
  - `spec.children: [daily-notion-log, daily-bip-tweet]` (plus their transitive atomic children).
  - Phases: `fetch` (run agenthud-report once, get run_id), `delegate-notion` (run daily-notion-log with `--agenthud-run-id`), `delegate-tweet` (run daily-bip-tweet with `--agenthud-run-id`), `aggregate` (summarize).
- One agenthud invocation total. Per-orchestrator LLM logic (Notion prep, tweet draft) lives in respective orchestrator (DRY preserved).
- Nesting depth: 3 (morning-ritual → daily-* → atomic) — within depth cap.

### Phase 5 — Deprecation (later)

- Mark `daily-progress` and `bip-daily-x` deprecated in manifest (`spec.deprecated: true` field, displayed in `zipsa list`).
- After N weeks of using `morning-ritual`, delete the old skill source dirs.

---

## Nested Orchestration

Orchestrator A → orchestrator B → atomic C is supported. Protections:

### Cycle Detection

- Env var `ZIPSA_CALL_TRACE = "skill1,skill2,..."` passed down through each `run_skill`.
- Launcher checks: requested child already in trace → reject with `error.code = "skill_cycle_detected"`.
- Hard depth cap: `ZIPSA_CALL_DEPTH >= 5` → reject with `error.code = "skill_depth_exceeded"`.

### Cost Budget (v1 Trust Model)

- Each skill has independent budget per its own `spec.limits`.
- Orchestrator's limits cover only its own agent turns, not its children's.
- Trust model: orchestrator author trusts children to respect their own limits.
- Runaway protection comes from depth cap.
- Future: `run_skill(... max_cost=X)` to subtract budget. BACKLOG.

### HITL Routing Convention

- Phases that call `run_skill` MUST NOT also use HITL tools (`mcp__zipsa__ask`, `confirm`, `choose`, `ask_once`).
- Manifest validator rejects co-occurrence in same phase's `allowed_tools` → ValidationError at load time.
- Pattern: one phase per concern (user interaction OR child invocation, not both).

### Concurrent Container Resource

- Depth N → N concurrent docker containers (parents are paused awaiting MCP response).
- ~200MB per container. Depth 5 → 1GB. Acceptable.
- Depth cap doubles as resource cap.

---

## New Manifest Fields

- `spec.kind: atomic | orchestrator` (optional, default: `orchestrator` — preserves existing skills' behavior of allowing run_skill if needed; explicit `atomic` opts in to validation).
- `spec.deprecated: bool` (optional, Phase 5).
- `spec.cache_hints: list[str]` (optional, orchestrator's declared cache passthrough hints — e.g. `["agenthud-run-id"]` for daily-notion-log).

Validation:
- `kind=atomic` + `spec.children` non-empty → ValidationError.
- `kind=atomic` + agent uses `run_skill` at runtime → hook denial (`error.code = "atomic_cannot_run_skill"`).

---

## User Cross-Skill Settings (Deferred)

`voice` and `notion_workspace` may be wanted by multiple orchestrators. v1 keeps these in per-orchestrator `ask_once` memory (each orchestrator asks separately). If a second orchestrator wants the same key, BACKLOG entry covers a `~/.zipsa/preferences.yaml` layer + `consumes_preferences: [...]` manifest field.

---

## Critical Files

- `launcher/zipsa/core/hitl_runner.py:1-120` — `HitlServer` (HTTP MCP server, registration point for new tools)
- `launcher/zipsa/core/hitl_mcp.py` — existing MCP tool pattern (template for new ones)
- `launcher/zipsa/core/executor.py:1145-1300` — container mounts + docker run command (Phase 1 mount addition here)
- `launcher/zipsa/core/summary.py` — summary.json contract
- `launcher/zipsa/core/models.py:222-308` — SkillSpec validation (new fields: `kind`, `deprecated`, `cache_hints`)
- `launcher/zipsa/paths.py:1-60` — path helpers (Phase 1 new helper here)
- `launcher/zipsa/system-prompts/runtime-contract.md` — agent contract (document new MCP tools)
- `runtime/Dockerfile` — NOT modified (per "essential tools only" rule established 2026-05-20)

---

## Verification (end-to-end, after all phases)

```bash
cd launcher && uv run pytest

# Phase 1
zipsa run agenthud-report yesterday
ls ~/.zipsa/agenthud-report@*/runs/*/artifacts/agenthud-report.json  # exists

# Phase 2
zipsa run <test-fixture-parent>  # parent → run_skill("hello-world") works

# Phase 3
zipsa run agenthud-report yesterday              # produces artifact, exits
zipsa run notion-page-write '{"db_id":"...",...}'  # writes Notion page
zipsa run x-post "test tweet"                    # posts to X

# Phase 4
zipsa run daily-notion-log yesterday  # same outcome as current daily-progress
zipsa run daily-bip-tweet today       # same outcome as current bip-daily-x

# Phase 5 (top-level)
zipsa run morning-ritual yesterday    # one agenthud call, Notion + tweet both
```

---

## Out of Scope

- Bash zipsa / docker socket mount / zipsa-in-container (security risk + "essential tools only" rule).
- Launcher-level pure-YAML orchestrator (orchestrators are LLM-driven for consistency).
- npm-style `spec.dependencies` field — `spec.children` is sufficient.
- `~/.zipsa/preferences.yaml` cross-skill preferences — BACKLOG.
- Cron / scheduling — host crontab + `zipsa run morning-ritual` suffices.
- Streaming child events to parent — post-mortem summary.json suffices.
- Container reuse across phases — BACKLOG (separate optimization, orthogonal to this spec).
