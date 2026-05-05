"""Tests for run logging functionality."""

import shutil
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from zipsa.core.executor import DockerExecutor
from zipsa.core.skill import Skill


class TestRunLogging:
    """Test run logging functionality."""

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_run_dir_created(self, mock_popen):
        """Run should create timestamped directory in skill/.zipsa/runs/."""

        # Mock process
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = ["", ""]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        # Load skill
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        # Clean up previous runs
        runs_dir = skill_dir / ".zipsa" / "runs"
        if runs_dir.exists():
            shutil.rmtree(runs_dir)

        # Execute
        list(executor.run(skill, "Test input", env={}))

        # Verify run directory created
        runs_dir = skill_dir / ".zipsa" / "runs"
        assert runs_dir.exists()

        # Should have exactly one run directory
        run_dirs = list(runs_dir.iterdir())
        assert len(run_dirs) == 1

        # Verify it's a directory with timestamp format
        run_dir = run_dirs[0]
        assert run_dir.is_dir()
        assert len(run_dir.name) == 23  # YYYY-MM-DD_HHMMSS_microseconds

        # Cleanup
        shutil.rmtree(skill_dir / ".zipsa" / "runs")

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_output_jsonl_saved(self, mock_popen):
        """Output should be saved to output.jsonl in real-time."""
        # Mock process with JSON output
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

        # Execute
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        list(executor.run(skill, "Test input", env={}))

        # Verify output.jsonl exists and contains events
        runs_dir = skill_dir / ".zipsa" / "runs"
        run_dirs = list(runs_dir.iterdir())
        run_dir = run_dirs[0]
        output_file = run_dir / "output.jsonl"

        assert output_file.exists()

        # Read and verify content
        lines = output_file.read_text().strip().split('\n')
        assert len(lines) == 3
        assert '"type":"system"' in lines[0]
        assert '"type":"assistant"' in lines[1]
        assert '"type":"result"' in lines[2]

        # Cleanup
        shutil.rmtree(skill_dir / ".zipsa" / "runs")

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

        # Call _save_summary
        from zipsa.core.executor import DockerExecutor
        executor = DockerExecutor()
        executor._save_summary(run_dir)

        # Verify summary.jsonl
        summary_file = run_dir / "summary.jsonl"
        assert summary_file.exists()

        lines = summary_file.read_text().strip().split('\n')
        assert len(lines) == 4  # system init, assistant, user, result (no rate_limit)

        # Verify content
        assert '"type":"system"' in lines[0]
        assert '"type":"assistant"' in lines[1]
        assert '"type":"user"' in lines[2]
        assert '"type":"result"' in lines[3]

        # Cleanup
        shutil.rmtree(Path(__file__).parent / "test_runs")

    def test_metadata_extraction(self):
        """Metadata should be extracted from result event."""
        # Create test run directory
        run_dir = Path(__file__).parent / "test_runs" / "test-metadata"
        run_dir.mkdir(parents=True, exist_ok=True)

        # Create test output.jsonl with result event
        output_file = run_dir / "output.jsonl"
        output_file.write_text(
            '{"type":"system","subtype":"init"}\n'
            '{"type":"result","duration_ms":15562,"duration_api_ms":14586,'
            '"num_turns":3,"total_cost_usd":0.099,"is_error":false,'
            '"stop_reason":"end_turn","terminal_reason":"completed",'
            '"usage":{"input_tokens":7,"output_tokens":302},'
            '"modelUsage":{"claude-sonnet-4-6":{"costUSD":0.08}}}\n'
        )

        # Create mock skill
        from zipsa.core.skill import Skill
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        # Call _save_metadata
        from zipsa.core.executor import DockerExecutor
        executor = DockerExecutor()
        executor._save_metadata(run_dir, skill)

        # Verify metadata.json
        metadata_file = run_dir / "metadata.json"
        assert metadata_file.exists()

        import json
        metadata = json.loads(metadata_file.read_text())

        assert metadata["skill_name"] == "test-skill"
        assert metadata["num_turns"] == 3
        assert metadata["total_cost_usd"] == 0.099
        assert metadata["is_error"] is False
        assert metadata["duration_ms"] == 15562

        # Cleanup
        shutil.rmtree(Path(__file__).parent / "test_runs")

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
    def test_error_partial_log(self, mock_popen):
        """Failed execution should preserve partial logs."""
        # Mock process that outputs then fails
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [
            '{"type":"system","subtype":"init"}\n',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Starting"}]}}\n',
            ""  # End of output
        ]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.wait.return_value = 1  # Non-zero exit
        mock_process.returncode = 1
        mock_popen.return_value = mock_process

        # Execute (should raise)
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        try:
            list(executor.run(skill, "Test", env={}))
        except RuntimeError:
            pass  # Expected

        # Verify partial log saved
        runs_dir = skill_dir / ".zipsa" / "runs"
        run_dirs = list(runs_dir.iterdir())
        run_dir = run_dirs[0]
        output_file = run_dir / "output.jsonl"

        assert output_file.exists()
        lines = output_file.read_text().strip().split('\n')
        assert len(lines) == 2  # Both events saved before failure

        # Verify metadata marks error
        metadata_file = run_dir / "metadata.json"
        assert metadata_file.exists()

        import json
        metadata = json.loads(metadata_file.read_text())
        assert metadata["is_error"] is True
        assert "No result event" in metadata["error"]

        # Cleanup
        shutil.rmtree(skill_dir / ".zipsa" / "runs")
