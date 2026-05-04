"""Tests for Docker executor."""

import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from zipsa.core.executor import DockerExecutor
from zipsa.core.skill import Skill


class TestDockerExecutor:
    """Test DockerExecutor."""

    def test_executor_initialization(self):
        """Executor should initialize with defaults."""
        executor = DockerExecutor()

        assert executor.image == "ghcr.io/westbrookai/zipsa-runtime:latest"
        assert executor.workspace == Path.cwd()
        assert executor.runtime.name == "claude"

    def test_executor_custom_runtime(self):
        """Executor should accept custom runtime."""
        executor = DockerExecutor(runtime="claude")

        assert executor.runtime.name == "claude"

    def test_executor_custom_image(self):
        """Executor should accept custom image."""
        executor = DockerExecutor(image="custom:latest")

        assert executor.image == "custom:latest"

    def test_build_system_prompt(self):
        """System prompt should include purpose, instructions, and rules."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        prompt = executor._build_system_prompt(skill)

        assert "test-skill agent" in prompt
        assert "v1.0.0" in prompt
        assert skill.manifest.spec.purpose in prompt
        assert "Test Skill" in prompt  # From SKILL.md
        assert "Read,Write" in prompt  # Allowed tools
        assert "Single-task focused" in prompt  # Behavior rules

    def test_build_docker_command_basic(self):
        """Build basic docker command without MCP mounts."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)

        mcp_config_path = Path("/tmp/test-mcp.json")
        env = {"CLAUDE_CODE_OAUTH_TOKEN": "test-token"}

        cmd = executor._build_docker_command(
            skill=skill,
            user_input="Test input",
            mcp_config_path=mcp_config_path,
            env=env,
        )

        # Check docker run basics
        assert cmd[0] == "docker"
        assert cmd[1] == "run"
        assert "--rm" in cmd
        assert "--name" in cmd

        # Check environment
        assert "-e" in cmd
        env_idx = cmd.index("-e")
        assert cmd[env_idx + 1] == "CLAUDE_CODE_OAUTH_TOKEN=test-token"

        # Check volume mounts
        assert "-v" in cmd
        # Should have workspace and mcp config mounts

        # Check image
        assert "ghcr.io/westbrookai/zipsa-runtime:latest" in cmd

        # Check Claude command is appended
        assert "claude" in cmd

    def test_build_docker_command_with_mcp_mounts(self):
        """Docker command should include MCP stdio mounts."""
        executor = DockerExecutor()
        manifest_path = Path(__file__).parent / "fixtures/manifests/with-mcp.yaml"
        skill = Skill.load(manifest_path)

        mcp_config_path = Path("/tmp/test-mcp.json")
        env = {}

        cmd = executor._build_docker_command(
            skill=skill,
            user_input="Test",
            mcp_config_path=mcp_config_path,
            env=env,
        )

        # Should have MCP mount (from with-mcp.yaml: ~/Documents -> /mnt/docs:ro)
        cmd_str = " ".join(cmd)
        assert "/mnt/docs:ro" in cmd_str

    @patch("zipsa.core.executor.subprocess.Popen")
    @patch("zipsa.core.executor.tempfile.NamedTemporaryFile")
    def test_run_creates_temp_mcp_config(self, mock_tempfile, mock_popen):
        """Run should create temporary MCP config file."""
        # Setup mocks
        mock_file = MagicMock()
        mock_file.name = "/tmp/test-mcp-123.json"
        mock_tempfile.return_value.__enter__.return_value = mock_file

        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = ["output line\n", ""]
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

        # Verify temp file was created
        mock_tempfile.assert_called_once()
        assert mock_tempfile.call_args[1]["mode"] == "w"
        assert mock_tempfile.call_args[1]["suffix"] == ".json"
        assert mock_tempfile.call_args[1]["delete"] is False

        # Verify MCP config was written (json.dump calls write multiple times)
        mock_file.write.assert_called()
        assert mock_file.write.call_count > 0

    @patch("zipsa.core.executor.subprocess.Popen")
    @patch("zipsa.core.executor.tempfile.NamedTemporaryFile")
    @patch("zipsa.core.executor.Path.unlink")
    def test_run_cleans_up_temp_file(self, mock_unlink, mock_tempfile, mock_popen):
        """Run should cleanup temp file after execution."""
        # Setup mocks
        mock_file = MagicMock()
        mock_file.name = "/tmp/test-mcp-123.json"
        mock_tempfile.return_value.__enter__.return_value = mock_file

        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = ["output\n", ""]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        # Execute
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        list(executor.run(skill, "Test", env={}))

        # Verify cleanup (unlink called)
        assert mock_unlink.call_count > 0

    @patch("zipsa.core.executor.subprocess.Popen")
    @patch("zipsa.core.executor.tempfile.NamedTemporaryFile")
    @patch("builtins.print")
    def test_dry_run_mode(self, mock_print, mock_tempfile, mock_popen):
        """Dry run should not execute Docker."""
        mock_file = MagicMock()
        mock_file.name = "/tmp/test-mcp.json"
        mock_tempfile.return_value.__enter__.return_value = mock_file

        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        # Dry run should return None
        result = executor.run(skill, "Test", env={}, dry_run=True)

        # Should not call Popen in dry run
        assert result is None
        mock_popen.assert_not_called()

        # Should print dry run info
        assert mock_print.called
