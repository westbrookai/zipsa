"""Tests for CLI commands."""

from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from typer.testing import CliRunner
from zipsa.cli import app


runner = CliRunner()


class TestRunCommand:
    """Test run command."""

    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_basic(self, mock_skill_cls, mock_executor_cls):
        """Run command should execute skill."""
        # Setup mocks
        mock_skill = Mock()
        mock_skill.name = "test-skill"
        mock_skill_cls.load.return_value = mock_skill

        mock_executor = Mock()
        mock_executor.run.return_value = iter([
            {"type": "text", "content": "Hello"}
        ])
        mock_executor_cls.return_value = mock_executor

        # Execute
        result = runner.invoke(app, ["run", "test-skill", "Hello world"])

        # Verify
        assert result.exit_code == 0
        mock_skill_cls.load.assert_called_once()
        mock_executor.run.assert_called_once_with(
            mock_skill, "Hello world", env={}, dry_run=False, shell=False, mcp_debug=False, extra_docker_opts=None
        )

    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_with_runtime(self, mock_skill_cls, mock_executor_cls):
        """Run with custom runtime."""
        mock_skill = Mock()
        mock_skill_cls.load.return_value = mock_skill
        mock_executor_cls.return_value.run.return_value = iter([])

        result = runner.invoke(
            app, ["run", "test-skill", "input", "--runtime", "codex"]
        )

        assert result.exit_code == 0
        mock_executor_cls.assert_called_once_with(
            runtime="codex",
            image="ghcr.io/westbrookai/zipsa-runtime:latest",
        )

    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_with_env_vars(self, mock_skill_cls, mock_executor_cls):
        """Run with environment variables."""
        mock_skill = Mock()
        mock_skill_cls.load.return_value = mock_skill
        mock_executor = Mock()
        mock_executor.run.return_value = iter([])
        mock_executor_cls.return_value = mock_executor

        result = runner.invoke(
            app,
            [
                "run",
                "test-skill",
                "input",
                "-e",
                "KEY1=value1",
                "-e",
                "KEY2=value2",
            ],
        )

        assert result.exit_code == 0
        mock_executor.run.assert_called_once()
        call_env = mock_executor.run.call_args[1]["env"]
        assert call_env == {"KEY1": "value1", "KEY2": "value2"}

    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_dry_run(self, mock_skill_cls, mock_executor_cls):
        """Dry run should not execute."""
        mock_skill = Mock()
        mock_skill_cls.load.return_value = mock_skill
        mock_executor = Mock()
        mock_executor.run.return_value = None
        mock_executor_cls.return_value = mock_executor

        result = runner.invoke(app, ["run", "test-skill", "input", "--dry-run"])

        assert result.exit_code == 0
        mock_executor.run.assert_called_once_with(
            mock_skill, "input", env={}, dry_run=True, shell=False, mcp_debug=False, extra_docker_opts=None
        )

    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_with_mcp_debug(self, mock_skill_cls, mock_executor_cls):
        """--mcp-debug should pass mcp_debug=True to executor."""
        mock_skill = Mock()
        mock_skill.name = "test-skill"
        mock_skill_cls.load.return_value = mock_skill
        mock_executor = Mock()
        mock_executor.run.return_value = iter([])
        mock_executor_cls.return_value = mock_executor

        result = runner.invoke(app, ["run", "test-skill", "input", "--mcp-debug"])

        assert result.exit_code == 0
        mock_executor.run.assert_called_once_with(
            mock_skill, "input", env={}, dry_run=False, shell=False, mcp_debug=True, extra_docker_opts=None
        )

    @patch("zipsa.cli.Skill")
    def test_run_invalid_skill(self, mock_skill_cls):
        """Run with invalid skill should fail."""
        mock_skill_cls.load.side_effect = FileNotFoundError("Not found")

        result = runner.invoke(app, ["run", "nonexistent", "input"])

        assert result.exit_code != 0


class TestValidateCommand:
    """Test validate command."""

    @patch("zipsa.cli.Skill")
    def test_validate_valid_skill(self, mock_skill_cls):
        """Validate valid skill."""
        mock_skill = Mock()
        mock_skill.name = "test-skill"
        mock_skill.manifest.metadata.version = "1.0.0"
        mock_skill.manifest.spec.purpose = "Test purpose"
        mock_skill.manifest.spec.mcp = []
        # tools is a SkillTools object with builtin and mcp lists
        mock_tools = Mock()
        mock_tools.builtin = ["WebFetch"]
        mock_tools.mcp = []
        mock_skill.manifest.spec.tools = mock_tools
        mock_skill_cls.load.return_value = mock_skill

        result = runner.invoke(app, ["validate", "test-skill"])

        assert result.exit_code == 0
        assert "valid" in result.stdout.lower() or "✓" in result.stdout

    @patch("zipsa.cli.Skill")
    def test_validate_invalid_skill(self, mock_skill_cls):
        """Validate invalid skill."""
        from pydantic import ValidationError

        mock_skill_cls.load.side_effect = ValidationError.from_exception_data(
            "SkillManifest", [{"type": "missing", "loc": ("spec",), "msg": ""}]
        )

        result = runner.invoke(app, ["validate", "invalid-skill"])

        assert result.exit_code != 0


class TestListCommand:
    """Test list command."""

    @patch("zipsa.cli.Skill")
    @patch("zipsa.cli.Path")
    def test_list_skills(self, mock_path_cls, mock_skill_cls):
        """List skills in directory."""
        # Mock directory structure
        skill1 = Mock()
        skill1.is_dir.return_value = True
        skill1.name = "skill-1"
        skill1.__truediv__ = lambda self, x: Mock(exists=Mock(return_value=True))

        skill2 = Mock()
        skill2.is_dir.return_value = True
        skill2.name = "skill-2"
        skill2.__truediv__ = lambda self, x: Mock(exists=Mock(return_value=True))

        mock_path = Mock()
        mock_path.exists.return_value = True
        mock_path.is_dir.return_value = True
        mock_path.iterdir.return_value = [skill1, skill2]
        mock_path_cls.return_value = mock_path

        # Mock skill loading
        def load_skill(path):
            mock = Mock()
            mock.name = path.name
            mock.manifest.metadata.version = "1.0.0"
            mock.manifest.spec.purpose = "Test purpose"
            return mock

        mock_skill_cls.load.side_effect = load_skill

        result = runner.invoke(app, ["list", "."])

        assert result.exit_code == 0
        assert "skill-1" in result.stdout
        assert "skill-2" in result.stdout

    @patch("zipsa.cli.Path")
    def test_list_empty_directory(self, mock_path_cls):
        """List empty skills directory."""
        mock_path = Mock()
        mock_path.exists.return_value = True
        mock_path.is_dir.return_value = True
        mock_path.iterdir.return_value = []
        mock_path_cls.return_value = mock_path

        result = runner.invoke(app, ["list", "."])

        assert result.exit_code == 0
        assert "no skills" in result.stdout.lower() or "0" in result.stdout


class TestRunOutputMode:
    """Test --output-mode option on run command."""

    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_defaults_to_pretty_mode(self, mock_skill_cls, mock_executor_cls):
        """run without --output-mode should use pretty rendering."""
        mock_skill = Mock()
        mock_skill.name = "test-skill"
        mock_skill_cls.load.return_value = mock_skill

        events = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}}
        ]
        mock_executor = Mock()
        mock_executor.run.return_value = iter(events)
        mock_executor_cls.return_value = mock_executor

        result = runner.invoke(app, ["run", "test-skill", "hello"])

        assert result.exit_code == 0
        assert "Done." in result.output
        # pretty mode adds Answer: prefix
        assert "Answer:" in result.output

    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_answer_mode_prints_only_text(self, mock_skill_cls, mock_executor_cls):
        mock_skill = Mock()
        mock_skill.name = "test-skill"
        mock_skill_cls.load.return_value = mock_skill

        events = [
            {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "hmm"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Final answer."}]}},
        ]
        mock_executor = Mock()
        mock_executor.run.return_value = iter(events)
        mock_executor_cls.return_value = mock_executor

        result = runner.invoke(app, ["run", "test-skill", "hello", "--output-mode", "answer"])

        assert result.exit_code == 0
        assert "Final answer." in result.output
        assert "Thinking" not in result.output
        assert "Turn" not in result.output

    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_json_mode_prints_raw_json(self, mock_skill_cls, mock_executor_cls):
        import json as _json
        mock_skill = Mock()
        mock_skill.name = "test-skill"
        mock_skill_cls.load.return_value = mock_skill

        event = {"type": "result", "total_cost_usd": 0.01}
        mock_executor = Mock()
        mock_executor.run.return_value = iter([event])
        mock_executor_cls.return_value = mock_executor

        result = runner.invoke(app, ["run", "test-skill", "hello", "--output-mode", "json"])

        assert result.exit_code == 0
        assert _json.loads(result.output.strip().splitlines()[-1]) == event


class TestRuntimesCommand:
    """Test runtimes command."""

    @patch("zipsa.cli.list_runtimes")
    def test_runtimes_list(self, mock_list_runtimes):
        """List available runtimes."""
        mock_list_runtimes.return_value = ["claude", "codex", "gemini"]

        result = runner.invoke(app, ["runtimes"])

        assert result.exit_code == 0
        assert "claude" in result.stdout
        assert "codex" in result.stdout
        assert "gemini" in result.stdout
