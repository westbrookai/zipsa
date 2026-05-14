# `zipsa view` and Output Rendering Design

## Goal

Add a shared output rendering system to zipsa so that both `zipsa run` (live) and `zipsa view` (replay) display skill execution results in a human-readable format. Output verbosity is controlled by `--output-mode`.

## Background

Currently `zipsa run` passes raw Claude Code `stream-json` events to stdout as-is. There is no way to inspect a past run in readable form. Users must manually parse `output.jsonl` to understand what happened.

## Architecture

### New file: `zipsa/core/renderer.py`

Single source of truth for rendering Claude Code stream-json events. Both `run` and `view` feed their event stream through this module.

```
Renderer
  input:  Iterator[dict]  (parsed stream-json events)
  output: printed to stdout
  config: OutputMode enum (pretty | answer | json)
```

### Changed files

- `zipsa/core/renderer.py` — new; `OutputMode` enum + `render(events, mode)` function
- `zipsa/cli.py` — add `--output-mode` to `run`; add `view` command
- `zipsa/core/executor.py` — no change to event yielding; rendering moves to CLI layer

---

## Output Modes

### `--output-mode pretty` (default)

Human-readable streaming output. Renders each event type as it arrives.

**Event rendering rules:**

| Event type | Rendered as |
|---|---|
| `assistant` / `thinking` content block | `[Thinking] <first 200 chars>...` |
| `assistant` / `tool_use` content block | `[Tool] <tool_name>\n  <key args>` |
| `tool_result` (success) | `  → Success` or `  → <brief content>` |
| `tool_result` (error) | `  → Error: <message>` |
| `assistant` / `text` content block | printed as-is (final answer) |
| `result` | separator line + `turns: N  cost: $X.XXXX  duration: Xs` |
| all other types | silently skipped |

Tool args rendering: print up to 3 key-value pairs from the input. Truncate long values to 80 chars.

### `--output-mode answer`

Prints only `text` content blocks from `assistant` events. No thinking, no tool calls, no result summary. Suitable for piping to other tools.

### `--output-mode json`

Passes raw JSONL to stdout unchanged. This is the current default behavior of `zipsa run`. Kept for scripting and debugging.

---

## `zipsa view` Command

### Usage

```bash
zipsa view <skill-dir>                           # most recent run, pretty mode
zipsa view <skill-dir> <run-id>                  # specific run by ID prefix
zipsa view <skill-dir> --output-mode answer      # answer only
zipsa view <skill-dir> <run-id> --output-mode json
```

### Run selection

- Runs are stored at `~/.zipsa/<name>@<version>/runs/<timestamp>/output.jsonl`
- No `run-id` given → pick the directory with the lexicographically latest timestamp (timestamps are sortable: `YYYY-MM-DD_HHmmss_ffffff`)
- `run-id` given → match as a prefix against run directory names; error if zero or multiple matches

### Error cases

| Condition | Error message |
|---|---|
| No runs directory exists | `No runs found for skill '<name>'` |
| Runs directory is empty | `No runs found for skill '<name>'` |
| `run-id` matches nothing | `No run matching '<run-id>' found` |
| `run-id` matches multiple | `Ambiguous run ID '<run-id>' — matches: <list>` |
| `output.jsonl` missing in run dir | `Run '<run-id>' has no output.jsonl` |

---

## `zipsa run` Changes

Add `--output-mode` option (default: `pretty`). The existing raw JSON stream becomes `--output-mode json`, which is no longer the default.

```bash
zipsa run <skill-dir> "query"                        # pretty (new default)
zipsa run <skill-dir> "query" --output-mode answer   # final answer only
zipsa run <skill-dir> "query" --output-mode json     # raw JSONL (old default)
```

---

## File Structure

```
launcher/
├── zipsa/
│   ├── core/
│   │   └── renderer.py          # new
│   └── cli.py                   # add view command + --output-mode to run
└── tests/
    └── test_renderer.py         # new
```

## Testing

- `test_renderer.py` — unit tests for each output mode against fixture event sequences:
  - pretty: thinking → tool_use → tool_result → text → result
  - answer: only text content extracted
  - json: raw lines passed through unchanged
- `test_cli.py` — `view` command: run selection (latest, by prefix, error cases)

## Out of Scope

- Interactive run selector (list + pick)
- Colorized output / ANSI formatting
- `--tail N` to show only last N events
- Exporting to file
- Markdown output mode (deferred to when `zipsa web` is designed)
