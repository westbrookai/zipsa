# Configurable per-phase exec timeout

> Design doc for GitHub issue #143. Lets a skill run a phase longer than
> the 10-minute default, declared inline (self-contained) with a CLI
> override.

## Context / problem

`exec_runner.run_phase` / `run_phases` hardcode `timeout_seconds=600`
(10 min) and nothing exposes it — not the CLI, not the skill. A phase
that legitimately needs longer is SIGKILL'd at 10 minutes.

Concrete: `skills/bus-575-hornsby-alert` polls TfNSW from 07:40 until the
bus departs or 08:00 — a **20-minute** window in one phase. Scheduled via
`zipsa exec`, run_phase would kill it at 07:50, defeating the purpose
(the whole point is catching *late* buses, which is exactly when it runs
past 07:50). The forge never caught this: its tests all hit the "outside
the window → skip immediately" path and never exercised the long poll.

## Design

A phase's timeout is a property of the **skill**, declared inline
(self-contained, like PEP 723 deps), with an invocation-level override.

### 1. Inline declaration (primary; Python phases)
PEP 723 `[tool.zipsa]` table in the script's inline metadata:
```python
# /// script
# dependencies = ["gtfs-realtime-bindings"]
# [tool.zipsa]
# timeout-seconds = 1500
# ///
```
`uv run --script` ignores unknown `[tool.*]` tables, so the script stays
valid. `exec_runner` parses the inline metadata block of a `.py` phase to
read `tool.zipsa.timeout-seconds`.

Parsing: extract the `# /// script … # ///` block (the standard PEP 723
regex), strip the leading `# ` from each line, `tomllib.loads(...)`, read
`["tool"]["zipsa"]["timeout-seconds"]` if present. Tolerate absence/parse
errors → fall back to the default. (tomllib is stdlib on 3.12.)

### 2. CLI override
`zipsa exec --timeout <seconds>` (and the run path) threads an explicit
timeout to `run_phases` → `run_phase`. `zipsa schedule add --timeout
<seconds>` bakes it into the scheduled command.

### 3. Precedence (per phase)
`CLI --timeout` (if given) > inline `[tool.zipsa].timeout-seconds` (if
present, for a `.py` phase) > default `600`.

Resolution is **per phase** (each `.py` phase can declare its own inline
timeout); a CLI `--timeout` applies to every phase in the run.

Non-`.py` phases (sh/js/ts/go/md) have no inline mechanism for now — they
use the CLI override or the default. (None need a long timeout yet.)

## Files
- `launcher/zipsa/exec_runner.py`:
  - a helper to parse `[tool.zipsa].timeout-seconds` from a `.py` phase's
    PEP 723 block (returns `int | None`).
  - `run_phase`: resolve timeout = explicit-arg → inline → 600. Keep the
    `timeout_seconds` param but let inline fill it when the caller passes
    the sentinel/None.
  - `run_phases`: accept + thread an optional explicit timeout (applies to
    all phases; per-phase inline still consulted when no explicit value).
- `launcher/zipsa/cli.py`: `--timeout` option on `exec` (and the run/forge
  test path as applicable); pass to `run_phases`.
- `launcher/zipsa/scheduling.py` + the `schedule add` command:
  `--timeout` option → include `--timeout N` in the baked
  `zipsa exec …` command.
- `launcher/zipsa/authoring/AUTHORING.md`: document the inline
  `[tool.zipsa] timeout-seconds` convention (next to the PEP 723 deps
  section) + note the default is 600 s.
- `skills/bus-575-hornsby-alert/zipsa-dist/1.detect-and-alert.py`: add
  `[tool.zipsa] timeout-seconds = 1500` (covers 07:40→08:00 + margin).
- Tests: `launcher/tests/test_exec_runner.py` (+ cli/scheduling tests).

## Verification
- Unit: a `.py` phase declaring `[tool.zipsa] timeout-seconds = 5` is run
  with timeout 5 (assert via the resolved value or a fast-timeout
  behavior); a phase with no inline value uses 600; a CLI `--timeout`
  overrides both. Precedence test.
- Inline parse tolerates: no PEP 723 block, block without `[tool.zipsa]`,
  malformed metadata → falls back to default (no crash).
- `schedule add --timeout 1500` bakes `--timeout 1500` into the command.
- `uv run --script` still runs a phase whose PEP 723 block contains
  `[tool.zipsa]` (uv ignores it) — i.e. adding the table doesn't break
  execution.
- Full suite green: `cd launcher && uv run --extra dev pytest`.

## Out of scope
- A general per-phase metadata file (`skill.toml`) — inline `[tool.zipsa]`
  is enough for now.
- The forge `ask`-timeout robustness (separate follow-up).
- Re-architecting the polling skill (a single long-poll phase is fine
  once the timeout is configurable).

## Follow-on (not this issue)
After this lands, `zipsa schedule add /…/skills/bus-575-hornsby-alert
--cron "40 7 * * 1-5" --mount <tfnsw> --mount <telegram>` will run the
full 07:40→08:00 window (the skill's inline `timeout-seconds = 1500`
governs), giving the real weekday live test.
