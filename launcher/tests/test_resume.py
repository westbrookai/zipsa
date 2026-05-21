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
            current_args="today", current_phase_count=1,
        ) is None

    def test_no_failed_phase_returns_none(self, tmp_path, monkeypatch):
        """If all phases succeeded but the overall status is failed
        (rare but possible — e.g. post-loop validation), no resume."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _make_run(tmp_path, "myskill", "0.1.0", "2026-05-21_100000_000000",
                  status="failed", user_input="today",
                  phases=[
                      {"id": "p1", "status": "ok", "cost_usd": 0.01, "turns": 1},
                      {"id": "p2", "status": "ok", "cost_usd": 0.02, "turns": 1},
                  ])
        assert find_resumable_run(
            skill="myskill", current_version="0.1.0",
            current_args="today", current_phase_count=2,
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
