# Empty user_query handling + 집사 intro — Design

**Date:** 2026-05-19
**Status:** Draft — pending user approval
**Scope:** `zipsa run <skill>` without a query no longer hard-errors at the
launcher. Skills opt into a sensible default via a new manifest field;
when neither user query nor default is present, the agent introduces
itself in the 집사 persona and elicits the user's request via HITL.

---

## Motivation

Today:

```
$ zipsa run hello-world
Error: user_input is required unless --shell is specified
Error: 1
```

Two problems compounded:

1. **Ugly error.** "Error: 1" is the exit code being printed by typer
   on top of the actual message. Looks broken.
2. **Hard fail is the wrong default for an agent platform.** zipsa is
   not a CLI tool that needs argv to do anything; it's an agent runtime
   where the agent CAN ask the user what they want. The pre-HITL
   reflex to refuse-on-empty-input is now redundant.

In addition, when `zipsa run <skill>` IS the right way to discover
what a skill does, we shouldn't dump a help-page-style printout —
zipsa is "집사" (a butler) and the intro should sound like it.

## Decisions

| # | Decision | Note |
|---|---|---|
| 1 | `metadata.description` becomes the official user-facing one-line intro. | Already exists on every skill; no new field. Skill authors should write it user-facing (not dev-facing). |
| 2 | New manifest field: `spec.default_query: string` (optional). | When user provides no input AND this is set, it becomes the user_query — agent runs the skill without an intro round. Skills with no per-request input (e.g. hello-world smoke test, "say hi") use this. |
| 3 | Runtime contract handles empty user_query. | When user_query is empty string AND no `default_query` was substituted, the contract instructs the agent: (a) introduce yourself as 집사, (b) name the skill and describe what it does, (c) `mcp__zipsa__ask` for the user's request, THEN do the normal phase work with that response. |
| 4 | CLI: drop the hard "user_input required" check. Pass empty string. Fix the "Error: 1" double-print. | One-line cleanup. |
| 5 | Non-TTY + no default_query + empty input → agent will try HITL ask → `HITL_UNATTENDED` → clean `status=failed` with `error.code="hitl_unattended"`. | Existing behavior; no special case needed. The agent's ask attempt naturally fails fast in non-interactive mode. |

## Why Option 2 (contract-side intro) over Option 1 (launcher-injected phase 0)

We considered injecting a Haiku-powered "phase 0" before the skill's
own phases for the intro. Decision (during brainstorming): keep the
intro inside the skill's first phase via runtime contract instructions.

**Why:** the agent already has the full SKILL.md loaded for phase 1.
It can give contextual examples ("어느 지역? 예: 서울, 도쿄, 시드니"
for the weather skill) using SKILL.md's own example block. A
launcher-injected phase 0 would only see metadata + the short
description — its prompts would be generic ("which city?" rather
than "어느 지역? 예: ...").

Cost trade-off acknowledged: the skill's declared model runs the
intro (could be Opus). For discovery flows the cost is paid once per
"fresh" invocation and the result feels noticeably more like an
agent and less like a CLI help page.

## User-facing scenarios

### Scenario A — skill has `default_query`, user provides nothing

```yaml
# hello-world manifest
spec:
  default_query: "Say hello."
```

```
$ zipsa run hello-world
# launcher substitutes user_query="Say hello." and runs the skill
# directly — no intro round
```

### Scenario B — no `default_query`, user provides nothing, interactive (TTY)

```
$ zipsa run weather

──── User input needed ────
[ask] 안녕하세요. 저는 집사입니다. 지금 선택하신 weather 스킬은
'어떤 지역의 현재 날씨든 알려드릴 수 있어요'. 어느 지역의 날씨가
궁금하신가요? (예: 서울, 도쿄, 시드니)
> 시드니
──── Resuming ────

# skill proceeds with user_query="시드니"
```

(The exact phrasing is the agent's, not a launcher template — the
contract just tells it what to do. Persona text and example
adaptation come from the agent reading SKILL.md.)

### Scenario C — no `default_query`, user provides nothing, non-TTY

```
$ zipsa run weather < /dev/null
# Agent attempts ask → HITL_UNATTENDED → clean failure
# Output:
# ✗ Phase 'main' aborted (status=failed): hitl_unattended
# (suggested error code: hitl_unattended, with user_facing_summary
#  pointing to either --shell or pass a query)
```

### Scenario D — user provides a query

```
$ zipsa run weather "시드니 날씨"
# Unchanged — runs exactly as today
```

## What changes in the codebase

| File | Change |
|---|---|
| `launcher/zipsa/cli.py` | Drop the "user_input required" check. When the positional `query` is omitted: substitute `spec.default_query` if set, else pass `""`. Fix the duplicated error/exit-code printout. |
| `launcher/zipsa/core/models.py` | Add `default_query: Optional[str] = None` to `SkillSpec`. No validator beyond type. |
| `launcher/zipsa/system-prompts/runtime-contract.md` | Add a section "Empty user_query → introduce yourself" that defines the 집사 persona behavior. |
| `skills/hello-world/manifest.yaml` | Add `default_query: "Say hi and confirm zipsa is working."` (single-source example). Rewrite `metadata.description` to be user-facing. |
| `skills/README.md` (or similar) | One paragraph: "metadata.description is the user-facing intro line. Write it for end users, not for fellow devs." |

No change to: executor, renderer, HITL plumbing, limits, memory, hooks.

## Empty-user_query contract text (proposed)

To add to runtime-contract.md:

```markdown
## Empty user_query

`user_query` in the execution context may be the empty string. That
happens when the user ran `zipsa run <skill>` with no arguments AND
the manifest didn't supply a `spec.default_query`. In that case,
your FIRST action in the FIRST phase must be:

1. Introduce yourself as `집사`, in the user's language (default
   Korean; switch to English if user_query later comes in English).
2. State the skill name and describe what it does, using
   `spec.purpose` or the SKILL.md overview. If SKILL.md has an
   "Examples" section, lift 1–2 examples into the ask prompt to make
   it concrete.
3. Use `mcp__zipsa__ask` (intent → tool mapping) to elicit the
   user's specific request. Treat the response AS the user_query
   for the rest of the run.
4. Then proceed with the skill's normal phase 1 work using that
   response.

If the ask returns a `HITL_UNATTENDED` error, end the phase with
`status=failed` and `error.code="hitl_unattended"`. Don't try to
guess what the user wanted.

Skills with a non-empty `spec.default_query` never enter this flow —
the launcher substitutes the default before the phase runs.
```

## YAGNI / out of scope

- **No new `intro:` or `examples:` manifest fields.** `description` +
  SKILL.md is enough for v1.
- **No launcher-side templated intro** (the dropped Option 1 path).
- **No mid-run "switch to intro mode"** — empty user_query only
  matters at run start. If a phase ends with non-ok and the user
  re-runs without a query, the SAME intro flow happens; no special
  resume hooks.
- **No language detection for the persona.** Skill's existing
  language-detection guidance in runtime-contract still applies
  (default Korean, switch by user's input). The 집사 line just adds
  to that.

## Test plan

Unit / pydantic:
- `SkillSpec.model_validate({...})` accepts and exposes
  `default_query` when present; defaults to None when absent.

CLI:
- `zipsa run <skill>` (no query) with skill that has `default_query`
  → invokes executor with user_input == default_query string.
- `zipsa run <skill>` (no query) without `default_query` → invokes
  executor with user_input == "".
- The old hard "user_input required" path no longer fires; "Error: 1"
  is no longer printed alongside the error.

Manual (interactive smoke):
- `zipsa run hello-world` → runs to completion with default_query
  substitution; no intro round; cost stays small.
- `zipsa run weather` (no query, TTY) → agent introduces itself as
  집사, asks for region with a SKILL.md-derived example, runs the
  skill with the response.
- `zipsa run weather < /dev/null` → `status=failed`,
  `error.code=hitl_unattended`, no infinite wait.

## Open questions

- Should `default_query` be allowed to override an explicit
  user-provided query in some `--use-default` mode? **No for v1.**
  An explicit user query always wins; the default is only a
  fallback for the empty case.
- Should `metadata.description` be required (validator)? Today it's
  optional in some skills. **Keep optional for now**, but the
  contract should say "if description is missing, fall back to
  spec.purpose for the intro."
