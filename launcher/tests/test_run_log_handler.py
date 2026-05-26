"""Tests for RunLogHandler — reads output.jsonl + compacts per-turn.

Thin orchestrator over runtime.format_event_compact; the per-event
shape is the runtime plugin's job. Here we pin the file plumbing,
capping, and path-safety.
"""

import json
from pathlib import Path

import pytest

from zipsa.core.run_log_handler import RunLogHandler


def _write_event(f, event: dict) -> None:
    f.write(json.dumps(event) + "\n")


def _make_log(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for ev in events:
            _write_event(f, ev)


@pytest.fixture
def zipsa_home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
    return tmp_path


def _single_phase_run(home: Path, *, skill="weather", version="0.5.0",
                      run_id="2026-05-26_120000_000000") -> Path:
    """Set up a single-phase run directory layout and return the
    output.jsonl path."""
    run_dir = home / f"{skill}@{version}" / "runs" / run_id
    return run_dir / "output.jsonl"


def _multi_phase_run(home: Path, *, skill="daily-progress",
                     version="0.5.0", run_id="2026-05-26_120000_000000",
                     phases: list[str]) -> dict[str, Path]:
    """Set up a multi-phase run layout — returns
    {phase_id: output.jsonl path}."""
    run_dir = home / f"{skill}@{version}" / "runs" / run_id
    return {
        pid: run_dir / "phases" / f"{i}-{pid}" / "output.jsonl"
        for i, pid in enumerate(phases)
    }


class TestSinglePhase:
    def test_reads_and_compacts(self, zipsa_home):
        log = _single_phase_run(zipsa_home)
        _make_log(log, [
            {"type": "system", "subtype": "init"},
            {"type": "assistant", "message": {"content": [
                {"type": "thinking", "thinking": "I'll fetch the weather"},
            ]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "WebFetch",
                 "input": {"url": "https://wttr.in"}},
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "content": "Sunny 23C"},
            ]}},
            {"type": "result", "subtype": "success",
             "total_cost_usd": 0.012, "num_turns": 3},
        ])
        result = RunLogHandler().read(
            skill="weather", version="0.5.0",
            run_id="2026-05-26_120000_000000",
        )
        # Compact log is a single text blob with one line per event
        assert "S: init" in result["log"]
        assert "I'll fetch the weather" in result["log"]
        assert "WebFetch" in result["log"]
        assert "Sunny 23C" in result["log"]
        assert "cost=$0.012" in result["log"]
        # Summary fields
        assert result["total_turns"] == 5
        assert result["total_cost_usd"] == pytest.approx(0.012)

    def test_missing_run_dir_raises(self, zipsa_home):
        with pytest.raises(RuntimeError, match="RUN_LOG_NOT_FOUND"):
            RunLogHandler().read(
                skill="weather", version="0.5.0", run_id="nonexistent",
            )

    def test_size_cap_truncates_oldest(self, zipsa_home):
        """If joined log exceeds the cap, the handler keeps the most
        recent turns (analysis usually wants the tail — what went wrong
        at the end) and sets truncated=True."""
        log = _single_phase_run(zipsa_home)
        # 400 events, each long enough that the total far exceeds the
        # cap. Per-event 280-char cap is enforced by the formatter; the
        # handler's job is the total-output cap.
        events = []
        for i in range(400):
            events.append({"type": "assistant", "message": {"content": [
                {"type": "thinking", "thinking": f"thought number {i} " + "x" * 200},
            ]}})
        _make_log(log, events)
        result = RunLogHandler().read(
            skill="weather", version="0.5.0",
            run_id="2026-05-26_120000_000000",
            max_bytes=10_000,
        )
        assert result["truncated"] is True
        assert len(result["log"]) <= 10_000
        # The kept content should include the LAST events (most recent),
        # not the first.
        assert "thought number 399" in result["log"]
        # Earliest events dropped
        assert "thought number 0 " not in result["log"]


class TestMultiPhase:
    def test_concatenates_all_phases_with_markers(self, zipsa_home):
        logs = _multi_phase_run(
            zipsa_home, phases=["precheck", "fetch", "report"],
        )
        for phase_id, path in logs.items():
            _make_log(path, [
                {"type": "system", "subtype": "init"},
                {"type": "assistant", "message": {"content": [
                    {"type": "text", "text": f"In phase {phase_id}"},
                ]}},
            ])
        result = RunLogHandler().read(
            skill="daily-progress", version="0.5.0",
            run_id="2026-05-26_120000_000000",
        )
        # All three phases present, in order, with a clear marker line
        assert "In phase precheck" in result["log"]
        assert "In phase fetch" in result["log"]
        assert "In phase report" in result["log"]
        precheck_pos = result["log"].find("In phase precheck")
        fetch_pos = result["log"].find("In phase fetch")
        report_pos = result["log"].find("In phase report")
        assert precheck_pos < fetch_pos < report_pos

    def test_phase_filter(self, zipsa_home):
        """When phase_id is given, only that phase's log is returned."""
        logs = _multi_phase_run(
            zipsa_home, phases=["precheck", "fetch"],
        )
        _make_log(logs["precheck"], [
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "PRE only"},
            ]}},
        ])
        _make_log(logs["fetch"], [
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "FETCH only"},
            ]}},
        ])
        result = RunLogHandler().read(
            skill="daily-progress", version="0.5.0",
            run_id="2026-05-26_120000_000000",
            phase_id="fetch",
        )
        assert "FETCH only" in result["log"]
        assert "PRE only" not in result["log"]


class TestPathSafety:
    def test_run_id_path_traversal_rejected(self, zipsa_home):
        for bad in ("../other", "..", "a/b"):
            with pytest.raises(RuntimeError, match="RUN_LOG_BAD_NAME"):
                RunLogHandler().read(
                    skill="weather", version="0.5.0", run_id=bad,
                )

    def test_skill_path_traversal_rejected(self, zipsa_home):
        with pytest.raises(RuntimeError, match="RUN_LOG_BAD_NAME"):
            RunLogHandler().read(
                skill="../other", version="0.5.0", run_id="x",
            )

    def test_phase_id_path_traversal_rejected(self, zipsa_home):
        with pytest.raises(RuntimeError, match="RUN_LOG_BAD_NAME"):
            RunLogHandler().read(
                skill="weather", version="0.5.0", run_id="x",
                phase_id="../etc",
            )


class TestMalformedLines:
    def test_garbage_line_skipped_not_raised(self, zipsa_home):
        log = _single_phase_run(zipsa_home)
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "w") as f:
            f.write('{"type": "system", "subtype": "init"}\n')
            f.write("this is not valid json\n")
            f.write('{"type": "assistant", "message": {"content": [{"type": "text", "text": "after garbage"}]}}\n')
        result = RunLogHandler().read(
            skill="weather", version="0.5.0",
            run_id="2026-05-26_120000_000000",
        )
        assert "S: init" in result["log"]
        assert "after garbage" in result["log"]
