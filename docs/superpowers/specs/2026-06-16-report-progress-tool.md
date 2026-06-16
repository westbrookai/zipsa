# Non-blocking `report` MCP tool (progress channel) + no-silent-retry discipline

> Design doc for GitHub issue #137. Gives the forge/run agents a way to
> emit progress without blocking, and a discipline to surface (not bury)
> repeated failures.

## Context / problem

The forge and run agents have only **blocking** user channels —
`ask` / `confirm` / `choose` (they read stdin and can hit the MCP tool
timeout). There is no **non-blocking progress** channel. In one live
`zipsa forge` E2E this caused silent-failure pain twice:

1. forge ran ~5 minutes silently (container boot + reasoning over a long
   intent) → the user reasonably thought it was broken.
2. a draft script crash-looped on the same error every 30s; the agent
   **silently retried** instead of surfacing it → only caught by manually
   reading the running container's logs.

A write-only progress channel fixes both: the agent narrates what it's
doing, and repeated failures become visible (so a human/relay can step
in) instead of being buried.

## Goal

Add a **non-blocking `report(message)` MCP tool** (write-only, returns
immediately, never reads stdin → no timeout), registered on **both**
ForgeServer (authoring) and RunServer (run-time). Plus a workflow
discipline: report at key transitions, and never silently retry the same
failure — report it and escalate after a couple of repeats.

## Design

### 1. `ReportHandler` (`launcher/zipsa/core/hitl_mcp.py`)
A write-only sibling of `AskHandler`/`ConfirmHandler`/`ChooseHandler`:
```python
REPORT_OPEN = "──── report ────"   # distinct from PROMPT_OPEN so a relay
                                   # can tell progress from a question

class ReportHandler:
    def __init__(self, io_: HitlIO) -> None:
        self._io = io_

    def run(self, message: str) -> str:
        # Write-only: no stdin read, no measure_wait, no HitlUnattended.
        # A report is just output; it works attended OR unattended.
        with self._io.stdout_lock:
            self._io.stdout.write(f"\n{REPORT_OPEN}\n[report] {message}\n")
            self._io.stdout.flush()
        return "ok"
```
Key differences from the HITL handlers: it does **not** read stdin, does
**not** call `measure_wait`, and does **not** raise `HitlUnattended`
(report is fire-and-forget output, valid even with no human watching).

### 2. Register `report` on both servers
`forge_server.py` and `run_server.py` — register a `report` tool and add
`"report"` to `tool_names()`:
```python
report_h = ReportHandler(self._io)

@mcp.tool(name="report")
def report(message: str) -> str:
    """Emit a NON-BLOCKING progress update to the user (does not wait for
    a reply). Use it to narrate what you're doing — starting a build,
    writing files, before/after each exec/run test, and especially on an
    error or retry. Prefer this over going silent. For an actual question
    use ask/confirm/choose instead."""
    return report_h.run(message)
```
(ForgeServer registers exec/run/promote/ask/confirm/choose/report;
RunServer registers exec/ask/confirm/choose/report.)

### 3. Workflow discipline (`skill-builder.md`)
Add to the workflow: call `mcp__zipsa__report(...)` at key transitions —
build start, while writing files, before and after each `exec`/`run`
test, and on any error. And the rule:

> **Never silently retry the same failure.** `report` what failed; if the
> same error repeats (~2–3×), stop retrying and `ask` the user / escalate
> — name the likely cause (e.g. "the script needs a PyPI lib — I'll
> declare it via PEP 723", or "this looks like a platform gap"). Going
> silent or looping is a failure mode.

### 4. Run-time note (`AUTHORING.md`)
Document that a skill's `SKILL.md` can have the run-time LLM call
`mcp__zipsa__report(...)` for progress (non-blocking) — useful for
long-running skills (e.g. a polling alert reporting each poll), distinct
from the blocking ask/confirm/choose.

## Files
- `launcher/zipsa/core/hitl_mcp.py` — `ReportHandler` + `REPORT_OPEN`.
- `launcher/zipsa/core/forge_server.py` — register `report`; `tool_names()`.
- `launcher/zipsa/core/run_server.py` — register `report`; `tool_names()`.
- `launcher/zipsa/authoring/skill-builder.md` — report discipline.
- `launcher/zipsa/authoring/AUTHORING.md` — run-time `report` note.
- Tests: `launcher/tests/test_forge_server.py`, `launcher/tests/test_run_server.py`,
  and a `ReportHandler` unit test (in the hitl test module).

## Verification
- Unit: `ReportHandler.run("hi")` writes `[report] hi` to the provided
  stdout and returns "ok" WITHOUT reading stdin (give it a stdin that
  would raise/block if read — e.g. assert no read occurred).
- Both servers' `tool_names()` include `"report"` (ForgeServer also keeps
  exec/run/promote/ask/confirm/choose; RunServer keeps exec/ask/confirm/
  choose). `"report"` is registered alongside, not replacing anything.
- Full suite green: `cd launcher && uv run --extra dev pytest`.

## Relay note (for whoever drives forge/run with a pipe)
`report` lines use `REPORT_OPEN` ("──── report ────"), distinct from the
HITL `PROMPT_OPEN` ("──── User input needed ────"). A relay should treat
a report as informational (show it to the user, do NOT send an answer to
the FIFO) and only answer on `PROMPT_OPEN`. (Update the create-HITL-relay
runbook accordingly once this lands.)

## Out of scope (separate follow-ups)
- Persisting reports to the run record (this is a live channel).
- The forge `ask`-timeout robustness (agent exits on a timed-out ask) —
  related but separate; the report discipline reduces its impact by
  surfacing trouble earlier, but doesn't fix the timeout itself.
