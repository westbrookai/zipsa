# forge/run HITL input robustness (#171)

Found dogfooding `zipsa forge` (epic #155). The shared HITL input layer
(`core/hitl_mcp.py`, used by Forge/Run/Create servers) is **lossy and
rigid**: a normal user's input was dropped or rejected, and forge shipped
a wrong skill as a result.

## Defects (recap)
- **D1 — `ask` multiline paste fragments.** `AskHandler.run` does one
  `stdin.readline()` (hitl_mcp.py:64). A pasted multi-line answer leaves
  lines 2..N buffered; they're consumed by *later* prompts → a cascade of
  wrong answers.
- **D2 — `confirm`/`choose` discard freeform answers (smoking gun).**
  `ChooseHandler` accepts only number/exact-text; `ConfirmHandler` only
  y/n. Non-matching input is re-prompted and, after 3 tries, **raises
  `ValueError`**. The user's literal text never reaches the agent. In the
  dogfood, the user's correction "report but output is JSON, not markdown"
  was thrown away and forge built `--format markdown`.

## Goal
The HITL layer is the user's only steering wheel mid-run. Make it
**lossless** (never silently drop typed input) and **non-fatal** (never
crash on unexpected input). Where the tool can't interpret the input, hand
it to the agent to interpret.

## Design

### D1 — accept a multi-line paste as one answer
`AskHandler` should return the **whole pasted block**, not just line 1.

- After the first `readline()`, gather any further **immediately-available**
  lines and join them. Detect availability with `select.select([fd], [], [], 0)`
  on `stdin.fileno()`; loop while readable, appending lines, until nothing
  is pending. A paste arrives as one burst, so the extra lines are all
  pending within microseconds; genuine single-line answers see nothing
  pending and return immediately (no added latency, no blocking).
- **Fallback:** if `stdin` has no real `fileno()` (tests use `StringIO`),
  or `select` raises, fall back to today's single-`readline()` behavior.
- `confirm`/`choose`: after reading their answer line, **drain** any
  remaining immediately-available lines (same select probe, discarded) so a
  stray multi-line paste can't leak into the next prompt and re-trigger the
  cascade.

### D2 — route a non-matching answer back to the agent
The tool can't always map free text to an option; the fix is to **let the
agent see it**, not loop or crash.

- **`choose`** (returns `str`): if the answer is neither a valid number nor
  an exact option, **return the raw text** to the agent. Type-compatible
  (still a string). The agent treats a non-option return as a correction /
  new instruction.
- **`confirm`**: broaden yes/no recognition (add common synonyms:
  yes/yeah/yep/ok/sure/y/true and no/nope/nah/n/false; keep it small and
  documented). For still-unrecognized input, **stop crashing** — route the
  raw text to the agent too. This requires confirm to be able to carry a
  string, so:
  - **Decision (confirm contract):** change the `confirm` MCP tool to
    return a small structured result `{"confirmed": true|false|null,
    "answer": "<raw>"}` (or, simpler, return a `str`: `"yes"` / `"no"` /
    the raw freeform). Recommended: **return `str`** — `"yes"`/`"no"` on a
    clean answer, else the raw text. Minimal, symmetric with `choose`, and
    the agent already reasons over strings.
- **Retries:** keep a small bounded loop only to catch an *empty* line
  (re-prompt once), but never discard non-empty input — a non-empty
  non-matching answer is returned, not looped to death.

### Agent-contract update (skill-builder.md / AUTHORING)
Document the new semantics so the forge agent uses them:
- "`choose`/`confirm` may return the user's literal text when they don't
  pick a listed option / answer yes-no. Treat that as a correction or new
  instruction — re-evaluate, don't ignore it."
- Nudge: prefer `ask` when a free-form correction is likely; reserve
  `choose` for genuinely closed sets.

## Key decision to confirm before implementing
**Does `confirm` change its return type** from `bool` → `str` (or a
structured object) to carry freeform overrides? Recommended: `str`. This is
a forge/run tool-contract change (the agent's view of the tool) — small,
but it touches `forge_server.py` / `run_server.py` tool registration and
`skill-builder.md`. Alternative (smaller): leave `confirm` bool but make it
**non-crashing** (return default after retries) and rely on `choose`/`ask`
for the lossless path. Recommendation is the `str` change for symmetry +
true losslessness.

## Tests (TDD)
- D1: a `StringIO`/pipe feeding `"line1\nline2\nline3\n"` to `ask` returns
  the joined block (or, with the select path unavailable, line1 — and the
  drain prevents leak into a following prompt). With a real pipe fd,
  multi-line paste returns as one answer.
- D1: `confirm`/`choose` drain trailing buffered lines (a following prompt
  reads fresh input, not a stale fragment).
- D2 choose: freeform non-option input is returned verbatim (not looped,
  not raised).
- D2 confirm: `yes/yeah/ok` → yes, `no/nope` → no, empty+default → default,
  freeform → returned verbatim, and it never raises `ValueError`.
- Regression: existing ask/confirm/choose happy paths unchanged
  (`test_hitl_*`, `test_forge_server`, `test_run_server`).

## Out of scope (separate follow-ups, see #171)
Agent rabbit-holing on unknown CLI, Ctrl-C/D interrupt, wrong `choose`
option text, stale SKILL.md example after rename, prompt verbosity / `\n`
literal display.
