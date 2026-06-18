# exec-skill metadata format + loader (keystone) (#156)

Part of epic **#155 — First-class exec skills**. This is the keystone:
it defines the metadata that replaces `manifest.yaml` for exec/run-format
skills, and a loader that the lifecycle commands (#157–#161) build on.

## North star (from #155)
A zipsa skill is **one portable artifact, two execution modes**: it runs
under the zipsa runtime (docker, deterministic exec, HITL tools,
scheduling, logging) AND runtime-free under plain Claude / Claude Code
(the agent reads SKILL.md and runs the scripts with its own bash, made
realistic by PEP 723 + `uv run --script`). **The runtime is an enhancer,
not a requirement.** This spec's litmus test:

> **`rm -rf zipsa/` from a skill directory must leave a valid, runnable
> Agent Skill.**

## Problem
exec/run-format skills carry no machine-readable identity. The legacy
`manifest.yaml` held name/version/purpose/requires/mcp/limits/etc., but
exec skills have no manifest, so `Skill.load` fails and every
manifest-bound command (install, run-by-name, list, validate, discover,
configure, connect) is closed to them. We need (a) a metadata format and
(b) a loader — the exec-world analog of `Skill.load`.

## Two-layer metadata (confirmed)

Metadata splits by **owner/audience**, verified against the official
Agent Skills spec (platform.claude.com — required frontmatter is `name`
+ `description` only; no `version`, no arbitrary `metadata` block) and
the Claude Code frontmatter reference (code.claude.com — adds optional
`model`, `allowed-tools`, `disallowed-tools`, etc.).

### Layer 1 — SKILL.md YAML frontmatter (standard; honored by plain Claude)
Standard Agent Skills / Claude Code fields. Plain Claude Code reads and
honors these natively in runtime-free mode, so they belong here:

```yaml
---
name: bus-575-hornsby-alert            # standard (required)
description: <what it does + WHEN to use it>   # standard (required)
allowed-tools: Bash(python3:*) Write   # optional (CC-honored)
model: claude-haiku-4-5-20251001       # optional (CC-honored)
---
```
- `name`, `description` — the only fields the Agent Skills standard
  requires. `description` absorbs the legacy `purpose` (what + when).
- `allowed-tools` / `disallowed-tools`, `model` — optional Claude Code
  fields; putting them here means plain Claude Code honors them
  runtime-free (a north-star win) and zipsa reads them too.

### Layer 2 — `zipsa/package.yaml` (zipsa-only sidecar; plain Claude ignores)
What the standard does not cover — zipsa execution/lifecycle:

```yaml
version: 0.1.0              # REQUIRED — (frontmatter name + this) = identity
author: westbrookai        # optional — registry/discovery
tags: [transit, telegram]  # optional — registry/discovery
limits:                    # optional — zipsa runtime guardrails
  max_turns: 6             #   (Claude Code has no cost/turn limits)
  max_cost_usd: 0.05
  timeout_seconds: 60
requires:                  # optional — host dirs (prompt + save + mount, folded)
  project_roots:
    type: list[directory]  # v1 types: directory | list[directory]
    prompt: "Which dirs contain your git projects?"
    container_prefix: /projects/   # list → <prefix>/<basename> per item
    mode: ro                       # single dir uses `container: /path`
```
- **`version` is the only required field.** It is NOT a standard
  frontmatter field, so it lives here; identity = `frontmatter.name` +
  `package.yaml version`.
- **`requires` folds the mount in.** A directory requirement carries its
  own container mapping (`container` or `container_prefix`, `mode`,
  optional `preserve_host_path`) — there is no separate `mounts:`
  section. v1 supports `directory` and `list[directory]` (the cases that
  need mounting); other types (`string`, `file`, …) are added when a real
  need appears. Runtime-free, the mount fields are ignored — the
  requirement just names a host path the agent uses directly.
- **Dropped vs legacy manifest:** `purpose` (→ description),
  `instructions` (always SKILL.md), `phases` (derived from
  `scripts/<n>.<slug>.<ext>` filenames), `apiVersion`/`kind`, `tools`
  (→ frontmatter `allowed-tools`), `model` (→ frontmatter). `mcp` and
  `children` are **deferred** (see Out of scope).

## What `zipsa/package.yaml` is
It is a **package manifest** — the deployable-unit metadata zipsa needs to
*identify (name+version), provenance (author/tags), provision (requires),
and bound (limits)* the skill. It is NOT a behavior spec (that is SKILL.md)
nor a requirements doc (the `requires` section is just host-provisioning
config). The four parts of a skill: **zipsa/INTENT.md** = why (original
intent / forge provenance); **SKILL.md** = how (instructions/constitution,
mechanism-agnostic); **scripts/** = what (implementation);
**zipsa/package.yaml** = packaging/lifecycle. It is to a zipsa skill what
`package.json` / `pyproject.toml` / `Cargo.toml` / `Chart.yaml` is to a
package — hence the name `package.yaml`.

## INTENT.md — the why (kept)
`zipsa/INTENT.md` records the original user intent + why the skill exists.
**Kept, not dropped.** Distinct from `description`: `description`
(frontmatter) is a short discovery blurb (what + when) the orchestrating
agent reads to decide whether to invoke; INTENT.md is the longer-form
requirements/why that (a) is forge's input and the bar for its
iterate-to-satisfied loop, (b) gives humans provenance, (c) is optional
extra context for the run-time LLM. It lives in `zipsa/` (forge-owned
provenance, not part of the portable Agent Skill payload — preserves the
litmus); the run path may inject it, plain Claude ignores it (running needs
SKILL.md, not the why).

## Skill directory layout (option Y — CONFIRMED)

```
skills/<name>/
├── SKILL.md          # standard frontmatter + mechanism-agnostic instructions
├── scripts/          # standard bundled-scripts location; portable phase scripts
│   ├── 1.fetch.py    # filename = order + id + slug (phase ordering convention)
│   └── 2.report.md
└── zipsa/            # zipsa-only sidecar; plain Claude ignores it
    ├── package.yaml  # the package manifest (identity/version/requires/limits)
    └── INTENT.md     # forge provenance — the "why" / original intent
```
- Replaces the current single `zipsa-dist/` dir: portable scripts move to
  the standard **`scripts/`**, zipsa-only metadata goes to **`zipsa/`**.
  This satisfies the litmus test (`rm -rf zipsa/` → still a valid Agent
  Skill) and is audience-honest (scripts = portable, zipsa/ = runtime).
- Phase ordering stays a filename convention (`1.`, `2.`); SKILL.md
  (mechanism-agnostic) describes the order in prose; the zipsa exec engine
  infers it from the numbering.

### Resolved
- **scripts location = Y** (confirmed): scripts in the standard `scripts/`,
  zipsa-only metadata in `zipsa/`. (Rejected X = everything in one `zipsa/`
  dir — cohesive but non-standard, makes the skill look zipsa-coupled,
  weakens the north star.)
- **sidecar = `zipsa/package.yaml`** (confirmed): named by role — the
  `zipsa/` dir is the "zipsa zone" marker, `package.yaml` says what the
  file is (the package manifest).

## Mechanism-agnostic SKILL.md (principle, enforced here)
For two-mode portability, **SKILL.md must never name `mcp__zipsa__*`
tools** (those are runtime-specific). It states intent + ordered steps
referencing the scripts; the runtime maps "report progress" →
`mcp__zipsa__report`, plain Claude maps it → conversation. (NOTE:
`bus-575-hornsby-alert`'s SKILL.md currently violates this and is the
migration exemplar to fix. See memory `feedback_skill_md_abstraction_level`.)

## The loader (this issue's deliverable)
A loader — the exec-world analog of `Skill.load` — that:
1. Resolves a skill directory (no `manifest.yaml` required; reuse
   `_is_exec_format`, which already treats manifest.yaml presence as the
   legacy marker).
2. Parses SKILL.md frontmatter (YAML) + `zipsa/package.yaml` (YAML).
3. Validates into a Pydantic model (e.g. `ExecSkill`) with: `name`
   (frontmatter), `description`, `allowed_tools`/`disallowed_tools`,
   `model` (frontmatter); `version` (required), `author`, `tags`,
   `limits`, `requires` (package.yaml). Missing `version` or `name` →
   clear error.
4. Exposes identity (`name`, `version`) for install/run-by-name and the
   `requires`/limits for configure/run.

No CLI behavior change in THIS issue beyond the loader + its tests; the
commands that consume it are #157–#161.

## Coexistence / migration
- Legacy manifest skills keep using `Skill.load`; exec skills use the new
  loader. Dispatch by `_is_exec_format` (manifest.yaml present → legacy).
- **Directory rename is a migration:** `zipsa-dist/` → `scripts/` +
  `zipsa/package.yaml`. Touches `exec_runner` (phase glob path),
  `AUTHORING.md`, the skill-builder workflow, and every existing exec
  skill (hello-world, weather, dad-joke, agenthud-report,
  wahroonga-umbrella-alert, bus-575-hornsby-alert). Stage it so exec keeps
  working throughout; consider a transition window where the runner
  accepts both `scripts/` and legacy `zipsa-dist/`.
- **INTENT.md moves** skill-root → `zipsa/INTENT.md`: update
  `build_run_prompt` (currently reads `skill_root/INTENT.md`) and forge's
  promote (which writes it) to the new path.

## Out of scope (parked — see #156 comment / #155)
- The data-passing / orchestration substrate: node unification, the typed
  result envelope, routing model, state model, and the **script
  invocation contract** (the current `{ctx,prev}` stdin protocol). Parked
  until a concrete use case.
- `children` / composition; `mcp` server declarations (add when #161
  needs them).
- The lifecycle commands themselves (#157 install/run-by-name, #158 list,
  #159 validate, #160 discover, #161 configure/connect).

## Tests
- Loader parses a skill with SKILL.md frontmatter + `zipsa/package.yaml`
  into the model; identity = name+version.
- Missing `version` (or missing `name`) → clear, specific error.
- `requires` with a `list[directory]` + `container_prefix` validates;
  mount fields land on the requirement (no separate mounts section).
- frontmatter `allowed-tools` (string AND YAML-list forms), `model` parse.
- A skill WITH `manifest.yaml` is routed to legacy (loader not used) by
  `_is_exec_format`; a skill withOUT one loads via the new loader.
- Litmus (documentation/structural test): a fixture skill with `zipsa/`
  removed still has SKILL.md + scripts/ and is structurally a valid Agent
  Skill (name+description present, scripts resolvable).

## Decisions recap
1. scripts location: **Y** — `scripts/` + `zipsa/`. ✅ confirmed.
2. sidecar filename: **`zipsa/package.yaml`** (package manifest). ✅ confirmed.
3. `allowed-tools` semantics: Claude Code's `allowed-tools` GRANTS auto-approval (not a restrict-allowlist like legacy `tools.builtin`). Hard tool RESTRICTION (if wanted) → `disallowed-tools` or zipsa-side enforcement. **Deferred to #159 (validate).**
