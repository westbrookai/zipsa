# Skill Runtime Redesign: the Forge model

> Design doc. Resolves the "is `zipsa exec` the right execution model?"
> question raised during dogfooding (2026-06-15). Supersedes the
> standalone-`exec` framing from the Phase-0 reset.

## Context

`zipsa exec` (Phases 0–2, built this session) made skill execution a
**deterministic pipeline**: fixed-order phases, code phases run blindly,
`.md` phases are single-shot `claude -p --max-turns 1` (no loop, no
tools). Dogfooding x-post through `zipsa create` confirmed exec handles
atomic deterministic tasks well — but surfaced the deeper problem:
**exec removed the agent.** An agent's essence is the loop (observe →
judge → act → repeat); exec has none at run-time. So exec, *as a
standalone execution model*, is wrong — it is a skill-*runner*, not an
agent runtime. Agency had been pushed entirely to authoring time,
leaving run-time skills as inert "code lumps."

The fix is not to discard exec but to **put the LLM back on top** as the
run-time orchestrator, and to recognize the exec runner (container
isolation, language-independent stdin/stdout contract, credential
mounts) as the right *tool-execution substrate* — just not the whole
model.

## Core ontology

**A skill = LLM + `SKILL.md` (constitution) + `scripts/` (validated
deterministic tools).** The presence of the run-time LLM is the
*defining* property of a skill. Remove the LLM and the artifact is no
longer a skill — it is a plain script/program (run it directly; zipsa is
not needed).

Why the LLM is non-negotiable: the happy path may be deterministic, but
the **unhappy path is unpredictable** (API schema drift, expired creds,
ambiguous results). A pure script dies with `exit 1`. A skill's LLM
judges (retry / skip / abort) and communicates the failure to the user
in natural language. That run-time judgment — needed exactly when you
cannot predict it — is the skill's entire value over a script.

Cost is absorbed, not avoided: the LLM is always present but its weight
**scales**. A fully-hardened happy path is a one-turn "call A, then B" —
a cheap model, near-free. Errors/novelty invoke heavier reasoning. We do
**not** skip the LLM for "deterministic" skills: skipping would start
cold on errors (losing run context) and blur the skill/script boundary.

## Artifacts: the triple

| File | Role | Mutated by |
|---|---|---|
| `INTENT.md` | the *why* — user requirements; the acceptance spec; defines "satisfied" | **user** |
| `SKILL.md` | the *how* — the constitution; the run-time LLM's instruction | **forge** |
| `scripts/` | the *what* — validated deterministic tools the LLM calls | **forge** |

`INTENT.md` stays a separate file (not folded into `SKILL.md`):
- It is the forge's persistent, lossless **north star** — the loop's
  exit condition is "INTENT satisfied," and re-forging an existing skill
  needs the original requirements. Folding it in means a hardened
  `SKILL.md` entangles spec with implementation → lossy reverse-engineering.
- It enforces a clean **mutation boundary**: the user edits `INTENT.md`;
  the forge edits `SKILL.md`/`scripts/`. Separate files make this
  enforceable; a "don't touch the preamble" convention inside one file
  is weaker.
- Run-time still gets the "why": load `INTENT.md` alongside `SKILL.md`
  (small, aids unhappy-path judgment), or have the forge restate intent
  in `SKILL.md`'s preamble.

## Run-time — `zipsa run <skill>`

The run-time LLM executes `SKILL.md` as its instruction (constitution)
and calls scripts via the `mcp__zipsa__exec` tool. Architecturally this
is the **same machinery as create-time**, with `SKILL.md` as the
instruction instead of an authoring prompt:

| | run-time | create-time (forge) |
|---|---|---|
| LLM | headless claude | headless claude |
| instruction | **`SKILL.md`** | authoring prompt + `INTENT.md` |
| tools | exec (+ HITL on demand) | exec + promote + HITL |
| output | skill executed | skill authored/refined |

Scripts execute through the existing exec runner (container isolation,
language dispatch by extension, stdin `{ctx, prev}` JSON, last-JSON-line
result, creds via mounts). The LLM selects a script by name through a
single **generic** `mcp__zipsa__exec` tool — not per-script typed tools
(YAGNI).

## Create-time — `zipsa forge` (was `zipsa create`)

The forge is an **iterative refinement loop that nests run-time as its
test step**:

```
seed: INTENT  (and/or an existing SKILL.md + scripts)
loop:
  1. authoring LLM (AUTHORING.md + INTENT + prior run results/feedback)
       → generate/modify SKILL.md + scripts
  2. run-time: execute the just-built skill (the test)     ← nests run-time
  3. observe the run record (result.json, stdout/stderr, artifacts)
  4. user AND authoring-LLM both satisfied?  — no → goto 1 (refine)
exit: both satisfied
```

Inputs generalize, so one loop covers every case:
- `INTENT` only → greenfield
- `INTENT` + existing skill → refine/extend
- legacy/imported skill → migrate

### Importing a skill with no `INTENT.md`

When a skill arrives without `INTENT.md` (external, legacy,
hand-authored), the forge first recovers intent:

```
import (SKILL.md + scripts, no INTENT.md)
 → forge drafts INTENT.md statically from SKILL.md + scripts
   [+ optional, safety-gated observed run to ground-truth real behavior]
 → user reviews/edits the draft → authoritative INTENT.md
 → forge refine loop against INTENT
```

Static extraction is the default (safe, no creds, no side effects). An
observed run is *optional enrichment*, gated on no-side-effects/dry-run +
available inputs — never blindly run an unknown side-effecting skill to
learn its intent. The confirmation step is **authoring, not mere
approval**: extraction yields "what the skill does"; the user reconciles
it to "what I want."

## Command surface

| Command | Purpose |
|---|---|
| `zipsa run <skill>` | Execute a skill (LLM + SKILL.md + scripts). Replaces legacy `run` and standalone `exec`. |
| `zipsa forge` | Author / refine / migrate a skill (the loop above). Was `zipsa create`. |
| `zipsa install` / `list` / `schedule` / `view` | Management (unchanged in spirit). |

**Removed:** the user-facing `zipsa exec` command. The capability
survives as the internal `mcp__zipsa__exec` tool. The exec *engine*
built this session (runner, container model, creds mounts, run logging)
is fully reused as substrate; only the CLI command is retired.

## Relationship to existing code

- **Reused as substrate:** `exec_runner` (script execution),
  `mcp__zipsa__exec` tool + handler, the host-MCP-server pattern, the
  container/creds-mount model, exec run logging (run records feed the
  forge's observe step).
- **Redesigned:** `zipsa run` becomes the LLM + `SKILL.md` + exec
  orchestrator. This is close to — and supersedes — the *legacy*
  `zipsa run` (which also ran an LLM over `SKILL.md`); the difference is
  the formal INTENT → forge hardening loop and scripts-as-validated-tools.
- **Retired:** the user-facing `zipsa exec` command; the
  "deterministic-pipeline-is-the-whole-model" framing.

## Determinism, cost, scheduling

Hardening reduces run-time nondeterminism by codifying judgment into
`SKILL.md` rules and pushing work into validated scripts — a mature
skill's run is bounded to thin orchestration glue. Combined with a cheap
model, scheduled skills (e.g. the umbrella alert) stay affordable
without abandoning the LLM, which remains available for the unhappy path
it cannot predict.

## Open questions / out of scope

- Exact `SKILL.md` format as a "constitution" (prose? a declared script
  catalog + when-to-call rules?) — pin in the implementation plan.
- Model-tier selection (how a skill declares "cheap model for the happy
  path") — likely a small field; ties into the deferred metadata question.
- Whether `INTENT.md` is passed to run-time or only restated in
  `SKILL.md`'s preamble — decide during implementation.
- Migrating the existing legacy skills (daily-progress, etc.) runs
  through the forge import path — separate execution work.
- **This is a large redesign; implementation must be decomposed into
  phased plans** (run-time orchestrator → forge loop → import path →
  migrations), each its own plan via writing-plans.

## Verification

- **Run-time:** a skill executes end-to-end via `zipsa run` — the LLM
  follows `SKILL.md`, calls scripts, handles a happy path AND an
  injected error path with a graceful user-facing message.
- **Forge:** greenfield (INTENT → skill), refine (existing skill +
  feedback), and import (no-INTENT skill → extracted+confirmed INTENT →
  refined) each produce a working skill verified by `zipsa run`.
- **Reuse:** existing exec-runner tests still pass; `mcp__zipsa__exec`
  behavior unchanged.
