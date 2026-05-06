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

    def test_write_env_file_creates_file(self):
        """_write_env_file should create .env in skill's .zipsa dir."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)
        env = {"FOO": "bar", "TOKEN": "secret"}

        env_file = executor._write_env_file(skill, env)

        assert env_file == skill.skill_dir / ".zipsa" / ".env"
        assert env_file.exists()
        content = env_file.read_text()
        assert "FOO=bar\n" in content
        assert "TOKEN=secret\n" in content

    def test_write_env_file_empty_env(self):
        """_write_env_file should create empty .env file when no env vars."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        env_file = executor._write_env_file(skill, {})

        assert env_file.exists()
        assert env_file.read_text() == ""

    def test_build_docker_command_uses_env_file(self):
        """Docker command should use --env-file instead of -e flags."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json()
        env = {"CLAUDE_CODE_OAUTH_TOKEN": "test-token"}

        cmd = executor._build_docker_command(
            skill=skill,
            user_input="Test input",
            claude_json_path=claude_json_path,
            env=env,
        )

        # Should use --env-file, not -e
        assert "--env-file" in cmd
        assert "-e" not in cmd

        # env file path should be in the command
        env_file = skill.skill_dir / ".zipsa" / ".env"
        assert str(env_file) in cmd

        # env file should contain the token
        assert "CLAUDE_CODE_OAUTH_TOKEN=test-token\n" in env_file.read_text()

        # Other docker basics
        assert cmd[0] == "docker"
        assert "--rm" in cmd
        assert "--name" in cmd
        assert "/home/agent/.claude.json" in " ".join(cmd)
        assert "ghcr.io/westbrookai/zipsa-runtime:latest" in cmd
        assert "claude" in cmd

    def test_build_docker_command_with_mcp_mounts(self):
        """Docker command should include MCP stdio mounts."""
        executor = DockerExecutor()
        manifest_path = Path(__file__).parent / "fixtures/manifests/with-mcp.yaml"
        skill = Skill.load(manifest_path)

        claude_json_path = skill.build_claude_json()

        cmd = executor._build_docker_command(
            skill=skill,
            user_input="Test",
            claude_json_path=claude_json_path,
            env={},
        )

        # Should have MCP mount (from with-mcp.yaml: ~/Documents -> /mnt/docs:ro)
        cmd_str = " ".join(cmd)
        assert "/mnt/docs:ro" in cmd_str

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_run_creates_claude_config(self, mock_popen):
        """Run should create .claude.json in skill's .zipsa directory."""
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = ["output line\n", ""]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        list(executor.run(skill, "Test input", env={}))

        zipsa_dir = skill.skill_dir / ".zipsa"
        assert zipsa_dir.exists()
        assert zipsa_dir.is_dir()
        assert (zipsa_dir / ".claude.json").exists()

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_run_persists_claude_config(self, mock_popen):
        """Run should keep .claude.json after execution (not clean it up)."""
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = ["output\n", ""]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        list(executor.run(skill, "Test", env={}))

        claude_json = skill.skill_dir / ".zipsa" / ".claude.json"
        assert claude_json.exists()

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_run_cleans_up_env_file(self, mock_popen):
        """Run should delete .env file after execution."""
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = ["output\n", ""]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.wait.return_value = 0
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        list(executor.run(skill, "Test", env={"SECRET": "value"}))

        env_file = skill.skill_dir / ".zipsa" / ".env"
        assert not env_file.exists()

    @patch("zipsa.core.executor.subprocess.Popen")
    @patch("builtins.print")
    def test_dry_run_mode(self, mock_print, mock_popen):
        """Dry run should not execute Docker."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        result = executor.run(skill, "Test", env={}, dry_run=True)

        assert result is None
        mock_popen.assert_not_called()
        assert mock_print.called

    @patch("zipsa.core.executor.subprocess.Popen")
    @patch("builtins.print")
    def test_dry_run_cleans_up_env_file(self, mock_print, mock_popen):
        """Dry run should also clean up .env file."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        executor.run(skill, "Test", env={"SECRET": "value"}, dry_run=True)

        env_file = skill.skill_dir / ".zipsa" / ".env"
        assert not env_file.exists()


class TestRuntimeConfig:
    """Test runtime config file handling."""

    def test_auto_inject_env_from_config(self, tmp_path):
        """Should auto-inject env vars specified in runtime config."""
        config_path = tmp_path / "runtime-config.yaml"
        config_path.write_text("""
runtimes:
  claude:
    auto_inject_env:
      - CLAUDE_CODE_OAUTH_TOKEN
""")

        executor = DockerExecutor(runtime_config_path=config_path)
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json()

        with patch.dict("os.environ", {"CLAUDE_CODE_OAUTH_TOKEN": "test-token"}):
            executor._build_docker_command(
                skill=skill,
                user_input="Test",
                claude_json_path=claude_json_path,
                env={},
            )

        env_file = skill.skill_dir / ".zipsa" / ".env"
        assert "CLAUDE_CODE_OAUTH_TOKEN=test-token\n" in env_file.read_text()

    def test_no_auto_inject_without_config(self):
        """Should not auto-inject if runtime config doesn't exist."""
        executor = DockerExecutor(runtime_config_path=Path("/nonexistent/config.yaml"))
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json()

        with patch.dict("os.environ", {"CLAUDE_CODE_OAUTH_TOKEN": "test-token"}):
            executor._build_docker_command(
                skill=skill,
                user_input="Test",
                claude_json_path=claude_json_path,
                env={},
            )

        env_file = skill.skill_dir / ".zipsa" / ".env"
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env_file.read_text()

    def test_no_auto_inject_if_runtime_not_in_config(self, tmp_path):
        """Should not auto-inject if runtime not specified in config."""
        config_path = tmp_path / "runtime-config.yaml"
        config_path.write_text("""
runtimes:
  codex:
    auto_inject_env:
      - CODEX_API_KEY
""")

        executor = DockerExecutor(runtime="claude", runtime_config_path=config_path)
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json()

        with patch.dict("os.environ", {"CLAUDE_CODE_OAUTH_TOKEN": "test-token"}):
            executor._build_docker_command(
                skill=skill,
                user_input="Test",
                claude_json_path=claude_json_path,
                env={},
            )

        env_file = skill.skill_dir / ".zipsa" / ".env"
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env_file.read_text()

    def test_user_env_overrides_config(self, tmp_path):
        """User-provided env should override runtime config."""
        config_path = tmp_path / "runtime-config.yaml"
        config_path.write_text("""
runtimes:
  claude:
    auto_inject_env:
      - CLAUDE_CODE_OAUTH_TOKEN
""")

        executor = DockerExecutor(runtime_config_path=config_path)
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json()

        with patch.dict("os.environ", {"CLAUDE_CODE_OAUTH_TOKEN": "host-token"}):
            executor._build_docker_command(
                skill=skill,
                user_input="Test",
                claude_json_path=claude_json_path,
                env={"CLAUDE_CODE_OAUTH_TOKEN": "user-token"},
            )

        env_file = skill.skill_dir / ".zipsa" / ".env"
        content = env_file.read_text()
        assert "CLAUDE_CODE_OAUTH_TOKEN=user-token\n" in content
        assert "host-token" not in content

    @patch("builtins.print")
    def test_warning_if_env_not_in_host(self, mock_print, tmp_path):
        """Should warn if auto_inject env var not set in host."""
        config_path = tmp_path / "runtime-config.yaml"
        config_path.write_text("""
runtimes:
  claude:
    auto_inject_env:
      - CLAUDE_CODE_OAUTH_TOKEN
""")

        executor = DockerExecutor(runtime_config_path=config_path)
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json()

        with patch.dict("os.environ", {}, clear=True):
            executor._build_docker_command(
                skill=skill,
                user_input="Test",
                claude_json_path=claude_json_path,
                env={},
            )

        env_file = skill.skill_dir / ".zipsa" / ".env"
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env_file.read_text()
        assert any("CLAUDE_CODE_OAUTH_TOKEN" in str(call) for call in mock_print.call_args_list)
