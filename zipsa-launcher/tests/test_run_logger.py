"""Tests for run logging functionality."""

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
        import shutil
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
        import shutil
        shutil.rmtree(skill_dir / ".zipsa" / "runs")
