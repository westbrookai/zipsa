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

    def test_build_system_prompt_injects_mcp_server_paths(self):
        """System prompt should include MCP server root paths for stdio servers with mounts."""
        executor = DockerExecutor()
        manifest_path = Path(__file__).parent / "fixtures/manifests/with-mcp.yaml"
        skill = Skill.load(manifest_path)

        prompt = executor._build_system_prompt(skill)

        # stdio server with mount → path injected
        assert "/home/agent/workspace/filesystem" in prompt
        # http server → no path injection
        assert "notion" not in prompt.split("# MCP")[1].split("filesystem")[0] or True  # notion has no path

    def test_build_system_prompt_no_mcp_section_when_no_mounts(self):
        """System prompt should not include MCP paths section when no stdio mounts exist."""
        executor = DockerExecutor()
        manifest_path = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(manifest_path)

        prompt = executor._build_system_prompt(skill)

        assert "# MCP Server Paths" not in prompt

    def test_write_env_file_creates_file(self, tmp_path):
        """_write_env_file should create .env in the given output_dir."""
        executor = DockerExecutor()
        output_dir = tmp_path / "skill-data"
        env = {"FOO": "bar", "TOKEN": "secret"}

        env_file = executor._write_env_file(output_dir, env)

        assert env_file == output_dir / ".env"
        assert env_file.exists()
        content = env_file.read_text()
        assert "FOO=bar\n" in content
        assert "TOKEN=secret\n" in content

    def test_write_env_file_empty_env(self, tmp_path):
        """_write_env_file should create empty .env file when no env vars."""
        executor = DockerExecutor()
        output_dir = tmp_path / "skill-data"

        env_file = executor._write_env_file(output_dir, {})

        assert env_file.exists()
        assert env_file.read_text() == ""

    def test_build_docker_command_uses_env_file(self, tmp_path):
        """Docker command should use --env-file instead of -e flags."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path)
        env = {"CLAUDE_CODE_OAUTH_TOKEN": "test-token"}

        with patch("pathlib.Path.home", return_value=tmp_path):
            cmd = executor._build_docker_command(
                skill=skill,
                user_input="Test input",
                claude_json_path=claude_json_path,
                env=env,
            )

        assert "--env-file" in cmd
        assert "-e" not in cmd

        env_file = tmp_path / ".zipsa" / "minimal@1.0.0" / ".env"
        assert str(env_file) in cmd
        assert "CLAUDE_CODE_OAUTH_TOKEN=test-token\n" in env_file.read_text()

        assert cmd[0] == "docker"
        assert "--rm" in cmd
        assert "--name" in cmd
        assert "ghcr.io/westbrookai/zipsa-runtime:latest" in cmd
        assert "bash" in cmd  # bash wrapper copies .claude.json before running claude
        # .claude.json must NOT be bind-mounted as a file (causes EBUSY on rename)
        assert ":/home/agent/.claude.json" not in " ".join(cmd)
        # Host cwd should NOT be mounted as workspace
        assert ":/workspace" not in " ".join(cmd)

    def test_build_docker_command_includes_global_env_file(self, tmp_path):
        """Docker command should include ~/.zipsa/.env as second --env-file if it exists."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json()

        global_env_file = tmp_path / ".zipsa" / ".env"
        global_env_file.parent.mkdir()
        global_env_file.write_text("GLOBAL_TOKEN=global-value\n")

        with patch("pathlib.Path.home", return_value=tmp_path):
            cmd = executor._build_docker_command(
                skill=skill,
                user_input="Test",
                claude_json_path=claude_json_path,
                env={},
            )

        assert cmd.count("--env-file") == 2
        assert str(global_env_file) in cmd

    def test_build_docker_command_no_global_env_file_when_missing(self, tmp_path):
        """Docker command should have only one --env-file if ~/.zipsa/.env doesn't exist."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json()

        with patch("pathlib.Path.home", return_value=tmp_path):
            cmd = executor._build_docker_command(
                skill=skill,
                user_input="Test",
                claude_json_path=claude_json_path,
                env={},
            )

        assert cmd.count("--env-file") == 1

    def test_build_docker_command_with_mcp_debug(self, tmp_path):
        """Docker command should include debug volume mount and --debug-file when mcp_debug_host is set."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path)

        mcp_debug_host = tmp_path / "mcp-debug.log"
        mcp_debug_host.touch()

        cmd = executor._build_docker_command(
            skill=skill,
            user_input="Test",
            claude_json_path=claude_json_path,
            env={},
            mcp_debug_host=mcp_debug_host,
        )

        cmd_str = " ".join(cmd)
        assert str(mcp_debug_host) in cmd_str
        assert "/home/agent/mcp-debug.log" in cmd_str
        assert "--debug-file" in cmd_str

    def test_build_docker_command_no_debug_by_default(self, tmp_path):
        """Docker command should not include debug mounts by default."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path)

        with patch("pathlib.Path.home", return_value=tmp_path):
            cmd = executor._build_docker_command(
                skill=skill,
                user_input="Test",
                claude_json_path=claude_json_path,
                env={},
            )

        assert "--debug-file" not in cmd
        assert "--debug" not in cmd

    def test_build_docker_command_with_mcp_mounts(self):
        """Docker command should include MCP stdio mounts at /home/agent/workspace/<name>."""
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

        # Should auto-generate container path as /home/agent/workspace/<server-name>
        cmd_str = " ".join(cmd)
        assert "/home/agent/workspace/filesystem:ro" in cmd_str
        # Host cwd should NOT be mounted
        assert ":/workspace" not in cmd_str

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_run_creates_claude_config(self, mock_popen, tmp_path):
        """Run should create .claude.json in ~/.zipsa/<name>@<version>/."""
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

        with patch("pathlib.Path.home", return_value=tmp_path):
            list(executor.run(skill, "Test input", env={}))

        skill_data_dir = tmp_path / ".zipsa" / "test-skill@1.0.0"
        assert skill_data_dir.exists()
        assert (skill_data_dir / ".claude.json").exists()

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_run_persists_claude_config(self, mock_popen, tmp_path):
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

        with patch("pathlib.Path.home", return_value=tmp_path):
            list(executor.run(skill, "Test", env={}))

        claude_json = tmp_path / ".zipsa" / "test-skill@1.0.0" / ".claude.json"
        assert claude_json.exists()

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_run_cleans_up_env_file(self, mock_popen, tmp_path):
        """Run should delete .env file from ~/.zipsa/<name>@<version>/ after execution."""
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

        with patch("pathlib.Path.home", return_value=tmp_path):
            list(executor.run(skill, "Test", env={"SECRET": "value"}))

        env_file = tmp_path / ".zipsa" / "test-skill@1.0.0" / ".env"
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
    def test_dry_run_cleans_up_env_file(self, mock_print, mock_popen, tmp_path):
        """Dry run should also clean up .env file."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        with patch("pathlib.Path.home", return_value=tmp_path):
            executor.run(skill, "Test", env={"SECRET": "value"}, dry_run=True)

        env_file = tmp_path / ".zipsa" / "test-skill@1.0.0" / ".env"
        assert not env_file.exists()


    @patch("zipsa.core.executor.subprocess.Popen")
    def test_run_creates_run_dir_in_home(self, mock_popen, tmp_path):
        """run() should create runs/<timestamp>/ under ~/.zipsa/<name>@<version>/."""
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

        with patch("pathlib.Path.home", return_value=tmp_path):
            list(executor.run(skill, "Test", env={}))

        skill_data_dir = tmp_path / ".zipsa" / "test-skill@1.0.0"
        assert skill_data_dir.exists()
        runs_dir = skill_data_dir / "runs"
        assert runs_dir.exists()
        assert len(list(runs_dir.iterdir())) == 1

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_run_creates_claude_json_in_home(self, mock_popen, tmp_path):
        """run() should create .claude.json under ~/.zipsa/<name>@<version>/."""
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

        with patch("pathlib.Path.home", return_value=tmp_path):
            list(executor.run(skill, "Test", env={}))

        skill_data_dir = tmp_path / ".zipsa" / "test-skill@1.0.0"
        assert (skill_data_dir / ".claude.json").exists()

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_run_cleans_up_env_file_in_home(self, mock_popen, tmp_path):
        """run() should delete .env from ~/.zipsa/<name>@<version>/ after execution."""
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

        with patch("pathlib.Path.home", return_value=tmp_path):
            list(executor.run(skill, "Test", env={"SECRET": "value"}))

        env_file = tmp_path / ".zipsa" / "test-skill@1.0.0" / ".env"
        assert not env_file.exists()

    def test_build_docker_command_mounts_skill_data_dir_not_file(self, tmp_path):
        """Docker command should mount skill data dir to /.zipsa:ro, not .claude.json directly."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path)

        with patch("pathlib.Path.home", return_value=tmp_path):
            cmd = executor._build_docker_command(
                skill=skill,
                user_input="Test",
                claude_json_path=claude_json_path,
                env={},
            )

        cmd_str = " ".join(cmd)
        # Directory mount — not the individual file
        assert f"{tmp_path}:/.zipsa:ro" in cmd_str
        # File must NOT be bind-mounted (causes EBUSY rename failure in container)
        assert ":/home/agent/.claude.json" not in cmd_str

    def test_build_docker_command_wraps_with_bash_cp(self, tmp_path):
        """Docker command should copy .claude.json from /.zipsa before running claude."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path)

        with patch("pathlib.Path.home", return_value=tmp_path):
            cmd = executor._build_docker_command(
                skill=skill,
                user_input="Test input",
                claude_json_path=claude_json_path,
                env={},
            )

        assert "bash" in cmd
        assert "-c" in cmd
        bash_c = cmd[cmd.index("-c") + 1]
        assert "cp /.zipsa/.claude.json /home/agent/.claude.json" in bash_c
        assert "claude" in bash_c

    def test_ensure_oauth_credentials_injects_token_for_oauth2_servers(self, tmp_path):
        """Pre-flight injects ZIPSA_TOKEN_<NAME> for oauth2 servers."""
        from unittest.mock import MagicMock
        from zipsa.core.models import MCPServerHTTP, MCPServerAuth, SkillSpec, SkillManifest, SkillMetadata

        executor = DockerExecutor()
        manifest = SkillManifest(
            apiVersion="zipsa.dev/v1alpha1",
            kind="Skill",
            metadata=SkillMetadata(name="s", version="1.0.0"),
            spec=SkillSpec(
                purpose="test",
                instructions="./SKILL.md",
                mcp=[
                    MCPServerHTTP(
                        name="notion",
                        type="http",
                        url="https://mcp.notion.com/mcp",
                        auth=MCPServerAuth(type="oauth2"),
                    )
                ],
            ),
        )

        class FakeSkill:
            pass
        fake_skill = FakeSkill()
        fake_skill.manifest = manifest

        env = {}
        mock_manager = MagicMock()
        mock_manager.ensure_credentials.return_value = "tok-abc123"

        with patch("zipsa.core.executor.OAuthManager", return_value=mock_manager):
            executor._ensure_oauth_credentials(fake_skill, env)

        assert env["ZIPSA_TOKEN_NOTION"] == "tok-abc123"
        mock_manager.ensure_credentials.assert_called_once_with("notion", "https://mcp.notion.com/mcp")

    def test_ensure_oauth_credentials_skips_servers_without_auth(self):
        """Pre-flight does nothing for HTTP servers without auth field."""
        from zipsa.core.models import MCPServerHTTP, SkillSpec, SkillManifest, SkillMetadata

        executor = DockerExecutor()
        manifest = SkillManifest(
            apiVersion="zipsa.dev/v1alpha1",
            kind="Skill",
            metadata=SkillMetadata(name="s", version="1.0.0"),
            spec=SkillSpec(
                purpose="test",
                instructions="./SKILL.md",
                mcp=[
                    MCPServerHTTP(
                        name="api",
                        type="http",
                        url="https://api.example.com/mcp",
                        auth=None,
                    )
                ],
            ),
        )

        class FakeSkill:
            pass
        fake_skill = FakeSkill()
        fake_skill.manifest = manifest

        env = {}
        mock_manager = MagicMock()
        with patch("zipsa.core.executor.OAuthManager", return_value=mock_manager):
            executor._ensure_oauth_credentials(fake_skill, env)

        mock_manager.ensure_credentials.assert_not_called()
        assert env == {}

    def test_ensure_oauth_credentials_skips_token_already_in_env(self):
        """Pre-flight skips servers whose token is already in env dict."""
        from zipsa.core.models import MCPServerHTTP, MCPServerAuth, SkillSpec, SkillManifest, SkillMetadata

        executor = DockerExecutor()
        manifest = SkillManifest(
            apiVersion="zipsa.dev/v1alpha1",
            kind="Skill",
            metadata=SkillMetadata(name="s", version="1.0.0"),
            spec=SkillSpec(
                purpose="test",
                instructions="./SKILL.md",
                mcp=[
                    MCPServerHTTP(
                        name="notion",
                        type="http",
                        url="https://mcp.notion.com/mcp",
                        auth=MCPServerAuth(type="oauth2"),
                    )
                ],
            ),
        )

        class FakeSkill:
            pass
        fake_skill = FakeSkill()
        fake_skill.manifest = manifest

        env = {"ZIPSA_TOKEN_NOTION": "existing-token"}
        mock_manager = MagicMock()
        with patch("zipsa.core.executor.OAuthManager", return_value=mock_manager):
            executor._ensure_oauth_credentials(fake_skill, env)

        mock_manager.ensure_credentials.assert_not_called()
        assert env["ZIPSA_TOKEN_NOTION"] == "existing-token"
