# Plan — `--dry-run` parity for exec/run paths (#173)

Restore `--dry-run` on the new exec/run paths (lost vs legacy
`DockerExecutor._print_dry_run`). Small, code-area, no prompt/agent behavior.
TDD throughout; English only.

## Goal
- `zipsa exec <skill> --dry-run` → print the per-phase docker command(s)
  that would run (in order), then exit 0 WITHOUT starting any container.
- `zipsa run <exec-skill> --dry-run` → print the orchestrator (claude)
  container command + mcp-config path, then exit 0 without running.
- Output format mirrors legacy `_print_dry_run` (`core/executor.py:1613`):
  one arg per line, scannable.

## Entry points (already located)
- `exec` command → `exec_runner.run_phases` (exec_runner.py:482), which calls
  `run_phase` (336) → builds argv via `_build_docker_argv` (257).
- `run` (exec) → `run_llm.run_skill_llm` (165) → `build_run_argv` (71) →
  `subprocess.run`.
- cli.py `exec` command options (no dry-run today); `_run_exec_format`
  currently REJECTS `--dry-run`.

## Phase 1 — `zipsa exec --dry-run`
1. Tests (`tests/test_exec_runner.py` + a cli test):
   - With `dry_run=True`, `run_phases` (or a new dry-run helper) returns/prints
     each phase's docker argv and **`subprocess.run` is NOT called** (patch it,
     assert not called). Assert the printed text contains `docker run` and the
     phase script filename for each phase, in phase order.
   - `--local` + `--dry-run`: print the host runner invocation
     (`_runner_for(phase)` + script path) instead of docker; still no exec.
   - CLI: `zipsa exec <fixture> --dry-run` exits 0, prints the commands, runs
     nothing.
2. Implement:
   - Add `dry_run: bool = False` to `run_phases` (and thread to `run_phase`, or
     add a `build_phase_argv`-style path). Simplest: in `run_phase`, when
     `dry_run`, build argv via `_build_docker_argv` (docker) or `_runner_for`
     (local), print it (one arg/line, with a per-phase header), and return an
     `ExecResult`-shaped no-op (exit_code 0, no stdout) without
     `subprocess.run`. `run_phases` skips result-chaining side effects under
     dry_run (see `phase_state.py:30` which already notes dry-run/shell skip
     multi-phase state).
   - cli.py `exec` command: add `--dry-run` option, pass through.

## Phase 2 — `zipsa run <exec-skill> --dry-run`
1. Tests (`tests/test_run_llm.py` + cli):
   - `run_skill_llm(..., dry_run=True)` prints the argv from `build_run_argv`
     + the mcp-config path and returns 0 with **`subprocess.run` NOT called**
     (patch + assert). The host MCP `RunServer` should not linger — start/stop
     cleanly or skip starting it under dry-run (verify no port leak).
   - CLI: `zipsa run <exec-fixture> --dry-run` exits 0, prints, runs nothing.
2. Implement:
   - Add `dry_run: bool = False` to `run_skill_llm`. Under dry_run: build
     `mcp_config` + argv as today, print them (mirror `_print_dry_run`), then
     return 0 BEFORE `subprocess.run`. Decide cleanly whether to start the
     RunServer at all under dry_run — prefer NOT starting it (no need; avoids a
     bound port) and print a placeholder for the mcp port, OR start+immediately
     stop. Keep the `finally: server.stop()` correct either way.
   - cli.py `_run_exec_format`: remove `--dry-run` from the rejected-flags
     tuple; pass `dry_run=dry_run` to `run_skill_llm`. Keep `--shell`/`--env`
     rejected (out of scope).

## Phase 3 — docs + verify
- Update `launcher/CLAUDE.md` Debugging Tips if its `--dry-run` example now
  applies to exec skills (it currently advertises dry-run generally).
- Full suite green: `cd launcher && uv run pytest`.

## Risks / notes
- The dry-run must spawn **nothing** — the key assertion in every test is
  `subprocess.run`/`Popen` not called and no container started. Patch at the
  right layer.
- `run` dry-run: don't leave a RunServer port bound. Prefer not starting it.
- Keep output legible and consistent with the legacy format so existing muscle
  memory / docs carry over.
- Scope discipline: only `--dry-run`. Do NOT implement `--shell`/`--env`.
