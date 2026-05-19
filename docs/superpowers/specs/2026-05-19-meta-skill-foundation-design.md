# Meta-skill Foundation — Design

**Date:** 2026-05-19
**Status:** Draft — pending user approval
**Scope:** Make a skill runnable from another skill via the existing
`zipsa run` CLI. This means unified exit codes, a structured
per-run `summary.json`, a `--summary-to` flag, a new manifest field
`spec.children`, and a startup-time budget/installation check for the
declared children. **Memory/state sharing rules and an `mcp__zipsa__run_skill` MCP
wrapper are explicitly out of scope** — see "Out of scope" at the end.

---

## Goal

One sentence: **A parent skill can invoke a child via `Bash(zipsa:*)`,
read the child's structured outcome from a known file, branch on
exit code, and trust that the launcher caught obvious budget /
installation mistakes before the run started.**

The user's motivating use case: skill-of-skills (e.g. an orchestrator
that runs `daily-progress` and `bip-daily-x` in sequence each morning).
This PR ships the contract; the first real meta-skill follows in a
separate PR.

## Why this is foundational

Right now, the same "did the skill succeed" question surfaces in four
different shapes depending on what failed:

| Cause | Exit code | Where the error lives |
|---|---|---|
| `status=ok` final JSON | 0 | Inline in last assistant text |
| `status=failed` final JSON | 0 (today) | Inline in last assistant text |
| `status=out_of_scope` final JSON | 0 (today) | Inline in last assistant text |
| Limit breach | non-zero (143 SIGTERM was suppressed in PR #30, but exit code remains undefined) | `zipsa_limits_breach` event in stream |
| HITL_UNATTENDED | varies | Inside the agent's emitted JSON OR raised |
| Docker crash | non-zero (1+) | RuntimeError |
| Ctrl+C | non-zero | RuntimeError |

A parent skill writing `Bash(zipsa:*)` today would need to handle all
seven shapes. We unify them.

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **Exit code semantics, locked.** 0=ok, 1=business failed, 2=out_of_scope, 3=limits_exceeded, 4=user_declined, 5=infra_failed, 130=KeyboardInterrupt. | Parent's `Bash` switch can dispatch cleanly. Numbers match Unix conventions (130 is canonical SIGINT). |
| 2 | **`run_dir/summary.json` always written** for any run that has a `run_dir` (i.e. non-`--dry-run`, non-`--shell`). Same shape every time. | Parent reads ONE file, ONE schema. |
| 3 | **`--summary-to <path>` CLI flag.** When passed, `summary.json` is ALSO copied (or hardlinked) to that path. | Parent needs a stable known path without inspecting `run_dir`; `--summary-to /tmp/parent-step3.json` is the parent-friendly idiom. |
| 4 | **New manifest field `spec.children: list[str]`** (optional). | Lets parent declare which child skill names it intends to invoke. Used for the static check below. |
| 5 | **Startup-time validation when `spec.children` is non-empty.** Two checks: (a) each named child is currently installed (`zipsa list` would show it as healthy); (b) `parent.limits.max_cost_usd >= sum(child.limits.max_cost_usd)` and same for `timeout_seconds`. | Catches the two obvious mistakes (typo'd child name, parent budget too small for declared children's worst case). |
| 6 | **Validation emits warnings on stderr but does NOT refuse to start.** Parent skill may invoke children conditionally; the worst case may never happen. | Hard refuse would block valid skill patterns. Warning makes the gap visible while letting the run proceed. |
| 7 | **No new MCP tool** (no `mcp__zipsa__run_skill`). Parent invokes children via `Bash(zipsa:*)` like any other shell command. | Stays inside the established tool model. Wrapper can be added later if Bash ergonomics become a pain point. |
| 8 | **No parent↔child memory/state sharing.** Each runs in its own container with its own memory file. | Sandboxing intact. Cross-container state passing is a separate (harder) design question. |

## summary.json — schema

Written to `run_dir/summary.json` at run completion (success or any failure). One JSON object:

```json
{
  "status": "ok | failed | out_of_scope | limits_exceeded | user_declined | infra_failed",
  "exit_code": 0,
  "skill": "weather",
  "version": "0.3.1",
  "started_at": "2026-05-19T11:32:00+10:00",
  "finished_at": "2026-05-19T11:32:18+10:00",
  "duration_seconds": 18.3,
  "cost_usd": 0.0707,
  "turns": 2,
  "phases": [
    {"id": "main", "status": "limits_exceeded", "cost_usd": 0.0707, "turns": 2}
  ],
  "result": null,
  "error": {
    "code": "limits_exceeded",
    "message": "phase cost for phase 'main': $0.0707 > $0.0010",
    "details": {"scope": "phase", "kind": "cost", "value": 0.0707, "limit": 0.001, "phase": "main"}
  }
}
```

Field rules:

- `status` matches the exit code's class (see table below).
- `result` is populated **only** for `status=ok`; it's the final phase's `result` from the contract JSON.
- `error` is populated for everything else; the code matches the error category.
- `phases` is the per-phase breakdown — useful for orchestrators that retry just one phase.
- Times in ISO 8601 with timezone (host tz_iana, consistent with what skills see in execution_context).
- Costs in USD, computed from `usage` blocks via the existing `pricing` module.

## Exit code ↔ status mapping (authoritative)

| Exit code | status | When |
|---|---|---|
| 0 | `ok` | Skill emitted `status=ok` final JSON for the last phase |
| 1 | `failed` | Skill emitted `status=failed` for any phase (run aborts at that phase) |
| 2 | `out_of_scope` | Skill emitted `status=out_of_scope` for any phase |
| 3 | `limits_exceeded` | Launcher detected limit breach (`zipsa_limits_breach` event), terminated the run. `error.details` carries scope/kind/value/limit. |
| 4 | `user_declined` | Either: HITL `confirm` answered no, OR `HITL_UNATTENDED` returned from any HITL tool. `error.code` distinguishes. |
| 5 | `infra_failed` | Docker exited non-zero for any reason OTHER than our intentional SIGTERM (PR #30 already isolates this). Includes crashes, missing image, etc. |
| 130 | (no status — process killed externally) | `KeyboardInterrupt`. summary.json may not get written; parent should handle missing file. |

For mixed scenarios (a phase succeeds, the next fails), `status` and `exit_code` reflect the FINAL phase's outcome — the launcher always aborts on first non-ok, so "final" = "first non-ok or last successful."

## CLI surface

### `zipsa run <skill> [query]` — unchanged ergonomically

Existing arguments and flags preserved. Two new bits:

1. **`--summary-to <path>`** (new): after the run, write `summary.json` to `<path>` as well as `run_dir/summary.json`. Parent's idiom:

    ```bash
    zipsa run daily-progress yesterday --summary-to /tmp/dp-summary.json --quiet
    case $? in
      0) cat /tmp/dp-summary.json | jq .result ;;
      3) echo "budget blown — abort orchestration" ;;
      *) echo "child failed: $(cat /tmp/dp-summary.json | jq -r .error.message)" ;;
    esac
    ```

2. **Exit code obeys the table above.** Today many non-ok paths exit 0; this PR fixes that.

### Startup validation (only when `spec.children` is non-empty)

When parent has `spec.children: [c1, c2]`:

```
$ zipsa run my-orchestrator "go"
Loaded skill: my-orchestrator
Warning: my-orchestrator declares children but cost limits don't add up.
  parent.max_cost_usd     = $0.50
  children sum            = $0.85
    daily-progress         = $0.45
    bip-daily-x            = $0.40
  If both children run, the parent budget will be exceeded.

Warning: my-orchestrator declares children that aren't installed:
  bip-daily-x (run: zipsa install --link skills/bip-daily-x)
... (continues with the run)
```

Single startup pass; warnings go to stderr, then normal run proceeds.

## What changes in the codebase

| File | Change |
|---|---|
| `launcher/zipsa/core/models.py` | Add `children: list[str] = []` to `SkillSpec` (no validator beyond type — installation/budget checks are runtime concerns, not schema). |
| `launcher/zipsa/core/summary.py` (new) | `SummaryWriter` class — builds and writes `summary.json` from the executor's run state. |
| `launcher/zipsa/core/executor.py` | Wire `SummaryWriter` into the run lifecycle. Track final status across the run; emit summary.json in the existing `finally` block. |
| `launcher/zipsa/cli.py` | `run` command: add `--summary-to` flag (copies to that path post-run); honor new exit code semantics (translate the final status to the right exit code). When `spec.children` is non-empty, perform startup validation (load each child manifest, compute sums, print warnings). |
| `launcher/zipsa/core/install_health.py` | No change (already handles missing skills via `check_install`); reuse for the "children installed?" check. |
| `launcher/tests/test_summary.py` (new) | Unit tests on summary.json shape for each status. |
| `launcher/tests/test_cli.py` | Integration tests: exit codes for each status, `--summary-to` honored, children validation prints warnings. |

No change to: renderer, HITL plumbing, limits, memory, hooks, runtime contract.

## Test plan

Unit (`test_summary.py`):
- Build summary from a fixture run state for each of the 6 status codes; assert schema and field values.

Integration (`test_cli.py`):
- `zipsa run` ok → exit 0, summary.json contains `status=ok` + populated `result`.
- `zipsa run` skill that returns `status=failed` → exit 1, summary populated, no `result`.
- `zipsa run` skill that breaches cost → exit 3, summary `error.details.kind=cost`.
- `zipsa run` skill that gets HITL_UNATTENDED → exit 4, summary `error.code=hitl_unattended`.
- `--summary-to <path>` copies file when set.
- `spec.children` declares a missing child → run proceeds, stderr has warning.
- `spec.children` declares children whose limits sum > parent's → run proceeds, stderr has warning.

Manual smoke (after merge):
- `zipsa run hello-world` → exit 0, `~/.zipsa/hello-world@0.1.2/runs/*/summary.json` exists with `status=ok`.
- `zipsa run weather` (tight cost limit reproducing the earlier scenario) → exit 3, summary `error.code=limits_exceeded`.
- Make a tiny parent skill manifest with `children: [hello-world]` and a budget = $0.05 (less than hello-world's $0.10) → run prints the budget warning to stderr.

## Out of scope (BACKLOG-worthy)

- **`mcp__zipsa__run_skill` MCP wrapper.** Bash invocation is sufficient for v1. Add the wrapper when parent ergonomics become a pain point (e.g. JSON escaping in Bash starts to bite).
- **Parent↔child memory/state sharing.** Sandbox separation is currently strict per skill version. Cross-skill memory is a separate design (security and namespace concerns).
- **Cost aggregation across containers.** Each child's claude API cost is accounted only against that child's `max_cost_usd`. Parent's startup check guards against "worst-case sum > parent's budget", but at runtime there's no cross-container ledger. If a future child overruns its declared limit (shouldn't happen given PR #23), parent's budget isn't directly affected.
- **Recursion limit / cycle detection.** A meta-skill that calls itself or two meta-skills that call each other would loop. v1 doesn't detect; first user who hits it will let us add a simple depth counter in env (`ZIPSA_CALL_DEPTH`).
- **Streaming child events to parent.** Today the parent reads only `summary.json` (a post-mortem). If parent needs to react to mid-run events (rare), we add a stream mode later.

## Open questions

- Should `--summary-to` overwrite an existing file silently? Decision: yes — parent invokes parent-step3.json with the expectation of fresh data each call; warning would be noise.
- Should `summary.json` schema be versioned (`schema_version: 1`)? Decision: yes, embed `schema_version: 1` so future evolutions are detectable. Cheap to add now.
- Should we cap warning count for `spec.children` (e.g. "and 8 more")? Decision: no — list is bounded by manifest authoring, will be short.
