"""Tests for run logging functionality."""

import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from zipsa.core.executor import DockerExecutor
from zipsa.core.skill import Skill
import pytest


class TestRunLogging:
    """Test run logging functionality."""

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_run_dir_created(self, mock_popen, tmp_path):
        """Run should create timestamped directory under ~/.zipsa/<name>@<version>/runs/."""
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = ["", ""]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        with patch("pathlib.Path.home", return_value=tmp_path):
            list(executor.run(skill, "Test input", env={}))

        runs_dir = tmp_path / ".zipsa" / "test-skill@1.0.0" / "runs"
        assert runs_dir.exists()

        run_dirs = list(runs_dir.iterdir())
        assert len(run_dirs) == 1

        run_dir = run_dirs[0]
        assert run_dir.is_dir()
        assert len(run_dir.name) == 23  # YYYY-MM-DD_HHMMSS_microseconds

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_output_jsonl_saved(self, mock_popen, tmp_path):
        """Output should be saved to output.jsonl in real-time."""
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [
            '{"type":"system","subtype":"init"}\n',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Hello"}]}}\n',
            '{"type":"result","num_turns":1}\n',
            ""
        ]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        with patch("pathlib.Path.home", return_value=tmp_path):
            list(executor.run(skill, "Test input", env={}))

        runs_dir = tmp_path / ".zipsa" / "test-skill@1.0.0" / "runs"
        run_dirs = list(runs_dir.iterdir())
        run_dir = run_dirs[0]
        output_file = run_dir / "output.jsonl"

        assert output_file.exists()

        lines = output_file.read_text().strip().split('\n')
        assert len(lines) == 3
        assert '"type":"system"' in lines[0]
        assert '"type":"assistant"' in lines[1]
        assert '"type":"result"' in lines[2]

    def test_summary_filtering(self):
        """Summary should contain only important events."""
        # Create test run directory
        run_dir = Path(__file__).parent / "test_runs" / "test-summary"
        run_dir.mkdir(parents=True, exist_ok=True)

        # Create test output.jsonl
        output_file = run_dir / "output.jsonl"
        output_file.write_text(
            '{"type":"system","subtype":"init","tools":["WebFetch"]}\n'
            '{"type":"rate_limit_event","status":"ok"}\n'
            '{"type":"assistant","message":{"content":[{"type":"thinking"}]}}\n'
            '{"type":"user","message":{"content":[{"type":"tool_result"}]}}\n'
            '{"type":"result","num_turns":1,"is_error":false}\n'
        )

        # Call _save_events (renamed from _save_summary; produces events.jsonl)
        from zipsa.core.executor import DockerExecutor
        executor = DockerExecutor()
        executor._save_events(run_dir)

        # Verify events.jsonl
        events_file = run_dir / "events.jsonl"
        assert events_file.exists()

        lines = events_file.read_text().strip().split('\n')
        assert len(lines) == 4  # system init, assistant, user, result (no rate_limit)

        # Verify content
        assert '"type":"system"' in lines[0]
        assert '"type":"assistant"' in lines[1]
        assert '"type":"user"' in lines[2]
        assert '"type":"result"' in lines[3]

        # Cleanup
        shutil.rmtree(Path(__file__).parent / "test_runs")

    # test_metadata_extraction removed: _save_metadata was deleted in
    # chore: merge metadata.json into summary.json. Equivalent coverage
    # lives in test_executor.py::TestSummaryWritten and
    # test_summary.py::TestBuildSummary (the new summary.json absorbs
    # the same fields — usage, model_usage, stop_reason, etc.).

    @patch("zipsa.core.executor.subprocess.Popen")
    @patch("builtins.print")
    def test_dry_run_no_logging(self, mock_print, mock_popen):
        """Dry-run should not create run directory or logs."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        # Execute in dry-run mode
        result = executor.run(skill, "Test", env={}, dry_run=True)

        # Should return None
        assert result is None

        # Should not call Popen
        mock_popen.assert_not_called()

        # Verify no run directory created
        runs_dir = skill_dir / ".zipsa" / "runs"
        if runs_dir.exists():
            # Should be empty
            assert len(list(runs_dir.iterdir())) == 0

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_error_partial_log(self, mock_popen, tmp_path):
        """Failed execution should preserve partial logs."""
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [
            '{"type":"system","subtype":"init"}\n',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Starting"}]}}\n',
            ""
        ]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.wait.return_value = 1
        mock_process.returncode = 1
        mock_popen.return_value = mock_process

        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        try:
            with patch("pathlib.Path.home", return_value=tmp_path):
                list(executor.run(skill, "Test", env={}))
        except RuntimeError:
            pass

        runs_dir = tmp_path / ".zipsa" / "test-skill@1.0.0" / "runs"
        run_dirs = list(runs_dir.iterdir())
        run_dir = run_dirs[0]
        output_file = run_dir / "output.jsonl"

        assert output_file.exists()
        lines = output_file.read_text().strip().split('\n')
        assert len(lines) == 2

        # summary.json (the new single-source-of-truth) is written even
        # on partial-log / error paths. status will reflect the failure
        # (infra_failed when Docker exits non-zero without our SIGTERM).
        summary_file = run_dir / "summary.json"
        assert summary_file.exists()

        import json
        summary = json.loads(summary_file.read_text())
        assert summary["status"] != "ok"
        # error dict is populated for non-ok statuses
        assert summary.get("error") is not None
