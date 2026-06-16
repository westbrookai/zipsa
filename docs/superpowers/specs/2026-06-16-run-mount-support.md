# `zipsa run` mount support (creds reach the script's exec container)

> Design doc for GitHub issue #145. Lets a credential-using skill run via
> the LLM run-time (`zipsa run`), not just `zipsa exec`.

## Context / problem

A cred-using skill (e.g. `bus-575-hornsby-alert`, `wahroonga-umbrella-alert`)
works via `zipsa exec --mount …` (the schedule path) but **not** via
`zipsa run`:
- `zipsa run` has no `--mount` flag; the exec-format dispatch calls
  `run_skill_llm(skill_path, user_input, image=image)` with no mounts
  (`cli.py`).
- `run_skill_llm` does have an `extra_mounts` param, but it routes it to
  `build_run_argv` — i.e. into the **headless-claude (run-time) container**,
  which does NOT read the creds. The **script runs in a separate exec
  sub-container** (RunServer's exec tool → `RunScriptHandler.run` →
  `run_phase`), and that container gets no mounts.
- `RunServer`'s exec tool is `exec_script(script, args, prev)` and
  `RunScriptHandler(docker_image, skill_root)` is built with no mounts.

So creds never reach the script. (`zipsa run skills/bus-575-hornsby-alert`
only ran clean because the script checks its time window before loading
creds; inside the window it would FATAL on missing creds.)

## Design decisions (pinned)

1. **Mounts go to the SCRIPT's exec sub-container, not the run-time
   (claude) container.** The claude container needs only Claude auth
   (env_file); the skill's creds belong in the container where the script
   runs. So route `extra_mounts` to `RunScriptHandler`, and stop putting
   them on `build_run_argv` (the run-time container).
2. **Operator-driven, not LLM-driven.** `zipsa run --mount HOST[:CONTAINER]`
   (repeatable) supplies the mounts; they are applied automatically to
   every script the run-time invokes. The `RunServer` exec tool stays
   `exec_script(script, args, prev)` — the orchestrating LLM does NOT get
   a `mounts` param and never handles credentials. (Mirrors how
   `zipsa exec --mount` is operator-supplied and applied to phases.)
3. `~` expansion is already handled (#139) in `RunScriptHandler.run` and
   `cli._parse_mount_spec`; reuse both.

## Changes

- **`launcher/zipsa/cli.py`** (`run` command): add
  `--mount HOST[:CONTAINER]` (repeatable), parsed with the existing
  `_parse_mount_spec`; pass to `run_skill_llm(..., extra_mounts=[...])`.
- **`launcher/zipsa/run_llm.py`** (`run_skill_llm`): route `extra_mounts`
  to the script container by constructing
  `RunScriptHandler(docker_image=image, skill_root=skill_root,
  default_mounts=extra_mounts)`. Remove `extra_mounts=` from the
  `build_run_argv(...)` call (the run-time container doesn't need skill
  creds). `build_run_argv` keeps its param for now but is called without
  it (or drop the arg if unused elsewhere — implementer's call, but do
  not mount skill creds into the claude container).
- **`launcher/zipsa/core/run_script_handler.py`**: add a
  `default_mounts: list[tuple[Path, str]] | None = None` constructor
  param; in `run()`, merge `default_mounts` with any per-call `mounts`
  into the `extra_mounts` passed to `run_phase` (keep the `#139`
  `expanduser().resolve()` treatment; `default_mounts` may already be
  resolved Paths — handle both `Path` and `str` host entries).
- **`RunServer` exec tool**: unchanged (no `mounts` param).
- **AUTHORING.md**: note that a cred-using skill is run with
  `zipsa run <skill> --mount …` (same `--mount` as `zipsa exec`).

## Verification
- Unit: `zipsa run --mount ~/x.json:/mnt/x.json` threads the mount to
  `run_skill_llm` → `RunScriptHandler(default_mounts=...)`; the host path
  is expanded (no literal `~`). Mock `run_skill_llm` to assert the kwarg,
  and unit-test `RunScriptHandler` applies `default_mounts` to
  `run_phase`'s `extra_mounts`.
- Creds are NOT mounted into the run-time (claude) container
  (`build_run_argv` gets no skill creds).
- Existing `run_skill_llm` / RunServer / RunScriptHandler tests pass.
- (Manual, optional) `zipsa run skills/bus-575-hornsby-alert --mount
  /…/tfnsw.json:/mnt/creds/tfnsw.json --mount /…/telegram.json:/mnt/creds/telegram.json`
  reaches cred-load (outside the window it still skips at `past_window`,
  but no cred-missing FATAL — proving creds now reach the script).

## Out of scope
- Per-call LLM-supplied mounts (the forge path already lets the authoring
  agent pass `mounts`; the run-time stays operator-driven).
- #140 (skills-dir), #146 (ask timeout) — separate issues.
