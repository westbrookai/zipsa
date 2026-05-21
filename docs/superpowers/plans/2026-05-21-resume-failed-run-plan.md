# Resume Failed Multi-Phase Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `zipsa run <skill>` detect a recent failed run of the same skill+version+args, prompt the user to resume from the failed phase, and skip already-completed phases when resuming.

**Architecture:** New `core/resume.py` owns eligibility check + interactive prompt. Executor writes per-phase `state.json` after each `ok` phase and accepts a `resume_from: int | None` kwarg that loads prior state and skips earlier phases. CLI wires the eligibility check + prompt into the `run` command. Zero manifest changes.

**Tech Stack:** Python 3.12+, pytest, Typer (CLI), existing Pydantic models, no new dependencies.

---

## Spec reference

`docs/superpowers/specs/2026-05-21-resume-failed-run-design.md` (merged in PR #65).

## File structure

**New files:**
- `launcher/zipsa/core/resume.py` — `ResumeCandidate` dataclass, `find_resumable_run()`, `format_resume_prompt()`, `prompt_user_to_resume()`, `load_resume_state()`. One module owns all resume logic.
- `launcher/tests/test_resume.py` — unit tests for the resume module.
- `launcher/tests/fixtures/skills/two-phase-fail/` — fixture skill (manifest + SKILL.md) with phase 1 always-succeeds, phase 2 always-fails. Used by integration test.

**Modified files:**
- `launcher/zipsa/core/executor.py`:
  - Write `phases/<idx>-<id>/state.json` after each phase that completes with `status="ok"` (1 change site).
  - `_execute_phases` accepts `resume_from: int | None = None`; when set, skips phases `0..resume_from-1`, loads `previous_output` from `phases/<resume_from-1>-*/state.json`, resets the resumed phase's cost/turn meters.
  - `run()` accepts and forwards `resume_from`.
- `launcher/zipsa/cli.py`:
  - Add `--no-resume` flag to the `run` command Typer signature.
  - After `_check_call_trace` and before `Skill.load`, call `resume.find_resumable_run()`; if eligible, branch on interactive/non-interactive/no-resume; pass `resume_from` index into the executor.
- `launcher/tests/test_executor.py`: extend to cover the state.json write + resume_from execution path.

---

## Task 1: Write `state.json` after each successful phase

**Files:**
- Modify: `launcher/zipsa/core/executor.py:1016-1028` (the `if status == "ok":` block in `_execute_phases`)
- Test: `launcher/tests/test_executor.py` (add to existing test class)

- [ ] **Step 1: Write the failing test**

Add to `launcher/tests/test_executor.py` after the existing `TestArtifactsDirCreation` class:

```python
class TestPhaseStateJsonWrite:
    """Each phase that completes with status=ok writes its full skill
    envelope to phases/<idx>-<id>/state.json. Failed/out_of_scope phases
    write nothing — only ok phases produce a state.json."""

    def test_ok_phase_writes_state_json(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.executor import DockerExecutor

        run_dir = tmp_path / "skillname@0.1.0" / "runs" / "2026-05-21_000000_000"
        phase_dir = run_dir / "phases" / "0-precheck"
        phase_dir.mkdir(parents=True)

        envelope = {
            "status": "ok",
            "phase": "precheck",
            "result": {"db_id": "abc"},
            "state_updates": {"db_id": "abc"},
            "next_phase_input": {"db_id": "abc", "date": "today"},
            "user_facing_summary": "DB resolved.",
        }
        DockerExecutor._write_phase_state(phase_dir, envelope)

        path = phase_dir / "state.json"
        assert path.exists()
        import json
        loaded = json.loads(path.read_text())
        assert loaded == envelope

    def test_write_phase_state_skips_when_phase_dir_none(self):
        """Dry-run and shell paths pass phase_dir=None; helper must
        no-op without raising."""
        from zipsa.core.executor import DockerExecutor
        DockerExecutor._write_phase_state(None, {"status": "ok"})  # no raise
```

- [ ] **Step 2: Run tests, verify fail**

```bash
cd launcher && uv run pytest tests/test_executor.py::TestPhaseStateJsonWrite -v
```

Expected: `AttributeError: type object 'DockerExecutor' has no attribute '_write_phase_state'`.

- [ ] **Step 3: Add the helper**

In `launcher/zipsa/core/executor.py`, inside the `DockerExecutor` class (any convenient location near the other `@staticmethod` helpers like `_ensure_run_artifacts_dir`), add:

```python
    @staticmethod
    def _write_phase_state(phase_dir: Optional[Path], envelope: dict) -> None:
        """Persist the phase's full skill envelope to state.json.

        Called after a phase completes with status="ok" so a future
        `zipsa run` invocation can resume from the next phase using
        the persisted `next_phase_input`. No-op when phase_dir is None
        (dry-run, shell, or single-shot path where multi-phase
        per-phase dirs aren't created).
        """
        if phase_dir is None:
            return
        path = phase_dir / "state.json"
        path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2))
```

- [ ] **Step 4: Wire the helper into the phase loop**

Still in `executor.py`, find the `if status == "ok":` block around line 1016-1028. Add the write right after the `phase_summaries.append(...)` call:

```python
                    if status == "ok":
                        phase_summaries.append(PhaseSummary(
                            id=phase.id,
                            status="ok",
                            cost_usd=shared_limits_state.phase_cost_usd,
                            turns=shared_limits_state.phase_turns,
                        ))
                        # NEW: persist the envelope so a future invocation
                        # can resume from the next phase.
                        self._write_phase_state(phase_dir, phase_out)
                        if phase_out.get("state_updates"):
                            self._apply_skill_state(skill, phase_out["state_updates"])
                            skill_state = self._load_skill_state(skill)
                        previous_output = phase_out.get("next_phase_input")
                        last_phase_out = phase_out
                        break
```

(`phase_dir` is already in scope at that point — it's the local computed around line 879.)

- [ ] **Step 5: Run tests, verify pass**

```bash
cd launcher && uv run pytest tests/test_executor.py::TestPhaseStateJsonWrite -v
```

Expected: 2 pass.

- [ ] **Step 6: Full suite for regression**

```bash
cd launcher && uv run pytest 2>&1 | tail -3
```

Expected: 674 passing (672 baseline + 2 new).

- [ ] **Step 7: Commit**

```bash
cd /Users/neochoon/WestbrookAI/zipsa/.worktrees/feat-resume-failed-run
git add launcher/zipsa/core/executor.py launcher/tests/test_executor.py
git commit -m "feat(executor): persist phase envelope to state.json on ok

After each phase that completes with status=ok, write its full skill
envelope to phases/<idx>-<id>/state.json. This is the only new
persistence required for resume; failed/out_of_scope phases write
nothing (a missing state.json signals 'this phase did not complete').

Helper is no-op when phase_dir is None (dry-run, shell, single-shot
paths)."
```

---

## Task 2: `ResumeCandidate` + `find_resumable_run`

**Files:**
- Create: `launcher/zipsa/core/resume.py`
- Test: `launcher/tests/test_resume.py`

- [ ] **Step 1: Write failing tests**

Create `launcher/tests/test_resume.py`:

```python
"""Tests for resume.find_resumable_run — pure eligibility check.

All eligibility conditions from the spec are tested here. Construction
of the prior run dir is done with raw filesystem writes; we don't go
through the executor."""

import json
from pathlib import Path

import pytest

from zipsa.core.resume import ResumeCandidate, find_resumable_run


def _make_run(
    home: Path, skill: str, version: str, ts: str,
    *, status: str, user_input: str,
    phases: list[dict],
    phase_state: dict[int, dict] | None = None,
) -> Path:
    """Build a synthetic run dir + summary.json + (optional) state.json
    files for the given phase indices."""
    run_dir = home / f"{skill}@{version}" / "runs" / ts
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({
        "schema_version": 1, "status": status, "exit_code": 1,
        "skill": skill, "version": version,
        "started_at": "2026-05-21T10:00:00+10:00",
        "finished_at": "2026-05-21T10:00:30+10:00",
        "duration_seconds": 30.0, "cost_usd": 0.05, "turns": 5,
        "phases": phases, "user_input": user_input,
    }))
    phases_dir = run_dir / "phases"
    phases_dir.mkdir()
    for idx, p in enumerate(phases):
        d = phases_dir / f"{idx}-{p['id']}"
        d.mkdir()
        if phase_state and idx in phase_state:
            (d / "state.json").write_text(json.dumps(phase_state[idx]))
    return run_dir


def _two_phase_failed(home, skill="myskill", version="0.1.0",
                      ts="2026-05-21_100000_000000",
                      user_input="today"):
    return _make_run(
        home, skill, version, ts,
        status="failed", user_input=user_input,
        phases=[
            {"id": "precheck", "status": "ok", "cost_usd": 0.01, "turns": 1},
            {"id": "post", "status": "failed", "cost_usd": 0.04, "turns": 2},
        ],
        phase_state={0: {
            "status": "ok", "phase": "precheck",
            "result": {"db_id": "abc"},
            "state_updates": None,
            "next_phase_input": {"tweet": "hello"},
            "user_facing_summary": "Verified creds.",
        }},
    )


class TestFindResumableRun:
    def test_eligible_returns_candidate(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _two_phase_failed(tmp_path)
        c = find_resumable_run(
            skill="myskill", current_version="0.1.0",
            current_args="today", current_phase_count=2,
        )
        assert isinstance(c, ResumeCandidate)
        assert c.skill == "myskill"
        assert c.version == "0.1.0"
        assert c.failed_phase_index == 1
        assert c.failed_phase_id == "post"
        assert c.last_successful_phase_index == 0
        assert c.last_successful_phase_id == "precheck"
        assert c.next_phase_input == {"tweet": "hello"}
        assert c.user_facing_summary == "Verified creds."
        assert c.original_args == "today"

    def test_no_runs_dir_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        assert find_resumable_run(
            skill="myskill", current_version="0.1.0",
            current_args="today", current_phase_count=2,
        ) is None

    def test_only_successful_runs_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _make_run(tmp_path, "myskill", "0.1.0", "2026-05-21_100000_000000",
                  status="ok", user_input="today",
                  phases=[{"id": "p1", "status": "ok", "cost_usd": 0.01, "turns": 1}])
        assert find_resumable_run(
            skill="myskill", current_version="0.1.0",
            current_args="today", current_phase_count=1,
        ) is None

    def test_version_mismatch_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _two_phase_failed(tmp_path, version="0.1.0")
        # Now we look for version 0.2.0 — different install
        assert find_resumable_run(
            skill="myskill", current_version="0.2.0",
            current_args="today", current_phase_count=2,
        ) is None

    def test_args_mismatch_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _two_phase_failed(tmp_path, user_input="today")
        assert find_resumable_run(
            skill="myskill", current_version="0.1.0",
            current_args="yesterday", current_phase_count=2,
        ) is None

    def test_single_phase_skill_returns_none(self, tmp_path, monkeypatch):
        """If the currently-installed manifest only declares 1 phase,
        nothing is resumable (no prior phase to roll forward from)."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _two_phase_failed(tmp_path)
        assert find_resumable_run(
            skill="myskill", current_version="0.1.0",
            current_args="today", current_phase_count=1,  # <-- now single-phase
        ) is None

    def test_no_failed_phase_returns_none(self, tmp_path, monkeypatch):
        """If all phases succeeded but the overall status is failed
        (rare but possible — e.g. post-loop validation), no resume."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _make_run(tmp_path, "myskill", "0.1.0", "2026-05-21_100000_000000",
                  status="failed", user_input="today",
                  phases=[{"id": "p1", "status": "ok", "cost_usd": 0.01, "turns": 1}])
        assert find_resumable_run(
            skill="myskill", current_version="0.1.0",
            current_args="today", current_phase_count=1,
        ) is None

    def test_missing_state_json_returns_none(self, tmp_path, monkeypatch):
        """If the last successful phase has no state.json (kill in the
        narrow window between summary write and state.json write),
        treat as not resumable. Spec: 'should treat as fresh start'."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _make_run(tmp_path, "myskill", "0.1.0", "2026-05-21_100000_000000",
                  status="failed", user_input="today",
                  phases=[
                      {"id": "p1", "status": "ok", "cost_usd": 0.01, "turns": 1},
                      {"id": "p2", "status": "failed", "cost_usd": 0.04, "turns": 2},
                  ],
                  phase_state=None)  # <-- no state.json
        assert find_resumable_run(
            skill="myskill", current_version="0.1.0",
            current_args="today", current_phase_count=2,
        ) is None

    def test_limits_exceeded_is_resumable(self, tmp_path, monkeypatch):
        """status='limits_exceeded' counts as failed for resume."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _make_run(tmp_path, "myskill", "0.1.0", "2026-05-21_100000_000000",
                  status="limits_exceeded", user_input="today",
                  phases=[
                      {"id": "p1", "status": "ok", "cost_usd": 0.01, "turns": 1},
                      {"id": "p2", "status": "limits_exceeded", "cost_usd": 0.20, "turns": 5},
                  ],
                  phase_state={0: {
                      "status": "ok", "phase": "p1",
                      "next_phase_input": {"x": 1},
                      "user_facing_summary": "done",
                      "state_updates": None, "result": None,
                  }})
        c = find_resumable_run(
            skill="myskill", current_version="0.1.0",
            current_args="today", current_phase_count=2,
        )
        assert c is not None
        assert c.failed_phase_id == "p2"

    def test_picks_most_recent_run(self, tmp_path, monkeypatch):
        """Multiple eligible runs — pick the newest by lex-sorted dir
        name (timestamps are lex-monotone)."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _two_phase_failed(tmp_path, ts="2026-05-20_100000_000000")
        _two_phase_failed(tmp_path, ts="2026-05-21_100000_000000")
        c = find_resumable_run(
            skill="myskill", current_version="0.1.0",
            current_args="today", current_phase_count=2,
        )
        assert c.run_id == "2026-05-21_100000_000000"
```

- [ ] **Step 2: Run, expect ImportError**

```bash
cd launcher && uv run pytest tests/test_resume.py -v
```

- [ ] **Step 3: Implement `resume.py`**

Create `launcher/zipsa/core/resume.py`:

```python
"""Resume a failed multi-phase run.

Eligibility check + state load for the `zipsa run` flow. Pure helpers
+ one dataclass. No I/O side effects beyond reading prior run dirs and
the interactive prompt (in this module's CLI helpers).

Spec: docs/superpowers/specs/2026-05-21-resume-failed-run-design.md
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import paths as zipsa_paths


@dataclass(frozen=True)
class ResumeCandidate:
    """A prior run that satisfies every resume eligibility check.

    Returned by find_resumable_run when resume is possible; None
    otherwise. Callers use this to render the interactive prompt and,
    if accepted, pass failed_phase_index to the executor as resume_from.
    """
    skill: str
    version: str
    run_id: str
    run_dir: Path
    original_args: str
    failed_phase_index: int
    failed_phase_id: str
    failed_phase_status: str  # "failed" or "limits_exceeded"
    failed_phase_error_code: Optional[str]
    failed_phase_error_message: Optional[str]
    last_successful_phase_index: int
    last_successful_phase_id: str
    next_phase_input: object  # the loaded next_phase_input from state.json
    user_facing_summary: Optional[str]
    started_at: str  # ISO timestamp from summary.json


_RESUMABLE_STATUSES = frozenset({"failed", "limits_exceeded"})


def find_resumable_run(
    *,
    skill: str,
    current_version: str,
    current_args: str,
    current_phase_count: int,
) -> Optional[ResumeCandidate]:
    """Inspect the most recent prior run for `skill` and return a
    ResumeCandidate iff every spec eligibility condition is met.

    See spec §"Resume Eligibility" for the rules. Any failed check
    returns None silently — the caller treats None as "no resume,
    fresh start, no prompt".
    """
    if current_phase_count < 2:
        return None  # single-shot skills not resumable

    # Find the most recent run dir for this skill+version. Versions
    # are part of the directory name, so `<skill>@<current_version>`
    # is the only dir we look at — older versions don't match the
    # version-eligibility check anyway.
    install_data_dir = zipsa_paths.skill_data_dir(skill, current_version)
    runs_dir = install_data_dir / "runs"
    if not runs_dir.exists():
        return None

    # Sort reverse-lex by name; the timestamp format is
    # YYYY-MM-DD_HHMMSS_µµµµµµ which is lex-monotone.
    candidates = sorted(
        (p for p in runs_dir.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    if not candidates:
        return None

    run_dir = candidates[0]
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        return None

    try:
        summary = json.loads(summary_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    # Eligibility checks
    if summary.get("status") not in _RESUMABLE_STATUSES:
        return None
    if summary.get("version") != current_version:
        return None
    if summary.get("user_input", "") != current_args:
        return None

    phases = summary.get("phases", [])
    if not phases:
        return None

    # Find the failed phase (the launcher only records phases up through
    # the failure; later phases never ran). Walk from the end backward
    # to find the first non-ok entry; that's the failed phase.
    failed_idx: Optional[int] = None
    for i in range(len(phases) - 1, -1, -1):
        if phases[i].get("status") != "ok":
            failed_idx = i
            break
    if failed_idx is None or failed_idx == 0:
        # All ok, or the very first phase failed — no prior successful
        # phase to load state from. Fresh start.
        return None

    last_ok_idx = failed_idx - 1
    last_ok_id = phases[last_ok_idx]["id"]
    state_path = run_dir / "phases" / f"{last_ok_idx}-{last_ok_id}" / "state.json"
    if not state_path.exists():
        return None  # kill-in-the-window — treat as fresh start
    try:
        state = json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    failed_phase = phases[failed_idx]
    err = (summary.get("error") or {})

    return ResumeCandidate(
        skill=skill,
        version=current_version,
        run_id=run_dir.name,
        run_dir=run_dir,
        original_args=summary.get("user_input", ""),
        failed_phase_index=failed_idx,
        failed_phase_id=failed_phase["id"],
        failed_phase_status=failed_phase.get("status", "failed"),
        failed_phase_error_code=err.get("code"),
        failed_phase_error_message=err.get("message"),
        last_successful_phase_index=last_ok_idx,
        last_successful_phase_id=last_ok_id,
        next_phase_input=state.get("next_phase_input"),
        user_facing_summary=state.get("user_facing_summary"),
        started_at=summary.get("started_at", ""),
    )
```

- [ ] **Step 4: Run tests, verify pass**

```bash
cd launcher && uv run pytest tests/test_resume.py -v
```

Expected: 10 pass.

- [ ] **Step 5: Full suite**

```bash
cd launcher && uv run pytest 2>&1 | tail -3
```

Expected: 684 passing (674 + 10).

- [ ] **Step 6: Commit**

```bash
git add launcher/zipsa/core/resume.py launcher/tests/test_resume.py
git commit -m "feat(resume): eligibility check + ResumeCandidate

Pure helper that inspects the most recent prior run dir and returns a
ResumeCandidate iff every spec condition is met:
  status in {failed, limits_exceeded}, version match, args match,
  multi-phase manifest, has at least one ok phase, state.json present
  for the last ok phase.

Any check fails -> returns None (caller treats as 'fresh start, no
prompt'). 10 unit tests cover each rejection path + the picks-most-
recent and limits_exceeded-counts-as-failed cases."
```

---

## Task 3: Resume prompt formatter + interactive confirm

**Files:**
- Modify: `launcher/zipsa/core/resume.py` (extend)
- Test: `launcher/tests/test_resume.py` (extend)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_resume.py`:

```python
from zipsa.core.resume import format_resume_prompt, prompt_user_to_resume
import io
from datetime import datetime


class TestFormatResumePrompt:
    """Pure string formatter — no I/O. Verifies the spec's prompt UX."""

    def _candidate(self, **overrides) -> ResumeCandidate:
        defaults = dict(
            skill="bip-daily-x", version="0.3.0",
            run_id="2026-05-21_100000_000000",
            run_dir=Path("/tmp/fake"),
            original_args="today",
            failed_phase_index=4,
            failed_phase_id="post",
            failed_phase_status="limits_exceeded",
            failed_phase_error_code="phase_cost_exceeded",
            failed_phase_error_message="phase cost $0.13 > limit $0.10",
            last_successful_phase_index=3,
            last_successful_phase_id="review",
            next_phase_input={"tweet_text": "Just shipped Phase 2..."},
            user_facing_summary="트윗 draft 확정. post로 진행.",
            started_at="2026-05-21T10:00:00+10:00",
        )
        defaults.update(overrides)
        return ResumeCandidate(**defaults)

    def test_includes_all_required_fields(self):
        c = self._candidate()
        now = datetime.fromisoformat("2026-05-21T10:47:00+10:00")
        out = format_resume_prompt(c, now=now)
        # Header fields
        assert "2026-05-21_100000_000000" in out
        assert "47 minutes ago" in out  # relative age
        # Args + failed phase
        assert 'args: "today"' in out
        assert "limits_exceeded" in out
        assert "phase 'post'" in out
        # Last successful state
        assert "review" in out
        assert "트윗 draft 확정" in out
        # The Y/n line — caller appends this, formatter doesn't
        # Truncated next_phase_input preview
        assert "tweet_text" in out

    def test_long_next_phase_input_value_truncated(self):
        c = self._candidate(next_phase_input={
            "tweet_text": "x" * 200,
        })
        now = datetime.fromisoformat("2026-05-21T10:01:00+10:00")
        out = format_resume_prompt(c, now=now)
        # The 200x should be shortened in the preview (80-char cap per spec)
        assert "x" * 200 not in out

    def test_relative_age_under_a_minute(self):
        c = self._candidate()
        now = datetime.fromisoformat("2026-05-21T10:00:30+10:00")
        out = format_resume_prompt(c, now=now)
        assert "30 seconds ago" in out

    def test_relative_age_hours(self):
        c = self._candidate()
        now = datetime.fromisoformat("2026-05-21T13:30:00+10:00")
        out = format_resume_prompt(c, now=now)
        assert "3 hours ago" in out

    def test_relative_age_yesterday(self):
        c = self._candidate()
        now = datetime.fromisoformat("2026-05-22T09:00:00+10:00")
        out = format_resume_prompt(c, now=now)
        assert "ago" in out  # exact wording not asserted; just sanity


class TestPromptUserToResume:
    """Reads from a provided stdin stream; default Y on empty input."""

    def test_empty_input_returns_true(self):
        c = TestFormatResumePrompt()._candidate()
        out = io.StringIO()
        result = prompt_user_to_resume(c, stdin=io.StringIO("\n"), stdout=out)
        assert result is True

    def test_y_returns_true(self):
        c = TestFormatResumePrompt()._candidate()
        result = prompt_user_to_resume(c, stdin=io.StringIO("y\n"),
                                        stdout=io.StringIO())
        assert result is True

    def test_n_returns_false(self):
        c = TestFormatResumePrompt()._candidate()
        result = prompt_user_to_resume(c, stdin=io.StringIO("n\n"),
                                        stdout=io.StringIO())
        assert result is False

    def test_uppercase_N_returns_false(self):
        c = TestFormatResumePrompt()._candidate()
        result = prompt_user_to_resume(c, stdin=io.StringIO("N\n"),
                                        stdout=io.StringIO())
        assert result is False

    def test_prompt_is_written_to_stdout(self):
        c = TestFormatResumePrompt()._candidate()
        out = io.StringIO()
        prompt_user_to_resume(c, stdin=io.StringIO("\n"), stdout=out)
        text = out.getvalue()
        assert "2026-05-21_100000_000000" in text
        assert "Resume from 'post'?" in text
```

- [ ] **Step 2: Run, expect fail**

```bash
cd launcher && uv run pytest tests/test_resume.py -v -k "Format or Prompt"
```

- [ ] **Step 3: Implement formatter + prompt**

Append to `launcher/zipsa/core/resume.py`:

```python
import json as _json
from datetime import datetime, timezone
from typing import TextIO


def _humanize_age(started_at_iso: str, now: datetime) -> str:
    """Convert ISO timestamp + a 'now' datetime into a human-readable
    relative age. Returns strings like '30 seconds ago', '47 minutes
    ago', '3 hours ago', '2 days ago'."""
    try:
        started = datetime.fromisoformat(started_at_iso)
    except ValueError:
        return "earlier"
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = now - started
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs} seconds ago"
    if secs < 3600:
        return f"{secs // 60} minutes ago"
    if secs < 86400:
        return f"{secs // 3600} hours ago"
    return f"{secs // 86400} days ago"


def _preview(value: object, max_len: int = 80) -> str:
    """Render a next_phase_input value as a short string for the prompt.
    Long strings get truncated; non-strings get JSON-encoded then
    truncated."""
    s = value if isinstance(value, str) else _json.dumps(
        value, ensure_ascii=False,
    )
    if len(s) > max_len:
        return s[:max_len] + "…"
    return s


def format_resume_prompt(
    candidate: ResumeCandidate, *, now: Optional[datetime] = None,
) -> str:
    """Render the multi-line preview text shown above the Y/n prompt.

    The caller is responsible for writing this to a stream and reading
    the user's response (see prompt_user_to_resume)."""
    if now is None:
        now = datetime.now(tz=timezone.utc)
    age = _humanize_age(candidate.started_at, now)

    err_msg = candidate.failed_phase_error_message or candidate.failed_phase_error_code or "(no error message)"
    lines = [
        "",
        f"Previous run: {candidate.run_id} ({age})",
        f'  args: "{candidate.original_args}"',
        f"  status: {candidate.failed_phase_status} — phase '{candidate.failed_phase_id}': {err_msg}",
        "",
        f"Last successful phase: {candidate.last_successful_phase_id}",
    ]
    if candidate.user_facing_summary:
        lines.append(f"  user_facing_summary: {candidate.user_facing_summary}")
    if isinstance(candidate.next_phase_input, dict):
        for k, v in list(candidate.next_phase_input.items())[:5]:
            lines.append(f"  next_phase_input.{k}: {_preview(v)}")
    elif candidate.next_phase_input is not None:
        lines.append(f"  next_phase_input: {_preview(candidate.next_phase_input)}")
    lines.append("")
    return "\n".join(lines)


def prompt_user_to_resume(
    candidate: ResumeCandidate, *,
    stdin: TextIO, stdout: TextIO,
    now: Optional[datetime] = None,
) -> bool:
    """Write the preview + ask 'Resume from 'X'? [Y/n]'. Returns True
    for empty/y/Y; False for n/N. Any other input is treated as 'n'
    (conservative: don't resume on unrecognized input)."""
    stdout.write(format_resume_prompt(candidate, now=now))
    stdout.write(f"Resume from '{candidate.failed_phase_id}'? [Y/n]: ")
    stdout.flush()
    line = stdin.readline().strip()
    if line == "" or line.lower() == "y":
        return True
    return False
```

- [ ] **Step 4: Tests pass**

```bash
cd launcher && uv run pytest tests/test_resume.py -v
```

Expected: ~20 passing total (10 from Task 2 + 5 + 5 from Task 3).

- [ ] **Step 5: Full suite**

```bash
cd launcher && uv run pytest 2>&1 | tail -3
```

Expected: 694 passing (684 + 10 new).

- [ ] **Step 6: Commit**

```bash
git add launcher/zipsa/core/resume.py launcher/tests/test_resume.py
git commit -m "feat(resume): prompt formatter + interactive confirm

format_resume_prompt() turns a ResumeCandidate into the multi-line
preview text described in the spec (run id + relative age + original
args + failed phase + last successful phase summary + truncated
next_phase_input preview).

prompt_user_to_resume() writes the preview to a stream and reads
Y/n from another — pure functions of (stream, stream, candidate)
so they're trivially unit-testable without monkeypatching stdin/stdout."
```

---

## Task 4: CLI wiring (`--no-resume` flag + dispatch)

**Files:**
- Modify: `launcher/zipsa/cli.py:197-246` (the `run` command)
- Test: `launcher/tests/test_cli.py`

- [ ] **Step 1: Write failing tests**

Add to `launcher/tests/test_cli.py`:

```python
class TestRunResumeFlag:
    """The run command grows a --no-resume flag that skips the
    eligibility check entirely. Other resume behavior (eligibility +
    prompt) is unit-tested in test_resume; here we just verify the
    flag plumbs through and the non-interactive exit-2 message fires."""

    def test_no_resume_flag_in_signature(self):
        import inspect
        from zipsa.cli import run
        sig = inspect.signature(run)
        assert "no_resume" in sig.parameters

    def test_non_interactive_with_candidate_exits_2(
        self, tmp_path, monkeypatch, capsys,
    ):
        """When eligibility says candidate exists AND stdin is not a
        TTY AND --no-resume not passed, run() exits 2 with the
        documented message."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        # Build a synthetic eligible prior run for "x"
        from tests.test_resume import _two_phase_failed
        _two_phase_failed(tmp_path, skill="x", version="0.1.0",
                          user_input="today")
        # Stub the skill resolver so we don't actually try to load
        # an installed skill named "x"
        import zipsa.cli as cli_mod
        from unittest.mock import MagicMock

        fake_skill = MagicMock()
        fake_skill.name = "x"
        fake_skill.manifest.metadata.version = "0.1.0"
        fake_skill.manifest.spec.phases = [MagicMock(id="p1"), MagicMock(id="p2")]
        monkeypatch.setattr(cli_mod, "_resolve_skill_path",
                            lambda n: tmp_path / "fake_install_dir")
        monkeypatch.setattr(cli_mod.Skill, "load", lambda p: fake_skill)
        # Force non-interactive
        import sys as _sys
        class _Pipe:
            def isatty(self): return False
            def readline(self): return ""
        monkeypatch.setattr(_sys, "stdin", _Pipe())

        from typer.testing import CliRunner
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(cli_mod.app, ["run", "x", "today"],
                                catch_exceptions=False)
        assert result.exit_code == 2
        assert "previous failed run found" in (result.stderr or "")
```

- [ ] **Step 2: Run, expect fails (flag not added yet)**

```bash
cd launcher && uv run pytest tests/test_cli.py -v -k Resume
```

- [ ] **Step 3: Add the flag + wiring**

In `launcher/zipsa/cli.py`, add the `--no-resume` flag to the `run` command signature (after `summary_to`):

```python
    no_resume: Annotated[
        bool,
        typer.Option(
            "--no-resume",
            help=(
                "Skip the auto-detect-failed-run check. Always start "
                "from phase 0, even if the previous run failed."
            ),
        ),
    ] = False,
```

In the function body, after `_check_call_trace(name)` and BEFORE `skill = Skill.load(...)`:

```python
    # Resume eligibility — auto-detect a recoverable prior run. See
    # docs/superpowers/specs/2026-05-21-resume-failed-run-design.md
    # for behavior matrix.
    resume_from: Optional[int] = None
    if not no_resume:
        # Load skill first so we know the current phase count.
        # (Skill.load is cheap — file parse.)
        skill_for_check = Skill.load(_resolve_skill_path(name))
        from .core.resume import find_resumable_run, prompt_user_to_resume
        current_args = user_input or ""
        candidate = find_resumable_run(
            skill=name,
            current_version=skill_for_check.manifest.metadata.version,
            current_args=current_args,
            current_phase_count=len(skill_for_check.manifest.spec.phases or []),
        )
        if candidate is not None:
            import sys as _sys
            if _sys.stdin.isatty():
                if prompt_user_to_resume(candidate, stdin=_sys.stdin, stdout=_sys.stderr):
                    resume_from = candidate.failed_phase_index
            else:
                typer.echo(
                    "Error: previous failed run found "
                    f"({candidate.run_id}, phase '{candidate.failed_phase_id}'); "
                    "pass --no-resume to start fresh, "
                    "or run interactively to resume",
                    err=True,
                )
                raise typer.Exit(code=2)
```

(`Optional[int]` requires that `Optional` already be in the imports — check; if not, add `from typing import Optional` near the top.)

Forward `resume_from` to the executor where the run is dispatched. Find the `executor.run(...)` call in the same function and add `resume_from=resume_from` to its kwargs.

- [ ] **Step 4: Run tests**

```bash
cd launcher && uv run pytest tests/test_cli.py -v -k Resume
```

If the test relies on imports inside the cli module that aren't there yet, the import error is expected. Fix until both pass.

- [ ] **Step 5: Full suite**

```bash
cd launcher && uv run pytest 2>&1 | tail -3
```

Expected: 696 passing.

- [ ] **Step 6: Commit**

```bash
git add launcher/zipsa/cli.py launcher/tests/test_cli.py
git commit -m "feat(cli): --no-resume flag + auto-detect resume in run command

After _check_call_trace and before the main Skill.load, call
resume.find_resumable_run. If a candidate exists:
  - interactive: prompt user; on Y/empty, set resume_from
  - non-interactive: exit 2 with the documented message (caller
    must pass --no-resume to override)
  - --no-resume: skip the check entirely

resume_from is forwarded to executor.run, which (next task) actually
skips the already-completed phases."
```

---

## Task 5: Executor `resume_from` parameter + phase skip

**Files:**
- Modify: `launcher/zipsa/core/executor.py` (`run` and `_execute_phases`)
- Test: `launcher/tests/test_executor.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_executor.py`:

```python
class TestResumeFromSkipsPhases:
    """When _execute_phases is called with resume_from=N, phases
    0..N-1 are skipped; previous_output is loaded from
    phases/<N-1>-*/state.json; the resumed phase's metering starts
    from zero."""

    def test_resume_from_loads_prior_state(self, tmp_path, monkeypatch):
        """The most testable hook: _load_resume_state returns the
        next_phase_input written by the prior run's phase N-1."""
        from zipsa.core.executor import DockerExecutor

        run_dir = tmp_path / "x@0.1.0" / "runs" / "2026-05-21_100000_000000"
        phase_dir = run_dir / "phases" / "1-second"
        phase_dir.mkdir(parents=True)
        envelope = {
            "status": "ok", "phase": "second",
            "next_phase_input": {"answer": 42, "marker": "ok"},
            "user_facing_summary": "phase 2 done",
            "result": None, "state_updates": None,
        }
        (phase_dir / "state.json").write_text(__import__("json").dumps(envelope))

        loaded = DockerExecutor._load_resume_state(run_dir, resume_from=2)
        assert loaded == {"answer": 42, "marker": "ok"}

    def test_resume_from_missing_state_raises(self, tmp_path):
        """If phase N-1's state.json is missing, executor cannot
        proceed — raises with a clear message."""
        from zipsa.core.executor import DockerExecutor
        run_dir = tmp_path / "x@0.1.0" / "runs" / "2026-05-21_100000_000000"
        (run_dir / "phases").mkdir(parents=True)
        with pytest.raises(FileNotFoundError, match="state.json"):
            DockerExecutor._load_resume_state(run_dir, resume_from=2)
```

- [ ] **Step 2: Run, expect fail**

```bash
cd launcher && uv run pytest tests/test_executor.py -v -k "ResumeFrom"
```

- [ ] **Step 3: Add `_load_resume_state` helper**

In `launcher/zipsa/core/executor.py`, near `_write_phase_state` from Task 1:

```python
    @staticmethod
    def _load_resume_state(run_dir: Path, resume_from: int) -> object:
        """Read the next_phase_input from the phase BEFORE resume_from.

        Used by _execute_phases when resume_from is set: the previous
        phase's state.json is the source of truth for what the resumed
        phase should see as previous_output.
        """
        prev_idx = resume_from - 1
        phases_dir = run_dir / "phases"
        # Lex-sorted directory list (idx is the prefix); pick the one
        # starting with f"{prev_idx}-".
        for d in sorted(phases_dir.iterdir()) if phases_dir.exists() else []:
            if d.name.startswith(f"{prev_idx}-"):
                state_path = d / "state.json"
                if state_path.exists():
                    return json.loads(state_path.read_text()).get(
                        "next_phase_input",
                    )
                break
        raise FileNotFoundError(
            f"state.json for phase {prev_idx} not found under {phases_dir}"
        )
```

- [ ] **Step 4: Add `resume_from` kwarg to `_execute_phases`**

Find `_execute_phases` (around line 840). Add `resume_from: Optional[int] = None` to its signature. Near the top of its body (after the phases list is loaded), add:

```python
        # Resume support: when resume_from is set, skip phases
        # 0..resume_from-1 and seed previous_output from the persisted
        # state.json of phase resume_from-1.
        start_phase_idx = 0
        if resume_from is not None and resume_from > 0:
            start_phase_idx = resume_from
            previous_output = self._load_resume_state(
                run_dir, resume_from=resume_from,
            )
            # Pre-populate phase_summaries with ok markers for the
            # skipped phases so the summary still records them as ran.
            for skipped_idx in range(resume_from):
                skipped = phases[skipped_idx]
                phase_summaries.append(PhaseSummary(
                    id=skipped.id, status="ok",
                    cost_usd=0.0,  # cost from prior run is in its own summary
                    turns=0,
                ))
```

Then change the phase-loop iteration to start at `start_phase_idx`:

```python
        for phase_idx, phase in enumerate(phases):
            if phase_idx < start_phase_idx:
                continue
            ...  # existing body
```

- [ ] **Step 5: Forward `resume_from` from `run()` to `_execute_phases`**

In `DockerExecutor.run()`, add `resume_from: Optional[int] = None` to the signature and forward to the `_execute_phases` call.

- [ ] **Step 6: Tests pass**

```bash
cd launcher && uv run pytest tests/test_executor.py -v -k "ResumeFrom"
cd launcher && uv run pytest 2>&1 | tail -3
```

Expected: 2 unit pass + 698 total.

- [ ] **Step 7: Commit**

```bash
git add launcher/zipsa/core/executor.py launcher/tests/test_executor.py
git commit -m "feat(executor): resume_from skips phases + loads prior state

DockerExecutor.run accepts resume_from: int | None. When set,
_execute_phases skips phases 0..resume_from-1, loads
previous_output from phases/<resume_from-1>-*/state.json, and
records the skipped phases in phase_summaries as ok with cost=0
turns=0 (their real cost is in the original run's summary).

The resumed phase's cost/turn meters start fresh (no carry-over
from the failed prior attempt) so a limits_exceeded retry is
actually possible."
```

---

## Task 6: End-to-end fixture + integration test

**Files:**
- Create: `launcher/tests/fixtures/skills/two-phase-fail/manifest.yaml`
- Create: `launcher/tests/fixtures/skills/two-phase-fail/SKILL.md`
- Test: `launcher/tests/test_resume_e2e.py`

- [ ] **Step 1: Build the fixture skill**

Create `launcher/tests/fixtures/skills/two-phase-fail/manifest.yaml`:

```yaml
apiVersion: zipsa.dev/v1alpha1
kind: Skill
metadata:
  name: two-phase-fail
  version: 0.1.0
  description: |
    Integration fixture for resume. Phase 1 always succeeds with a
    deterministic next_phase_input. Phase 2 always fails with a
    declared error code.
spec:
  purpose: Resume integration test fixture.
  instructions: ./SKILL.md
  default_query: "go"
  model:
    name: claude-haiku-4-5-20251001
  tools:
    builtin: []
  phases:
    - id: succeed
      goal: "Always emit ok with a deterministic next_phase_input."
      allowed_tools: []
      limits:
        max_turns: 2
        max_cost_usd: 0.03
        timeout_seconds: 30
    - id: fail
      goal: "Always emit failed with an error."
      allowed_tools: []
      limits:
        max_turns: 2
        max_cost_usd: 0.03
        timeout_seconds: 30
```

Create the matching `SKILL.md`:

```markdown
# Two-Phase Fail (resume integration fixture)

## Phase: succeed

Return this exact JSON envelope. Do not call any tool first.

```json
{
  "status": "ok",
  "phase": "succeed",
  "result": {"phase": 1},
  "state_updates": null,
  "next_phase_input": {"phase1_done": true, "marker": "abc"},
  "user_facing_summary": "phase 1 done",
  "error": null
}
```

## Phase: fail

Return this exact JSON envelope. Do not call any tool first.

```json
{
  "status": "failed",
  "phase": "fail",
  "result": null,
  "state_updates": null,
  "next_phase_input": null,
  "user_facing_summary": "phase 2 intentional failure",
  "error": {"code": "test_failure", "message": "intentional"}
}
```
```

- [ ] **Step 2: Write the integration test**

Create `launcher/tests/test_resume_e2e.py`:

```python
"""End-to-end resume verification.

These tests actually spawn the executor against the two-phase-fail
fixture. Marked @pytest.mark.integration so they can be excluded from
fast unit-test runs."""

import json
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration


def _fixture_path() -> Path:
    return Path(__file__).parent / "fixtures/skills/two-phase-fail"


@pytest.fixture
def installed_fixture(tmp_path, monkeypatch):
    """Install the two-phase-fail fixture into a temporary ZIPSA_HOME."""
    monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
    subprocess.run(
        ["uv", "run", "zipsa", "install", "--link", str(_fixture_path())],
        check=True,
    )
    yield


def test_state_json_written_for_phase1(installed_fixture, tmp_path):
    """First run: phase 1 should succeed (state.json present), phase 2
    fails (no state.json)."""
    r = subprocess.run(
        ["uv", "run", "zipsa", "run", "two-phase-fail", "--no-resume"],
        capture_output=True, env={**__import__("os").environ,
                                   "ZIPSA_HOME": str(tmp_path)},
    )
    assert r.returncode != 0  # phase 2 fails

    # Locate the run dir
    runs_dir = tmp_path / "two-phase-fail@0.1.0" / "runs"
    runs = sorted(runs_dir.iterdir())
    assert runs
    run_dir = runs[-1]
    # Phase 1 wrote state.json
    p1 = run_dir / "phases" / "0-succeed" / "state.json"
    assert p1.exists()
    envelope = json.loads(p1.read_text())
    assert envelope["next_phase_input"] == {"phase1_done": True, "marker": "abc"}
    # Phase 2 wrote no state.json
    p2 = run_dir / "phases" / "1-fail" / "state.json"
    assert not p2.exists()


def test_second_run_with_no_resume_starts_fresh(installed_fixture, tmp_path):
    """Two consecutive --no-resume invocations should not see each
    other — both run phase 1 + 2."""
    for _ in range(2):
        subprocess.run(
            ["uv", "run", "zipsa", "run", "two-phase-fail", "--no-resume"],
            env={**__import__("os").environ, "ZIPSA_HOME": str(tmp_path)},
        )
    runs = sorted((tmp_path / "two-phase-fail@0.1.0" / "runs").iterdir())
    assert len(runs) == 2
    for r in runs:
        # Each run executed phase 1 (state.json present)
        assert (r / "phases" / "0-succeed" / "state.json").exists()


def test_non_interactive_without_no_resume_exits_2(installed_fixture, tmp_path):
    """After a failed run, a follow-up without --no-resume and without
    a TTY should exit 2 (refusing to silently resume or start fresh)."""
    # First run — fails
    subprocess.run(
        ["uv", "run", "zipsa", "run", "two-phase-fail", "--no-resume"],
        env={**__import__("os").environ, "ZIPSA_HOME": str(tmp_path)},
    )
    # Second run — no --no-resume, no TTY (subprocess inherits no tty)
    r = subprocess.run(
        ["uv", "run", "zipsa", "run", "two-phase-fail"],
        capture_output=True, stdin=subprocess.DEVNULL,
        env={**__import__("os").environ, "ZIPSA_HOME": str(tmp_path)},
    )
    assert r.returncode == 2
    assert b"previous failed run found" in r.stderr
```

- [ ] **Step 3: Run the integration tests**

```bash
cd launcher && uv run pytest tests/test_resume_e2e.py -v -m integration
```

These actually spawn docker. Expect ~2-3 minutes total. If your machine doesn't have docker available, mark them skip-on-no-docker (use `pytest.importorskip` or a helper).

- [ ] **Step 4: Verify they don't break the fast suite**

```bash
cd launcher && uv run pytest -m "not integration" 2>&1 | tail -3
```

Should still be 698 passing.

- [ ] **Step 5: Commit**

```bash
git add launcher/tests/fixtures/skills/two-phase-fail launcher/tests/test_resume_e2e.py
git commit -m "test(resume): two-phase-fail fixture + e2e integration tests

Fixture skill with phase 1 (always ok, deterministic
next_phase_input) and phase 2 (always failed, declared error code).
Integration tests verify:
  - state.json written for phase 1, NOT for failed phase 2
  - --no-resume invocations stay independent (no false eligibility)
  - non-interactive + candidate + no flag = exit 2

Marked @pytest.mark.integration so the fast unit-test run skips them."
```

---

## Task 7: Documentation

**Files:**
- Modify: `launcher/zipsa/system-prompts/runtime-contract.md` (mention resume in execution_context section if relevant)
- Modify: `launcher/README.md` or whichever readme has CLI examples
- Modify: BACKLOG.md — strike through or move the resume item to "shipped"

- [ ] **Step 1: Update runtime-contract.md if it references run lifecycle**

```bash
grep -n "resume\|--no-resume\|failed run" launcher/zipsa/system-prompts/runtime-contract.md
```

If there's nothing about run lifecycle, the contract is unaffected (resume is a launcher concern, transparent to the in-container agent). Skip this step.

If there IS run-lifecycle text, add a one-line note that the launcher may resume from a failed phase: the agent's `previous_phase_output` may come from a prior failed run's last-ok phase rather than the current run's phase N-1. The agent doesn't need to do anything different — the contract from its perspective is identical.

- [ ] **Step 2: Update CLI help / README**

If `launcher/README.md` (or equivalent) has a CLI usage section, add a brief paragraph and example:

```markdown
### Resuming a failed run

After a multi-phase skill fails (e.g. `bip-daily-x` at the `post`
phase), re-running the same command auto-detects the prior failure
and prompts:

```
$ zipsa run bip-daily-x today
Previous run: 2026-05-21_231116 (1 hour ago)
  args: "today"
  status: limits_exceeded — phase 'post': cost $0.13 > limit $0.10
...
Resume from 'post'? [Y/n]:
```

Accept (`Y` or empty enter) to skip the already-completed phases and
retry the failed one. The prior run's `next_phase_input` is loaded
automatically, so HITL-iterated state (like an approved draft) is
preserved across the retry.

In non-interactive contexts (cron, piped stdin), zipsa refuses to
silently auto-resume or auto-start-fresh — it exits 2 with a message
asking you to pass `--no-resume` explicitly:

```
$ zipsa run bip-daily-x today < /dev/null
Error: previous failed run found (2026-05-21_231116, phase 'post'); pass --no-resume to start fresh, or run interactively to resume
```
```

- [ ] **Step 3: Mark BACKLOG item shipped**

In `BACKLOG.md`, find the `## Resume a failed run from the failed phase (2026-05-18)` section. Add a one-line `**Status: shipped 2026-05-21 (PR #TBD)**` near the top of that section, or move it to a "## Shipped" section if one exists.

- [ ] **Step 4: Commit**

```bash
git add launcher/README.md BACKLOG.md launcher/zipsa/system-prompts/runtime-contract.md
git commit -m "docs(resume): CLI usage example + mark BACKLOG item shipped"
```

---

## Verification (end-to-end)

After all tasks:

```bash
cd /Users/neochoon/WestbrookAI/zipsa/.worktrees/feat-resume-failed-run/launcher
uv run pytest                                       # all green incl. integration
zipsa install --link tests/fixtures/skills/two-phase-fail
zipsa run two-phase-fail --no-resume                # phase 1 ok, phase 2 fails
zipsa run two-phase-fail                            # prompt appears: Resume from 'fail'? [Y/n]
# enter Y — should re-execute phase 'fail' only (phase 1 skipped)
```

## Out of scope (deferred; do not implement in this PR)

- Named run selection (`--resume <run-id>`, `zipsa runs <skill>`)
- Skill manifest opt-in (`spec.resume: enabled/disabled`)
- Automatic HITL-aware rewind to user-confirming phases
- `--auto-resume` for cron / non-interactive auto-retry

(All listed in the spec's "What This Does NOT Do" section.)
