"""Tests for skill execution limits."""

import subprocess
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from zipsa.core.executor import DockerExecutor
from zipsa.core.skill import Skill
import pytest


class TestLimits:
    """Test execution limits enforcement."""

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_timeout_limit_enforced(self, mock_popen):
        """Execution should timeout if timeout_seconds is exceeded."""
        # Mock a slow process that times out
        mock_stdout = MagicMock()
        mock_stdout.readline.return_value = ""  # Empty stream

        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.wait.side_effect = subprocess.TimeoutExpired(cmd="docker", timeout=1)
        mock_process.returncode = 1
        mock_process.terminate = Mock()
        mock_popen.return_value = mock_process

        # Load skill with timeout limit
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        # Override limits for test
        from zipsa.core.models import SkillLimits
        skill.manifest.spec.limits = SkillLimits(timeout_seconds=1)

        # Execute - should raise RuntimeError due to timeout
        with pytest.raises(RuntimeError, match="timed out"):
            list(executor.run(skill, "Test", env={}))

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_max_turns_enforced(self, mock_popen):
        """Execution should stop if max_turns is exceeded."""
        # Mock process with many turns
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [
            '{"type":"system","subtype":"init"}\n',
            '{"type":"assistant","message":{"content":[{"type":"thinking","thinking":"Turn 1"}]}}\n',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Response 1"}]}}\n',
            '{"type":"assistant","message":{"content":[{"type":"thinking","thinking":"Turn 2"}]}}\n',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Response 2"}]}}\n',
            '{"type":"assistant","message":{"content":[{"type":"thinking","thinking":"Turn 3 - should be stopped"}]}}\n',
            ""
        ]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_process.terminate = Mock()
        mock_popen.return_value = mock_process

        # Load skill with max_turns limit
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        # Override limits for test
        from zipsa.core.models import SkillLimits
        skill.manifest.spec.limits = SkillLimits(max_turns=2)

        # Execute - should stop after 2 turns
        with pytest.raises(RuntimeError, match="Exceeded max_turns"):
            list(executor.run(skill, "Test", env={}))

        # Verify process was terminated
        mock_process.terminate.assert_called_once()

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_max_cost_warning(self, mock_popen, capfd):
        """Should warn if max_cost_usd is exceeded."""
        # Mock process with high cost result
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [
            '{"type":"system","subtype":"init"}\n',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Answer"}]}}\n',
            '{"type":"result","total_cost_usd":0.50,"is_error":false}\n',
            ""
        ]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        # Load skill with max_cost limit
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        # Override limits for test
        from zipsa.core.models import SkillLimits
        skill.manifest.spec.limits = SkillLimits(max_cost_usd=0.10)

        # Execute - should complete but warn
        list(executor.run(skill, "Test", env={}))

        # Check metadata for cost warning
        runs_dir = skill_dir / ".zipsa" / "runs"
        run_dirs = sorted(runs_dir.iterdir(), reverse=True)  # Most recent first
        metadata_file = run_dirs[0] / "metadata.json"

        import json
        metadata = json.loads(metadata_file.read_text())
        assert metadata["cost_exceeded"] is True
        assert metadata["cost_limit_usd"] == 0.10

        # Cleanup
        import shutil
        shutil.rmtree(skill_dir / ".zipsa" / "runs")

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_no_limits_set(self, mock_popen):
        """Should run normally when no limits are set."""
        # Mock normal process
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [
            '{"type":"system","subtype":"init"}\n',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Answer"}]}}\n',
            '{"type":"result","total_cost_usd":0.50,"is_error":false}\n',
            ""
        ]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        # Load skill without limits
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        # Ensure no limits
        skill.manifest.spec.limits = None

        # Execute - should complete normally
        events = list(executor.run(skill, "Test", env={}))

        # Should get all events
        assert len(events) > 0

        # Cleanup
        import shutil
        shutil.rmtree(skill_dir / ".zipsa" / "runs")

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_keyboard_interrupt_terminates_process(self, mock_popen):
        """Should terminate Docker process on KeyboardInterrupt (Ctrl+C)."""
        # Mock process that simulates user interruption
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [
            '{"type":"system","subtype":"init"}\n',
            KeyboardInterrupt("User pressed Ctrl+C"),
        ]

        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None  # Process still running
        mock_process.terminate = Mock()
        mock_process.wait = Mock()
        mock_process.kill = Mock()
        mock_popen.return_value = mock_process

        # Load skill
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        # Execute - should catch KeyboardInterrupt and terminate process
        with pytest.raises(KeyboardInterrupt):
            list(executor.run(skill, "Test", env={}))

        # Verify process was terminated
        mock_process.terminate.assert_called()
        mock_process.wait.assert_called()
