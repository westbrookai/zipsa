# Launcher Architecture Review (#180)

> Living map of the `launcher/` Python CLI (~12.6k LOC, 57 files). Built from a
> read-only fan-out survey (7 cluster agents) on 2026-06-19. Purpose: see what
> each file is for, the dependency shape, and where a better structure exists —
> then spawn concrete refactor Issues. **Scope: launcher only** (runtime/skills
> later).

## How to read this

- **§1 The spine** — the one architectural fact that frames everything.
- **§2 Cross-cutting themes** — patterns that span clusters (the real findings).
- **§3 Cluster maps** — per-file role / deps / smells.
- **§4 Refactor backlog** — prioritized candidate Issues.

---

## §1 The spine: legacy `executor.py` vs the new exec/run paths

There are **two parallel execution engines**:

| | Legacy | New |
|---|---|---|
| Engine | `core/executor.py` `DockerExecutor` (1640 LOC) | `exec_runner.py` + `run_llm.py` + `host_served_container.py` |
| Skill format | `manifest.yaml` + `spec.phases` (manifest-based, LLM-driven) | exec-format: `SKILL.md` + `scripts/` (no manifest) |
| Live? | **Yes** — `zipsa run <legacy-skill>` (cli.py:510) constructs `DockerExecutor` | `zipsa run <exec-skill>` short-circuits (cli.py:405-418) → `run_skill_llm`; `zipsa exec` → `exec_runner.run_phases` |
| MCP host server | `HitlServer` (hitl_runner.py) | `RunServer` / `ForgeServer` |

`DockerExecutor` is **still live but on a narrowing path** — every new skill is authored exec-format and bypasses it. The whole codebase carries the cost of running both: **three independent docker-argv builders** (`executor._build_docker_command` ~280 LOC, `exec_runner._build_docker_argv`, `host_served_container.build_host_served_argv`) that drift (e.g. the Linux `--add-host host.docker.internal:host-gateway` guard exists in `executor` but not the exec path), and **two HITL hosting stacks**.

**The central question of this review:** what is the migration path off `DockerExecutor`, and what can be unified/retired now vs. what must wait for legacy-skill deprecation.

---

## §2 Cross-cutting themes

**T1 — Two god-files.** `cli.py` (1855) and `executor.py` (1640) each own far too much.
- `cli.py`: arg-parsing + pre-flight orchestration + run-record rendering + run-dir scanning + install-repair + the exec/legacy dispatch — repeated across 7 commands. Natural seams: command-group modules (`commands/exec.py`, `run.py`, `forge.py`, `schedule.py`, `install.py`, `inspect.py`).
- `executor.py`: docker argv + env-file + multi-phase orchestration + limits + resume + summary + HitlServer lifecycle + OAuth + dev-overlay, plus a pile of 8 backward-compat static-delegator shims kept only for old tests.

**T2 — Three docker-argv builders, no shared base.** `executor._build_docker_command`, `exec_runner._build_docker_argv`, `host_served_container.build_host_served_argv`. Concrete drift already exists (`--add-host`). A shared `docker_argv.py` (MountSpec + EnvSpec → argv) is the highest-value de-duplication.

**T3 — Four MCP servers duplicate boilerplate.** `HitlServer`, `RunServer`, `ForgeServer`, `CreateServer` each re-implement socket-bind / uvicorn / daemon-thread / poll-for-ready / stop (~60-70 LOC each). They share `_bind_free_socket` + `_ALLOWED_HOSTS` by importing them as private names *from* `hitl_runner.py` — a leaky boundary. `forge_server.py`'s own docstring defers "a shared base." `tool_names()` is a hand-maintained literal in each → drifts.

**T4 — Confirmed dead code.** `CreateServer` (`create_server.py`), `run_create`, `build_create_prompt` — zero production callers, only tests + docstring mentions. (Validates **#178**.) Plus dead memory handler classes in `hitl_mcp.py` (`RecallHandler`/`RememberHandler`/`ForgetHandler`/`ListMemoryHandler`/`AskOnceHandler`) — `HitlServer.start()` re-implements them inline as closures, so the standalone classes are unused.

**T5 — Cluster misassignment (file lives ≠ file belongs).** `phase_state.py`, `phase_allow.py`, `skill_validator_handler.py` sit next to the exec pipeline but are only used by the legacy/run path. Misleading for maintainers; the true dependency graph isn't visible from the directory.

**T6 — Localized duplication (each with an apologetic comment).**
- PEP 723 parsing: `exec_runner._PEP723_RE`/`_inline_timeout_seconds` vs `exec_validate._PEP723_RE`/`_pep723_toml_error` → extract `_pep723.py`.
- Child-skill subprocess spawn (~60 LOC: drain-stderr thread, poll loop, returncode-2 decode): `RunSkillHandler` vs `RunStagingSkillHandler`, copy-pasted verbatim.
- `<name>@<version>` dir enumeration: `skill_catalog_handler._compute_run_stats` vs `requires.carry_over_from_previous` → `paths.skill_data_dirs(name)`.

**T7 — `paths.py` isn't quite the single source of truth.** `exec_runner.py` builds `~/.zipsa/<skill>/runs/<ts>` and `~/.zipsa/exec-out/…` inline at ~4 sites; `scheduling.py` hardcodes `~/.zipsa/schedule-logs`. Add `exec_run_dir` / `exec_out_dir` to `paths.py`.

**T8 — Implicit handler contracts.** Many `*_handler.py` injected into servers as untyped duck-types (`RunServer.exec_handler`). A `typing.Protocol` (`.run(script, args, prev) -> dict`) would make the contract explicit.

**T9 — Speculative / doc-only surface.** `runtimes/` plugin registry is real but single-impl (`ClaudeRuntime`) — speculative until a 2nd runtime; `run_log_handler` even hardcodes `get_runtime("claude")`. `SkillSpec.phases`/`state_schema`/`PhaseSpec` are parsed but never enforced ("v1: docs only").

---

## §3 Cluster maps

### Cluster A — CLI surface (`cli.py` 1855, `__main__.py` 10)
- `cli.py` — **Role:** the single authoritative CLI surface: registers every command, the `zipsa <skill-name>` shortcut, `main()`. Hub-and-spoke into all lower layers. **Smells:** oversized; `run` cmd 280 LOC; `list` cmd 260 LOC with run-stats re-implementation; `install` inlines broken-entry repair (dup of `installer.py`); `_render_run_record` (pure formatting) belongs in `renderer.py`; exec/legacy dispatch (`_is_exec_format`) duplicated across 7 commands.
- `__main__.py` — `python -m zipsa` shim. Clean.

### Cluster B — Exec path (deterministic phase runner)
- `exec_runner.py` (651) — run one phase / a phase chain (docker or local) → ExecResult. **Smells:** mixes 4 concerns (docker argv / LLM prompt build / chain orchestration / run-record); `out_dir` alloc duplicated in `run_phase`+`run_phases`; `_PEP723_RE` dup.
- `exec_skill.py` (320) — parse/validate exec-format metadata (SKILL.md frontmatter + package.yaml) into Pydantic. Clean.
- `exec_skill_handler.py` (108) — `mcp__zipsa__exec` tool for the authoring container. **Smell:** `mode` field name collision (docker/local vs exec/run); no dry-run passthrough.
- `phase_discovery.py` (146) — scan `scripts/` for ordered phase files. Clean.
- `exec_validate.py` (135) — static schema/PEP723 validation. **Smell:** PEP 723 dup; comment numbering off.
- `phase_allow.py` (86) — build per-phase tool allow-list. **Misassigned** — consumers are the LLM/legacy path (`Skill`, not `ExecSkill`).
- `phase_state.py` (64) — persist/reload phase state for resume. **Misassigned** — legacy run-dir only; no exec caller.
- `skill_validator_handler.py` (59) — `mcp__zipsa__validate_skill` for *legacy* manifest skills. **Misassigned** — not exec.

### Cluster C — Run / LLM path + host-served core
- `run_llm.py` (224) — orchestrate headless `claude -p` following SKILL.md; tee → run record. **Smell:** `_server()` pins HitlIO to `sys.stdin/out`, not the injected `stdout` (HITL non-injectable for tests).
- `host_served_container.py` (179) — shared core for run+forge (#175): mcp-config, dry-run, server lifecycle, factory seams. Clean.
- `run_server.py` (157) — FastMCP host for run path (exec/ask/confirm/choose/report). **Smell:** untyped `exec_handler`; `tool_names()` hardcoded.
- `run_skill_handler.py` (322) — `run_skill` tool: spawn child `zipsa run` subprocess. **Smell:** mid-fn imports; mtime-glob race; 0.1s busy-wait poll.
- `run_staging_skill_handler.py` (245) — same as above for staging dir. **Smell:** spawn block copy-pasted verbatim from `run_skill_handler`.
- `run_script_handler.py` (76) — run-time analogue of exec handler → `exec_runner.run_phase`. Clean.
- `run_log_handler.py` (194) — read past run's `output.jsonl` for skill-builder. **Smell:** hardcoded `get_runtime("claude")`.
- `run_draft_handler.py` (21) — forge-side draft test → `run_skill_llm`. Clean.

### Cluster D — Forge / create path
- `create.py` (204) — `zipsa forge`/`create` entry: build prompt, wire ForgeServer + handlers → `run_host_served_container`. **Smell:** `build_create_prompt` dead (tests only); `run_create` dead alias.
- `forge_server.py` (195) — FastMCP host for forge (exec/run/promote/ask/confirm/choose/report), path-scoped staging. **Smell:** `tool_names()` hand-maintained literal.
- `create_server.py` (172) — **DEAD** predecessor of ForgeServer. Zero production callers (tests + docstrings only). → **#178**.
- `promote_skill_handler.py` (66) — `promote` tool: atomic move staging → `skills/<name>/`. Clean.
- `prompts.py` (165) — **belongs to run/legacy, not forge**: system-prompt + user-message renderer used by `executor.py`.

### Cluster E — Legacy executor + HITL
- `executor.py` (1640) — `DockerExecutor`: full legacy run lifecycle. **Smells:** god object; `_build_docker_command` 280 LOC dup of exec path; stranded `_print_dry_run`; 8 backward-compat shim delegators.
- `hitl_runner.py` (548) — `HitlServer`: per-run HTTP MCP server exposing all tools. **Used only by legacy `DockerExecutor`.** **Smells:** entire tool registry inline in 350-LOC `start()`; `_logged`/`_bind_free_socket`/`_ALLOWED_HOSTS` exported as de-facto shared utils to 3 other servers.
- `hitl_mcp.py` (356) — transport-agnostic HITL handlers + `HitlIO`. Genuine shared lib. **Smell:** dead memory handler classes (server reimplements inline); `ask_once` implemented twice.

### Cluster F — Skills model / install / config
- `skill.py` (291) — `Skill` abstraction + builds Claude Code runtime artifacts. **Smell:** `build_claude_json` does config-gen AND file-writing.
- `models.py` (325) — Pydantic `SkillManifest` schema (root + nested). **Smell:** `SkillSpec.phases`/`state_schema`/`PhaseSpec` doc-only, never enforced.
- `skill_files_handler.py` (100) — `write_skill_files` tool. Clean (defense-in-depth path checks).
- `skill_catalog_handler.py` (90) — `list_skills_catalog` tool + run stats. **Smell:** `<name>@<version>` enumeration dup.
- `install_health.py` (100) — `check_install` health classifier. **Smell:** `requires_set` count duplicates `resolve_requires` logic.
- `installer.py` (299) — install from GitHub / local. **Smell:** all intra-imports deferred (import-graph blind).
- `requires.py` (371) — per-user host config lifecycle (validate/classify/prompt/save). **Smell:** `carry_over_from_previous` dir-enumeration dup.
- `memory_store.py` (56) — JSON KV store (0600). **Smell:** no write lock (corruption risk under concurrent writers).

### Cluster G — Output / services / plugins / infra
- `renderer.py` (317) — event stream → terminal output. **Smell:** lenient `_extract_envelope` vs strict `envelope.parse_envelope_strict` diverge silently.
- `envelope.py` (163) — strict final-JSON-envelope parser. Clean.
- `summary.py` (130) — build/write `summary.json`. Clean.
- `artifact_handler.py` (106) — sandboxed `get_artifact` reads. Clean.
- `resume.py` (232) — resume eligibility + prompt. Clean.
- `limits.py` (260) — per-event turn/cost/time limit bookkeeping. Clean.
- `pricing.py` (57) — static model pricing table + cost estimate. **Smell:** silently lags Anthropic rate changes; unknown model → Opus fallback silently.
- `caller_context.py` (101) — ASGI middleware: Bearer-token → CallerInfo ContextVar. Clean.
- `dev_overlay.py` (82) — `ZIPSA_DEV_OVERLAY` mounts/env injection. Clean.
- `scheduling.py` (280) — macOS launchd backend for `zipsa schedule`. **Smell:** hardcodes `~/.zipsa/schedule-logs`.
- `paths.py` (190) — `~/.zipsa/` layout SSOT. **Smell:** exec run-dirs built inline elsewhere; `default_forge_skills_dir` un-cached `git rev-parse` per call.
- `runtimes/` (base/claude) — `AgentRuntime` ABC + registry + `ClaudeRuntime`. Live but single-impl (speculative).
- `hooks/pretooluse.py` — in-container PreToolUse tool-whitelist enforcement. Clean (standalone script).

---

## §4 Refactor backlog (candidate Issues — to prioritize together)

Ordered by leverage; effort S/M/L. (Not yet filed — we decide which to spawn.)

1. **[T4] Delete dead code** — `CreateServer`, `run_create`, `build_create_prompt`, dead `hitl_mcp` memory handler classes. **S.** (Partly = #178.) Lowest risk, immediate clarity.
2. **[T2] Shared `docker_argv.py`** — one builder for executor + exec_runner + host_served_container. **M.** Highest de-dup value; fixes `--add-host` drift.
3. **[T1] Split `cli.py` into command-group modules** + push `_render_run_record`/run-stats down to `renderer`/`core`. **M.**
4. **[T3] MCP server base** — extract socket/uvicorn/lifecycle + `_bind_free_socket`/`_ALLOWED_HOSTS` into a base/util; derive `tool_names()` from registration. **M.**
5. **[T1] Decompose `executor.py`** — start by deleting the 8 backward-compat shims (**S**), then extract limits/summary/hitl-lifecycle seams (**L**).
6. **[T5] Re-home misassigned files** — `phase_state`/`phase_allow`/`skill_validator_handler` → run/legacy grouping. **S.**
7. **[T6] Extract `_pep723.py`; de-dup child-spawn; `paths.skill_data_dirs`.** **S each.**
8. **[T7] Complete `paths.py` SSOT** — `exec_run_dir`/`exec_out_dir`; route `scheduling` logs through it. **S.**
9. **[T8] `Protocol` for injected handlers.** **S.**
10. **[T9] Honest doc-only surface** — gate/remove `SkillSpec.phases`/`PhaseSpec`; fix `run_log_handler` runtime hardcode; pricing staleness guard. **S.**

**The framing decision (do first, together):** §1 — the legacy-vs-new path. Most of T1/T2/T3/T5 are shaped by whether we're (a) unifying both engines onto shared primitives while legacy lives, or (b) actively deprecating `DockerExecutor`. That choice sets the order of everything below.
