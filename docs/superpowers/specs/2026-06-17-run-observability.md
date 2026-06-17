# Run observability — `zipsa run` logs a record + `zipsa view` reads exec/run runs (#151)

## Problem

Two halves of one gap: a run that isn't watched live is hard or
impossible to inspect afterward.

1. **`zipsa run` (LLM run-time path) leaves no record.** `run_llm.py`'s
   `run_skill_llm` does `subprocess.run(argv, stdin=DEVNULL)` with
   stdout/stderr **inherited** (live to terminal) and **not captured**,
   and never creates a run dir. `exec` persists a run record per
   invocation (`new_run_dir` + `write_run_record` → `~/.zipsa/<skill>/
   runs/<ts>/` with `result.json` + `stdout.log` + `stderr.log` +
   `artifacts/`, #124), but `run` persists nothing. A scheduled
   `zipsa run` therefore leaves zero trace.

2. **`zipsa view` only reads the legacy layout.** `view` (`cli.py:859`)
   calls `Skill.load(resolve_skill(name))` (needs a `manifest.yaml`) and
   reads `<name>@<version>/runs/<id>/output.jsonl`. exec/run-format
   skills have **no manifest** (so `Skill.load` fails outright) and their
   runs live at `<name>/runs/<ts>/result.json` (no `@version`, no
   `output.jsonl`). So exec runs — including scheduled ones (the live
   bus-575 skill) — are invisible to `view`.

Doing these separately risks the `run` record diverging in format from
exec's `result.json`, forcing `view` to grow two readers. Unify on **one
record format** read by a single `view` path.

## Decisions

### D1 — Capture run output via tee (keep live streaming)

`run` is interactive: the operator watches claude stream. We must not
lose that. So `run_skill_llm` switches from `subprocess.run(... )` with
inherited stdio to `subprocess.Popen(stdout=PIPE, stderr=PIPE)` + reader
threads that **tee** each stream: write every chunk to the real
`sys.stdout`/`sys.stderr` (live, unchanged UX) **and** to
`run_dir/stdout.log` / `stderr.log`. stdin stays `DEVNULL` (the relay
FIFO feeds the *MCP* HITL tools, not claude's stdin — unchanged).

Rejected alternatives: (a) `subprocess.run(capture_output=True)` — loses
live streaming, unacceptable for interactive run; (b) shell `tee`
redirection — platform-fragile, doesn't split stdout/stderr cleanly.

### D2 — Unified `result.json` with a `mode` discriminator

Both paths write `result.json` sharing common fields; mode-specific
fields differ:

```jsonc
{
  "skill_name": "bus-575-hornsby-alert",
  "mode": "exec" | "run",          // discriminator
  "exit_code": 0,
  "duration_ms": 12345,
  "run_dir": "/Users/.../.zipsa/<skill>/runs/<ts>",
  "user_input": "…",               // both (the query/args)

  // mode == "exec" (unchanged from #124):
  "result": { … },                 // last phase's parsed result
  "out_dir": "…",
  "phases": [ {id, slug, exit_code, duration_ms}, … ],

  // mode == "run":
  "final_message": "…"             // claude's last assistant text, if
                                   // recoverable; else omitted
}
```

`exec`'s existing schema is preserved (just gains an explicit
`"mode": "exec"`). `run` omits `phases`/`result`/`out_dir` and adds
`final_message` (best-effort).

### D3 — `run` reuses the exec run-dir helpers

`run_skill_llm` creates the run dir with the existing
`exec_runner.new_run_dir(skill_root.name)` (same `~/.zipsa/<skill>/runs/
<ts>/` location, no `@version`) and an `artifacts/` subdir, so exec and
run share one on-disk shape. Writing is a small `run`-specific record
writer (the streams come from the tee, not from `ExecResult`s, so we
don't reuse `write_run_record` verbatim — but we match its filenames:
`result.json`, `stdout.log`, `stderr.log`).

### D4 — `final_message` capture (best-effort, low-risk)

Recovering claude's final assistant text cleanly would mean switching the
run to `--output-format stream-json` and parsing it. That is a larger
change to `build_run_argv` and the live UX (stream-json is not
human-pretty). **For v1, keep the current human-facing output** and make
`final_message` best-effort: if it's not trivially available, omit it —
`stdout.log` already holds the full transcript. A stream-json upgrade is
a follow-up, not a blocker. (Flagged for spec review: accept omitting
`final_message` in v1?)

### D5 — `view` resolves layout, exec/run first, legacy fallback

`view <name> [run_id]`:

1. Look for the exec/run layout: `zipsa_home()/<name>/runs/`. If it
   exists and has runs, resolve `run_id` with the existing `_find_run_dir`
   (lexicographic-latest / prefix-match — layout-agnostic) and render
   `result.json` + `stdout.log` (+ `stderr.log` on non-zero exit). For
   `mode=="exec"` show the per-phase table; for `mode=="run"` show
   `final_message`/transcript.
2. Else fall back to the legacy path (`Skill.load` →
   `<name>@<version>/runs/<id>/output.jsonl` → `render(events())`),
   unchanged.

`--output-mode {pretty,answer,json}` keeps working: `json` prints
`result.json` as-is; `answer` prints `final_message` (run) or `result`
(exec); `pretty` is the formatted view.

### D6 — `zipsa runs <skill>` (bonus, include if cheap)

List recent runs for a skill with timestamp + status + duration (newest
first), so users don't guess run-id prefixes. Reads the same run dirs.
Small; include in this change unless it balloons.

## Out of scope

- Switching `run` to `--output-format stream-json` (the richer
  `final_message`); follow-up.
- Per-script sub-run records inside a `run` (the RunServer `exec` tool /
  `run_phase` invoked mid-run do not themselves write run dirs today).
  v1 captures the run-time transcript only; deeper per-script logging is
  a separate item.
- Any change to the legacy `output.jsonl` format.

## Backlog cleanup carried by this branch

- Remove `launcher/BACKLOG.md` → "zipsa view should read exec runs
  (2026-06-15)" (this issue subsumes it).
- Remove `launcher/BACKLOG.md` → "Forge HITL robustness — agent gives up
  on slow ask (2026-06-16)" (resolved by #146, now stale).

## Implementation sketch

- `launcher/zipsa/run_llm.py`: create run dir; replace `subprocess.run`
  with `Popen` + two tee reader threads (stdout→sys.stdout+file,
  stderr→sys.stderr+file); on exit write `result.json` (mode="run").
  Best-effort logging (an OSError must not sink the run — mirror
  `write_run_record`'s `except OSError: pass`).
- `launcher/zipsa/exec_runner.py`: add `"mode": "exec"` to the summary
  dict (or set it at the cli.py exec call site, wherever the summary is
  built — `cli.py:810`).
- `launcher/zipsa/cli.py`: `view` grows the layout-resolving branch (D5);
  optional `runs` command (D6). Keep all legacy behavior intact.
- Rendering: extend `render`/the view formatter to handle the exec/run
  `result.json` shapes.

## Tests

- `run_skill_llm` writes `~/.zipsa/<skill>/runs/<ts>/result.json`
  (mode="run", correct exit_code/duration) + `stdout.log`/`stderr.log`
  with the captured output, while STILL writing to the provided stdout
  (assert tee: both the file and the passed stream receive the bytes).
  Mock the `claude` subprocess to emit known stdout/stderr + exit code.
- Logging failure (un-writable run dir) does not change the returned exit
  code (best-effort).
- exec summary now carries `"mode": "exec"` (extend an existing exec
  test).
- `view` on an exec run dir renders the phase table + result (no
  manifest required); on a run dir renders the transcript/final_message;
  legacy `output.jsonl` path still renders. `run_id` prefix + latest
  resolution work on the new layout.
- `--output-mode json/answer/pretty` on both new shapes.
- (if D6) `zipsa runs <skill>` lists newest-first with status.
