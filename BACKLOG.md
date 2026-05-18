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
