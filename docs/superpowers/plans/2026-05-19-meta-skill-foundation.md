# Meta-skill Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship the contract that lets a parent skill invoke a child via `zipsa run <child>` and trust the result. Five locked deliverables: unified exit codes, `run_dir/summary.json`, `--summary-to` flag, `spec.children` manifest field, and startup-time children validation (warn-only).

**Architecture:** One new module (`zipsa/core/summary.py`), one new manifest field, exit-code translation in cli.py, validation in cli.py. No change to executor's streaming/limits/HITL/memory/hooks code paths — we just hook into the existing `finally` block to write summary.json.

**Tech Stack:** Python 3.10+ stdlib (json, dataclasses, datetime), pydantic v2 (existing), pytest. No new runtime dependencies.

---

## Commit boundaries

| Commit | What |
|---|---|
| **1** | `feat(models): SkillSpec.children list field` (Task 1, pure schema + tests) |
| **2** | `feat(summary): SummaryWriter — build + write run summary.json` (Task 2, pure module + tests) |
| **3** | `feat(executor): wire SummaryWriter into run lifecycle` (Task 3, executor integration + tests) |
| **4** | `feat(cli): exit codes + --summary-to + children validation` (Task 4, CLI surface + tests) |

Tasks 1+2 are pure modules and can ship independently. Task 3 consumes Task 2. Task 4 consumes Task 1+2+3.

---

## File map

| File | Role |
|---|---|
| `launcher/zipsa/core/models.py` | Add `children: list[str] = []` on `SkillSpec` |
| `launcher/zipsa/core/summary.py` (new) | `SummaryWriter` class + `build_summary` + `write_summary` |
| `launcher/zipsa/core/executor.py` | Track final status; write summary in `finally`; return final status for cli to translate |
| `launcher/zipsa/cli.py` | `--summary-to` flag; exit-code translation; children validation on parent startup |
| `launcher/tests/test_models.py` | Tests for `children` field |
| `launcher/tests/test_summary.py` (new) | Tests for SummaryWriter |
| `launcher/tests/test_executor.py` | Tests for summary.json being written + final-status tracking |
| `launcher/tests/test_cli.py` | Tests for exit codes per status, --summary-to, children validation warnings |

---

## Task 1: `SkillSpec.children` field

**Files:**
- Modify: `launcher/zipsa/core/models.py`
- Test: `launcher/tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `launcher/tests/test_models.py`:

```python
class TestChildren:
    """SkillSpec.children declares which other skills this skill may invoke
    via Bash(zipsa:*). Optional list[str]; defaults to []."""

    def _spec(self, **overrides):
        from zipsa.core.models import SkillSpec
        data = {
            "purpose": "test",
            "instructions": "./SKILL.md",
        }
        data.update(overrides)
        return SkillSpec.model_validate(data)

    def test_children_absent_defaults_to_empty_list(self):
        spec = self._spec()
        assert spec.children == []

    def test_children_accepts_list_of_strings(self):
        spec = self._spec(children=["daily-progress", "bip-daily-x"])
        assert spec.children == ["daily-progress", "bip-daily-x"]

    def test_children_rejects_non_string_entries(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            self._spec(children=["valid-name", 42, None])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd launcher && uv run pytest tests/test_models.py::TestChildren -v
```
Expected: 3 failures — `children` attribute doesn't exist.

- [ ] **Step 3: Implement**

In `launcher/zipsa/core/models.py`, add to `SkillSpec` (after `default_query` or similar):

```python
class SkillSpec(BaseModel):
    # ... existing fields ...
    children: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_models.py::TestChildren -v
uv run pytest                # full suite
```
Expected: 3 new passing; 447 baseline + 3 = 450 total.

- [ ] **Step 5: Commit (boundary 1)**

```bash
git add launcher/zipsa/core/models.py launcher/tests/test_models.py
git commit -m "feat(models): SkillSpec.children list field

Optional list of child skill names a parent skill may invoke via
Bash(zipsa:*). Used by the launcher at parent startup to validate
that the children are installed and that the parent's budget covers
the worst-case sum of their declared limits (warning only — actual
invocation may be conditional)."
```

---

## Task 2: `SummaryWriter` — build + write summary.json

**Files:**
- Create: `launcher/zipsa/core/summary.py`
- Create: `launcher/tests/test_summary.py`

This task ships the pure module. Task 3 wires it into the executor.

- [ ] **Step 1: Write the failing test**

```python
# launcher/tests/test_summary.py
"""SummaryWriter tests — pure module, no executor."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zipsa.core.summary import (
    SCHEMA_VERSION,
    PhaseSummary,
    RunSummary,
    build_summary,
    write_summary,
)


def _utc(s):
    """Helper for ISO 8601 with timezone."""
    return datetime.fromisoformat(s)


class TestPhaseSummary:
    def test_phase_summary_shape(self):
        p = PhaseSummary(id="precheck", status="ok", cost_usd=0.01, turns=2)
        assert p.id == "precheck"
        assert p.status == "ok"


class TestBuildSummary:
    def test_ok_status(self):
        s = build_summary(
            status="ok",
            exit_code=0,
            skill="weather",
            version="0.3.1",
            started_at=_utc("2026-05-19T11:32:00+10:00"),
            finished_at=_utc("2026-05-19T11:32:18+10:00"),
            cost_usd=0.0707,
            turns=2,
            phases=[PhaseSummary(id="main", status="ok", cost_usd=0.07, turns=2)],
            result={"temp_C": 19, "city": "Sydney"},
        )
        assert s["status"] == "ok"
        assert s["exit_code"] == 0
        assert s["skill"] == "weather"
        assert s["version"] == "0.3.1"
        assert s["schema_version"] == SCHEMA_VERSION
        assert s["duration_seconds"] == pytest.approx(18.0, abs=0.1)
        assert s["cost_usd"] == pytest.approx(0.0707)
        assert s["turns"] == 2
        assert s["result"] == {"temp_C": 19, "city": "Sydney"}
        assert s["error"] is None
        assert len(s["phases"]) == 1
        assert s["phases"][0]["status"] == "ok"

    def test_failed_status_omits_result_includes_error(self):
        s = build_summary(
            status="failed",
            exit_code=1,
            skill="x",
            version="1.0.0",
            started_at=_utc("2026-05-19T11:00:00+10:00"),
            finished_at=_utc("2026-05-19T11:00:05+10:00"),
            cost_usd=0.001,
            turns=1,
            phases=[PhaseSummary(id="main", status="failed", cost_usd=0.001, turns=1)],
            error={"code": "x_post_failed", "message": "HTTP 402 CreditsDepleted"},
        )
        assert s["status"] == "failed"
        assert s["exit_code"] == 1
        assert s["result"] is None
        assert s["error"]["code"] == "x_post_failed"
        assert "CreditsDepleted" in s["error"]["message"]

    def test_limits_exceeded_status(self):
        s = build_summary(
            status="limits_exceeded",
            exit_code=3,
            skill="weather",
            version="0.3.1",
            started_at=_utc("2026-05-19T11:00:00+10:00"),
            finished_at=_utc("2026-05-19T11:00:05+10:00"),
            cost_usd=0.0707,
            turns=2,
            phases=[PhaseSummary(id="main", status="limits_exceeded", cost_usd=0.0707, turns=2)],
            error={
                "code": "limits_exceeded",
                "message": "phase cost: $0.0707 > $0.001",
                "details": {"scope": "phase", "kind": "cost", "value": 0.0707, "limit": 0.001, "phase": "main"},
            },
        )
        assert s["status"] == "limits_exceeded"
        assert s["exit_code"] == 3
        assert s["error"]["details"]["kind"] == "cost"


class TestWriteSummary:
    def test_write_creates_file_atomically(self, tmp_path):
        target = tmp_path / "summary.json"
        s = build_summary(
            status="ok", exit_code=0, skill="x", version="1.0.0",
            started_at=_utc("2026-05-19T11:00:00+10:00"),
            finished_at=_utc("2026-05-19T11:00:01+10:00"),
            cost_usd=0.01, turns=1, phases=[], result={},
        )
        write_summary(target, s)
        assert target.exists()
        loaded = json.loads(target.read_text())
        assert loaded["status"] == "ok"

    def test_write_overwrites_existing(self, tmp_path):
        target = tmp_path / "summary.json"
        target.write_text('{"stale": true}')
        s = build_summary(
            status="ok", exit_code=0, skill="x", version="1.0.0",
            started_at=_utc("2026-05-19T11:00:00+10:00"),
            finished_at=_utc("2026-05-19T11:00:01+10:00"),
            cost_usd=0.01, turns=1, phases=[], result={},
        )
        write_summary(target, s)
        loaded = json.loads(target.read_text())
        assert "stale" not in loaded
        assert loaded["status"] == "ok"

    def test_write_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "summary.json"
        s = build_summary(
            status="ok", exit_code=0, skill="x", version="1.0.0",
            started_at=_utc("2026-05-19T11:00:00+10:00"),
            finished_at=_utc("2026-05-19T11:00:01+10:00"),
            cost_usd=0.01, turns=1, phases=[], result={},
        )
        write_summary(target, s)
        assert target.exists()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd launcher && uv run pytest tests/test_summary.py -v
```
Expected: ModuleNotFoundError on `zipsa.core.summary`.

- [ ] **Step 3: Implement `summary.py`**

```python
# launcher/zipsa/core/summary.py
"""Per-run summary.json — the structured outcome readable by a parent
skill (or anyone with shell access).

Written to run_dir/summary.json after every run, regardless of how the
run ended (ok / business failure / limits / HITL / infra). Same shape
every time. The CLI's exit code matches the `exit_code` field (which
in turn matches `status` per the table in the design spec).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal, Any

SCHEMA_VERSION = 1

Status = Literal[
    "ok", "failed", "out_of_scope",
    "limits_exceeded", "user_declined", "infra_failed",
]


@dataclass(frozen=True)
class PhaseSummary:
    """Per-phase rollup for the summary's phases[] array."""
    id: str
    status: str       # may be any of Status; phases that ran before a
                      # failure are "ok", the failing phase is the failure status
    cost_usd: float
    turns: int


def build_summary(
    *,
    status: Status,
    exit_code: int,
    skill: str,
    version: str,
    started_at: datetime,
    finished_at: datetime,
    cost_usd: float,
    turns: int,
    phases: list[PhaseSummary],
    result: Optional[dict[str, Any]] = None,
    error: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build the summary dict in the schema documented in the design spec.

    `result` is included only when status == "ok"; `error` only otherwise.
    Callers are responsible for the status / error semantics — this
    function does NOT enforce consistency. (The executor's status
    tracking is the source of truth.)
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "exit_code": exit_code,
        "skill": skill,
        "version": version,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": (finished_at - started_at).total_seconds(),
        "cost_usd": cost_usd,
        "turns": turns,
        "phases": [
            {"id": p.id, "status": p.status, "cost_usd": p.cost_usd, "turns": p.turns}
            for p in phases
        ],
        "result": result,
        "error": error,
    }


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    """Atomically write summary.json to `path`.

    Creates parent directories as needed. Overwrites any existing file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    tmp.replace(path)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_summary.py -v
uv run pytest                # full suite
```
Expected: all passing.

- [ ] **Step 5: Commit (boundary 2)**

```bash
git add launcher/zipsa/core/summary.py launcher/tests/test_summary.py
git commit -m "feat(summary): SummaryWriter — build + write run summary.json

Pure module. build_summary() produces the dict in the schema from
the design spec (schema_version=1). write_summary() lands it at a
path atomically (tmp file + rename, creates parent dirs).

The executor will wire this in the next commit — for now it's a
self-contained module."
```

---

## Task 3: Wire `SummaryWriter` into executor

**Files:**
- Modify: `launcher/zipsa/core/executor.py`
- Test: `launcher/tests/test_executor.py`

### Step 1: Locate state to capture

The executor must track, for the run as a whole:
- `started_at` (set on `run()` entry)
- `final_status` (one of the Status literals — defaults to `"infra_failed"`, set as the run progresses)
- `final_exit_code` (mirrors final_status — set together)
- `final_error` (dict or None)
- `final_result` (dict or None — set only on `status=ok` final phase)
- `total_cost_usd`, `total_turns` (already tracked via limits machinery — reuse the running totals)
- `phases` list (per-phase rollup)

Find the existing run_dir setup. Around line 100-110 in run() the run_dir is created. That's where `started_at = datetime.now().astimezone()` should land.

### Step 2: Write the failing tests

Add to `launcher/tests/test_executor.py`:

```python
class TestSummaryWritten:
    """The executor writes summary.json to run_dir at the end of every
    run, regardless of how it ended. summary fields reflect the run's
    final status."""

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_summary_written_on_ok_run(self, mock_popen, tmp_path):
        from unittest.mock import MagicMock
        import json as _json

        result_line = _json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": '{"status": "ok", "phase": "main", "result": {"hello": "world"}}'}],
                "usage": {"input_tokens": 100},
            },
        }) + "\n"
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [result_line, ""]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None
        mock_process.wait = Mock(return_value=0)
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)
        executor = DockerExecutor()

        # Force run_dir into tmp_path so we can find summary.json
        with patch("zipsa.core.executor.zipsa_paths.skill_data_dir", return_value=tmp_path):
            list(executor.run(skill, "Hi", env={}))

        # Find the summary.json (only one runs dir)
        summary_path = next((tmp_path / "runs").glob("*/summary.json"))
        s = _json.loads(summary_path.read_text())
        assert s["status"] == "ok"
        assert s["exit_code"] == 0
        assert s["skill"] == skill.name
        assert s["result"] == {"hello": "world"}
        assert s["error"] is None
        assert s["schema_version"] == 1

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_summary_written_on_limit_breach(self, mock_popen, tmp_path):
        # ... similar setup, but assistant message that triggers limits breach ...
        # Assert: summary["status"] == "limits_exceeded", summary["exit_code"] == 3,
        # summary["error"]["details"]["kind"] in ("cost","time","turns")
        # ... (Implementer fills in the mock; the assertions are the contract)
        pass  # implementer writes this

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_summary_written_on_failed_status(self, mock_popen, tmp_path):
        # ... assistant emits status=failed JSON ...
        # Assert: summary["status"] == "failed", summary["exit_code"] == 1,
        # summary["result"] is None, summary["error"]["code"] is whatever the
        # contract JSON had.
        pass  # implementer writes this
```

The implementer expands the two `pass` tests to mirror the ok-case's mock pattern, using assistant message text that emits `status=failed` and a sequence that triggers a limit breach respectively.

### Step 3: Implement in executor.py

In `run()` or `_execute_skill()` (wherever run_dir is created):

```python
from datetime import datetime
from .summary import PhaseSummary, build_summary, write_summary

# At run start:
started_at = datetime.now().astimezone()

# Track these as the run progresses (alongside limits_state):
final_status = "infra_failed"  # default — overwritten as we learn the outcome
final_exit_code = 5
final_error: Optional[dict] = None
final_result: Optional[dict] = None
phase_summaries: list[PhaseSummary] = []
```

After parsing each phase's final output:
- If `status="ok"` and it's the last phase → `final_status="ok"`, `final_exit_code=0`, `final_result=phase_out["result"]`
- If `status="failed"` → `final_status="failed"`, `final_exit_code=1`, `final_error=phase_out["error"] or {"code":"failed","message":phase_out["user_facing_summary"]}`
- If `status="out_of_scope"` → `final_status="out_of_scope"`, `final_exit_code=2`
- Append a `PhaseSummary` for this phase regardless.

When `zipsa_limits_breach` event fires:
- `final_status="limits_exceeded"`, `final_exit_code=3`, `final_error={"code":"limits_exceeded","message":...,"details":{scope,kind,value,limit,phase}}`

When HITL_UNATTENDED bubbles up (look at how hitl_runner currently surfaces this):
- `final_status="user_declined"`, `final_exit_code=4`, `final_error={"code":"hitl_unattended","message":"..."}`

When user `confirm` returns no (HITL):
- Same as above but `error.code="user_declined"`.

When Docker dies non-zero AND not breach_terminated:
- `final_status="infra_failed"`, `final_exit_code=5`, `final_error={"code":"docker_failed","message":"exit code N"}`

In the `finally` block, right before existing run_dir cleanup:

```python
if run_dir is not None:
    finished_at = datetime.now().astimezone()
    summary = build_summary(
        status=final_status,
        exit_code=final_exit_code,
        skill=skill.name,
        version=skill.manifest.metadata.version,
        started_at=started_at,
        finished_at=finished_at,
        cost_usd=limits_state.run_cost_usd if limits_state else 0.0,
        turns=limits_state.run_turns if limits_state else 0,
        phases=phase_summaries,
        result=final_result if final_status == "ok" else None,
        error=final_error if final_status != "ok" else None,
    )
    write_summary(run_dir / "summary.json", summary)
```

Important: don't let summary writing fail the run if it can't write (best-effort).

### Step 4: Return final status to caller (cli.py needs it)

Change `_execute_skill` (or `run`) to RETURN a final-status object (or set it on a known instance attribute, or yield a final `zipsa_run_complete` event with status+exit_code). The cleanest path:

- Add a `zipsa_run_complete` event yielded as the LAST event of every run, with shape `{"type": "zipsa_run_complete", "status": ..., "exit_code": ...}`.
- cli.py reads the stream, captures this event, uses it to exit with the right code.

(If a `zipsa_run_complete` event already exists in the codebase per earlier work, extend it with `exit_code`.)

### Step 5: Run tests

```bash
uv run pytest tests/test_executor.py::TestSummaryWritten -v
uv run pytest                # full suite
```
Expected: 3 passing; no regressions.

### Step 6: Commit (boundary 3)

```bash
git add launcher/zipsa/core/executor.py launcher/tests/test_executor.py
git commit -m "feat(executor): wire SummaryWriter into run lifecycle

Track final status, error, result, and per-phase rollups across the
run. In the finally block, write summary.json to run_dir using the
SummaryWriter module from the previous commit.

Yield a 'zipsa_run_complete' event as the last event of every run,
carrying status + exit_code so cli.py can translate to a process
exit code (next commit)."
```

---

## Task 4: CLI — exit codes + `--summary-to` + children validation

**Files:**
- Modify: `launcher/zipsa/cli.py`
- Test: `launcher/tests/test_cli.py`

### Step 1: Exit-code translation

Find the `run` command in cli.py. Today it iterates the event stream and prints (via renderer) but doesn't translate any per-run status to an exit code.

After consuming the stream:

```python
# Find the final zipsa_run_complete event (last event)
exit_code = 0
for event in events:
    # renderer already handles this — we just consume in passing
    if event.get("type") == "zipsa_run_complete":
        exit_code = event.get("exit_code", 0)

raise typer.Exit(exit_code)
```

If `zipsa_run_complete` is never emitted (shouldn't happen, but defensively): exit 5 (`infra_failed`).

### Step 2: `--summary-to <path>` flag

Add to the `run` command's typer decorator:

```python
summary_to: Annotated[
    Optional[Path],
    typer.Option("--summary-to", help="Copy run summary.json to this path after the run."),
] = None,
```

After the run completes (and run_dir has been populated):

```python
if summary_to and run_dir:
    src = run_dir / "summary.json"
    if src.exists():
        summary_to.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy(src, summary_to)
```

Quiet on missing run_dir (dry-run / shell modes).

### Step 3: Startup validation for `spec.children`

After loading skill, before executor.run(...):

```python
if skill.manifest.spec.children:
    _validate_children(skill)  # prints warnings to stderr, returns nothing
```

Helper `_validate_children`:

```python
def _validate_children(parent: Skill) -> None:
    """Warn (on stderr) about (a) declared children that aren't installed,
    (b) sum of children's max_cost_usd / timeout_seconds exceeding
    parent's. Never raises."""
    from .core.install_health import check_install
    skills_dir = zipsa_home() / "skills"

    missing = []
    child_skills = []
    for name in parent.manifest.spec.children:
        path = skills_dir / name
        health = check_install(path)
        if not health.ok:
            missing.append((name, health.reason))
            continue
        try:
            child_skills.append(Skill.load(path))
        except Exception as e:
            missing.append((name, f"failed to load: {e}"))

    if missing:
        typer.echo(
            f"Warning: {parent.name} declares children that can't be loaded:", err=True
        )
        for name, reason in missing:
            typer.echo(f"  {name}: {reason}", err=True)

    parent_limits = parent.manifest.spec.limits
    if parent_limits and child_skills:
        # Budget check: cost
        sum_cost = sum(
            (c.manifest.spec.limits.max_cost_usd or 0.0)
            for c in child_skills if c.manifest.spec.limits
        )
        if parent_limits.max_cost_usd is not None and sum_cost > parent_limits.max_cost_usd:
            typer.echo(
                f"Warning: {parent.name} child cost limits don't add up.", err=True
            )
            typer.echo(
                f"  parent.max_cost_usd  = ${parent_limits.max_cost_usd:.4f}", err=True
            )
            typer.echo(f"  children sum         = ${sum_cost:.4f}", err=True)
            for c in child_skills:
                if c.manifest.spec.limits and c.manifest.spec.limits.max_cost_usd:
                    typer.echo(
                        f"    {c.name:20} = ${c.manifest.spec.limits.max_cost_usd:.4f}",
                        err=True,
                    )

        # Same for timeout
        sum_to = sum(
            (c.manifest.spec.limits.timeout_seconds or 0)
            for c in child_skills if c.manifest.spec.limits
        )
        if parent_limits.timeout_seconds is not None and sum_to > parent_limits.timeout_seconds:
            typer.echo(
                f"Warning: {parent.name} child timeouts don't add up.", err=True
            )
            typer.echo(
                f"  parent.timeout_seconds = {parent_limits.timeout_seconds}s", err=True
            )
            typer.echo(f"  children sum           = {sum_to}s", err=True)
```

### Step 4: Write failing tests

Add to `launcher/tests/test_cli.py`:

```python
class TestRunExitCodes:
    """zipsa run exit code matches the final status of the run."""

    def _setup_skill_and_run(self, tmp_path, monkeypatch, final_event_status, exit_code):
        # Use a fixture that yields a single zipsa_run_complete event with the
        # specified status + exit_code. The implementer adapts the mock pattern
        # used in other CLI tests.
        pass

    def test_run_ok_exits_0(self, tmp_path, monkeypatch):
        # ... mock executor.run to yield zipsa_run_complete with status=ok, exit_code=0
        # invoke runner, assert exit_code == 0
        pass

    def test_run_failed_exits_1(self, tmp_path, monkeypatch):
        # status=failed, exit_code=1 ...
        pass

    def test_run_limits_exceeded_exits_3(self, tmp_path, monkeypatch):
        # status=limits_exceeded, exit_code=3 ...
        pass


class TestSummaryToFlag:
    def test_summary_to_copies_file(self, tmp_path, monkeypatch):
        # Mock so a run_dir/summary.json gets written. Pass --summary-to <path>.
        # Assert: file exists at the passed path, content matches run_dir's.
        pass


class TestChildrenValidation:
    """When spec.children is declared, the launcher warns on stderr about
    (a) missing children and (b) budget mismatches."""

    def test_missing_child_emits_warning(self, tmp_path, monkeypatch):
        # Parent declares children=["missing"]. Run it (mock executor.run
        # to return immediately). Assert stderr contains the missing-child
        # warning.
        pass

    def test_budget_sum_warning(self, tmp_path, monkeypatch):
        # Parent has max_cost_usd=0.05, declares children whose sum is $0.10.
        # Assert stderr has the budget warning with the right numbers.
        pass

    def test_no_warning_when_budgets_fit(self, tmp_path, monkeypatch):
        # Parent has max_cost_usd=$1.00, declares children summing $0.50.
        # Assert: no "Warning:" lines in stderr.
        pass
```

The implementer fills in the mock setups using the patterns from `TestRunEmptyQuery` (PR #27) and `TestListBrokenEntries` (PR #29). The TESTS' CONTRACTS (the assertions) must stay as documented above.

### Step 5: Run tests

```bash
uv run pytest tests/test_cli.py -v
uv run pytest                # full suite
```
Expected: green; new tests + no regressions.

### Step 6: Manual smoke

```bash
# Reproduce the exit-code expectations from the spec's tables
zipsa run hello-world           # exit 0
echo "exit: $?"                 # 0

# Create a parent fixture with mismatched children
mkdir /tmp/parent-test
cat > /tmp/parent-test/manifest.yaml <<'EOF'
apiVersion: zipsa.dev/v1alpha1
kind: Skill
metadata: {name: parent-test, version: 0.1.0}
spec:
  purpose: test
  instructions: ./SKILL.md
  children: [hello-world, missing-child]
  limits: {max_cost_usd: 0.05, timeout_seconds: 10}
EOF
echo "# Test" > /tmp/parent-test/SKILL.md

zipsa install --link /tmp/parent-test
zipsa run parent-test           # stderr should warn about missing-child AND budget mismatch
zipsa uninstall parent-test
rm -rf /tmp/parent-test
```

### Step 7: Commit (boundary 4)

```bash
git add launcher/zipsa/cli.py launcher/tests/test_cli.py
git commit -m "feat(cli): exit codes + --summary-to + children validation

Three pieces:
- exit code: translate the run's final status to the exit code per
  the spec table (0=ok, 1=failed, 2=oos, 3=limits, 4=user_declined,
  5=infra_failed).
- --summary-to <path>: copy the post-run summary.json to a known
  path so parent skills can read it without inspecting run_dir.
- children validation: when spec.children is non-empty, warn on
  stderr about missing children and budget mismatches before
  invoking executor. Never refuses (parent may invoke children
  conditionally)."
```

---

## Wrap-up

After all 4 commits:

- [ ] `git log --oneline ffaf34d..HEAD` — 4 task commits + 1 spec commit at the head + 1 plan commit.
- [ ] `uv run pytest` from `launcher/` — green (~460 expected).
- [ ] Manual smoke: exit codes match each status (table above); --summary-to lands the file; declared children warnings show up.
- [ ] Push branch, open PR. Reference spec + plan.
