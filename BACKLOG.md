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

## `ask_once` should accept a `default` parameter (2026-05-18)

**Symptom.** Skills want to suggest a default value when asking a
question for the first time (e.g. daily-progress's `notion_db_name`
defaults to `zipsa-daily-log`). Today the skill writes the default
into the prompt text and hopes the agent infers the right behavior
when the user submits an empty answer.

In the first daily-progress run after the v0.4.0 migration the agent
*did* infer correctly — the user hit Enter on the db-name prompt and
the agent stored `"zipsa-daily-log"` rather than `""`. But that worked
by luck: nothing in the contract says empty input means "use the
default mentioned in the prompt." A different agent (or the same agent
on a different day) might just as easily store `""`, which would then
be cached forever and break the skill silently.

**Fix sketch.**

- Extend `mcp__zipsa__ask_once`'s schema with an optional `default`
  parameter. When the user submits an empty string, store the default
  instead, and return it to the caller.
- Update runtime-contract.md to document the parameter and to say
  "if the skill mentions a default value in the prompt, pass it as
  `default` — don't rely on inference."
- Consider the same treatment for plain `mcp__zipsa__ask` if any skill
  needs a non-remembered default.

**Test plan.** Unit test the handler with empty input + default set,
empty input + no default, and non-empty input (default must be
ignored). Add an integration test that runs an ask_once with a default
in a non-interactive HITL run and confirms the default is stored.

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
