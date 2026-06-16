# Forge authoring workflow: feasibility gate + prerequisites step

> Design doc. Adds an early "can we build this, and what does it need?"
> step to the forge authoring workflow (`skill-builder.md`). Surfaced by
> a live `zipsa forge` E2E (2026-06-16) that timed out.

## Context / problem

During a live `zipsa forge` run (build a Sydney bus-departure → Telegram
alert), the authoring agent asked **one** clarifying question (bus route
+ time) and blocked on it. The host relay took ~15 min to answer (the
operator was researching the route), but the container agent's
`mcp__zipsa__ask` MCP call has a **10-min timeout** (`_MCP_TOOL_TIMEOUT_MS
= 600_000`). The call timed out, the agent re-asked, then gave up —
printing the *full* list of things it actually needed (TfNSW API key,
Telegram bot token + chat id, polling window) and exiting. The skill was
never built.

Two root causes in `launcher/zipsa/authoring/skill-builder.md`:

1. **No prerequisites step.** The workflow never tells the agent to
   enumerate, up front, everything the skill needs from the outside
   (external API keys + where to register, credential files + format +
   mount path, accounts, per-user config) and ask for them in one batch
   before drafting. So the agent dribbles questions and discovers missing
   inputs late — which (a) multiplies HITL round-trips, and (b) only got
   surfaced here when the agent gave up.

2. **No real feasibility gate.** The only "can this be done?" guidance is
   one line in the *script-split* step (line 48–51): "check AUTHORING §9
   platform gaps … tell the user and adapt." It covers only the
   enumerated not-yet-supported features (env injection for code scripts,
   branching, in-skill scheduling, composition) and is buried mid-step.
   There is no general instruction for "this request fundamentally can't
   be done with an LLM + deterministic scripts — say so honestly,
   propose the closest feasible version, renegotiate scope; don't
   silently build something that can't work."

## Goal

Add a single early workflow step — **"Check feasibility & gather
prerequisites"** — between *Clarify the intent* and *Decide the script
split*, so the forge agent decides up front whether the intent is
buildable and collects every external dependency before drafting. This
both improves skill quality and makes the HITL relay robust (fewer,
batched round-trips → far less timeout exposure).

## Design

### Decisions

- **One combined step** (feasibility + prerequisites), not two: both
  answer "before you draft — can we, and what's needed?".
- **Placement:** new **Step 2**, after *Clarify the intent* (Step 1),
  before the current *Decide the script split*. Renumber the rest
  (split → 3, write files → 4, test → 5, finalize → 6).
- **Absorb the §9 check:** move the existing platform-gaps line out of
  the script-split step into the new feasibility step (it's a feasibility
  concern), generalized beyond the enumerated list.
- **Infeasibility handling = honest renegotiation, not silent best-effort
  or hard refusal:** name the limit → propose the closest feasible
  version (or the platform feature it would need) → get the user's
  agreement on the reduced scope via `ask`/`confirm` → reflect the agreed
  scope in `INTENT.md`. Never build something that silently can't work.
- **Prerequisites are gathered before drafting** and recorded so they
  carry into `INTENT.md` / `SKILL.md` (e.g. the required `--mount` and
  the credential-file format).

### New Step 2 text (to insert into `skill-builder.md`)

```markdown
### 2. Check feasibility & gather prerequisites

Before drafting anything, decide **whether** the intent is buildable as
a zipsa skill (an LLM following `SKILL.md` + deterministic scripts, run
on demand) and **what** it needs from the outside.

**Feasibility.** If part of the intent needs a capability that isn't
there — AUTHORING §9 platform gaps (in-skill HITL, env injection for
code scripts, branching, composition, in-skill scheduling), or anything
fundamentally outside "LLM + a script that makes API calls" (a
persistent background daemon, hardware access, an action someone must
take in the physical world) — do NOT quietly build a broken version.
Use `ask`/`confirm` to: name the limit, propose the closest feasible
version (or the platform feature it would need), and agree on the scope.
Workarounds for the common gaps: secrets → a mounted file (§6);
scheduling → stays out of the skill, run on demand (or via
`zipsa schedule`).

**Prerequisites.** Enumerate everything the skill needs from outside and
ask for it now, in order, before drafting — don't discover a missing key
at test time:
- external **API keys** — name the service and the registration URL
  (e.g. opendata.transport.nsw.gov.au) and ask the user to obtain one;
- **credential files** — state the exact JSON shape and the mount path
  the scripts will read (e.g. `~/.zipsa/credentials/<x>.json` →
  `/mnt/creds/<x>.json`), per §6;
- **accounts / per-user config values** — anything that varies per user
  (locations, ids, thresholds, schedules).

Record the agreed scope and the prerequisites so they land in `INTENT.md`
and in `SKILL.md`'s run example (the `--mount` it needs).
```

(Exact wording may be tightened during implementation; the required
*content* is: feasibility-gate-with-honest-renegotiation + upfront
batched prerequisites with acquisition instructions.)

### Companion edits

- **Step 3 (was 2) "Decide the script split":** remove the now-duplicated
  AUTHORING §9 platform-gaps paragraph (moved into Step 2); leave a short
  pointer if useful.
- **Step 1 "Clarify the intent":** it currently says to write `INTENT.md`
  at the end of step 1. Move/relax that so `INTENT.md` is written *after*
  feasibility may have reduced scope (i.e. capture INTENT once scope +
  prerequisites are settled — end of Step 2, or in Step 4 "Write the
  files" where INTENT.md already appears). Keep INTENT.md a first-class
  artifact; just don't freeze it before the feasibility/prereq pass.
- **`AUTHORING.md`:** no change required (§6 secrets + §9 gaps already
  exist and are referenced). Confirm §9's list matches what Step 2 cites.

## Files

- Modify: `launcher/zipsa/authoring/skill-builder.md` (the only behavioral
  change — it's inlined verbatim into the forge prompt by
  `create.py:build_forge_prompt`).
- Verify/adjust tests that assert the bundled workflow is inlined:
  `launcher/tests/test_create.py` (`TestBuildCreatePrompt`),
  `launcher/tests/test_forge.py` (`TestBuildForgePrompt`). If any assert
  exact heading text / step numbers, update them; prefer asserting on
  stable substrings (e.g. "feasibility", "prerequisites", "INTENT.md")
  rather than step numbers.

## Out of scope (separate follow-ups)

- **Relay / MCP-timeout robustness.** The deeper issue that the agent
  *gives up and exits* when HITL is slow (10-min `ask` timeout) is real
  but separate. The prerequisites step mitigates it (batched questions →
  fewer, shorter waits) but doesn't fix it. Options for later: raise
  `_MCP_TOOL_TIMEOUT_MS`, or make the agent tolerate a timed-out `ask` by
  retrying instead of bailing. Note in BACKLOG.
- The **bus-departure skill itself** — that's a separate forge run once
  this workflow improvement lands.

## Verification

- `cd launcher && uv run --extra dev pytest tests/test_create.py
  tests/test_forge.py -q` — the inlined-doc assertions pass with the new
  step.
- Full suite green: `uv run --extra dev pytest`.
- (Manual, optional) Re-run the bus-departure `zipsa forge` E2E with the
  prerequisites gathered up front; confirm the agent asks for route+time,
  TfNSW key, Telegram creds, and polling window in one early batch rather
  than discovering them at the end.
```
