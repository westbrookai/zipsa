# forge `ask` timeout resilience (#146)

## Problem

During a forge run, the host's HITL tools (`ask`/`confirm`/`choose`)
block on `stdin.readline()` over the relay FIFO **with no host-side
timeout** (`launcher/zipsa/core/hitl_mcp.py` — `AskHandler.run` etc.).

The only bound is the **MCP client** (the container's headless
`claude`): each tool call is capped at `_MCP_TOOL_TIMEOUT_MS`
(`launcher/zipsa/create.py`, currently 1_800_000 ms = 30 min). When the
operator does not answer within that window, the client aborts the `ask`
tool call with an error, and the agent — having no instruction on how to
recover — **abandons the forge run** instead of re-asking.

This bit us in practice: a relayed forge run where the human stepped away
(or the relay was slow) lost the whole session even though the human was
still there a few minutes later.

`skill-builder.md` already forbids going silent / looping, but says
nothing about what to do when an `ask` itself errors or returns empty
because the human was briefly away.

## Decision

Two low-risk, complementary changes. Both ship now. A third (host-side
bounded-wait sentinel) is **considered and rejected** below.

### B — Agent behaviour (prose, `skill-builder.md`)

Add explicit recovery guidance to the HITL-tools section: if `ask` /
`confirm` / `choose` returns an error or an empty answer, treat it as
"the operator is briefly away" — do **not** abandon the run; **call the
same tool again with the same question**. Only stop when the user
explicitly says to stop. This is a natural extension of the existing
"never go silent, never loop silently" guidance and carries zero code
risk. Re-asking the *same* question is also what makes a late FIFO answer
still land correctly.

### C — Client timeout (`_MCP_TOOL_TIMEOUT_MS`)

Raise the per-call MCP tool timeout so realistic human latency (including
an overnight / relayed forge where the operator steps away) does not trip
it. New value: **10_800_000 ms (3 h)**. Rationale:

- `ask`/`confirm`/`choose` legitimately wait on a human; 30 min is too
  short for a relay or an away operator.
- It does **not** make `exec`/`run` hang longer than their own bounds:
  each phase is governed by its inline `timeout-seconds` (#143) /
  `run_phase` default, so the MCP call returns when the phase finishes
  regardless of this outer cap.
- It does **not** mask hangs in unattended runs: when non-interactive,
  the HITL tools raise `HitlUnattended` immediately (they never block),
  so this larger cap only ever applies when a human is genuinely
  expected. forge runs in the foreground and is Ctrl-C-able, so a truly
  stuck call is still recoverable by the operator.

### A — Host-side bounded wait + sentinel (REJECTED)

Considered: give `AskHandler.run` a `select()`-based timeout shorter than
the client cap and return a sentinel string ("(no answer yet…)") so the
tool call always succeeds and the agent can decide. **Rejected** because
it desyncs the relay FIFO: once the sentinel is returned, a *late* answer
written to the FIFO is consumed by the *next* `readline`, mis-attributing
it to a different question. It is only safe if the agent always re-asks
the identical question, which we cannot guarantee. B + C achieve the goal
without this hazard. If we later want a hard host-side bound, revisit
this with a request/answer correlation id on the FIFO protocol — out of
scope here.

## Implementation

### `launcher/zipsa/authoring/skill-builder.md`

In the bullet describing `ask`/`confirm`/`choose` (the "talk to the user"
tool), append guidance (wording may be tuned for flow):

> If `ask`/`confirm`/`choose` comes back with an error or an empty
> answer, the operator is briefly away — **do not abandon the run.** Call
> the same tool again with the same question and keep waiting. Only stop
> when the user explicitly tells you to stop.

Place it so it reads continuously with the existing "Never just print a
request and stop" sentence; do not duplicate or contradict the
"never go silent / never loop silently" guidance later in the file.

### `launcher/zipsa/create.py`

Change `_MCP_TOOL_TIMEOUT_MS = 1_800_000` to `10_800_000` and update the
adjacent comment to explain the 3 h human-latency rationale (HITL tools
wait on a human; per-phase bounds keep exec/run from over-running;
unattended runs raise `HitlUnattended` so this cap only applies with a
human present).

## Tests

`launcher/tests/test_create.py` (or wherever `create.py` constants are
covered):

1. `_MCP_TOOL_TIMEOUT_MS >= 10_800_000` — guards the human-latency bound
   so a future tweak does not silently drop it back to a too-short value.
2. `build_mcp_config(...)` propagates `_MCP_TOOL_TIMEOUT_MS` into the
   server entry's `timeout` (assert the produced dict's
   `mcpServers.zipsa.timeout == _MCP_TOOL_TIMEOUT_MS`). (Add only if not
   already covered.)

The prose change (B) has no unit test (it is agent-facing guidance);
verify by reading that the addition is consistent with the surrounding
bullets and does not contradict them.

## Out of scope

- A host-side `select()` timeout / FIFO request-id correlation (the
  rejected option A).
- Any change to the relay mechanism (keepalive writer, PROMPT_OPEN
  polling) — see `reference_create_hitl_relay`.
- `report` (already non-blocking, returns immediately).
