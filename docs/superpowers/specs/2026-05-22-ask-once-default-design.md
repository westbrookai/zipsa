# `ask_once` `default` parameter — Design

**Date:** 2026-05-22
**Status:** Draft — pending user approval
**Scope:** Add an optional `default` argument to the `mcp__zipsa__ask_once`
MCP tool so skills can declare a fallback value. Empty user input (and
non-interactive runs) resolve to the default instead of caching an empty
string or hard-failing.
**Backlog:** resolves BACKLOG "`ask_once` should accept a `default`
parameter (2026-05-18)".

---

## Motivation

`mcp__zipsa__ask_once` caches the first answer to a question permanently
(`~/.zipsa/memory/<skill>/skill-mem.json`). Today a skill that wants to
suggest a default writes it into the prompt text and *hopes* the agent
infers the right behavior when the user submits an empty answer.

In the first daily-progress run after the v0.4.0 migration this worked —
the user hit Enter on the db-name prompt and the agent stored
`"zipsa-daily-log"`. But that was luck: nothing in the contract says
empty input means "use the default mentioned in the prompt." A different
agent (or the same agent on a different day) could store `""`, which is
then cached forever and breaks the skill silently.

Two failure modes follow from there being no first-class default:

1. **Empty input is cached literally.** `AskHandler.run` returns
   `answer.strip()`; an empty line becomes `""`, which `ask_once` stores
   and returns on every future run.
2. **Non-interactive runs hard-fail even when a sensible default exists.**
   `ask_once` raises `HITL_UNATTENDED` whenever the run is
   non-interactive, even for a question the skill author already knows a
   good fallback for.

## Decisions

| # | Decision | Note |
|---|---|---|
| 1 | Add optional `default: str \| None = None` to the `ask_once` MCP tool. | Mirrors `ConfirmHandler.run(message, default=None)` — same "empty input ⇒ default" semantics. |
| 2 | Cache hit short-circuits before `default` is consulted. | A stored value always wins; `default` only matters when the key is unset. |
| 3 | Interactive + empty input (`""` after `strip()`) + `default` set ⇒ store and return `default`. | Empty input with no default keeps the current behavior (stores `""`). |
| 4 | Interactive + non-empty input ⇒ store and return the typed answer; `default` ignored. | The user explicitly answered. |
| 5 | Non-interactive + `default` set ⇒ store and return `default`; do **not** raise `HITL_UNATTENDED`. | Lets a skill run unattended when every open question has a default. |
| 6 | Non-interactive + no `default` ⇒ raise `HITL_UNATTENDED` (unchanged). | Regression guard. |
| 7 | All branching lives inside the `ask_once` tool closure. `AskHandler` is **not** modified. | Keeps the I/O handler pure; `ask_once` already owns the cache + unattended logic. |

### Resolved behavior table

| Run mode | Cached? | User input | `default` | Result |
|---|---|---|---|---|
| any | yes | — | any | return cached (default ignored) |
| interactive | no | non-empty | any | store & return typed answer |
| interactive | no | empty | set | **store & return `default`** |
| interactive | no | empty | none | store & return `""` (current) |
| non-interactive | no | — | set | **store & return `default`** (no raise) |
| non-interactive | no | — | none | raise `HITL_UNATTENDED` (current) |

## Out of scope (YAGNI)

- **No `default` on the bare `ask` tool.** `ask` answers are not cached,
  so a "default" for a one-off question has marginal value. Add later if
  a concrete skill needs it.
- **No automatic echo of the default into the prompt.** `default`
  governs the *contract* (what is stored on empty / unattended input),
  not the display. Skill authors who want the user to *see* the default
  write it into the prompt text themselves (e.g. `... (default:
  zipsa-daily-log)`). This keeps `AskHandler` untouched and the change
  small.

## Implementation

Single file for behavior: `launcher/zipsa/core/hitl_runner.py`, the
`ask_once` tool closure (currently around line 267):

```python
@mcp.tool()
@_logged
def ask_once(
    key: str, prompt: str, scope: str = "skill", default: str | None = None
) -> str:
    store = _store_for_scope(scope)
    cached = store.get(key)
    if cached is not None:
        return cached if isinstance(cached, str) else str(cached)
    try:
        answer = ask_h.run(prompt=prompt)
    except HitlUnattended as e:
        if default is None:
            raise RuntimeError(f"HITL_UNATTENDED: {e}") from e
        answer = default
    else:
        if answer == "" and default is not None:
            answer = default
    store.set(key, answer)
    return answer
```

Docstring gains a short paragraph documenting `default`.

## Documentation

`launcher/zipsa/system-prompts/runtime-contract.md`:

- Update the intent → tool mapping row to
  `mcp__zipsa__ask_once({key, prompt, scope?, default?})`.
- Add a guideline line: "If the prompt mentions a default value, pass it
  as `default` — don't rely on the agent inferring that empty input
  means the default. With a `default` set, the question is also
  answerable in non-interactive runs."

## Testing

TDD, reusing the existing end-to-end HTTP-MCP harness in
`tests/test_hitl_runner.py` (`HitlServer` + in-memory `HitlIO`, stdin as a
`StringIO`, `is_interactive` toggled per test):

1. **Interactive, empty input, default set** — stdin `"\n"`, args include
   `default="zipsa-daily-log"`; assert the call returns the default AND
   the skill store now holds it.
2. **Interactive, empty input, no default** — stdin `"\n"`; assert `""`
   is stored and returned (documents current behavior).
3. **Interactive, non-empty input, default set** — stdin `"my-db\n"`,
   `default` provided; assert `"my-db"` stored, default ignored.
4. **Cache hit, default set** — pre-seed the store; assert the cached
   value is returned and the default is ignored.
5. **Non-interactive, default set** — `is_interactive=False`,
   `default="x"`; assert `"x"` is stored & returned with no error.
6. **Non-interactive, no default** — `is_interactive=False`, no default;
   assert the call surfaces `HITL_UNATTENDED` (regression guard).

Run the full launcher suite (`uv run pytest`) to confirm no regression in
the existing `ask_once` / memory tests.

## Rollout

Backward compatible: `default` is optional and defaults to `None`, which
reproduces today's behavior exactly. No skill manifest changes required;
skills adopt `default` at their own pace. After merge, remove the
corresponding BACKLOG entry and tag the PR `feat: ... (BACKLOG)`.
