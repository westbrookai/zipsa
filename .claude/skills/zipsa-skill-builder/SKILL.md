---
name: zipsa-skill-builder
description: Author a zipsa skill (zipsa-dist/* phases) from a user's intent. Use whenever the user asks to create a new zipsa skill or migrate a legacy skill to the exec format — never hand-write zipsa-dist files without this workflow.
---

# zipsa-skill-builder

You are authoring a zipsa skill: a directory of sequential phases
(code and/or LLM) that `zipsa exec` runs deterministically.

**The contract lives in [skills/AUTHORING.md](../../../skills/AUTHORING.md).
Read it first, every time** — it changes as the platform grows, and
authoring against a remembered version of it is how broken skills get
written.

## Workflow

### 1. Clarify the intent

The user's first description is almost always ambiguous. Don't guess —
ask. One focused multiple-choice question at a time, 2–4 questions
total, covering whichever of these are genuinely unclear:

- What exactly should happen? (scan the raw wording for ambiguous
  nouns/verbs and offer concrete interpretations)
- What's the input? (`user_query` meaning, empty-query behavior)
- What's the output? (result shape; artifact files?)
- Where's the boundary? (what the skill explicitly does NOT do)

Then restate the refined intent in one sentence and confirm it.

### 2. Check what exists

Look at `skills/` for a skill that already does this or comes close.
If one does, say so and stop — extending beats duplicating.

### 3. Decide the phase split

The thumb rule from the rethink: **deterministic work goes in code
phases, reasoning goes in LLM phases.** Most skills are one of:

- `1.do.py` — pure deterministic, no LLM at all (legitimate!)
- `1.fetch.py` + `2.report.md` — fetch/compute, then natural-language
  output (the weather pattern)
- `1.validate.py` + `2.work.py` + `3.summarize.md` — bigger pipelines

Per phase, pick the language that fits (§3 of AUTHORING.md): Python
for APIs/parsing, bash for CLI-wrapping, ts/js for SDK ecosystems.
An LLM phase only earns its place when the step genuinely needs
inference — if a regex or a lookup table would do, it's a code phase.

Check §8 of AUTHORING.md (platform gaps): if the intent needs HITL,
credentials, or scheduling, stop and tell the user it's blocked on a
platform feature — don't hack around it.

### 4. Write the files

- `SKILL.md` — 2–4 sentences of intent prose + a run example. For
  humans; the runtime never reads it.
- `zipsa-dist/<n>.<slug>.<ext>` — real, working code. No TODO
  skeletons. Follow the stdin/stdout contract exactly (AUTHORING.md
  §2). Code phases: validate inputs, fail loudly with stderr + exit 1.
  LLM phases: instruction + result keys only — the runtime injects
  the input payload and output rule.

### 5. Test for real

**Inside `zipsa create`** (you're in the runtime container, files are
in a staging dir): call **`mcp__zipsa__exec`** with the staging path
and a representative query. The host runs the skill the real way (a
fresh runtime container per phase) and hands back the result —
read its `phases[]` and `result`. If the skill reads a credential or
data file, pass `mounts` (HOST:CONTAINER strings, e.g.
`~/.zipsa/credentials/x.json:/mnt/creds/x.json`) so it's testable for
real.

**You run headless** — whenever you need the user to do or answer
something (set up an API token, confirm a name, paste a value), you
MUST call `mcp__zipsa__ask`/`confirm`/`choose` to block and wait.
Never just print a request and end your turn: if you stop calling
tools the session ends and the user can't reply.

**Outside** (direct Claude Code session, files already under skills/):
```bash
zipsa exec <skill-path> "<representative query>" --local   # fast loop
zipsa exec <skill-path> "<representative query>"           # docker, the real thing
```

Either way, run at least: one happy path per language the user will
actually use (e.g. Korean and English if the skill is language-aware),
the empty-query case, and one failure case (bad input → clean exit 1
+ readable stderr). A code phase taking LLM-scale time is a smell.

Iterate with the user until they're satisfied. Their feedback is new
raw intent — go back to step 1's discipline (ask, don't guess) for
anything vague.

### 6. Finalize

The skill name is decided **last** — only once the user is happy. Until
then the draft has no committed name.

**Inside `zipsa create`**: propose a kebab-case name based on what you
built, confirm it with the user, then call **`mcp__zipsa__promote`**
with the staging path + name. That moves the skill into skills/<name>/.

**Outside**: place the files at skills/<name>/ yourself.

Done means:
- [ ] the real (docker) run passes the happy path
- [ ] failure case exits non-zero with a clear stderr message
- [ ] `SKILL.md` matches what the skill actually does
- [ ] no dead files (no manifest.yaml, no pyproject.toml, no templates)

## Migrating a legacy skill

Same workflow, plus:
- The old `SKILL.md` (LLM-instruction format) and `manifest.yaml` are
  the *requirements document* — mine them for intent, edge cases, and
  output shapes, then delete them in the same change (policy:
  migrated skills go exec-only).
- Check nothing references the skill as a child:
  `grep -rn "<name>" skills/*/manifest.yaml`
- Uninstall the legacy symlink: `zipsa uninstall <name>`
