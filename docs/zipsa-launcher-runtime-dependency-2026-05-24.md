# Launcher → Claude Runtime Dependency Audit

> Date: 2026-05-24
> Scope: `launcher/zipsa/**` — how tightly the launcher is coupled to
> Claude Code as its runtime engine, and what blocks activating the
> already-stubbed Codex / Gemini runtimes.
> Assumption (per user): the single Docker image bakes in all three
> CLIs (claude, codex, gemini), so image↔runtime mapping is **out of
> scope**. Only code-level coupling is audited here.

## Legend

| Mark | Meaning |
|------|---------|
| ✅ | Clean seam — goes through the runtime abstraction, low/no Claude coupling |
| ⚠️ | Partial — abstracted in form, but leaks Claude assumptions |
| ❌ | Leak — Claude-hardcoded, bypasses the runtime abstraction entirely |

---

## The intended seam (the clean part)

```
runtimes/
├── __init__.py        ✅  registry: register_runtime / get_runtime / list_runtimes
├── base.py            ✅  AgentRuntime ABC: build_command(), parse_output(), supports_mcp()
└── claude.py          ✅  ClaudeRuntime — all `claude` CLI specifics isolated here (by design)
```

- `runtimes/__init__.py:18` — `get_runtime(name)` resolves by registry key. Adding a runtime = register a class. No Claude assumption.
- `runtimes/base.py:21` — `build_command(...)` abstract: takes `system_prompt`, `allowed_tools`, `model`, `extra_dirs`, `mcp_debug_file`. **Note: no MCP-server config parameter** — that is the root of leak #1.
- `runtimes/base.py:49` — `parse_output(stream) -> Iterator[dict]`. Contract *promises* a "common format" but the only implementation returns raw vendor JSON (see leak #2).
- `runtimes/claude.py:52` — emits `claude --print --append-system-prompt … --output-format=stream-json`. Correctly isolated.

**Verdict:** the seam exists and is clean for *command construction* and *line transport*. The abstraction was started but stops here.

---

## File-by-file dependency tree (the leaks)

```
core/executor.py        ⚠️/❌  the orchestrator — touches the seam in 3 places, bypasses it everywhere else
core/skill.py           ❌     build_claude_json() — emits Claude's private config file
core/limits.py          ❌     parses raw Claude stream-json (usage / message / thinking)
core/renderer.py        ❌     parses raw Claude assistant content blocks
core/summary.py         ⚠️     stores `claude_version` field (cosmetic naming, low severity)
core/pricing.py         ⚠️     hardcoded claude-* model rate table (structure is generic)
```

### `core/executor.py`

```
:50    runtime: str = "claude"                     ⚠️  default runtime hardcoded (just a default; get_runtime is generic)
:59    self.runtime = get_runtime(runtime)         ✅  selection via registry
:167   skill.build_claude_json(...)                ❌  config generation called DIRECTLY, not via runtime
:327   skill.build_claude_json(...)                ❌  (rebuild path — same leak)
:346   etype = event.get("type"); == "result"      ❌  Claude stream-json event types
:349   etype == "system" and subtype == "init"     ❌  Claude init event shape
:351   event.get("model")                          ❌  field from Claude system.init
:353   event.get("claude_code_version")            ❌  field from Claude system.init
:401   etype == "assistant"                        ❌  Claude event type
:403   event["message"]["content"]                 ❌  Anthropic Messages content-block shape
:405   block["type"] == "text"                     ❌  Anthropic content-block type
:747   event["type"] == "result"                   ❌  Claude result event
:748   event.get("total_cost_usd")                 ❌  Claude result field
:873   {"system","assistant","user","result"}      ❌  Claude event-type allowlist
:1107  event["type"] == "assistant" → content      ❌  Claude content-block parse (again)
:1561  cp /.zipsa/.claude.json …                   ❌  copies Claude config into container at startup
:1590  "MCP config is now in .claude.json"         ❌  MCP wiring lives outside the seam
:1595  self.runtime.build_command(...)             ✅  command built via abstraction
```

> The pattern: **`build_command` / `parse_output` are reached through `self.runtime`,
> but everything that interprets the resulting events (lines 346–414, 747, 873, 1107)
> reads the raw Claude schema directly.** `parse_output` hands back vendor dicts
> unchanged, so the orchestrator is bound to Claude's event vocabulary.

### `core/skill.py` — leak #1 (config)

```
:129   def build_claude_json(...)                  ❌  method named for Claude, called directly by executor
:180   for server in spec.mcp: build mcp_servers   ❌  Claude mcpServers schema (command/args | type/url)
:198   headersHelper = …                           ❌  Claude-Code-specific MCP auth feature
:226   claude_config = {                           ❌  Claude's private .claude.json shape:
         "hasCompletedOnboarding": True,           ❌    onboarding bypass flag
         "projects": {<ws>: {                      ❌    project-scoped config
           "hasTrustDialogAccepted": True,         ❌    trust-dialog bypass flag
           "mcpServers": mcp_servers }}}           ❌    MCP registration
:237   output_dir / ".claude.json"                 ❌  fixed Claude filename
```

> This is the **single biggest structural debt**. MCP setup + agent
> bootstrapping is encoded entirely in Claude Code's private config
> format, and it is invoked as `skill.build_claude_json(...)` — never
> routed through `self.runtime`. Codex/Gemini have completely different
> config files and MCP registration mechanisms.

### `core/limits.py` — leak #2a (token/cost accounting)

```
:103   etype = event.get("type")                   ❌  Claude event type
:121   etype == "assistant"                        ❌
:122   msg = event["message"]                      ❌  Anthropic message envelope
:126   block["type"] == "thinking"                 ❌  Anthropic thinking block
:134   usage = msg["usage"]                        ❌  Anthropic usage object (input/output/cache tokens)
:131-137  dedupe by message.id                     ❌  encodes a Claude stream-json QUIRK:
                                                        one event per content block, all sharing
                                                        the same usage object → must dedupe by msg id
```

> Not only the schema but a **Claude-specific streaming quirk** is baked
> into the limits logic. Any other runtime's token accounting would need
> its own path.

### `core/renderer.py` — leak #2b (terminal output)

```
:105   event["type"] == "assistant"                ❌  Claude event type
:106   event["message"]["content"]                 ❌  Anthropic content blocks
:108   b["type"] == "text"                          ❌  Anthropic content-block type
:133   event_type in ("system","rate_limit_event") ❌  Claude/Anthropic event types
:176   event["type"] == "assistant" → content      ❌  (second content-block parse)
```

> ~28 event-field reads. The live renderer assumes Claude's event stream
> end to end. (Note: `zipsa_phase_start`, `zipsa_limits_breach` etc. are
> zipsa-injected synthetic events — those are fine, runtime-neutral.)

### `core/summary.py` — ⚠️ low severity

```
:56    claude_version: Optional[str]               ⚠️  just a field name; carries the value through
:117   "claude_version": claude_version            ⚠️  cosmetic — rename to runtime_version when generalizing
```

### `core/pricing.py` — ⚠️ low severity

```
:31-33 MODEL_PRICING = { "claude-opus-4-7": …,     ⚠️  rate table is claude-only TODAY
                         "claude-sonnet-4-6": …,        but the structure is model-name keyed —
                         "claude-haiku-4-5-…": … }      adding non-Claude models is additive
:36    _FALLBACK_MODEL = "claude-opus-4-7"          ⚠️  claude default fallback
```

---

## Blast radius — what consumes the raw Claude event schema

| File | Direct schema reads | Severity | Why |
|------|:---:|:---:|-----|
| `executor.py` | ~12 sites | ❌ high | orchestration, summary metadata, limits hook |
| `limits.py` | ~12 sites | ❌ high | token/cost accounting + Claude streaming quirk |
| `renderer.py` | ~28 sites | ❌ high | live terminal output |
| `skill.py` | config file | ❌ high | MCP + bootstrap config (not event schema, but Claude-format) |
| `summary.py` | 1 field | ⚠️ low | naming only |
| `pricing.py` | model table | ⚠️ low | additive |

---

## Root cause

The abstraction normalizes **command construction** (`build_command`) and
**line transport** (`parse_output` returns dicts) — but it never
normalizes the **event vocabulary**. `parse_output` yields raw Claude
JSON, so four files (`executor`, `limits`, `renderer`, and indirectly
`summary`) decode Claude's schema directly. Config generation
(`build_claude_json`) sits entirely outside the seam.

## What it takes to activate Codex / Gemini

1. **Normalize the event stream (highest leverage).**
   Make `parse_output` emit a runtime-neutral event vocabulary, e.g.
   `{type: "assistant_text", text}`, `{type: "usage", input_tokens,
   output_tokens, cache_*}`, `{type: "result", cost_usd, model,
   runtime_version}`. Then `executor` / `limits` / `renderer` / `summary`
   consume the neutral schema and need (almost) no per-runtime branches.
   This single change defuses leaks #2a and #2b across 3 files.

2. **Move config generation behind the seam.**
   Replace `skill.build_claude_json(...)` with
   `runtime.build_config(skill, …) -> (host_path, container_path)`.
   `ClaudeRuntime` keeps today's `.claude.json` logic; Codex/Gemini emit
   their own. Update the `cp_preamble` (executor.py:1561) to use the
   runtime-supplied container path instead of the hardcoded
   `/home/agent/.claude.json`. Defuses leak #1.

3. **Generalize pricing/model naming.**
   Add non-Claude models to the `MODEL_PRICING` table; rename
   `claude_version` → `runtime_version` in summary. Low effort, additive.

## Bottom line

- The runtime abstraction is **~30% done**: selection + command building
  are clean; event normalization + config generation are not.
- **Adding a runtime is not "implement 2 methods."** Without step 1 above,
  every new runtime forces edits in `executor`, `limits`, and `renderer`.
- **Highest-ROI move:** define the normalized event schema and make
  `parse_output` emit it. That converts ~52 raw-schema read sites across
  3 files into runtime-neutral code in one stroke.
