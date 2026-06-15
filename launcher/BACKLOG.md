# Launcher Backlog

Known issues and follow-ups for the **launcher** component that aren't
urgent enough to block current work but should be addressed before they
hurt users. Add new items at the bottom; remove items when they ship.

For cross-cutting items that span more than one component (launcher +
runtime + skills), see the repo-root [`../BACKLOG.md`](../BACKLOG.md).

**Resolved items** are removed from this file when the fix lands. To find
the design/discussion of a shipped item, run
`git log --grep "BACKLOG #N"` or check the PR title (we tag PRs that
resolve a BACKLOG item, e.g. `fix: enforce skill limits mid-execution (BACKLOG #1)`).

> **Legacy-run vs exec.** Some items below are tagged **[legacy run
> only]** — they describe the original `zipsa run` LLM-loop path
> (manifest + PreToolUse hooks + stream-json + ask_once), which still
> ships but is no longer the primary direction. `zipsa exec`
> (deterministic phases) does not use those mechanisms. When the legacy
> run path is eventually retired, these items retire with it.

---

## `zipsa doctor` + worktree-cleanup integration (2026-05-19)

PR #29 fixed the urgent friction (broken entries visible in `list`,
transparently replaced by `install`). Two deferred follow-ups remain:

1. **`zipsa doctor`** — batch find/repair broken entries
   (currently the user fixes them one at a time via re-install or
   `uninstall`). Useful when several worktrees get cleaned up at once.

2. **Worktree-cleanup integration** — `git worktree remove` leaves
   linker symlinks dangling. Either a `superpowers:finishing-a-development-branch`
   hook that runs `zipsa list` before remove, or a `zipsa` helper that
   the cleanup flow calls. Same gap applies to any `rm -rf` of a linked
   source dir, so a wrapper command is more honest than a hook.

Pick up if dangling-link pain recurs.

---

## Hook denial messages should hint at the phase's allow list (2026-05-18) — [legacy run only]

**Symptom.** In the daily-progress `report` phase the agent invoked
`Bash` with a `python3 ...` command to post-process the agenthud JSON.
The PreToolUse hook denied it with:

> `command 'python3' not allowed; allowed: Bash(npx:*)`

The agent recovered (built the summary by hand instead), but it
burned a turn doing it. The denial message correctly names the
allowed pattern, but the agent doesn't always parse and act on that
hint on the first try — and skill authors writing new phases tend to
underestimate which utilities the model will reach for.

This is mostly a UX paper-cut, but it shows up every time a phase has
a tight Bash allowlist.

**Fix sketch.**

- Keep the deny path identical; just tighten the wording so the agent
  doesn't have to puzzle over it. Something like:
  > `Bash: 'python3' is not allowed in this phase. Allowed commands:
  > Bash(npx:*). To run other commands you must declare them in the
  > phase's allowed_tools.`
- Optional: when the hook denies a Bash invocation, also surface a
  one-line tip on the executor side (stderr) so skill authors notice
  during development.
- Longer-term: a `--strict` lint pass on the manifest that flags
  phases whose goal text mentions tools (`python`, `jq`, `curl`)
  not in the allow list.

**Test plan.** Unit test the hook output for the wording change. No
behavior change beyond message text.

---

## Investigate SDK-injection graceful stop (Path A) for limit breaches (2026-05-19) — [legacy run only]

**Context.** The enforce-limits PR shipped Path B for graceful stop:
when a limit is breached, the executor lets the current `assistant`
event flush, then `process.terminate()` + 5s grace + `process.kill()`
fallback. The agent doesn't get to emit a clean final JSON for the
breached phase; the launcher emits the `zipsa_limits_breach` event for
the renderer instead.

Path A (preferred-if-feasible) was ruled out in a ~2-minute scan of
the current implementation: `_execute_skill` consumes the Claude Code
CLI's stdout via `subprocess.Popen`; there's no stdin pipe back to
the agent, and the SDK doesn't expose a documented mid-stream
injection point. So we can't synthesize a tool-error and let the
agent react with a clean status=failed JSON.

**Why revisit.** If a future SDK version (or a CLI flag we missed)
does expose mid-stream injection — even via a sentinel control line
on stdin — Path A is strictly better UX: the user gets the agent's
own apology for the breach in `user_facing_summary`, state_updates
that the agent considered safe survive (right now, the breached
phase's partial state_updates are intentionally dropped, which is
sometimes too conservative).

**What to investigate.**

- Read the latest Claude Code Agent SDK release notes / CLI man page
  for any mid-stream control mechanism. Look for: stdin-line protocols,
  HTTP callback URLs, `--control-pipe` flags.
- If found: prototype a path where the executor pushes a synthetic
  tool_result with `{"is_error": true, "content": [{"type": "text",
  "text": "limit_exceeded: ..."}]}` on the matching `tool_use_id` of
  whatever tool the agent is currently running, then waits one more
  `assistant` event to capture the final JSON.
- If not found: re-confirm Path B is still the best we can do, and
  close this item.

**Test plan.** If Path A is built, add an integration test that
exercises the breach scenario and asserts (a) the agent's final
JSON's `status` is `"failed"` and (b) `error.code` is `"limits_exceeded"`
emitted by the agent (not synthesized by the launcher).

---

## Flaky `test_state_mismatch_raises_oauth_callback_error` (2026-05-19)

**Symptom.** `tests/auth/test_browser.py::TestLocalCallbackServer::
test_state_mismatch_raises_oauth_callback_error` fails intermittently
in CI with:

```
urllib.error.URLError: <urlopen error [Errno 111] Connection refused>
```

Same commit / same code path passes on Python 3.12 and fails on
Python 3.13 in the same workflow run (or vice-versa). Re-running the
job almost always passes. Caught CI on PR #32's merge run; harmless
on retry but produces a red X that hides real failures and erodes
trust in main-branch CI signal.

**Root cause (likely).** The test starts a `LocalCallbackServer` in a
background thread, sleeps `time.sleep(0.1)`, then opens an
`urllib.request.urlopen` to the local port. The 100ms sleep is racing
with the server's `socket.bind() + listen()`. On a slow runner /
Python startup that's slower than 100ms, the urlopen happens before
the server is ready → connection refused.

**Fix sketch.**

- Replace `time.sleep(0.1)` with a deterministic wait: poll the
  server's `is_ready()` (add such a method, set by the `serve_forever`
  loop's "actually listening" callback) with a short timeout.
- Or: expose the bound port from the server only after `listen()`
  succeeds, and have the test wait on a `threading.Event`.
- Pick a free port at test setup (currently hardcoded 54394 →
  collision risk on shared CI runners that don't fully clean up
  between tests).

**Test plan.** Add a stress test that runs the test 100x in a loop
locally and on a CI matrix. Must pass every iteration before closing.

---

## `zipsa memory` CLI for managing skill memory values (2026-05-20) — [legacy run only]

**Symptom.** `mcp__zipsa__ask_once` stores answers durably (key →
value) in `~/.zipsa/memory/<skill>/skill-mem.json`. Once a value is
set, there's no first-class way for the user to change or clear it
short of editing the JSON file by hand.

Concrete pain: in a real bip-daily-x run, voice was answered once and
locked in. If the user wants the next outputs to sound different (less
self-promoting, more compact, different language), they have to:

```bash
cat ~/.zipsa/memory/bip-daily-x/skill-mem.json   # see what's stored
# Manually edit the value (jq -e or text editor)
# Or wipe + let next run re-prompt
```

That's friction. The skill-author-and-user-are-the-same situation
hides it (the user remembers the file path); for any future "skill
shared with non-author user", this becomes a real wall.

**Fix sketch.** Add a `zipsa memory` subcommand group:

```
zipsa memory list <skill>          # show all keys + values
zipsa memory show <skill> <key>    # show one key
zipsa memory edit <skill> <key>    # interactive: show current, prompt new
zipsa memory edit <skill> <key> "<value>"   # non-interactive set
zipsa memory clear <skill> <key>   # delete key (next ask_once re-prompts)
zipsa memory clear <skill>         # delete entire skill memory file
```

Atomic writes (tmp + rename, same pattern as `save_requires` in
`core/requires.py`). Confirmation prompt for `clear` of whole file.

**Why this is in BACKLOG, not active.** Not blocking — manual JSON
edit is annoying but works for power users. Wait until either (a) a
real user can't figure out the manual path, or (b) a skill ships with
multiple ask_once values that change semantics over time.

**Test plan.** Unit tests on the memory-file mutation primitives
(reuse `MemoryStore` from `core/memory_store.py`). Integration tests
on the CLI commands with `--yes` flag for non-interactive paths.

**Related.** This is the memory-side analog of `zipsa configure
<skill>` (which already exists for `spec.requires`).

---

## Container reuse across phases — `zipsa exec` (2026-05-21, retargeted 2026-06-15)

**Symptom (latent).** `exec_runner.run_phases` spawns a fresh `docker
run` for every phase. An N-phase skill = N container startups, each
cold (npm cache empty, Go build cache empty, /tmp empty). Each cold
startup costs ~1-3s; the first `npx tsx` / `go run` of a phase also
pays a download/compile cost that the next phase can't reuse.

> Originally filed against the legacy `executor.py:_execute_phases`
> (`zipsa run`). The same per-phase-container cost now exists in
> `exec_runner.run_phases`, which is the primary path going forward —
> so this entry is retargeted at exec.

**Proposed direction.** Reuse one container across all phases of a
single `zipsa exec` invocation: `docker run` once at loop start, tear
down once at loop end, and `docker exec` each phase against it. The
skill stays mounted read-only; `/out` stays the shared writable mount.

Benefits:

- Container startup: 1× per `zipsa exec` instead of N.
- npm / Go build caches persist between phases naturally (the second
  `go run` reuses the first's `~/.cache/go-build`).
- `/tmp` shared between phases — lighter intra-skill data passing.

Risks / trade-offs:

- Phase isolation weakens: a phase that corrupts in-container state
  (broken /tmp file, mutated env) affects later phases. Today each
  phase starts pristine. Skill author takes on more cleanup
  responsibility.
- LLM (`.md`) phases currently get `--env-file` (Claude auth) only on
  their own container; with a shared container the auth env would be
  present for code phases too. Decide whether that's acceptable or
  whether auth must still be scoped per-exec (`docker exec --env-file`).

**Why not done now.** Pure optimization; correctness is fine today.
Defer until real-world phase counts + measured startup cost justify
the isolation trade-off. Measure on a 3-phase fixture first.

**Implementation sketch.**

- Split `_build_docker_argv` into setup-once (image + mounts) vs
  per-phase command.
- `run_phases`: `docker run -d` (or `--rm` + keep-alive) once;
  `docker exec -i` per phase with the stdin payload; remove at end.
- Keep TZ + per-phase env handling working under `docker exec`.

**Test plan.** Existing `run_phases` tests still pass. New test:
assert one `docker run` + N `docker exec` for an N-phase skill.
Measure wall-time on a 3-phase fixture — expect several seconds faster.

---

## `zipsa view` should read `zipsa exec` runs (2026-06-15)

**Symptom.** `zipsa exec` now persists a run record under
`~/.zipsa/<skill>/runs/<ts>/` (`result.json`, `stdout.log`,
`stderr.log`, `artifacts/`), but `zipsa view` only knows about the
legacy `zipsa run` layout (`<name>@<version>/runs/<id>/` with
`output.jsonl` / `summary.json`). So exec runs — including scheduled
ones — are invisible to `view`; the user has to `cat` the JSON by hand.

**Fix sketch.**

- Teach `view` to resolve a skill name to the exec run dir
  (`<name>/runs/`, no `@version`) and render `result.json` +
  the per-phase logs. Fall back to / detect the legacy layout so both
  work during the transition.
- Bonus: `zipsa runs <skill>` to list recent exec runs with status +
  timestamp (timestamps alone are unfriendly), mirroring the
  run-id-discovery idea from the (shipped) resume work.

**Test plan.** Fixture exec run dir; assert `view` prints the result
+ phase summary and surfaces stderr on a failed run.
