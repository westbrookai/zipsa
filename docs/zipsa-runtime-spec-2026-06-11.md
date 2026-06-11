# Zipsa Runtime Spec — Hybrid Phase Runtime (2026-06-11)

> Companion to `docs/zipsa-architecture-2026-06-11.md` (high-level
> architecture) and `skills/AUTHORING.md` (author guide).
>
> This doc is the runtime engineer's contract. Derived from the
> SkillBuilder v2.1 + hello-world design probe.

## Context

The design probe wrote two skills in the new B' format **before any
runtime change**, then asked: what does the runtime have to do for
these files to actually execute?

The skills:
- `launcher/zipsa/builtin_skills/skill-builder/` — 3 phase (1.clarify.md
  LLM, 2.iterate.md LLM, 3.finalize.py Python). Tests cross-type state
  transitions, HITL from both Python and LLM, in-phase Bash, multi-round
  LLM loops with cost monitoring.
- `skills/hello-world/zipsa-dist/` — 1 phase (1.report.py Python).
  Minimal Python phase baseline.

This spec turns each observation from that probe into a concrete
runtime requirement, then breaks down implementation into PRs.

## 1. Skill Loading

### 1.1 `pyproject.toml` is the only manifest

The runtime resolves skill metadata from `<skill>/zipsa-dist/pyproject.toml`.

- `[project]` — PEP 621 standard. `name`, `version`, `description`,
  `dependencies`. Used for `uv pip install -e ./zipsa-dist` at container
  startup.
- `[tool.zipsa]` — runtime config. `description` (mandatory),
  `credentials: list[str]` (optional), `schedule: str` (optional cron),
  `allows_staging_run: bool` (optional, from #102).
- `[tool.zipsa.limits]` — `max_cost_usd`, `timeout_seconds`. Apply to
  the whole skill run, not per phase.
- `[tool.zipsa.phases."<id>"]` — per-phase overrides. `max_turns`,
  `allowed_tools`, `cost_warn_threshold_usd`, `model.name`. Optional;
  absence = runtime defaults.

`apiVersion` / `kind` / `metadata` / `spec.*` from the old manifest
schema are **not parsed**. Skills that still ship them load fine — the
runtime just ignores those keys.

### 1.2 Filename-based phase discovery

Discovery scans `<skill>/zipsa-dist/*.{py,md}`. A file is a phase iff
its filename matches `^(\d+(\.\d+)*)\.([a-z][a-z0-9-]*)\.(py|md)$`.

- The dotted prefix is the **phase id** (`1`, `2`, `3.1`, `3.2`).
- The middle segment is the **slug** (used for log dir names).
- The extension is the **phase kind**: `.py` = Python phase, `.md` = LLM
  phase.

Phases are ordered by lexicographic tuple of their dotted id parts
(`(1,)` < `(2,)` < `(3, 1)` < `(3, 2)` < `(4,)`).

Files that don't match the pattern (e.g. helper Python modules imported
by phases, READMEs) are ignored by discovery.

First phase **should** be `.py` (preflight). Authoring tools (built-in
`skill-builder`) are exempt — the runtime emits a warning, not an error,
for `.md` first phases.

### 1.3 `SKILL.md` is author-intent only

The runtime does not parse `SKILL.md`. It's the author's source of
truth, surfaced in `zipsa list` / web UI for humans, never read for
behavior.

## 2. Phase Execution

### 2.1 Python phase executor

Python phases run inside the same Docker container model the LLM
phases already use. The launcher spawns a fresh container per phase,
exposes the host MCP server (HitlServer) via `host.docker.internal:
<ZIPSA_HITL_PORT>` with the per-run `ZIPSA_HITL_TOKEN`, and runs:

```
uv run python -m zipsa_phase_runner <phase-file>
```

Where `zipsa_phase_runner` is a small entrypoint shipped in the
container's `zipsa` package. It:

1. Imports the phase file as a Python module.
2. Validates `run(ctx, prev) -> dict` is exported. (Optional
   `should_run(prev) -> bool` for branching, evaluated separately.)
3. Builds `ctx` (see §2.4) and `prev` (the previous phase's output dict
   from `state.json`, or `{}` for the first phase).
4. Calls `run(ctx, prev)`.
5. Serializes the return dict to JSON, writes to
   `runs/<id>/phases/<n>-<slug>/state.json`.
6. Exits 0 on success, non-zero on uncaught exception (with the
   traceback on stderr).

`stdout` / `stderr` from the container go to
`runs/<id>/phases/<n>-<slug>/log.txt`, same as LLM phases.

**Why container, not in-process:** keeps Python phases at the same
isolation level as LLM phases, avoids the launcher process importing
arbitrary skill code, lets `pyproject.toml` deps install cleanly via
`uv pip install -e ./zipsa-dist`, and reuses the existing HitlServer /
MCP infrastructure without circular imports.

**Error handling.** Container exit code ≠ 0 → phase status = `error`,
captured stderr in `error` field, run stops. Resume from this phase
on next `zipsa run` (existing semantics from `phase_state.py`).

### 2.2 LLM phase executor

For a `.md` phase the runtime:

1. Parses YAML frontmatter (between `---` markers at the top, optional).
   Recognized keys: `description`, `guard`.
2. Strips frontmatter, sends the remaining markdown as the instruction
   to Claude.
3. Applies `[tool.zipsa.phases."<id>"]` from pyproject (allowed_tools,
   max_turns, model overrides).
4. Existing strict envelope parser (PR #95) handles output. Envelope
   semantics unchanged for `.md` phases.

### 2.3 Cross-type state transition

The runtime hands data between phases through `state.json` files
written by each phase. Four transitions:

| Transition | Mechanism |
|---|---|
| Python → Python | Phase N's return dict → state.json → next phase's `prev` arg |
| Python → LLM | Same state.json → LLM phase reads it as `execution_context.previous_phase_output` |
| LLM → Python | LLM envelope's `next_phase_input` field → state.json → next phase's `prev` arg |
| LLM → LLM | Existing — envelope's `next_phase_input` (current behavior preserved) |

For Python → LLM the runtime must serialize the dict to JSON and
expose it through the existing `previous_phase_output` channel. The
LLM phase reads the same envelope-input shape it already does today.

For LLM → Python, the runtime extracts `next_phase_input` from the
parsed envelope and writes that dict to state.json. The next Python
phase reads it as `prev`.

### 2.4 `ctx` shape

The minimum fields the runtime must populate in `ctx`:

```python
{
    "skill_name": str,        # from pyproject [project].name
    "version": str,           # from pyproject [project].version
    "user_query": str,        # arg given to `zipsa run <skill> <arg>`
    "run_id": str,            # UUID for this run
    "run_dir": str,           # absolute path to runs/<id>/ inside container
    "depth": int,             # nesting depth (0 = outermost); see §3.x
    "env": dict[str, str],    # subset of env vars: credentials, plus
                              # ZIPSA_HOME, RUN_ID, ZIPSA_HITL_PORT,
                              # ZIPSA_HITL_TOKEN, ZIPSA_RUN_DEPTH
}
```

Phase authors generally don't read `env` directly — `zipsa.hitl` and
the runtime read the MCP env vars on their behalf, and credentials
are exposed as their own env vars (e.g. `NOTION_TOKEN`) via the
existing OAuth manager.

Phase authors may rely on these keys being present. Additions are
backward-compatible; removals are breaking.

## 3. Branching

### 3.1 Sub-phase XOR selection

When discovery finds multiple phases at the same dotted level (e.g.
`3.1.fetch-from-db.py`, `3.2.fetch-from-web.py`), the runtime evaluates
each sibling's guard:

- Python sibling: call `should_run(prev) -> bool`. If module doesn't
  define it, treat as `True`.
- LLM sibling: parse frontmatter `guard:` value. If absent, treat as
  `True`. If present, evaluate the string as a Python expression with
  `prev` bound to the previous phase's output dict. Any other names
  raise `NameError`.

Selection rule: **exactly one sibling's guard must be true**. Zero or
more than one → runtime error before any sibling runs.

The selected sibling runs as if it were the only phase at that level.
Siblings whose guard is false are skipped silently (logged, but not
errored).

## 4. Helper Modules

### 4.1 `zipsa.hitl`

Python module bundled with the launcher's container image. Python
phases import and call it directly:

```python
from zipsa import hitl

city = hitl.ask("Which city?")
ok = hitl.confirm("Proceed?", default=True)
choice = hitl.choose("Source?", ["DB", "Web", "Cache"])
```

**Spec:**
- `ask(question: str, *, default: str | None = None) -> str`
- `confirm(question: str, *, default: bool = False) -> bool`
- `choose(question: str, options: list[str], *, default: int = 0) -> str`

**Implementation:** a small synchronous MCP client. Each function
makes an HTTP POST to the HitlServer at
`host.docker.internal:$ZIPSA_HITL_PORT/mcp` with the corresponding
tool call (`ask`, `confirm`, `choose`), Bearer auth using
`$ZIPSA_HITL_TOKEN`. The server blocks until the user replies, returns
the value over HTTP. The Python caller sees a normal sync function.

No async event loop, no thread bridge. The HTTP request/response cycle
of MCP is already sync from the client's perspective — same pattern
Claude Code uses today for `mcp__zipsa__ask`.

If `$ZIPSA_HITL_PORT` is missing the call falls back to the
`hitl.unattended` mode (existing behavior — return default or raise).

### 4.2 `zipsa.llm` (deferred)

Spec'd but not implemented in this round. The hello-world / skill-builder
probes don't exercise it. Add when a Tier 2 skill (likely daily-progress)
forces the issue.

## 5. Tools surface in LLM phases

Existing MCP tools must continue to work, with the additions below.

### 5.1 `mcp__zipsa__ask` / `confirm` / `choose`

Already exist. Routing unchanged.

### 5.2 `mcp__zipsa__run_staging_skill` (PR #102)

The SkillBuilder iterate phase calls this from inside an `.md` phase.
Current implementation must support being invoked from within an LLM
phase that itself has a `run_dir`. The nested run gets its own `run_id`
under `~/.zipsa/staging/<name>/runs/`, not inside the caller's run_dir.

**Recursion depth.** The launcher tracks depth via
`ZIPSA_RUN_DEPTH` env var, incrementing on each nested invocation.
The outermost skill's `[tool.zipsa].max_run_depth` (default `3`) caps
how deep recursion can go. If a call would push depth past the cap,
the tool returns an error envelope to the caller without launching a
container.

`max_run_depth` is read from the outermost run's pyproject and frozen
for the entire run tree — nested skills can't raise it.

### 5.3 `mcp__zipsa__read_run_log` (PR #101)

Already works. SkillBuilder iterate uses it on the `run_id` returned by
`run_staging_skill` to get a compact summary the LLM can analyze.

### 5.4 `mcp__zipsa__get_phase_cost` (new)

Returns the cumulative cost of the current phase so far. Used by
iterate-style loops to decide when to ask the user "keep going?".

**Spec:**
- No arguments.
- Returns `{cost_usd: float, tokens_in: int, tokens_out: int}`.
- Counts only the current phase's own LLM turns (not sub-runs called
  via `run_staging_skill` — those return their own cost in the
  `run_staging_skill` reply).

Available in all `.md` phases. Not exposed to Python phases by default
(Python phases can call it via the same HTTP MCP endpoint if needed,
but the typical Python phase doesn't drive an LLM turn).

## 6. Envelope contract changes

### 6.1 `status: short_circuited`

Phase 1 (clarify) in SkillBuilder can decide the user picked an existing
skill and the build should stop cleanly. Mechanism:

- LLM phase emits envelope with `status: "short_circuited"` and any
  payload in `result`.
- The runtime writes state.json normally, marks the phase as completed
  (not errored), and **propagates the short_circuit flag** to all
  subsequent phases as `prev.short_circuited == True` (whether or not
  the LLM put it in `next_phase_input`).
- Subsequent phases see `prev.short_circuited` and decide. SkillBuilder's
  phase 2 and 3 both check this and no-op.

`completed` log status is fine for short_circuited phases — they're not
failures. Run end summary distinguishes "normal completion" from
"short-circuited at phase N" for clarity.

## 7. Cost monitoring

`run_staging_skill` already returns `cost_usd` in its result. SkillBuilder's
iterate phase accumulates this across rounds and surfaces totals through
HITL.

For LLM cost of the **current** phase itself (the SkillBuilder
conversation, not the sub-runs), see open design decision §8.3.

## 8. Locked design decisions

All resolved 2026-06-11.

### 8.1 ~~`zipsa.hitl` sync ↔ async bridge~~ — non-issue

Dropped. Python phases run inside a container subprocess and talk to
the host HitlServer over HTTP MCP (same as Claude Code does today for
`mcp__zipsa__ask`). The MCP request/response cycle is sync from the
client's perspective, so `zipsa.hitl` is just a thin sync HTTP client
— no event loop, no thread bridge. See §2.1 and §4.1.

### 8.2 `run_staging_skill` depth: config, default 3

`[tool.zipsa].max_run_depth` in the outermost skill's pyproject (default
`3`). Frozen for the run tree — nested skills can't raise it.

Tracked via `ZIPSA_RUN_DEPTH` env var, incremented on each nested
invocation. `ctx["depth"]` surfaces it to phase authors. See §5.2.

### 8.3 Cost exposure: `mcp__zipsa__get_phase_cost` MCP tool

New MCP tool (§5.4). Returns cumulative cost of the current phase only;
sub-run cost stays in the `run_staging_skill` return so the LLM can
distinguish.

### 8.4 Resume policy: preserve staging

On mid-loop crash the staging dir is left in place. On resume, the
iterate phase reads the existing dir, asks the user "I see files from
the previous attempt — continue from here or start over?" and proceeds
based on the answer. SkillBuilder's `2.iterate.md` will pick this up
when we revise it for resume.

## 9. Implementation breakdown

Each row = one PR.

| PR | What | Depends on |
|---|---|---|
| R1 | Filename-based phase discovery + ordering | — |
| R2 | pyproject.toml `[tool.zipsa]` parser (loader) | — |
| R3 | `zipsa_phase_runner` entrypoint (container-side) + Dockerfile additions | — |
| R4 | Python phase executor wiring (launcher → container with phase file mounted) | R1, R2, R3 |
| R5 | Cross-type state bridge (Python ↔ LLM via state.json) | R4, existing phase_state |
| R6 | `zipsa.hitl` sync MCP HTTP client module | R3 |
| R7 | LLM phase frontmatter parser (description, guard) | existing |
| R8 | Branching (should_run / guard, sibling XOR) | R4, R7 |
| R9 | Envelope `status: short_circuited` propagation | existing envelope parser |
| R10 | `mcp__zipsa__get_phase_cost` MCP tool | existing executor |
| R11 | `run_staging_skill` depth tracking (`ZIPSA_RUN_DEPTH`, `max_run_depth` cap) | existing #102 |
| R12 | iterate-phase resume (read existing staging, prompt user) | R4–R6 |
| R13 | `Skill.load` accepts pyproject-only skills (no manifest.yaml needed) | R2 |
| R14 | `zipsa list` reads description from pyproject | R2, R13 |
| R15 | `zipsa validate` accepts pyproject + filename-phase skills | R1, R2 |
| R16 | E2E test: hello-world (Python-only baseline) | R1–R6, R13 |
| R17 | E2E test: skill-builder (full hybrid) | R1–R15 |

Total: 17 PRs. R1–R6 are the minimum to run hello-world end-to-end.
R7–R12 are needed for SkillBuilder. R13–R15 are surface plumbing.
R16–R17 are integration tests that gate the whole thing.

Critical path to first green E2E: R1 → R2 → R3 → R4 → R6 → R13 → R16.
That's 7 PRs to a working hello-world. Everything else can land in
parallel afterwards.

## 10. Verification

Each PR's acceptance criteria (high level):

- **R1**: discovery returns the right phase list and order for both
  test skills.
- **R2**: parser surfaces `[tool.zipsa]` fields. Errors clearly on
  invalid pyproject.
- **R3**: `run(ctx, prev)` invoked, return → state.json written,
  exception → phase errored with traceback.
- **R4**: hello-world end-to-end. Python phase return surfaces as run
  output.
- **R5**: `hitl.ask` from a Python phase round-trips through HitlServer.
- **R6–R7**: a hybrid skill with `3.1` + `3.2` siblings runs only one
  branch.
- **R8**: SkillBuilder's "use_existing" path actually exits cleanly.
- **R9**: phase calls `get_phase_cost`, gets a number, can branch on it.
- **R10**: pyproject-only skill installs and runs.
- **R11**: `zipsa list` shows pyproject-based skills.
- **R12**: `zipsa validate` accepts the new layout.
- **R13**: `zipsa run hello-world` succeeds end-to-end without manifest.yaml.
- **R14**: `zipsa run skill-builder` produces a working skill end-to-end
  with at least 2 disambiguation rounds and 2 iterate rounds.

## 11. Out of scope (this round)

- `zipsa.llm` Python helper (deferred — daily-progress migration will
  drive its design)
- Multi-runtime backend (Codex, Gemini — keep Claude single backend)
- `zipsa create` CLI (replaced by `zipsa run skill-builder` directly —
  no separate CLI entry needed)
- Web UI changes (minor follow-up, not in this Tier)
- Existing skill migration beyond hello-world (Tier 2, separate plan)
- Manifest schema cleanup (`models.py` SkillSpec / SkillManifest) —
  Tier 4 cleanup, after the new path is fully working

## Cross-references

- Architecture overview: `docs/zipsa-architecture-2026-06-11.md`
- Author guide: `skills/AUTHORING.md`
- 2026-05-15 rethink: `docs/zipsa-rethink-2026-05-15.md`
- SkillBuilder probe: `launcher/zipsa/builtin_skills/skill-builder/`
- hello-world probe: `skills/hello-world/zipsa-dist/`
