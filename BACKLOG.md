# Backlog

Known issues and follow-ups that aren't urgent enough to block current work
but should be addressed before they hurt users. Add new items at the bottom;
remove items when they ship.

**Resolved items** are removed from this file when the fix lands. To find
the design/discussion of a shipped item, run
`git log --grep "BACKLOG #N"` or check the PR title (we tag PRs that
resolve a BACKLOG item, e.g. `fix: enforce skill limits mid-execution (BACKLOG #1)`).

---

## Broken-install follow-ups: `zipsa doctor` + worktree-cleanup integration (2026-05-19)

PR #29 fixed the urgent friction (broken entries visible in `list`,
transparently replaced by `install`). Two deferred follow-ups remain:

1. **`zipsa doctor`** — batch find/repair broken entries
   (currently the user fixes them one at a time via re-install or
   `uninstall`). Useful when several worktrees get cleaned up at once.

2. **Worktree-cleanup integration** — `git worktree remove` leaves
   linker symlinks dangling. Either a `superpowers:finishing-a-development-branch`
   hook that runs `zipsa list` before remove, or a `zipsa` helper that
   the cleanup flow calls. Same gap applies to any `rm -rf` of a linked
   source dir, so a wrapper command is more honest than a hook.

Pick up if dangling-link pain recurs.

---

## Hook denial messages should hint at the phase's allow list (2026-05-18)

**Symptom.** In the daily-progress `report` phase the agent invoked
`Bash` with a `python3 ...` command to post-process the agenthud JSON.
The PreToolUse hook denied it with:

> `command 'python3' not allowed; allowed: Bash(npx:*)`

The agent recovered (built the summary by hand instead), but it
burned a turn doing it. The denial message correctly names the
allowed pattern, but the agent doesn't always parse and act on that
hint on the first try — and skill authors writing new phases tend to
underestimate which utilities the model will reach for.

This is mostly a UX paper-cut, but it shows up every time a phase has
a tight Bash allowlist.

**Fix sketch.**

- Keep the deny path identical; just tighten the wording so the agent
  doesn't have to puzzle over it. Something like:
  > `Bash: 'python3' is not allowed in this phase. Allowed commands:
  > Bash(npx:*). To run other commands you must declare them in the
  > phase's allowed_tools.`
- Optional: when the hook denies a Bash invocation, also surface a
  one-line tip on the executor side (stderr) so skill authors notice
  during development.
- Longer-term: a `--strict` lint pass on the manifest that flags
  phases whose goal text mentions tools (`python`, `jq`, `curl`)
  not in the allow list.

**Test plan.** Unit test the hook output for the wording change. No
behavior change beyond message text.

---

## Resume a failed run from the failed phase (2026-05-18)

**Status: shipped 2026-05-22 (PR #TBD)**

**Symptom.** Every `zipsa run` starts at phase 0. If a multi-phase
skill fails on phase N, the user must re-do phases 0..N-1 on retry.

Concretely: the new `bip-daily-x` skill has `precheck → report →
draft → review → post`. The `review` phase is HITL — the user can
spend minutes giving feedback and iterating. If the final `post`
phase fails (e.g. X API returned `CreditsDepleted`, the network blipped,
rate limit), the user has to fix the cause AND then re-do the entire
draft+review loop from scratch. That's a real disincentive to retry.

**Why this matters.** The state machine zipsa already maintains makes
this fixable: `state_updates` persists declared state across runs, and
`next_phase_input` is a structured contract between phases. The
launcher just doesn't currently use either to short-circuit.

**Fix sketch.**

- Persist per-run progress alongside the existing
  `~/.zipsa/<skill>@<ver>/runs/<timestamp>/summary.json`: the index
  of the last successful phase + the `next_phase_input` it produced.
- Add `zipsa run --resume <run-id>` (or `--resume-last`): load the
  persisted `next_phase_input`, jump straight to the failed phase,
  re-execute from there with the same HITL context (so e.g. an
  approved draft survives the retry without re-asking the user).
- Resume must validate the skill version matches the original run —
  if the skill was upgraded in between, refuse with a clear message
  (state schemas may have changed). User can pass `--force` to
  override at their own risk.
- Phases whose outputs are non-deterministic and user-facing
  (specifically the `review` phase) should be the dividing line for
  resume: by default, resume rewinds to *after* the last successful
  user-confirming phase, not after every successful phase. Otherwise
  resume from `post` after a `confirm("Post this to X?")` could
  silently re-post without re-asking.

**Adjacent decisions to make at fix time.**

- Should `--resume` be skill-opt-in (`spec.resume: enabled`) or
  always-on? Skills with side effects in middle phases may prefer to
  refuse resume.
- Run id discovery: timestamps are unfriendly. Maybe show last 5
  runs with their final status in `zipsa list <skill>` or
  `zipsa runs <skill>`.

**Test plan.** Multi-phase fixture skill where phase 2 fails. Run,
fail, resume, verify phase 1's `next_phase_input` was re-loaded
correctly and phase 2 retried (not phase 0).

---

## Investigate SDK-injection graceful stop (Path A) for limit breaches (2026-05-19)

**Context.** The enforce-limits PR (#TBD-when-merged) shipped Path B
for graceful stop: when a limit is breached, the executor lets the
current `assistant` event flush, then `process.terminate()` + 5s grace
+ `process.kill()` fallback. The agent doesn't get to emit a clean
final JSON for the breached phase; the launcher emits the
`zipsa_limits_breach` event for the renderer instead.

Path A (preferred-if-feasible) was ruled out in a ~2-minute scan of
the current implementation: `_execute_skill` consumes the Claude Code
CLI's stdout via `subprocess.Popen`; there's no stdin pipe back to
the agent, and the SDK doesn't expose a documented mid-stream
injection point. So we can't synthesize a tool-error and let the
agent react with a clean status=failed JSON.

**Why revisit.** If a future SDK version (or a CLI flag we missed)
does expose mid-stream injection — even via a sentinel control line
on stdin — Path A is strictly better UX: the user gets the agent's
own apology for the breach in `user_facing_summary`, state_updates
that the agent considered safe survive (right now, the breached
phase's partial state_updates are intentionally dropped, which is
sometimes too conservative).

**What to investigate.**

- Read the latest Claude Code Agent SDK release notes / CLI man page
  for any mid-stream control mechanism. Look for: stdin-line protocols,
  HTTP callback URLs, `--control-pipe` flags.
- If found: prototype a path where the executor pushes a synthetic
  tool_result with `{"is_error": true, "content": [{"type": "text",
  "text": "limit_exceeded: ..."}]}` on the matching `tool_use_id` of
  whatever tool the agent is currently running, then waits one more
  `assistant` event to capture the final JSON.
- If not found: re-confirm Path B is still the best we can do, and
  close this item.

**Test plan.** If Path A is built, add an integration test that
exercises the breach scenario and asserts (a) the agent's final
JSON's `status` is `"failed"` and (b) `error.code` is `"limits_exceeded"`
emitted by the agent (not synthesized by the launcher).

---

## Skill ↔ launcher independent release / distribution (2026-05-19)

**Today.** Skills live at `skills/<name>/` inside the launcher repo.
"Releasing" a skill change = a launcher-repo PR + merge. There's no
notion of a skill version that's independent of the launcher repo's
state at the time `zipsa install` ran.

This couples release cadence in both directions:

- A 1-line typo fix in `skills/weather/SKILL.md` requires a launcher PR.
- A new skill feature that depends on a new launcher field (e.g.
  `default_query` in PR #27) ships intertwined with launcher code,
  because shipping the manifest alone is a no-op for users until the
  launcher knows the field.

**Why this matters when zipsa goes public.**

- Community contributors who want to publish "their own skill" can't —
  they'd PR into the main repo or fork it.
- Skill iteration speed is capped at launcher review speed (small skill
  tweaks block on launcher CI/review even when the launcher itself isn't
  touched).
- No "skill marketplace" or registry concept; every install resolves
  via hardcoded GitHub paths.
- Compatibility breakage is invisible: a skill written today against
  launcher v0.1 will silently misbehave on launcher v0.3 unless we
  introduce a launcher-API-version negotiation.

**Approaches to consider when this is picked up.**

1. **Skills repo separate from launcher repo.** Lightest split:
   `westbrookai/zipsa-skills` holds `<name>/` directories. `zipsa
   install <name>` reads from there. Adds a layer of indirection
   without forcing a registry. Per-skill GitHub releases (tags) become
   meaningful.

2. **One repo per skill.** Maximum decoupling, matches how `npm` /
   `pip` / `cargo` work. `zipsa install <name>` resolves name →
   registry → repo URL → tag. Heavy for v1 (build the registry, build
   the search, build the auth for publish), best for v3+.

3. **Status quo + skill tags within the same repo.** Each skill gets
   its own tag namespace (e.g. `skill/weather/0.3.2`). `zipsa install
   weather@0.3.2` resolves to that tag's `skills/weather/` snapshot.
   Cheaper than a new repo. Doesn't solve the contributor-onboarding
   problem (still need launcher-repo write access).

4. **Launcher API versioning + skill compatibility declaration.**
   Orthogonal to where skills live: add `min_launcher_version` (or a
   capabilities list) to manifest. `zipsa install` refuses skills the
   running launcher can't handle, with a clear upgrade message. This
   should ship regardless of which distribution model we pick.

**Hard design questions.**

- **Skill identity:** name alone or namespaced (`westbrookai/weather`
  vs just `weather`)? Namespacing prevents future collisions but is
  ceremony.
- **Trust model:** can any GitHub repo be installed as a skill? Sandbox
  is already strong (manifest tool allowlist, network allowlist,
  Docker isolation), but credentials in `~/.zipsa/.env` could be
  exfiltrated by a malicious skill that asks the user to set
  unrelated env vars.
- **Update model:** explicit `zipsa update <name>` vs. auto-pull on
  every run vs. pinned versions only.
- **Backward compatibility:** how does the "skills directory" inside
  the current launcher repo retire — sudden cut, or both supported
  for N months?

**Triggering event.** When zipsa is first shared publicly, OR when a
second person/team wants to publish a skill that isn't westbrookai's.
Whichever comes first.

**Test plan.** Define a target distribution model. Stand up the
chosen split (e.g. extract `skills/` to a separate repo). Write an
end-to-end test: install a skill from the new location, run it,
upgrade it to a new tag, run again.

---

## Flaky `test_state_mismatch_raises_oauth_callback_error` (2026-05-19)

**Symptom.** `tests/auth/test_browser.py::TestLocalCallbackServer::
test_state_mismatch_raises_oauth_callback_error` fails intermittently
in CI with:

```
urllib.error.URLError: <urlopen error [Errno 111] Connection refused>
```

Same commit / same code path passes on Python 3.12 and fails on
Python 3.13 in the same workflow run (or vice-versa). Re-running the
job almost always passes. Caught CI on PR #32's merge run; harmless
on retry but produces a red X that hides real failures and erodes
trust in main-branch CI signal.

**Root cause (likely).** The test starts a `LocalCallbackServer` in a
background thread, sleeps `time.sleep(0.1)`, then opens an
`urllib.request.urlopen` to the local port. The 100ms sleep is racing
with the server's `socket.bind() + listen()`. On a slow runner /
Python startup that's slower than 100ms, the urlopen happens before
the server is ready → connection refused.

**Fix sketch.**

- Replace `time.sleep(0.1)` with a deterministic wait: poll the
  server's `is_ready()` (add such a method, set by the `serve_forever`
  loop's "actually listening" callback) with a short timeout.
- Or: expose the bound port from the server only after `listen()`
  succeeds, and have the test wait on a `threading.Event`.
- Pick a free port at test setup (currently hardcoded 54394 →
  collision risk on shared CI runners that don't fully clean up
  between tests).

**Test plan.** Add a stress test that runs the test 100x in a loop
locally and on a CI matrix. Must pass every iteration before closing.

---

## Standalone runtime: own the system prompt, plug in any model backend (2026-05-20)

**Vision.** Today zipsa is a thin layer over Claude Code: invokes
`claude --print` with `--append-system-prompt` carrying the runtime
contract + SKILL.md. The agent's identity stays "Claude Code with
appended instructions" and Anthropic's default system prompt (guard
rails, tone, tool-use conventions) still runs in front of ours.

The architectural endpoint: zipsa **fully owns the system prompt** and
**doesn't depend on Claude Code as the runtime layer**. Possible
backends:

- Anthropic API directly (no `claude` CLI in the loop)
- Codex CLI (OpenAI)
- Gemini CLI (Google)
- **`pi-mono`** — open-source agent runtime that OpenClaw uses;
  attractive because zipsa would no longer be tied to a particular
  vendor's CLI lifecycle

**What this unlocks.**

- "zipsa" becomes a first-class product identity to users (not just a
  CLI wrapping someone else's CLI). System prompt 100% reflects the
  zipsa runtime contract — every behavior is explainable from one
  document the project owns.
- Skill portability across backends: same `manifest.yaml` + `SKILL.md`
  runs on Anthropic / OpenAI / open-source agents.
- Cost arbitrage: cheap backend for low-stakes skills, frontier model
  for hard ones — declared per skill or per phase.

**What we lose / have to rebuild.**

- Anthropic's guard rails (security refusal policy, etc.) — would need
  to re-encode in the zipsa contract or accept the gap.
- Claude Code's session/auth/permission UX — replace with zipsa-native
  equivalents (OAuth flows we already have most of).
- PreToolUse hook integration — runtime-specific; needs an abstraction.
- Stream-json output parsing — different on every backend; the
  `runtimes/<name>.py` plugins already abstract this but real coverage
  for non-Claude is untested.

**Sketch of phases.**

1. **Drop `--append-`, switch to `--system-prompt`** on Claude Code
   (override instead of append). Smaller leap: still uses Claude Code
   CLI but zipsa's prompt is now the whole prompt. Verify nothing
   important breaks (probably some MCP/hooks integration assumes the
   Anthropic prefix). Likely needs zipsa-side replacement of some
   default behaviors.
2. **Anthropic API direct call**, bypassing `claude` CLI entirely. Now
   zipsa controls the full request: system prompt, tools, model. Most
   of `runtimes/claude.py` becomes a thin HTTP wrapper. PreToolUse hook
   becomes zipsa-side enforcement (already mostly the case since we
   own the hook script — just wire it without going through Claude
   Code's hook infrastructure).
3. **Codex + Gemini runtime plugins** activated. The `runtimes/` ABC
   already accommodates them; we just haven't exercised them end-to-end.
4. **`pi-mono` integration** as a fully open-source backend option.

**Why this is in BACKLOG, not active.** Each step above is large.
Today (PR #37–#48 morning automation) shows that zipsa+Claude Code IS
useful and works. The override → API → other backends progression is
about positioning, not about a current bug. Defer until either (a)
zipsa wants public branding distinct from Claude Code, (b) a real
multi-backend use case appears, or (c) Claude Code does something
that gets in the way.

**First concrete next step (when we get there).** Investigate whether
`claude` CLI has a `--system-prompt` override flag (vs only
`--append-system-prompt`). If not, the path is straight to step 2
(Anthropic API direct).

---

## `zipsa memory` CLI for managing skill memory values (2026-05-20)

**Symptom.** `mcp__zipsa__ask_once` stores answers durably (key →
value) in `~/.zipsa/memory/<skill>/skill-mem.json`. Once a value is
set, there's no first-class way for the user to change or clear it
short of editing the JSON file by hand.

Concrete pain: in a real bip-daily-x run, voice was answered as
"영어로 작성하고, 전문적이 스타일, 20년차 개발자 경험여서, BIP가 확실히
느껴지도록" — that's locked in. If the user wants the next tweets to
sound different (less self-promoting, more compact, different
language), they have to:

```bash
cat ~/.zipsa/memory/bip-daily-x/skill-mem.json   # see what's stored
# Manually edit voice value (jq -e or text editor)
# Or wipe + let next run re-prompt
```

That's friction. The skill-author-and-user-are-the-same situation
hides it (the user remembers the file path); for any future "skill
shared with non-author user", this becomes a real wall.

**Fix sketch.** Add a `zipsa memory` subcommand group:

```
zipsa memory list <skill>          # show all keys + values
zipsa memory show <skill> <key>    # show one key
zipsa memory edit <skill> <key>    # interactive: show current, prompt new
zipsa memory edit <skill> <key> "<value>"   # non-interactive set
zipsa memory clear <skill> <key>   # delete key (next ask_once re-prompts)
zipsa memory clear <skill>         # delete entire skill memory file
```

Atomic writes (tmp + rename, same pattern as `save_requires` in
`core/requires.py`). Confirmation prompt for `clear` of whole file.

**Why this is in BACKLOG, not active.** Not blocking — manual JSON
edit is annoying but works for power users. Wait until either (a) a
real user can't figure out the manual path, or (b) a skill ships with
multiple ask_once values that change semantics over time (e.g.
preferences that legitimately evolve).

**Test plan.** Unit tests on the memory-file mutation primitives
(reuse `MemoryStore` from `core/memory_store.py`). Integration tests
on the CLI commands with `--yes` flag for non-interactive paths.

**Related.** This is the memory-side analog of `zipsa configure
<skill>` (which already exists for `spec.requires`). Could be
implemented in parallel since memory_store + paths.skill_memory_file
already do the heavy lifting.

---

## Container reuse across phases (intra-skill optimization, 2026-05-21)

**Symptom (latent).** `executor.py:_execute_phases` spawns a new
docker container for every phase. A 5-phase skill = 5 container
startups + each starts cold (npm cache empty, /tmp empty). Each cold
startup costs 1-3 seconds plus the first agenthud/npx call has to
re-download. Multi-phase orchestrator skills feel this most.

The original "container per phase" decision predates zipsa hosting
MCP servers. The MCP servers (HITL, future `run_skill`, future
`get_artifact`) all live on the host and aren't affected by container
boundaries — so phase isolation, the only real reason for fresh
containers, can be revisited.

**Proposed direction.** Reuse the docker container across all phases
of a single `zipsa run` invocation. Each phase still issues its own
`claude --print` invocation (per-phase model override + system prompt
+ tool restriction unchanged — PR #42's `phase.model` mechanism
keeps working) but they execute inside the same container.

Benefits:

- Container startup: 1× per `zipsa run` instead of N (saves 1-3s per
  eliminated phase boundary).
- `npm`/`npx` cache: persists between phases naturally. The agenthud
  wrapper's warmup call (PR #55) only pays the download cost on the
  first phase that uses it; subsequent phases reuse the cache.
- `/tmp` shared between phases: removes the need for the artifact-mount
  gymnastics planned in the skill-composition spec (Phase 1) for
  intra-skill phase data passing. Cross-skill (parent → child) still
  needs the artifact mount.
- Claude prompt cache: NOT shared — each `claude --print` is still a
  fresh invocation with its own cache. The savings here are npm /
  filesystem level, not Claude-prompt-cache level.

Risks:

- Phase isolation weakens. A phase that corrupts in-container state
  (broken /tmp file, mutated env var) affects subsequent phases.
  Currently each container starts pristine. Trade-off: skill author
  takes on more responsibility for cleanup.
- Crash recovery: if one phase crashes the container itself (rare —
  most failures are contract-level via `zipsa_limits_breach`), the
  run ends. Currently the executor handles per-phase failures via
  contract JSON, NOT via container death, so semantics are mostly
  unchanged.

**Why not done now.** This is a launcher-level optimization. The
in-flight skill-composition spec (atomic + orchestrator + MCP
`run_skill`) is orthogonal. Tackling both together would balloon the
PR series. Defer until skill composition lands and we can measure
real-world phase counts + startup cost on the orchestrator skills.

**Out of scope for this entry.** Container reuse ACROSS `zipsa run`
invocations (long-lived launcher daemon). Different architecture —
BACKLOG candidate if very-frequent invocations materialize. This
entry covers only WITHIN a single invocation.

**Implementation sketch.**

- Modify `_execute_phases` (`launcher/zipsa/core/executor.py`) to
  spawn container once at loop start, tear down once at loop end.
- `_build_docker_command` splits into setup-once (image + mounts)
  vs per-phase `--print` invocation.
- Use `docker exec` for subsequent phase invocations against the
  same container.
- Hook script + phase-allow.json rewrite per phase (already
  per-phase, no change).

**Test plan.** Verify existing `_execute_phases` integration tests
still pass. New test: assert one `docker run` invocation per skill
execution + N `docker exec` invocations for an N-phase skill. Measure
wall-time on a 3-phase fixture skill — should be ~3-9 seconds faster
than current.

---
