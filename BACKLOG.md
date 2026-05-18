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
