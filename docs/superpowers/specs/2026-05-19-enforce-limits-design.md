# Enforce Skill Limits Mid-Execution — Design

**Date:** 2026-05-19
**Status:** Draft — pending user approval
**Scope:** Make `max_turns`, `max_cost_usd`, `timeout_seconds` actually
enforced for skill runs, at both phase and aggregate granularity. Today
`max_turns` is half-enforced, `timeout_seconds` only fires after the
stdout loop drains, and `max_cost_usd` is a post-hoc warning. The
first BACKLOG entry has the full diagnosis; this spec is its resolution.

---

## Goal

One sentence: **When a skill run reaches any declared limit, the
launcher stops it gracefully at the next safe boundary, regardless of
whether the limit is on turns, dollars, or compute seconds.**

The weather runaway that motivated the BACKLOG entry (declared
$0.10 / 60s / 6 turns, actually consumed $0.24 / 82s / 6 turns)
should be impossible after this ships. The launcher kills the process
the moment cost crosses $0.10 — not in the postmortem.

## Design decisions

Five decisions taken during brainstorming (2026-05-19):

| # | Decision | Why |
|---|---|---|
| Q1 | **Stop semantics: graceful**. Limit crossed → current LLM/tool operation finishes, no new tool call starts, agent gets a chance to emit a final JSON. NOT immediate process termination. | Lets the contract JSON cleanup land (state_updates, error). 1-turn overshoot is small (<5% of budget). |
| Q2 | **Cost: live computation from per-message `usage` + pricing table**. Each assistant message has a `usage` block (input/output/cache tokens). Multiply by a model-specific pricing dict; sum across messages. | Real-time, accurate. The pricing table is small (~5 Claude models) and updates infrequently. |
| Q3 | **Timeout: compute time only**. HITL wait is excluded. HITL idle (user not answering) is the responsibility of `mcp__zipsa__ask*` itself (separate "ask timeout"). | Mental model: `timeout_seconds` answers "is the system hung", not "is the user slow". Removes the need for the ~1800s review-phase budgets we have today. |
| Q4 | **Both phase-level AND aggregate enforcement**. `phase.limits` (per-phase) AND `spec.limits` (run total) both checked on every event. Aggregate accumulates across completed phases + current phase usage. | Phase limits express phase intent; aggregate is the user's absolute upper bound. Both serve different purposes, both currently declared, only phase currently enforced. |
| Q5 | **HITL exclusion via event-stream observation**. The executor already iterates the event stream. When it sees a `tool_use` for `mcp__zipsa__ask|confirm|choose|ask_once`, it pauses the timeout clock; when the matching `tool_result` arrives, it resumes. No coupling to the HitlServer. | Pure observation, no new callbacks. Handles `ask_once` cache hits for free (tool_use → tool_result interval is ms when cached). |

## What's being fixed

| Limit | Today | After this PR |
|---|---|---|
| `max_turns` | Checked while streaming; `process.terminate()` on breach. Works for the per-phase value, but the check is duplicated across two streaming branches (`output_file` vs not) and uses immediate-termination, not graceful. | Single `_check_limits()` call site. Graceful stop. Same per-phase semantics, plus aggregate-level enforcement. |
| `timeout_seconds` | Only checked via `process.wait(timeout=)` *after* the stdout loop finishes. As long as the subprocess keeps producing output, never fires. | Tracked per event in the streaming loop. Excludes time inside HITL ask/confirm/choose/ask_once. Graceful stop on breach. Per-phase and aggregate. |
| `max_cost_usd` | Only checked on the final `result` event. Just prints a warning to stderr. No termination. | Sum of (model_pricing × usage) per assistant message. Checked every event. Graceful stop on breach. Per-phase and aggregate. |

## Implementation sketch

### New module: `zipsa/core/limits.py`

```python
@dataclass
class LimitsState:
    """Per-phase + run-aggregate counters, mutated as events stream in."""
    phase_id: str
    phase_started_at: float        # monotonic time
    phase_compute_started_at: float  # excludes HITL pauses

    # Counts
    phase_turns: int = 0
    phase_cost_usd: float = 0.0
    # Aggregate (across all completed + current phases)
    run_turns: int = 0
    run_cost_usd: float = 0.0
    run_started_at: float          # monotonic, never paused for HITL

    # HITL pause tracking
    _hitl_open_at: Optional[float] = None
    _hitl_total_paused: float = 0.0

def update_for_event(state: LimitsState, event: dict, pricing: PricingTable) -> None:
    """Mutate state from a single event. No limit checks here — just bookkeeping."""

def check_limits(state: LimitsState, phase_limits: SkillLimits, agg_limits: SkillLimits) -> Optional[LimitBreach]:
    """Return a LimitBreach if any limit is over, else None."""

@dataclass
class LimitBreach:
    scope: Literal["phase", "aggregate"]
    kind: Literal["turns", "cost", "time"]
    value: float
    limit: float
```

### New module: `zipsa/core/pricing.py`

```python
@dataclass(frozen=True)
class ModelPricing:
    """USD per 1M tokens."""
    input: float
    output: float
    cache_read: float
    cache_creation: float

PRICING: dict[str, ModelPricing] = {
    # As of 2026-05-19 — see https://docs.anthropic.com/en/docs/about-claude/pricing
    "claude-opus-4-7":            ModelPricing(input=15.0, output=75.0, cache_read=1.50, cache_creation=18.75),
    "claude-sonnet-4-6":          ModelPricing(input=3.00, output=15.0, cache_read=0.30, cache_creation=3.75),
    "claude-haiku-4-5-20251001":  ModelPricing(input=0.80, output=4.00, cache_read=0.08, cache_creation=1.00),
    # ... etc
}

def estimate_cost(model: str, usage: dict) -> float:
    """Sum cost from a Claude Code usage block.

    Falls back to conservative Opus pricing if model is unknown — that
    way an unknown model TRIPS limits earlier rather than under-counts."""
```

Pricing source: Anthropic's published pricing page. Updates handled
manually (no PyPI lookup, no env var override, no YAML config — keep it
in Python so it's grep-able and gets a code review).

### Modifications: `zipsa/core/executor.py`

The two duplicated streaming branches (line 286 and line 328 today) collapse to one. The per-event handler becomes:

```python
for line in raw_stream:
    if not line:
        continue
    if output_file:
        output_file.write(line)
    for event in self.runtime.parse_output([line]):
        limits.update_for_event(limits_state, event, pricing)
        breach = limits.check_limits(limits_state, phase_limits, agg_limits)
        if breach:
            # Mark stop intent. The agent's current operation completes
            # naturally; we stop the loop before the next event.
            yield event
            self._terminate_after_current_op(process)
            yield {"type": "zipsa_limits_breach", **dataclasses.asdict(breach)}
            return
        yield event
```

"Graceful stop" implementation idea: send the agent a tool_result with an
error indicating limit breach, so its next turn writes a clean status=failed
JSON. If that's infeasible (Claude Code SDK doesn't accept injected results),
fall back to `process.terminate()` after a brief delay so any in-flight
JSON has a chance to flush. We'll pick during implementation based on
what the SDK actually supports.

### Modifications: `zipsa/core/renderer.py`

New event type to render: `zipsa_limits_breach`. Shows which limit
breached (phase / aggregate × turns / cost / time), the observed value,
and the declared limit. Footer color: red.

### Modifications: `zipsa/core/models.py`

No schema change. `SkillLimits` already exists.

## File map

| File | Change |
|---|---|
| `launcher/zipsa/core/limits.py` (new) | `LimitsState`, `update_for_event`, `check_limits`, `LimitBreach` |
| `launcher/zipsa/core/pricing.py` (new) | `ModelPricing`, `PRICING` table, `estimate_cost` |
| `launcher/zipsa/core/executor.py` | Collapse duplicated stream branches; call into `limits` per event; emit `zipsa_limits_breach`; remove the old inline turn/cost/timeout code |
| `launcher/zipsa/core/renderer.py` | Render `zipsa_limits_breach` event |
| `launcher/tests/test_limits.py` (new) | Pure unit tests on `limits` + `pricing` (no Docker) |
| `launcher/tests/test_executor.py` | Replace the existing limit-edge tests with the new flow; add HITL-exclusion test |

## What's stored on disk

Nothing new. State lives in memory during the run; limits breach is
already surfaced via the existing run logs (`output.jsonl` +
`metadata.json`) — we'll add `limit_breach: {scope, kind, value, limit}`
to `metadata.json` if one occurred.

## YAGNI / out of scope

- **Per-model cost overrides at runtime.** Pricing is hardcoded. If
  Anthropic changes prices, this PR is updated in a follow-up commit.
  We don't need a "fetch live pricing" mechanism.
- **Cost in non-USD currencies.** USD only.
- **Token-level budgets** (e.g. `max_input_tokens`). The three existing
  limits are enough.
- **Per-tool cost** (e.g. limit `WebFetch` separately). Out of scope.
- **Aggregate `state_updates` rollback on aggregate breach.** If
  aggregate fires mid-phase, the phase's partial state_updates may have
  already been written from prior phases — that's fine, we preserve
  what's done. The current phase that breached gets no state_updates
  applied (existing behavior for failed phases).
- **HITL idle timeout enforcement.** Separate concern; tracked as its own
  BACKLOG item.
- **`zipsa run --no-limits` override.** Skipped intentionally — the user
  should fix the skill, not bypass safety.

## Failure surfaces

- `error.code="limits_exceeded"`, with `error.detail` naming the breach:
  ```json
  {
    "code": "limits_exceeded",
    "scope": "phase",          // "phase" | "aggregate"
    "kind": "cost",            // "turns" | "cost" | "time"
    "value": 0.107,
    "limit": 0.10,
    "phase": "report"
  }
  ```
- `user_facing_summary` (set by the agent if it can, else by the
  launcher if the agent didn't get a clean exit): "Phase 'report' hit
  its cost limit ($0.107 > $0.10). Run aborted to prevent runaway."

## Test plan

Unit tests (pure, no Docker):

- `update_for_event` with assistant message → turn count + cost both increase
- `update_for_event` with `zipsa_phase_start` → phase counters reset, run counters preserved
- `update_for_event` with `tool_use` for `mcp__zipsa__ask` → starts HITL pause
- `update_for_event` with matching `tool_result` → ends HITL pause, accumulates pause time
- `check_limits` returns breach for each kind × scope combination
- `check_limits` returns None when within all limits
- `estimate_cost` matches Anthropic published numbers for a small fixture (one input + one output token of each model)
- `estimate_cost` for unknown model uses Opus pricing (safety upper bound)

Integration tests:

- Fixture skill with a long sleep tool call + tight `timeout_seconds`. Verify
  process stops within `timeout + small grace`, NOT after sleep completes.
- Fixture skill that intentionally loops tool calls. Verify it stops at the
  declared `max_turns` (existing behavior, just confirm it still works after
  refactor).
- Fixture HITL skill with `timeout_seconds: 30`. Manually delay the HITL
  response for 60s. Verify the run does NOT hit timeout (HITL excluded).
  Then make the agent loop after the response. Verify timeout fires.

## Open questions

- **Graceful-stop mechanism.** Whether the Claude Code SDK lets us
  inject a tool error mid-stream (preferred) or whether we have to fall
  back to a short-grace `process.terminate()`. Decide in the plan after
  reading the SDK docs / a quick experiment.

- **Pricing table maintenance.** Should we add a CI check that compares
  our `PRICING` dict against Anthropic's published page? Probably yes
  (BACKLOG follow-up).
