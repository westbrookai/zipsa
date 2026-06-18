# Plan — forge/run HITL input robustness (#171)

Implements spec `docs/superpowers/specs/2026-06-18-forge-hitl-input-robustness.md`.
Decisions confirmed: **D1** (gather multiline paste), **D2 = option A**
(`confirm` returns `str`; `choose` returns freeform verbatim).

TDD throughout: write the test, see it fail for the right reason, implement,
see it pass, then run the full related suite. All code/comments/docs in English.

## Files in play
- `launcher/zipsa/core/hitl_mcp.py` — `HitlIO`, `AskHandler`,
  `ConfirmHandler`, `ChooseHandler` (the fix).
- `launcher/zipsa/core/forge_server.py:120`, `run_server.py:82`,
  `create_server.py:87` — `confirm` tool registration (`-> bool` → `-> str`).
- `launcher/zipsa/authoring/skill-builder.md`, `AUTHORING.md` — agent contract.
- Tests: `launcher/tests/test_hitl_mcp.py` (primary), `test_forge_server.py`,
  `test_run_server.py`, `test_create_server.py`.

## Centralize input on HitlIO (enables D1 + testability)
Add two helpers to `HitlIO` so all handlers share one input path:
- `read_answer() -> str` — read one line, then **gather** any further
  immediately-available lines and return them joined (D1). Availability via
  `select.select([self.stdin.fileno()], [], [], 0)` in a loop. **Fallbacks:**
  if `stdin.fileno()` raises (e.g. `io.StringIO` in tests) or `select`
  errors, return the single `readline()` (today's behavior). Wrap the read in
  `measure_wait()` as today. Gotcha: a `TextIOWrapper` may hold buffered
  chars not visible to `select` on the raw fd — gather should also keep
  reading while the wrapper reports buffered content; keep it simple and
  documented, and lean on the pipe-fd test to prove burst gathering.
- `drain() -> None` — discard any immediately-available pending lines (same
  select probe), best-effort, no-op when not selectable. Used by
  confirm/choose after they capture their answer so a stray paste can't leak
  forward.

## Phase 1 — `confirm` → str, synonyms, non-crash (D2-A)
1. Tests (`test_hitl_mcp.py`, `TestConfirmHandler`):
   - `yes`/`yeah`/`yep`/`ok`/`sure`/`y`/`true` → `"yes"`; `no`/`nope`/`nah`/`n`/`false` → `"no"` (case-insensitive).
   - empty + `default=True` → `"yes"`; empty + `default=False` → `"no"`;
     empty + `default=None` → re-prompt once then, if still empty, return `""`
     (or document chosen behavior) — **never raise**.
   - freeform (`"그래"`, `"actually json not markdown"`) → returned verbatim, no raise.
   - Update existing confirm tests that asserted `is True`/`is False` to the
     new `str` contract.
2. Implement `ConfirmHandler.run -> str`: synonym sets → `"yes"`/`"no"`;
   empty→default mapping; otherwise return the raw stripped text. Call
   `self._io.read_answer()`; `self._io.drain()` before returning. Remove the
   `ValueError("too many invalid answers")` crash path.

## Phase 2 — `choose` returns freeform verbatim (D2-A)
1. Tests (`TestChooseHandler`): number-in-range → option; exact option text →
   option; **freeform non-match → returned verbatim** (not looped, not
   raised); empty → re-prompt once. Drain trailing lines after capture.
2. Implement: on non-number / non-option, `return line` (the raw text).
   Keep the empty-line re-prompt (bounded), drop the `ValueError` crash.

## Phase 3 — `ask` gathers multiline paste (D1)
1. Tests (`TestAskHandler`): with a **real pipe** (`os.pipe`) feeding
   `"l1\nl2\nl3\n"` in one write, `ask` returns the joined block. With
   `StringIO`, returns line 1 (fallback) and a following `ask`/`confirm`
   reads fresh input (drain prevents leak). 
2. Implement: `AskHandler.run` uses `self._io.read_answer()`.

## Phase 4 — server tool contract + agent docs
1. `forge_server.py` / `run_server.py` / `create_server.py`: change the
   `confirm` tool signature `-> bool` → `-> str` (it now passes the handler's
   str through). `ask`/`choose` already return `str` — unchanged.
2. Server-level tests (`test_forge_server.py`, `test_run_server.py`,
   `test_create_server.py`): update any confirm assertions to `str`.
3. `skill-builder.md` + `AUTHORING.md`: document the contract —
   "`choose`/`confirm` may return the user's literal text when they don't
   pick a listed option / answer yes-no; treat it as a correction or new
   instruction, never ignore it. Prefer `ask` when a free-form correction is
   likely; reserve `choose` for genuinely closed sets."

## Phase 5 — verify
- `cd launcher && uv run pytest` — full suite green. Fix any regression in
  forge/run/create server tests caused by the `confirm` str change.
- Confirm `select`-fallback path is covered (StringIO test) so non-tty
  contexts (CI, scheduled runs) don't regress.

## Risks / notes
- `confirm` bool→str is a tool-contract change but only the forge/run agent
  consumes it (no Python caller branches on the bool except tests). Grep
  `confirm_h.run` / `\.confirm(` to confirm before finalizing.
- Keep synonym sets small and English-documented; non-English yes/no
  (`그래`, `네`, `아니`) deliberately fall through to "freeform returned to
  agent" rather than being hardcoded — the agent interprets them. (Document
  this choice.)
- The select gather must add **zero** latency to a normal single-line answer
  (timeout=0 probe returns immediately when nothing pending).
