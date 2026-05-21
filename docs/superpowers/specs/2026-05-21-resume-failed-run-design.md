# Resume a Failed Multi-Phase Run

**Scope:** Let users retry the failed phase of a multi-phase skill without redoing the phases that already succeeded. Targeted at recovering from late-phase failures (network, rate limits, cost-cap breaches) where rerunning from phase 0 would waste a HITL-iterated draft.

## Motivation

Today every `zipsa run` starts at phase 0. If `bip-daily-x` ships its `post` phase and the X API rate-limits the call, the user has to redo `precheck → discover → interests → ask_agenthud → report → draft → review` (including minutes of HITL iteration on `review`) before the retry even reaches `post`. This is a hard disincentive to retry, observed in practice with bip-daily-x@0.3.0.

The underlying state machine already records phase status and persists `state_updates` to disk. The launcher just doesn't currently use that to short-circuit.

## Design Principles

- **Zero new configuration.** Skill authors don't add manifest fields, opt-in flags, or per-phase metadata. Resume is a launcher feature, not a skill contract.
- **Default-safe.** Resume requires explicit user confirmation in interactive mode and refuses to silently auto-resume in non-interactive mode.
- **Fresh start by default.** Resume only activates when every eligibility check passes; any ambiguity → fresh run. No false-positive resumes from runs that aren't truly the same.
- **One flag, one file.** Surface: `--no-resume` flag + per-phase `state.json` file. Nothing else.

## Resume Eligibility

When `zipsa run <skill> [args...]` is invoked, the launcher finds the most recent prior run for `<skill>` (run dirs sorted reverse-lexically by name; the timestamp encoding `YYYY-MM-DD_HHMMSS_µµµµµµ` is lexically monotone) and checks **all of the following:**

1. **Run exists.** A previous run dir (`~/.zipsa/<skill>@<ver>/runs/<ts>/`) is present and contains a readable `summary.json`.
2. **Failed status.** That `summary.json` reports `status ∈ {failed, limits_exceeded}`.
3. **Version match.** The summary's `skill` + `version` match the currently installed skill+version. (Skill upgraded since the run → fresh start, no prompt.)
4. **Args match.** The summary's recorded `user_input` is exact-string-equal to the args of the current invocation. (Different args mean a different intent → fresh start. Whitespace and case matter — match the comparison rules already used elsewhere in the launcher.)
5. **Multi-phase.** The currently installed manifest declares ≥ 2 phases. Single-shot skills (no `phases:`) cannot be resumed — there is no prior phase to roll forward from.
6. **Failed phase recoverable.** The summary's `phases[]` array contains at least one entry with `status == "ok"` (i.e., we have a prior-phase state to load) and at least one entry with `status != "ok"` (i.e., a phase to resume from). If `phases[]` is empty or all-ok or all-failed-with-no-success, fresh start.

If any check fails, the launcher proceeds with a fresh run, no prompt, no error. Eligibility is binary: candidate or not. There is no "partial resume" mode.

## Behavior Matrix

|  | Resume candidate exists | No resume candidate |
|---|---|---|
| **Interactive (`sys.stdin.isatty() == True`)** | Print resume preview + prompt `Resume from '<phase>'? [Y/n]: ` (default `Y`). Response `n`/`N` → fresh start. | Fresh start, no message. |
| **Non-interactive (cron, pipe)** | Exit 2 with message: `previous failed run found; pass --no-resume to start fresh, or run interactively to resume`. | Fresh start, no message. |
| **`--no-resume` passed** | Fresh start, no message (eligibility check skipped entirely). | Fresh start, no message. |

The exit-2 behavior in non-interactive + candidate is the safety guard: a cron user who hasn't decided on resume policy gets a loud failure (cron will alert), not a silent re-execution of a partial run.

## Prompt UX

The interactive resume prompt displays enough state for the user to decide:

```
Previous run: 2026-05-21_231116 (47 minutes ago)
  args: "today"
  status: limits_exceeded — phase 'post': cost $0.13 > limit $0.10

Last successful phase: review
  user_facing_summary: "트윗 draft 확정. post로 진행."
  next_phase_input.tweet_text: "Just shipped Phase 2 — child skills now reuse..."

Resume from 'post'? [Y/n]:
```

Fields shown:
- Run timestamp + human-readable relative age (e.g., "47 minutes ago", "yesterday at 14:23")
- Original args
- Failed phase id + error code + message
- Last successful phase's `user_facing_summary` (skill-author-controlled, designed to be human-readable)
- A truncated (80-char) preview of `next_phase_input` (so the user can spot if state looks wrong)
- The phase that will resume

If the user types anything other than `n`/`N` (including empty enter), the resume proceeds.

## Persistence

The only NEW file written by this feature:

```
~/.zipsa/<skill>@<ver>/runs/<ts>/phases/<idx>-<phase_id>/state.json
```

Written by the launcher after a phase finishes with `status == "ok"`. Contents = the full skill envelope the agent returned:

```json
{
  "status": "ok",
  "phase": "review",
  "result": {...},
  "state_updates": null,
  "next_phase_input": {"tweet_text": "...", "draft_iterations": 3},
  "user_facing_summary": "트윗 draft 확정. post로 진행."
}
```

Resume reads only the most recent phase's `state.json` (the last one written with `status == "ok"`). Failed/in-progress phases don't have a `state.json` (the launcher only writes it after the agent successfully returns the envelope).

Existing files (`events.jsonl`, `output.jsonl`, `summary.json`) are unchanged. The per-phase JSON envelope was previously only in memory (`previous_output` local variable in `executor._execute_phases`); now we just serialize it.

## Resume Execution

When the user accepts the resume prompt (or implicitly via interactive default):

1. **Identify failed phase index.** Read `summary.json`. Find the last entry in `phases[]` whose `status != "ok"`. Call this index `N`.
2. **Load prior state.**
   - `previous_output` = `phases/<N-1>-<id>/state.json: next_phase_input`
   - `skill_state` = current disk state (`~/.zipsa/<skill>@<ver>/state.json`) — already includes all of the prior run's `state_updates`, no replay needed
3. **Skip phases 0..N-1.** No execution, no docker spawn for them.
4. **Reset phase N metering.** Cost meter and turn counter for phase N start at 0. (Otherwise a `limits_exceeded` retry would be immediately re-rejected.)
5. **Execute phase N onward** with the loaded `previous_output` + `skill_state`. From phase N's perspective, this is indistinguishable from a fresh run that just happened to reach phase N.

The new run gets its own `runs/<ts>/` directory. The original failed run dir is preserved untouched (audit trail).

## CLI Surface

```
zipsa run <skill> [args...]                  # auto-detect + interactive prompt
zipsa run <skill> [args...] --no-resume      # skip eligibility check, fresh start
```

One flag total. No `--resume`, `--resume-last`, `--resume <run-id>`, `--force`, `--auto-resume`, or run-listing UX in v1. (Each is a documented v1-out-of-scope item below; revisit when there's evidence the missing flag is causing actual pain.)

## What This Does NOT Do (v1 Out-of-Scope)

- **Named run selection.** No `zipsa runs <skill>` listing UX, no `--resume <run-id>` to target an older run. Always the most recent.
- **Skill manifest opt-in.** No `spec.resume: enabled/disabled` field. All multi-phase skills are resumable.
- **HITL-aware automatic rewind.** No mechanism to declare a phase as "irreversible" or "user-confirming." The user's runtime confirmation at the resume prompt is the only HITL safety; rewinding to an earlier phase to re-collect user input is not automated.
- **Resume from successful runs.** A `status == "ok"` run is not a resume candidate. (Re-running a successful run is a separate concept, not addressed here.)
- **Auto-resume in non-interactive mode.** No `--auto-resume` flag. Cron users either accept `--no-resume` (fresh start) or handle the exit-2 failure manually.
- **Cross-run skill_state recovery.** If the prior run's middle phases applied `state_updates` and a later phase failed, those state changes have already been persisted by the existing code path — no special recovery for them. Implied behavior of the existing state model; nothing new to design.

## Implementation Surface

The change touches three areas of `launcher/zipsa/`:

1. **`core/executor.py`** — after each phase completes with `status == "ok"`, write its envelope to `phases/<idx>-<id>/state.json`. ~5 LOC inside the existing phase loop.
2. **`cli.py`** — at the top of the `run` command (after `_check_call_trace`), run an eligibility check + interactive prompt. Add `--no-resume` flag to the Typer signature. ~60-80 LOC including a small helper for the prompt formatting and run-dir lookup.
3. **`core/executor.py`** (entry point) — accept a `resume_from: int | None` parameter that skips phases 0..N-1, loads `previous_output` from disk, and resets phase N's metering. ~10-15 LOC.

No new modules. No changes to `Skill`, `SkillSpec`, manifests, or any skill-side contract.

## Risks & Open Edges

- **Race with concurrent runs.** If the user fires `zipsa run bip-daily-x today` twice in parallel, both might see the same "previous failed run" and try to resume it. Not common; not addressed in v1. Future hardening: an `in_progress` marker file in the run dir.
- **state.json corruption.** If the launcher process is killed between writing `state.json` and the next phase finishing, the orphan state.json is from a phase that didn't fully complete. Mitigated by writing state.json *after* the phase's `status: ok` is confirmed and serialized to summary.json — but a kill in the narrow window between summary.json write and state.json write would leave a phase recorded as ok in summary but without state.json. Resume would see `summary.json: ok, phases/N/state.json: missing` and should treat as fresh start (the next phase's previous_output is unrecoverable). Implementation must handle this case explicitly.
- **next_phase_input shape evolves silently.** If a skill author changes the shape of `next_phase_input` between phases (e.g., renames a field) without bumping the skill version, resume might fail in confusing ways. Not detected by version check (same version). Skill author responsibility; not a launcher concern.

## Test Plan

The implementation plan (next document) will detail tests. At minimum:

- Unit: `_check_resume_eligibility` returns the right verdict for: no prior runs / prior succeeded / prior failed / version mismatch / args mismatch / single-shot skill.
- Unit: `state.json` written exactly when phase status is `ok`, contains full envelope, never written on `failed`/`out_of_scope`/`limits_exceeded`.
- Integration: 2-phase fixture skill where phase 1 succeeds and phase 2 deliberately fails. Run; confirm state.json written for phase 1, not phase 2; second invocation prompts to resume; accepting resume executes only phase 2 (not phase 1); rejecting starts fresh.
- Integration: same fixture, non-interactive (`stdin=DEVNULL`). Confirm exit 2 message; confirm `--no-resume` bypasses and starts fresh.
