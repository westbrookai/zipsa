# Backlog

Known issues and follow-ups that aren't urgent enough to block current work
but should be addressed before they hurt users. Add new items at the bottom;
remove items when they ship.

---

## Limits are not enforced mid-execution (weather runaway, 2026-05-18)

**Symptom.** Weather skill declared `max_cost_usd: 0.10`, `timeout_seconds:
60`, `max_turns: 6`. A bad run took **81.8s** and cost **$0.2415** — 1.4×
the declared timeout and 2.4× the declared cost. The launcher printed
`Warning: Cost $0.2415 exceeded limit $0.1000` *after* the run finished.
It should have killed the process the moment the limit was breached.

**Root cause** (in `launcher/zipsa/core/executor.py`, ~lines 285–378):

| Limit | Current behavior | Verdict |
|---|---|---|
| `max_turns` | Counts `thinking` blocks while streaming, calls `process.terminate()` when exceeded. | Works. |
| `timeout_seconds` | Only checked via `process.wait(timeout=...)` *after* the stdout loop finishes. As long as the subprocess keeps producing output, the timeout never fires. | Broken by design. |
| `max_cost_usd` | Only checked on the final `result` event, and only emits a `Warning:` to stderr. No termination. | Broken by design. |

**Why this matters.** `max_cost_usd` and `timeout_seconds` are the two
limits that protect against pathological runs (infinite tool loops, huge
context blowups, model regressions). They are exactly the ones currently
not enforced. A misbehaving skill can burn arbitrary money/time and the
launcher will only complain in the postmortem.

**Fix sketch.**

- `timeout_seconds`: record `started_at` outside the stream loop, check
  `time.monotonic() - started_at > timeout` on every parsed event, and
  call `process.terminate()` + raise `RuntimeError` on breach. Same kill
  pattern as `max_turns` already uses.
  **Must exclude user-wait time** spent inside `mcp__zipsa__ask*` calls
  — otherwise any phase that asks a question hits the timeout the
  moment the user pauses to think. Track HITL wait as a pause/resume
  window around each ask call and subtract from elapsed.
- `max_cost_usd`: Claude Code emits a `result` event only at the end, so
  for true mid-run enforcement we'd need a running cost estimate. Either
  (a) sum per-message `usage` blocks if the runtime provides them, or
  (b) keep the post-hoc warning but additionally fail the *next* run for
  the same skill if the last run exceeded the budget (cheap, blunt).
  Option (a) is the right answer if usage events are reliable.
- Consider unifying all three checks into a single `_check_limits(event,
  state)` called from one place — the current code duplicates the turn
  check across two streaming branches (`output_file` vs not), which is
  how the cost warning ended up dead-ended.

**Test plan.** Add a fixture skill whose first tool call is a long
sleep / large payload, declare aggressive limits, assert the launcher
kills the process within the budget (not after it).

---

## Broken linked installs are invisible to `zipsa list` but block `zipsa install` (2026-05-18)

**Symptom.** After removing a git worktree where a skill had been
installed via `zipsa install --link`, the symlink at
`~/.zipsa/skills/<name>` becomes dangling.

- `zipsa list` silently omits the broken entry. The user sees N skills
  and assumes everything is fine.
- `zipsa install --link …` for the same name then fails with
  `Error: Skill 'X' is already installed. Use --force to overwrite.`

The two commands disagree about whether the skill exists. The user has
no way to discover or repair the broken install without poking around
`~/.zipsa/skills/` manually.

**Reproduction.**
```bash
zipsa install --link /tmp/worktree/skills/foo
rm -rf /tmp/worktree                              # or `git worktree remove ...`
zipsa list                                         # foo not shown
zipsa install --link ./skills/foo                  # "already installed"
```

**Root cause.** Two separate gaps:

1. `zipsa list` swallows load failures (broken symlink → manifest read
   raises → entry filtered out). Hiding errors is the wrong default for
   a status command.
2. The launcher has no concept of "uninstall on worktree removal."
   `git worktree remove` knows nothing about `~/.zipsa/skills/` and
   leaves the symlinks behind. Same problem would happen if someone
   `rm -rf`s the source dir for any other reason.

**Fix sketch.**

- `zipsa list`: when a skill directory exists but its manifest can't be
  loaded, show it with a clear `[broken]` marker and the underlying
  reason (e.g. `linked source missing: <path>`). Continue listing other
  skills.
- `zipsa install`: when the existing entry is broken, treat
  re-installation as an upgrade rather than a duplicate — i.e. fall
  through to the normal install path instead of erroring. (Still respect
  `--force` semantics for the not-broken case.)
- Optional: `zipsa doctor` (or `zipsa list --fix`) that finds dangling
  linked installs and offers to remove them.
- Workflow nudge: the `finishing-a-development-branch` / worktree
  cleanup flow should run `zipsa list` and warn about broken links
  before removing a worktree that has linked skills in it.

**Test plan.** Add an integration test that creates a tmp source dir,
installs --link from it, deletes the source dir, and asserts both
`list` and `install` behave sensibly (list shows broken marker; install
overwrites cleanly).

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
  `~/.zipsa/<skill>@<ver>/runs/<timestamp>/metadata.json`: the index
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
