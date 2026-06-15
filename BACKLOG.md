# Backlog (cross-cutting)

Strategic and **cross-cutting** follow-ups that span more than one
component (launcher + runtime + skills + web). Component-local backlog
items live with their component:

- [`launcher/BACKLOG.md`](./launcher/BACKLOG.md) — CLI, executor, exec
  runner, auth, tests
- [`skills/BACKLOG.md`](./skills/BACKLOG.md) — skill migrations &
  authoring-content debt
- `runtime/` and `web/` get their own `BACKLOG.md` when they accrue
  items (none yet)

Add new items at the bottom; remove items when they ship.

**Resolved items** are removed when the fix lands. To find the
design/discussion of a shipped item, run `git log --grep "BACKLOG"` or
check the resolving PR's title.

---

## Skill ↔ launcher independent release / distribution (2026-05-19)

**Today.** Skills live at `skills/<name>/` inside the launcher repo.
"Releasing" a skill change = a launcher-repo PR + merge. There's no
notion of a skill version that's independent of the launcher repo's
state at the time `zipsa install` ran.

This couples release cadence in both directions:

- A 1-line typo fix in `skills/weather/SKILL.md` requires a launcher PR.
- A new skill feature that depends on a new launcher field ships
  intertwined with launcher code, because shipping the manifest alone
  is a no-op for users until the launcher knows the field.

**Why this matters when zipsa goes public.**

- Community contributors who want to publish "their own skill" can't —
  they'd PR into the main repo or fork it.
- Skill iteration speed is capped at launcher review speed.
- No "skill marketplace" or registry concept; every install resolves
  via hardcoded GitHub paths.
- Compatibility breakage is invisible: a skill written today against
  launcher v0.1 will silently misbehave on launcher v0.3 unless we
  introduce a launcher-API-version negotiation.

**Approaches to consider when this is picked up.**

1. **Skills repo separate from launcher repo.** Lightest split:
   `westbrookai/zipsa-skills` holds `<name>/` directories. `zipsa
   install <name>` reads from there. Adds indirection without forcing a
   registry. Per-skill GitHub releases (tags) become meaningful.

2. **One repo per skill.** Maximum decoupling, matches `npm` / `pip` /
   `cargo`. `zipsa install <name>` resolves name → registry → repo URL →
   tag. Heavy for v1, best for v3+.

3. **Status quo + skill tags within the same repo.** Each skill gets
   its own tag namespace (e.g. `skill/weather/0.3.2`). Cheaper than a
   new repo. Doesn't solve contributor onboarding.

4. **Launcher API versioning + skill compatibility declaration.**
   Orthogonal to where skills live: add `min_launcher_version` (or a
   capabilities list) to the manifest. `zipsa install` refuses skills
   the running launcher can't handle. Should ship regardless of which
   distribution model we pick.

**Hard design questions.**

- **Skill identity:** name alone or namespaced (`westbrookai/weather`)?
- **Trust model:** can any GitHub repo be installed? Sandbox is strong
  (tool allowlist, network allowlist, Docker isolation), but
  credentials in `~/.zipsa/.env` could be exfiltrated by a malicious
  skill that asks the user to set unrelated env vars.
- **Update model:** explicit `zipsa update <name>` vs. auto-pull vs.
  pinned versions only.
- **Backward compatibility:** how does the in-repo `skills/` directory
  retire — sudden cut, or both supported for N months?

**Triggering event.** When zipsa is first shared publicly, OR when a
second person/team wants to publish a skill that isn't westbrookai's.
Whichever comes first.

**Test plan.** Define a target distribution model. Stand up the chosen
split (e.g. extract `skills/` to a separate repo). End-to-end test:
install a skill from the new location, run it, upgrade it to a new tag,
run again.

---

## Standalone runtime: own the system prompt, plug in any model backend (2026-05-20)

**Vision.** Today zipsa is a thin layer over Claude Code: it invokes
`claude --print` with `--append-system-prompt` carrying the runtime
contract + SKILL.md (legacy `zipsa run`), and `claude -p` for exec LLM
phases. The agent's identity stays "Claude Code with appended
instructions" and Anthropic's default system prompt still runs in front
of ours.

The architectural endpoint: zipsa **fully owns the system prompt** and
**doesn't depend on Claude Code as the runtime layer**. Possible
backends:

- Anthropic API directly (no `claude` CLI in the loop)
- Codex CLI (OpenAI)
- Gemini CLI (Google)
- **`pi-mono`** — open-source agent runtime that OpenClaw uses;
  attractive because zipsa would no longer be tied to a vendor's CLI
  lifecycle

**What this unlocks.**

- "zipsa" becomes a first-class product identity (not a CLI wrapping
  someone else's CLI). The system prompt 100% reflects the zipsa
  runtime contract.
- Skill portability across backends: same skill runs on Anthropic /
  OpenAI / open-source agents.
- Cost arbitrage: cheap backend for low-stakes skills, frontier model
  for hard ones — declared per skill or per phase.

**What we lose / have to rebuild.**

- Anthropic's guard rails — re-encode in the zipsa contract or accept
  the gap.
- Claude Code's session/auth/permission UX — replace with zipsa-native
  equivalents (OAuth flows we already have most of).
- PreToolUse hook integration — runtime-specific; needs an abstraction.
- Stream-json output parsing — different per backend; the
  `runtimes/<name>.py` plugins abstract this but non-Claude coverage is
  untested.

**Sketch of phases.**

1. **Drop `--append-`, switch to `--system-prompt`** on Claude Code
   (override instead of append). Verify nothing important breaks.
2. **Anthropic API direct call**, bypassing `claude` CLI. zipsa
   controls the full request: system prompt, tools, model.
3. **Codex + Gemini runtime plugins** activated end-to-end.
4. **`pi-mono` integration** as a fully open-source backend option.

**Why this is in BACKLOG, not active.** Each step is large, and
zipsa+Claude Code already works. The override → API → other-backends
progression is about positioning, not a current bug. Defer until either
(a) zipsa wants public branding distinct from Claude Code, (b) a real
multi-backend use case appears, or (c) Claude Code does something that
gets in the way.

**First concrete next step (when we get there).** Investigate whether
`claude` CLI has a `--system-prompt` override flag (vs only
`--append-system-prompt`). If not, the path is straight to step 2.
