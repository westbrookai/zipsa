"""SummaryWriter tests — pure module, no executor."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zipsa.core.summary import (
    SCHEMA_VERSION,
    PhaseSummary,
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
        # New fields absorbed from metadata.json — present but None/empty
        # when not provided by the caller.
        assert s["user_input"] == ""
        assert s["stop_reason"] is None
        assert s["usage"] is None
        assert s["model_usage"] is None

    def test_metadata_fields_passed_through(self):
        """user_input, stop_reason, usage, model_usage forwarded to summary."""
        s = build_summary(
            status="ok",
            exit_code=0,
            skill="weather",
            version="0.3.1",
            started_at=_utc("2026-05-19T11:00:00+10:00"),
            finished_at=_utc("2026-05-19T11:00:05+10:00"),
            cost_usd=0.01,
            turns=1,
            phases=[],
            result={},
            user_input="시드니 날씨",
            stop_reason="end_turn",
            usage={"input_tokens": 7, "output_tokens": 302},
            model_usage={"claude-sonnet-4-6": {"costUSD": 0.08}},
        )
        assert s["user_input"] == "시드니 날씨"
        assert s["stop_reason"] == "end_turn"
        assert s["usage"]["input_tokens"] == 7
        assert s["model_usage"]["claude-sonnet-4-6"]["costUSD"] == 0.08

    def test_version_fields_present_for_debugging(self):
        """Five version fields needed to reproduce / debug a run."""
        s = build_summary(
            status="ok", exit_code=0,
            skill="weather", version="0.3.1",
            started_at=_utc("2026-05-19T11:00:00+10:00"),
            finished_at=_utc("2026-05-19T11:00:01+10:00"),
            cost_usd=0.01, turns=1, phases=[], result={},
            zipsa_version="0.1.5",
            runtime_image="ghcr.io/westbrookai/zipsa-runtime:0.4.6",
            runtime_version="0.4.6",
            claude_version="2.1.114",
            model="claude-sonnet-4-6",
        )
        assert s["zipsa_version"] == "0.1.5"
        assert s["runtime_image"] == "ghcr.io/westbrookai/zipsa-runtime:0.4.6"
        assert s["runtime_version"] == "0.4.6"
        assert s["claude_version"] == "2.1.114"
        assert s["model"] == "claude-sonnet-4-6"

    def test_version_fields_default_to_none_when_absent(self):
        """Best-effort: if image inspect failed or SDK didn't emit init,
        version fields are present-but-None instead of missing keys.
        Consumers can safely `.get()` without KeyError handling."""
        s = build_summary(
            status="ok", exit_code=0,
            skill="x", version="1.0.0",
            started_at=_utc("2026-05-19T11:00:00+10:00"),
            finished_at=_utc("2026-05-19T11:00:01+10:00"),
            cost_usd=0.0, turns=0, phases=[], result={},
        )
        assert "zipsa_version" in s and s["zipsa_version"] is None
        assert "runtime_image" in s and s["runtime_image"] is None
        assert "runtime_version" in s and s["runtime_version"] is None
        assert "claude_version" in s and s["claude_version"] is None
        assert "model" in s and s["model"] is None

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
