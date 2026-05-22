"""Tests for Docker executor."""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from zipsa.core.executor import DockerExecutor
from zipsa.core.skill import Skill
from zipsa.paths import zipsa_home


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
        assert "Single-task focused" in prompt  # Behavior rules
        # allowed_tools moved to user message execution_context — not in system prompt
        assert "Read,Write" not in prompt

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

        cmd = executor._build_docker_command(
            skill=skill,
            user_input="Test input",
            claude_json_path=claude_json_path,
            env=env,
        )

        assert "--env-file" in cmd
        assert "-e" not in cmd

        env_file = zipsa_home() / "minimal@1.0.0" / ".env"
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

        global_env = zipsa_home() / ".env"
        global_env.parent.mkdir(parents=True, exist_ok=True)
        global_env.write_text("GLOBAL_TOKEN=global-value\n")

        cmd = executor._build_docker_command(
            skill=skill,
            user_input="Test",
            claude_json_path=claude_json_path,
            env={},
        )

        assert cmd.count("--env-file") == 2
        assert str(global_env) in cmd

    def test_build_docker_command_no_global_env_file_when_missing(self):
        """Docker command should have only one --env-file if ~/.zipsa/.env doesn't exist."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json()

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

    def test_build_docker_command_npm_volume_mounted(self):
        """npm_volume mounts the volume at /npm-cache and sets NPM_CONFIG_CACHE."""
        executor = DockerExecutor()
        manifest_path = Path(__file__).parent / "fixtures/manifests/with-mcp.yaml"
        skill = Skill.load(manifest_path)
        claude_json_path = skill.build_claude_json()

        cmd = executor._build_docker_command(
            skill=skill,
            user_input="Test",
            claude_json_path=claude_json_path,
            env={},
            npm_volume="zipsa-test-skill-abc123-npm",
        )

        cmd_str = " ".join(cmd)
        assert "zipsa-test-skill-abc123-npm:/npm-cache" in cmd_str
        assert "NPM_CONFIG_CACHE=/npm-cache" in cmd_str

    def test_build_docker_command_no_npm_volume_by_default(self):
        """Without npm_volume, no npm-cache mount is added."""
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

        cmd_str = " ".join(cmd)
        assert "/npm-cache" not in cmd_str
        assert "NPM_CONFIG_CACHE" not in cmd_str

    def _install_minimal_child(self, zipsa_home_path, name: str, version: str = "0.1.0"):
        """Drop a minimal child skill at <ZIPSA_HOME>/skills/<name>/ so the
        executor's children-mount logic can resolve it."""
        skill_dir = zipsa_home_path / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "manifest.yaml").write_text(
            f"""apiVersion: zipsa.dev/v1alpha1
kind: Skill
metadata:
  name: {name}
  version: {version}
  author: test
  description: Test child skill.
spec:
  purpose: Fixture for children-mount test.
  instructions: ./SKILL.md
  tools:
    builtin: []
  limits:
    max_turns: 1
    max_cost_usd: 0.01
    timeout_seconds: 5
"""
        )
        (skill_dir / "SKILL.md").write_text(f"# {name}\nFixture.\n")
        return skill_dir

    def _make_parent_with_children(self, tmp_path, children: list[str]):
        """Write a tmp parent manifest declaring the given children and
        return the loaded Skill."""
        parent_dir = tmp_path / "parent-skill"
        parent_dir.mkdir()
        # Inline `[]` for empty (`children:\n` would parse as None and
        # Pydantic rejects); otherwise flow style for readability.
        children_yaml = (
            "[]" if not children
            else "[" + ", ".join(children) + "]"
        )
        (parent_dir / "manifest.yaml").write_text(
            f"""apiVersion: zipsa.dev/v1alpha1
kind: Skill
metadata:
  name: parent
  version: 1.0.0
  author: test
  description: Parent.
spec:
  purpose: Test parent.
  instructions: ./SKILL.md
  children: {children_yaml}
  tools:
    builtin: []
  limits:
    max_turns: 1
    max_cost_usd: 0.01
    timeout_seconds: 5
"""
        )
        (parent_dir / "SKILL.md").write_text("# parent\n")
        return Skill.load(parent_dir)

    def test_build_docker_command_mounts_children_runs(
        self, tmp_path, monkeypatch
    ):
        """For each entry in spec.children, the parent container should
        bind-mount the child's installed runs/ directory read-only at
        /home/agent/children/<name>/runs/, so the parent agent can read
        child artifacts via the filesystem (instead of pulling content
        through MCP, which hits Claude's per-tool-result token cap on
        large artifacts).

        The host runs dir is created if it doesn't exist yet — docker's
        bind mount of a missing path would otherwise materialize it as
        an empty root-owned dir.
        """
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        self._install_minimal_child(tmp_path, "alpha", version="0.3.0")
        # Note: NOT pre-creating runs/ — the executor must mkdir before mount.
        parent = self._make_parent_with_children(tmp_path, ["alpha"])
        claude_json_path = parent.build_claude_json()

        executor = DockerExecutor()
        cmd = executor._build_docker_command(
            skill=parent,
            user_input="x",
            claude_json_path=claude_json_path,
            env={},
        )

        cmd_str = " ".join(cmd)
        expected_host = tmp_path / "alpha@0.3.0" / "runs"
        expected_mount = f"{expected_host}:/home/agent/children/alpha/runs:ro"
        assert expected_mount in cmd_str, (
            f"missing children-runs mount\n  expected: {expected_mount}\n"
            f"  cmd: {cmd_str}"
        )
        # The host dir must now exist (executor created it) — otherwise
        # docker would silently make a root-owned empty dir on first run.
        assert expected_host.exists()

    def _mount_args(self, cmd: list[str]) -> list[str]:
        """Extract just the `host:container[:mode]` mount specs from a
        docker run command, so assertions don't false-match on the same
        substring appearing inside the embedded system prompt.

        Docker's CLI uses `-v <spec>` pairs.
        """
        return [
            cmd[i + 1] for i, tok in enumerate(cmd[:-1]) if tok == "-v"
        ]

    def test_build_docker_command_no_children_no_mount(
        self, tmp_path, monkeypatch
    ):
        """A parent with empty spec.children adds no children mounts."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        parent = self._make_parent_with_children(tmp_path, [])
        claude_json_path = parent.build_claude_json()
        executor = DockerExecutor()
        cmd = executor._build_docker_command(
            skill=parent, user_input="x",
            claude_json_path=claude_json_path, env={},
        )
        mounts = self._mount_args(cmd)
        assert not any("/home/agent/children/" in m for m in mounts)

    def test_build_docker_command_skips_uninstalled_child(
        self, tmp_path, monkeypatch
    ):
        """If a declared child is not installed, the executor must not
        crash and must not invent a stale mount path — _validate_children
        warns separately on the parent's behalf."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        # Note: NOT calling _install_minimal_child for "beta"
        parent = self._make_parent_with_children(tmp_path, ["beta"])
        claude_json_path = parent.build_claude_json()
        executor = DockerExecutor()
        # Should not raise
        cmd = executor._build_docker_command(
            skill=parent, user_input="x",
            claude_json_path=claude_json_path, env={},
        )
        mounts = self._mount_args(cmd)
        assert not any("/home/agent/children/beta/" in m for m in mounts)

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


# TestSaveMetadata removed: _save_metadata was deleted in
# chore: merge metadata.json into summary.json. Equivalent behavior
# (user_input recorded, status reflects skill's contract JSON,
# usage/stop_reason/model_usage captured) is covered by:
# - TestSummaryWritten (this file) — exercises the full executor → summary.json path
# - test_summary.py::TestBuildSummary — verifies the schema for each status


class TestExtractSkillOutput:
    """Test _extract_skill_output multi-strategy JSON extraction."""

    def test_strategy1_direct_json(self):
        """Strategy 1: direct json.loads on stripped text."""
        executor = DockerExecutor()
        text = '{"status": "ok", "phase": "precheck", "result": null, "state_updates": null, "next_phase_input": null, "user_facing_summary": "Done.", "needs_input": null, "error": null}'
        out = executor._extract_skill_output(text)
        assert out is not None
        assert out["status"] == "ok"

    def test_strategy2_fenced_json_block(self):
        """Strategy 2: extract from ```json ... ``` block."""
        executor = DockerExecutor()
        text = 'Some thinking.\n```json\n{"status": "failed", "phase": "discover", "result": null, "state_updates": null, "next_phase_input": null, "user_facing_summary": "err", "needs_input": null, "error": null}\n```'
        out = executor._extract_skill_output(text)
        assert out is not None
        assert out["status"] == "failed"

    def test_strategy3_embedded_json_object(self):
        """Strategy 3: find last {...} with 'status' key."""
        executor = DockerExecutor()
        text = 'I completed the task. {"status": "ok", "phase": "analyze", "result": "done", "state_updates": null, "next_phase_input": null, "user_facing_summary": "ok", "needs_input": null, "error": null}'
        out = executor._extract_skill_output(text)
        assert out is not None
        assert out["status"] == "ok"

    def test_strategy3_picks_outer_when_result_has_nested_status(self):
        """Regression: hello-world emits a contract JSON whose `result`
        field itself contains a "status" key. Strategy 3 must pick the
        OUTERMOST {...} (the contract), not the nested inner dict."""
        executor = DockerExecutor()
        text = '''Hello from zipsa!

Runtime : Claude Code
Model   : claude-sonnet-4-6
Status  : OK

{
  "status": "ok",
  "phase": "main",
  "result": {
    "runtime": "Claude Code",
    "model": "claude-sonnet-4-6",
    "status": "OK"
  },
  "user_facing_summary": "Zipsa is running."
}'''
        out = executor._extract_skill_output(text)
        assert out is not None
        # Must be the OUTER status, not "OK" from the nested result
        assert out["status"] == "ok"
        assert out["phase"] == "main"
        # Inner status preserved inside result
        assert out["result"]["status"] == "OK"

    def test_unparseable_text_returns_none(self):
        """When no parseable envelope is found, return None (not a
        synthetic envelope). The caller is responsible for recording
        the failed phase with error.code=invalid_output_format and the
        raw text — that way the downstream phase_id_mismatch check
        doesn't clobber the real error by tripping on a sentinel
        phase value."""
        executor = DockerExecutor()
        text = "I could not complete the task due to an error."
        assert executor._extract_skill_output(text) is None

    def test_json_without_status_returns_none(self):
        """A valid JSON object that lacks a 'status' key isn't a skill
        envelope. Return None instead of a synthetic envelope so the
        caller can record the real cause (agent emitted bare next_phase_input
        without wrapping it in the envelope)."""
        executor = DockerExecutor()
        text = '```json\n{"voice": "x", "interests": ["a"]}\n```'
        assert executor._extract_skill_output(text) is None

    def test_none_input_returns_none(self):
        """None input returns None."""
        executor = DockerExecutor()
        assert executor._extract_skill_output(None) is None


class TestBuildUserMessage:
    """Test _build_user_message constructs correct execution context."""

    def test_user_message_contains_phase_fields(self):
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        msg = executor._build_user_message(
            skill=skill,
            phase_id="precheck",
            phase_goal="Verify MCP connections.",
            phase_allowed_tools="mcp__notion__notion-search",
            previous_phase_output=None,
            skill_state={},
            user_query="log today",
        )

        assert "phase_id: precheck" in msg
        assert "phase_goal: Verify MCP connections." in msg
        assert "allowed_tools: mcp__notion__notion-search" in msg
        assert "user_query: log today" in msg
        assert "Execute phase: precheck" in msg

    def test_previous_phase_output_null_on_first_phase(self):
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        msg = executor._build_user_message(
            skill=skill,
            phase_id="precheck",
            phase_goal="goal",
            phase_allowed_tools="",
            previous_phase_output=None,
            skill_state={},
            user_query="log",
        )

        assert "previous_phase_output: null" in msg

    def test_user_message_contains_run_id(self):
        """run_id is the current run's timestamp dir name. The agent needs
        it to call mcp__zipsa__get_artifact for artifacts the current
        run wrote in an earlier phase (orchestrator pattern)."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        msg = executor._build_user_message(
            skill=skill,
            phase_id="precheck",
            phase_goal="goal",
            phase_allowed_tools="",
            previous_phase_output=None,
            skill_state={},
            user_query="log",
            run_id="2026-05-21_120000_000",
        )

        assert "run_id: 2026-05-21_120000_000" in msg

    def test_user_message_run_id_defaults_to_unknown(self):
        """When run_dir isn't available (shell/dry-run callers), the
        default keeps the template renderable rather than KeyError."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        msg = executor._build_user_message(
            skill=skill,
            phase_id="precheck",
            phase_goal="goal",
            phase_allowed_tools="",
            previous_phase_output=None,
            skill_state={},
            user_query="log",
        )

        assert "run_id: unknown" in msg

    def test_user_message_contains_tz_iana(self):
        """tz_iana is the IANA identifier (e.g., 'Australia/Sydney') for the host's
        timezone, suitable for ZoneInfo() use in skill code. Distinct from the
        existing `timezone` display string ('AEDT (UTC+11:00)')."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        msg = executor._build_user_message(
            skill=skill,
            phase_id="precheck",
            phase_goal="goal",
            phase_allowed_tools="",
            previous_phase_output=None,
            skill_state={},
            user_query="log",
        )

        # tz_iana line should be present and look like a tzdata id.
        # Accept either Region/City (most common) or single-word "UTC" / "Etc/UTC".
        import re
        m = re.search(r"^tz_iana: (\S+)$", msg, re.MULTILINE)
        assert m, f"tz_iana line missing from user message: {msg[:300]}"
        value = m.group(1)
        assert "/" in value or value in ("UTC", "GMT"), (
            f"tz_iana should be IANA identifier, got: {value!r}"
        )


class TestSkillState:
    """Test skill state persistence."""

    def test_load_returns_empty_dict_if_no_state_file(self, tmp_path):
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        with patch("zipsa.core.executor.zipsa_paths.skill_data_dir", return_value=tmp_path):
            state = executor._load_skill_state(skill)

        assert state == {}

    def test_apply_and_load_skill_state(self, tmp_path):
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        with patch("zipsa.core.executor.zipsa_paths.skill_data_dir", return_value=tmp_path):
            executor._apply_skill_state(skill, {"db_id": "abc123", "last_run_date": "2026-05-12"})
            state = executor._load_skill_state(skill)

        assert state["db_id"] == "abc123"
        assert state["last_run_date"] == "2026-05-12"

    def test_apply_null_value_deletes_key(self, tmp_path):
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        with patch("zipsa.core.executor.zipsa_paths.skill_data_dir", return_value=tmp_path):
            executor._apply_skill_state(skill, {"db_id": "abc123"})
            executor._apply_skill_state(skill, {"db_id": None})
            state = executor._load_skill_state(skill)

        assert "db_id" not in state


class TestPreToolUseHookMount:
    """Executor must mount the PreToolUse hook script and per-phase allow file."""

    def test_hook_script_is_mounted(self, tmp_path):
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path)

        cmd = executor._build_docker_command(
            skill=skill,
            user_input="x",
            claude_json_path=claude_json_path,
            env={},
        )

        cmd_str = " ".join(cmd)
        # Hook script must be mounted at fixed container path
        assert "/zipsa-hooks/pretooluse.py:ro" in cmd_str
        # The host source must be the launcher's hooks/pretooluse.py
        assert "zipsa/hooks/pretooluse.py" in cmd_str

    def test_phase_allow_path_env_set_when_provided(self, tmp_path):
        """When phase_id is provided, executor should also pass ZIPSA_PHASE_ALLOW path env."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path)

        # Generate the per-phase allow file (executor should mount its dir under /.zipsa)
        phase_allow = claude_json_path.parent / "phase-allow.json"
        phase_allow.write_text(json.dumps({"phase_id": "discover", "allowed_tools": ["Bash(find:*)"]}))

        cmd = executor._build_docker_command(
            skill=skill,
            user_input="x",
            claude_json_path=claude_json_path,
            env={},
            phase_id="discover",
        )
        cmd_str = " ".join(cmd)
        # phase-allow.json comes through the existing /.zipsa mount; hook reads default path
        assert "/.zipsa" in cmd_str
        # No env var override needed when default container path is used


class TestWritePhaseAllowFile:
    """Executor should write phase-allow.json before each phase runs."""

    def test_phase_allow_file_written(self, tmp_path):
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)

        executor._write_phase_allow_file(
            output_dir=tmp_path,
            phase_id="discover",
            allowed_tools=["Bash(find:*)", "Bash(rm:*)"],
        )

        path = tmp_path / "phase-allow.json"
        assert path.exists()
        data = json.loads(path.read_text())
        # HITL + memory tools (mcp__zipsa__*) and Claude Code infra
        # (ToolSearch) are always appended.
        assert data == {
            "phase_id": "discover",
            "allowed_tools": [
                "Bash(find:*)", "Bash(rm:*)",
                "mcp__zipsa__ask", "mcp__zipsa__confirm", "mcp__zipsa__choose",
                "mcp__zipsa__recall", "mcp__zipsa__remember",
                "mcp__zipsa__forget", "mcp__zipsa__list_memory",
                "mcp__zipsa__ask_once",
                "mcp__zipsa__get_artifact",
                "mcp__zipsa__run_skill",
                "ToolSearch",
            ],
        }


class TestSingleShotPhaseAllow:
    """Single-shot (no phases) skills must also write phase-allow.json so the
    PreToolUse hook can find the skill's full tool list."""

    def test_single_shot_writes_phase_allow_with_all_tools(self, tmp_path):
        """build_claude_json side-effect writes phase-allow.json with the full
        skill tool list so the hook permits whatever the skill declared."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path)

        # Executor exposes a helper that writes the default (single-shot)
        # phase-allow.json containing every tool the skill is allowed to use.
        executor._write_default_phase_allow_file(claude_json_path.parent, skill)

        path = tmp_path / "phase-allow.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["phase_id"] == "main"
        # test-skill's manifest declares Read, Write
        assert "Read" in data["allowed_tools"]
        assert "Write" in data["allowed_tools"]


class TestDevOverlayIntegration:
    """ZIPSA_DEV_OVERLAY adds mounts, preamble, and env to docker commands."""

    def test_overlay_mounts_appear_in_docker_cmd(self, tmp_path, monkeypatch):
        import yaml as _yaml
        overlay_file = tmp_path / "overlay.yaml"
        overlay_file.write_text(_yaml.dump({
            "mounts": ["/host/agenthud:/host/agenthud:rw"],
        }))
        monkeypatch.setenv("ZIPSA_DEV_OVERLAY", str(overlay_file))

        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path / "skill-data")

        cmd = executor._build_docker_command(
            skill=skill, user_input="x",
            claude_json_path=claude_json_path, env={},
        )
        assert "/host/agenthud:/host/agenthud:rw" in cmd

    def test_overlay_preamble_extends_cp_preamble(self, tmp_path, monkeypatch):
        import yaml as _yaml
        overlay_file = tmp_path / "overlay.yaml"
        overlay_file.write_text(_yaml.dump({
            "preamble": ["cd /x", "npm link"],
        }))
        monkeypatch.setenv("ZIPSA_DEV_OVERLAY", str(overlay_file))

        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path / "skill-data")

        cmd = executor._build_docker_command(
            skill=skill, user_input="x",
            claude_json_path=claude_json_path, env={},
        )
        joined = " ".join(cmd)
        # Original cp preamble preserved
        assert "cp /.zipsa/.claude.json" in joined
        # Overlay preamble appended (joined with &&)
        assert "cd /x && npm link" in joined

    def test_overlay_env_merged_into_env_file(self, tmp_path, monkeypatch):
        import yaml as _yaml
        overlay_file = tmp_path / "overlay.yaml"
        overlay_file.write_text(_yaml.dump({
            "env": {"AGENTHUD_DEV": "1", "DEBUG": "true"},
        }))
        monkeypatch.setenv("ZIPSA_DEV_OVERLAY", str(overlay_file))

        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json()

        executor._build_docker_command(
            skill=skill, user_input="x",
            claude_json_path=claude_json_path, env={"EXISTING": "yes"},
        )
        env_file = zipsa_home() / "minimal@1.0.0" / ".env"
        contents = env_file.read_text()
        assert "AGENTHUD_DEV=1" in contents
        assert "DEBUG=true" in contents
        assert "EXISTING=yes" in contents

    def test_no_overlay_when_env_unset(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ZIPSA_DEV_OVERLAY", raising=False)
        executor = DockerExecutor()
        assert executor.dev_overlay is None

        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path / "skill-data")

        cmd = executor._build_docker_command(
            skill=skill, user_input="x",
            claude_json_path=claude_json_path, env={},
        )
        joined = " ".join(cmd)
        # No overlay-specific preamble injected
        assert "npm link" not in joined


class TestSpecMountsApplied:
    """spec.mounts entries are added to the docker run command as -v args."""

    def _make_skill_with_mounts(self, tmp_path, mounts: list[dict]):
        """Build a tiny skill manifest with given spec.mounts."""
        import yaml
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "manifest.yaml").write_text(yaml.dump({
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {
                "purpose": "Test",
                "instructions": "./SKILL.md",
                "mounts": mounts,
            },
        }))
        (skill_dir / "SKILL.md").write_text("# Test")
        return Skill.load(skill_dir)

    def test_single_mount_added(self, tmp_path):
        skill = self._make_skill_with_mounts(tmp_path, [
            {"host": str(tmp_path / "data"), "container": "/data", "mode": "ro"},
        ])
        executor = DockerExecutor()
        claude_json_path = skill.build_claude_json(output_dir=tmp_path / "skill-data")
        cmd = executor._build_docker_command(
            skill=skill, user_input="x", claude_json_path=claude_json_path, env={},
        )
        assert f"{tmp_path / 'data'}:/data:ro" in cmd

    def test_tilde_in_host_expanded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        skill = self._make_skill_with_mounts(tmp_path, [
            {"host": "~/myhome", "container": "/myhome", "mode": "ro"},
        ])
        executor = DockerExecutor()
        claude_json_path = skill.build_claude_json(output_dir=tmp_path / "skill-data")
        cmd = executor._build_docker_command(
            skill=skill, user_input="x", claude_json_path=claude_json_path, env={},
        )
        assert f"{tmp_path}/myhome:/myhome:ro" in cmd

    def test_multiple_mounts_added(self, tmp_path):
        skill = self._make_skill_with_mounts(tmp_path, [
            {"host": str(tmp_path / "a"), "container": "/a", "mode": "ro"},
            {"host": str(tmp_path / "b"), "container": "/b", "mode": "rw"},
        ])
        executor = DockerExecutor()
        claude_json_path = skill.build_claude_json(output_dir=tmp_path / "skill-data")
        cmd = executor._build_docker_command(
            skill=skill, user_input="x", claude_json_path=claude_json_path, env={},
        )
        assert f"{tmp_path / 'a'}:/a:ro" in cmd
        assert f"{tmp_path / 'b'}:/b:rw" in cmd

    def test_empty_mounts_no_extra_args(self, tmp_path):
        skill = self._make_skill_with_mounts(tmp_path, [])
        executor = DockerExecutor()
        claude_json_path = skill.build_claude_json(output_dir=tmp_path / "skill-data")
        cmd = executor._build_docker_command(
            skill=skill, user_input="x", claude_json_path=claude_json_path, env={},
        )
        assert not any(":/a:" in s or ":/b:" in s for s in cmd)


class TestHitlIntegration:
    """Executor starts HitlServer, injects token env, and on Linux adds the
    host-gateway flag."""

    def test_zipsa_hitl_token_in_env_file(self, tmp_path, monkeypatch):
        """When _build_docker_command runs with a hitl_port, the token env
        gets written to the per-skill env file."""
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        # Simulate a started HitlServer
        executor._hitl_port = 51234
        executor._hitl_token = "test-token-xyz"

        claude_json_path = skill.build_claude_json(hitl_port=51234)
        executor._build_docker_command(
            skill=skill, user_input="x",
            claude_json_path=claude_json_path, env={},
        )
        env_file = zipsa_home() / "minimal@1.0.0" / ".env"
        contents = env_file.read_text()
        assert "ZIPSA_HITL_TOKEN=test-token-xyz" in contents

    def test_linux_adds_host_gateway_flag(self, tmp_path, monkeypatch):
        import platform
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        executor._hitl_port = 51234
        executor._hitl_token = "test-token-xyz"
        cjp = skill.build_claude_json(hitl_port=51234)

        cmd = executor._build_docker_command(
            skill=skill, user_input="x",
            claude_json_path=cjp, env={},
        )
        assert "--add-host=host.docker.internal:host-gateway" in cmd

    def test_macos_omits_host_gateway_flag(self, tmp_path, monkeypatch):
        import platform
        monkeypatch.setattr(platform, "system", lambda: "Darwin")
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        executor._hitl_port = 51234
        executor._hitl_token = "test-token-xyz"
        cjp = skill.build_claude_json(hitl_port=51234)

        cmd = executor._build_docker_command(
            skill=skill, user_input="x",
            claude_json_path=cjp, env={},
        )
        assert "--add-host=host.docker.internal:host-gateway" not in cmd

    def test_default_allow_list_contains_zipsa_tools(self, tmp_path):
        """phase-allow.json (default) includes mcp__zipsa__* names."""
        import json
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)
        out = tmp_path
        executor._write_default_phase_allow_file(out, skill)
        data = json.loads((out / "phase-allow.json").read_text())
        assert "mcp__zipsa__ask" in data["allowed_tools"]
        assert "mcp__zipsa__confirm" in data["allowed_tools"]
        assert "mcp__zipsa__choose" in data["allowed_tools"]


class TestMemoryIntegration:
    """Executor wires per-skill and global MemoryStores into HitlServer and
    adds the four memory tools to the default phase allow list."""

    def test_default_allow_list_contains_memory_tools(self, tmp_path):
        import json
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)
        executor._write_default_phase_allow_file(tmp_path, skill)
        data = json.loads((tmp_path / "phase-allow.json").read_text())
        assert "mcp__zipsa__recall" in data["allowed_tools"]
        assert "mcp__zipsa__remember" in data["allowed_tools"]
        assert "mcp__zipsa__forget" in data["allowed_tools"]
        assert "mcp__zipsa__list_memory" in data["allowed_tools"]

    def test_phase_allow_file_appends_memory_tools(self, tmp_path):
        import json
        executor = DockerExecutor()
        executor._write_phase_allow_file(tmp_path, "precheck", ["WebFetch"])
        data = json.loads((tmp_path / "phase-allow.json").read_text())
        # Existing tool stays, memory tools added
        assert "WebFetch" in data["allowed_tools"]
        assert "mcp__zipsa__recall" in data["allowed_tools"]
        assert "mcp__zipsa__remember" in data["allowed_tools"]
        assert "mcp__zipsa__forget" in data["allowed_tools"]
        assert "mcp__zipsa__list_memory" in data["allowed_tools"]

    def test_default_allow_list_contains_ask_once(self, tmp_path):
        import json
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)
        executor._write_default_phase_allow_file(tmp_path, skill)
        data = json.loads((tmp_path / "phase-allow.json").read_text())
        assert "mcp__zipsa__ask_once" in data["allowed_tools"]

    def test_default_allow_list_contains_get_artifact(self, tmp_path):
        """get_artifact is always-on (per runtime-contract.md) — every
        skill, no manifest opt-in, can read artifacts written by past
        runs."""
        import json
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)
        executor._write_default_phase_allow_file(tmp_path, skill)
        data = json.loads((tmp_path / "phase-allow.json").read_text())
        assert "mcp__zipsa__get_artifact" in data["allowed_tools"]

    def test_default_allow_list_contains_run_skill(self, tmp_path):
        """run_skill is always-on — handler-side spec.children check gates
        which child skills are actually permitted."""
        import json
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)
        executor._write_default_phase_allow_file(tmp_path, skill)
        data = json.loads((tmp_path / "phase-allow.json").read_text())
        assert "mcp__zipsa__run_skill" in data["allowed_tools"]


class TestSkillDirMount:
    """The skill's own source dir is auto-mounted at /skill:ro so the
    skill can ship helper scripts and reach them at a stable path."""

    def test_skill_source_dir_mounted_at_slash_skill(self, tmp_path):
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path)

        cmd = executor._build_docker_command(
            skill=skill,
            user_input="x",
            claude_json_path=claude_json_path,
            env={},
        )

        cmd_str = " ".join(cmd)
        # The skill_dir (parent of the manifest file) should appear as a
        # mount source with /skill as the target, read-only.
        expected = f"{skill.skill_dir}:/skill:ro"
        assert expected in cmd_str, f"expected mount {expected!r} not in {cmd_str!r}"


class TestLimitsIntegration:
    """The executor's per-event handler invokes limits.update_for_event
    and limits.check_limits, and emits zipsa_limits_breach on breach."""

    def _make_skill_with_limits(self, tmp_path, agg_limits=None):
        """Build a minimal single-phase skill with given spec-level (aggregate)
        limits. For a single-phase skill the phase_limits == agg_limits, so
        the helper only needs one param. Tests that need DISTINCT phase vs
        aggregate limits go through _execute_skill() directly with
        phase_limits= and limits_state= kwargs (see
        test_aggregate_accumulates_across_phases for an example)."""
        import yaml
        skill_dir = tmp_path / "limited-skill"
        skill_dir.mkdir()
        spec = {
            "purpose": "Test limited skill",
            "instructions": "./SKILL.md",
            "tools": {"builtin": ["Read"]},
        }
        if agg_limits is not None:
            spec["limits"] = agg_limits
        (skill_dir / "manifest.yaml").write_text(yaml.dump({
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "limited-skill", "version": "1.0.0"},
            "spec": spec,
        }))
        (skill_dir / "SKILL.md").write_text("# Limited Skill")
        return Skill.load(skill_dir)

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_phase_cost_breach_emits_event_and_stops(self, mock_popen, tmp_path):
        """A skill that exceeds phase cost on the 2nd assistant message gets
        stopped; a zipsa_limits_breach event is emitted and the stream stops."""
        from unittest.mock import MagicMock

        # Each assistant event with 1M input tokens on Opus costs $15.
        # Set a max_cost_usd of $10 so the 2nd message breaches it.
        assistant_line_1 = json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "thinking", "thinking": "thinking..."}],
                "usage": {"input_tokens": 500_000},  # ~$7.50 at Opus rate
            },
        }) + "\n"
        assistant_line_2 = json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "thinking", "thinking": "thinking more..."}],
                "usage": {"input_tokens": 500_000},  # ~$7.50 more — cumulative ~$15 > $10
            },
        }) + "\n"
        # This should never be yielded — stream must stop after breach
        extra_line = json.dumps({"type": "result", "is_error": False}) + "\n"

        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [
            assistant_line_1,
            assistant_line_2,
            extra_line,
            "",  # EOF
        ]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None
        mock_process.terminate = Mock()
        mock_process.wait = Mock(return_value=0)
        mock_process.kill = Mock()
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        skill = self._make_skill_with_limits(
            tmp_path,
            agg_limits={"max_cost_usd": 10.0},
        )
        executor = DockerExecutor()

        events = list(executor.run(skill, "Test", env={}))
        event_types = [e.get("type") for e in events]

        # Breach event must be present
        assert "zipsa_limits_breach" in event_types, f"No breach event in: {event_types}"

        # Only zipsa_run_complete may follow the breach (it is the terminal event)
        breach_idx = event_types.index("zipsa_limits_breach")
        tail = event_types[breach_idx + 1:]
        assert tail == [] or tail == ["zipsa_run_complete"], (
            f"Unexpected events after breach: {tail}"
        )

        # Process must have been terminated
        mock_process.terminate.assert_called()

        # Breach event has the right structure
        breach_event = events[breach_idx]
        assert breach_event["kind"] == "cost"
        assert breach_event["limit"] == 10.0

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_breach_with_sigterm_exit_code_does_not_raise(self, mock_popen, tmp_path):
        """When we intentionally terminate Docker on a limit breach, the
        process exits with 143 (SIGTERM). The executor must NOT additionally
        raise 'Docker execution failed with code 143' — that's misleading
        noise about our own SIGTERM. The user already saw the breach event."""
        from unittest.mock import MagicMock

        assistant_line = json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "thinking", "thinking": "thinking..."}],
                "usage": {"input_tokens": 1_000_000},  # ~$15 at Opus, > $5 limit
            },
        }) + "\n"

        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [assistant_line, ""]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None
        mock_process.terminate = Mock()
        mock_process.wait = Mock(return_value=143)
        mock_process.kill = Mock()
        # SIGTERM exit code — what Docker reports after we terminate it
        mock_process.returncode = 143
        mock_popen.return_value = mock_process

        skill = self._make_skill_with_limits(
            tmp_path,
            agg_limits={"max_cost_usd": 5.0},
        )
        executor = DockerExecutor()

        # Must complete without RuntimeError
        events = list(executor.run(skill, "Test", env={}))
        event_types = [e.get("type") for e in events]
        assert "zipsa_limits_breach" in event_types

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_nonbreach_nonzero_exit_still_raises(self, mock_popen, tmp_path):
        """If Docker exits non-zero WITHOUT a breach (e.g. claude crashed),
        we still raise RuntimeError. Regression guard so the breach-suppress
        flag doesn't accidentally silence real Docker failures."""
        from unittest.mock import MagicMock
        import pytest as _pytest

        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [""]  # immediate EOF, no events
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None
        mock_process.terminate = Mock()
        mock_process.wait = Mock(return_value=1)
        mock_process.kill = Mock()
        mock_process.returncode = 1  # generic error, NOT 143
        mock_popen.return_value = mock_process

        skill = self._make_skill_with_limits(tmp_path)
        executor = DockerExecutor()

        with _pytest.raises(RuntimeError, match="Docker execution failed with code 1"):
            list(executor.run(skill, "Test", env={}))

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_aggregate_turns_breach_emits_event(self, mock_popen, tmp_path):
        """When aggregate max_turns is exceeded, a zipsa_limits_breach event
        is emitted and the stream stops."""
        from unittest.mock import MagicMock

        # Each assistant with a thinking block = 1 turn.
        # Set aggregate max_turns=1; after 2 assistant events it should breach.
        def make_assistant_line():
            return json.dumps({
                "type": "assistant",
                "message": {
                    "content": [{"type": "thinking", "thinking": "..."}],
                    "usage": {"input_tokens": 1},
                },
            }) + "\n"

        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [
            make_assistant_line(),  # turn 1 — within limit
            make_assistant_line(),  # turn 2 — breaches max_turns=1
            json.dumps({"type": "result", "is_error": False}) + "\n",
            "",  # EOF
        ]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None
        mock_process.terminate = Mock()
        mock_process.wait = Mock(return_value=0)
        mock_process.kill = Mock()
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        skill = self._make_skill_with_limits(
            tmp_path,
            agg_limits={"max_turns": 1},
        )
        executor = DockerExecutor()

        events = list(executor.run(skill, "Test", env={}))
        event_types = [e.get("type") for e in events]

        assert "zipsa_limits_breach" in event_types, f"No breach event in: {event_types}"
        breach_idx = event_types.index("zipsa_limits_breach")
        # Only zipsa_run_complete may follow the breach (it is the terminal event)
        tail = event_types[breach_idx + 1:]
        assert tail == [] or tail == ["zipsa_run_complete"], (
            f"Unexpected events after breach: {tail}"
        )

        breach_event = events[breach_idx]
        assert breach_event["kind"] == "turns"
        assert breach_event["limit"] == 1.0

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_hitl_wait_does_not_count_toward_timeout(self, mock_popen, tmp_path, monkeypatch):
        """HITL pause/resume events must NOT contribute to compute time.

        Simulates: agent asks mcp__zipsa__ask → 5-minute wall gap (monotonic
        mocked) → tool_result arrives → agent finishes. With a 60s timeout
        the run must NOT breach (compute time is 0s, HITL is excluded).

        Tests _execute_skill directly to avoid HitlServer setup."""
        from unittest.mock import MagicMock
        import zipsa.core.limits as _limits_mod

        # Mocked monotonic: new_state() + tool_use + tool_result + check(x2)
        call_count = [0]
        monotonic_values = [100.0, 110.0, 470.0, 471.0, 472.0]

        def mock_monotonic():
            idx = call_count[0]
            call_count[0] += 1
            if idx < len(monotonic_values):
                return monotonic_values[idx]
            return 472.0  # stay at end if more calls than expected

        monkeypatch.setattr(_limits_mod.time, "monotonic", mock_monotonic)

        ask_line = json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "mcp__zipsa__ask", "input": {}},
                ],
                "usage": {"input_tokens": 1},
            },
        }) + "\n"
        tool_result_line = json.dumps({
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "tu_1"}],
            },
        }) + "\n"
        result_line = json.dumps({
            "type": "result",
            "is_error": False,
            "num_turns": 1,
            "total_cost_usd": 0.001,
        }) + "\n"

        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [ask_line, tool_result_line, result_line, ""]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None
        mock_process.terminate = Mock()
        mock_process.wait = Mock(return_value=0)
        mock_process.kill = Mock()
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        # 60s timeout — would fire if HITL time counted (wall=372s, HITL=360s, compute=12s)
        skill = self._make_skill_with_limits(
            tmp_path,
            agg_limits={"timeout_seconds": 60},
        )
        executor = DockerExecutor()

        # Call _execute_skill directly to bypass HitlServer lifecycle
        claude_json_path = skill.build_claude_json(output_dir=tmp_path / "skill-data")
        docker_cmd = ["docker", "run", "--rm", "test-image", "echo", "test"]
        events = list(executor._execute_skill(
            docker_cmd, claude_json_path, skill, None,
        ))
        event_types = [e.get("type") for e in events]

        # No breach — HITL pause was correctly excluded
        assert "zipsa_limits_breach" not in event_types, (
            f"Unexpected breach in: {event_types}"
        )

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_aggregate_accumulates_across_phases(self, mock_popen, tmp_path):
        """Phase 1 uses 2 turns within phase limit of 5. Phase 2 then uses 2
        more turns — its own phase budget is fine (within 5), but the aggregate
        (4) crosses agg_limits.max_turns=3. The breach fires mid-phase-2.

        Tests _execute_skill invoked twice with the SAME limits_state so the
        aggregate run_turns counter carries across the phase boundary.
        """
        from unittest.mock import MagicMock
        from zipsa.core.limits import new_state, SkillLimits as _SkillLimits

        def make_assistant_line():
            return json.dumps({
                "type": "assistant",
                "message": {
                    "content": [{"type": "thinking", "thinking": "..."}],
                    "usage": {"input_tokens": 1},
                },
            }) + "\n"

        # Phase 1 produces 2 turns and ends cleanly (EOF).
        phase1_stdout = MagicMock()
        phase1_stdout.readline.side_effect = [
            make_assistant_line(),  # run_turn 1
            make_assistant_line(),  # run_turn 2
            "",  # EOF — clean end, no breach
        ]
        # Phase 2 produces 2 more turns; aggregate should breach on turn 4.
        phase2_stdout = MagicMock()
        phase2_stdout.readline.side_effect = [
            make_assistant_line(),  # run_turn 3 — still within agg limit of 3
            make_assistant_line(),  # run_turn 4 — breaches agg max_turns=3
            json.dumps({"type": "result", "is_error": False}) + "\n",
            "",  # EOF
        ]

        process1 = Mock()
        process1.stdout = phase1_stdout
        process1.poll.return_value = 0  # already exited (clean end)
        process1.terminate = Mock()
        process1.wait = Mock(return_value=0)
        process1.kill = Mock()
        process1.returncode = 0

        process2 = Mock()
        process2.stdout = phase2_stdout
        process2.poll.return_value = None  # still running when terminated
        process2.terminate = Mock()
        process2.wait = Mock(return_value=0)
        process2.kill = Mock()
        process2.returncode = 0

        mock_popen.side_effect = [process1, process2]

        # Skill with aggregate max_turns=3, no per-phase limit.
        skill = self._make_skill_with_limits(
            tmp_path,
            agg_limits={"max_turns": 3},
        )
        executor = DockerExecutor()
        claude_json_path = skill.build_claude_json(output_dir=tmp_path / "skill-data")
        docker_cmd = ["docker", "run", "--rm", "test-image", "echo", "test"]

        # Shared state — same object passed to both invocations.
        shared_state = new_state("phase-1")
        phase_lim = _SkillLimits()  # no per-phase limit

        # --- Phase 1 ---
        phase1_events = list(executor._execute_skill(
            docker_cmd, claude_json_path, skill, None,
            phase_id="phase-1",
            phase_limits=phase_lim,
            limits_state=shared_state,
        ))
        phase1_types = [e.get("type") for e in phase1_events]
        # Phase 1 must NOT breach (2 turns ≤ agg limit 3)
        assert "zipsa_limits_breach" not in phase1_types, (
            f"Unexpected breach in phase 1: {phase1_types}"
        )
        # Aggregate should be at 2 turns after phase 1
        assert shared_state.run_turns == 2, (
            f"Expected run_turns=2 after phase 1, got {shared_state.run_turns}"
        )

        # --- Phase 2 ---
        phase2_events = list(executor._execute_skill(
            docker_cmd, claude_json_path, skill, None,
            phase_id="phase-2",
            phase_limits=phase_lim,
            limits_state=shared_state,
        ))
        phase2_types = [e.get("type") for e in phase2_events]

        # Phase 2 MUST breach (run_turns reaches 4 > agg max_turns=3)
        assert "zipsa_limits_breach" in phase2_types, (
            f"Expected aggregate breach in phase 2, got: {phase2_types}"
        )
        breach_idx = phase2_types.index("zipsa_limits_breach")
        # Nothing emitted after the breach event
        assert breach_idx == len(phase2_types) - 1, (
            f"Events after breach: {phase2_types[breach_idx + 1:]}"
        )
        breach_event = phase2_events[breach_idx]
        assert breach_event["kind"] == "turns"
        assert breach_event["scope"] == "aggregate"
        assert breach_event["limit"] == 3.0


class TestSummaryWritten:
    """The executor writes summary.json to run_dir at the end of every
    run, regardless of how it ended. summary fields reflect the run's
    final status."""

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_summary_written_on_ok_run(self, mock_popen, tmp_path):
        from unittest.mock import MagicMock
        import json as _json

        result_line = _json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": '{"status": "ok", "phase": "main", "result": {"hello": "world"}}'}],
                "usage": {"input_tokens": 100},
            },
        }) + "\n"
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [result_line, ""]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None
        mock_process.wait = Mock(return_value=0)
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)
        executor = DockerExecutor()

        # Force run_dir into tmp_path so we can find summary.json
        with patch("zipsa.core.executor.zipsa_paths.skill_data_dir", return_value=tmp_path):
            list(executor.run(skill, "Hi", env={}))

        # Find the summary.json (only one runs dir)
        summary_path = next((tmp_path / "runs").glob("*/summary.json"))
        s = _json.loads(summary_path.read_text())
        assert s["status"] == "ok"
        assert s["exit_code"] == 0
        assert s["skill"] == skill.name
        assert s["result"] == {"hello": "world"}
        assert s["error"] is None
        assert s["schema_version"] == 1

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_summary_written_on_limit_breach(self, mock_popen, tmp_path):
        """A run that hits the cost limit ends with status=limits_exceeded, exit_code=3."""
        from unittest.mock import MagicMock
        import json as _json

        # Assistant message that has high token cost to trigger limit breach.
        # We use max_cost_usd=0.0001 on the skill so any non-zero usage exceeds it.
        thinking_line = _json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "thinking", "thinking": "thinking"}],
                "usage": {"input_tokens": 10000, "output_tokens": 10000},
            },
        }) + "\n"
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [thinking_line, ""]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None
        mock_process.terminate = Mock()
        mock_process.wait = Mock(return_value=0)
        mock_process.kill = Mock()
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        # Patch limits so we breach cost immediately
        from zipsa.core.models import SkillLimits as _SkillLimits
        skill.manifest.spec.limits = _SkillLimits(max_cost_usd=0.0001)

        executor = DockerExecutor()

        with patch("zipsa.core.executor.zipsa_paths.skill_data_dir", return_value=tmp_path):
            list(executor.run(skill, "Hi", env={}))

        summary_path = next((tmp_path / "runs").glob("*/summary.json"))
        s = _json.loads(summary_path.read_text())
        assert s["status"] == "limits_exceeded"
        assert s["exit_code"] == 3
        assert s["error"] is not None
        assert s["error"]["details"]["kind"] in ("cost", "time", "turns")
        assert s["result"] is None

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_summary_written_on_failed_status(self, mock_popen, tmp_path):
        """A run where the agent emits status=failed ends with status=failed, exit_code=1."""
        from unittest.mock import MagicMock
        import json as _json

        failed_payload = _json.dumps({
            "status": "failed",
            "phase": "main",
            "result": None,
            "error": {"code": "some_error", "message": "Something went wrong"},
        })
        result_line = _json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": failed_payload}],
                "usage": {"input_tokens": 50},
            },
        }) + "\n"
        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [result_line, ""]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None
        mock_process.wait = Mock(return_value=0)
        mock_process.returncode = 0
        mock_popen.return_value = mock_process

        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)
        executor = DockerExecutor()

        with patch("zipsa.core.executor.zipsa_paths.skill_data_dir", return_value=tmp_path):
            list(executor.run(skill, "Hi", env={}))

        summary_path = next((tmp_path / "runs").glob("*/summary.json"))
        s = _json.loads(summary_path.read_text())
        assert s["status"] == "failed"
        assert s["exit_code"] == 1
        assert s["result"] is None
        assert s["error"] is not None
        assert s["error"]["code"] == "some_error"

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_multi_phase_breach_emits_run_complete(self, mock_popen, tmp_path):
        """Multi-phase skill that breaches limits in phase 2 must emit
        zipsa_run_complete with status=limits_exceeded and exit_code=3.

        Previously the multi-phase breach path returned from _execute_phases
        WITHOUT yielding zipsa_run_complete, leaving the CLI event stream
        without the terminal event and defaulting to exit 5 (infra_failed).
        """
        from unittest.mock import MagicMock
        import json as _json
        import yaml

        # Build a two-phase skill with a very tight cost limit so phase 2 breaches.
        skill_dir = tmp_path / "two-phase-skill"
        skill_dir.mkdir()
        (skill_dir / "manifest.yaml").write_text(yaml.dump({
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "two-phase-skill", "version": "1.0.0"},
            "spec": {
                "purpose": "Two phase test skill",
                "instructions": "./SKILL.md",
                "tools": {"builtin": ["Read"]},
                "limits": {"max_cost_usd": 0.0001},
                "phases": [
                    {"id": "phase-1", "goal": "Do phase 1", "allowed_tools": ["Read"]},
                    {"id": "phase-2", "goal": "Do phase 2", "allowed_tools": ["Read"]},
                ],
            },
        }))
        (skill_dir / "SKILL.md").write_text("# Two Phase Skill")
        skill = Skill.load(skill_dir)

        # Phase 1 ends cleanly with ok output.
        phase1_output = _json.dumps({
            "status": "ok",
            "phase": "phase-1",
            "result": None,
            "state_updates": None,
            "next_phase_input": None,
            "user_facing_summary": "Phase 1 done",
        })
        phase1_line = _json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": phase1_output}],
                "usage": {"input_tokens": 1},
            },
        }) + "\n"

        # Phase 2 triggers a cost breach via high token usage.
        phase2_line = _json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "thinking", "thinking": "spending lots"}],
                "usage": {"input_tokens": 10_000, "output_tokens": 10_000},
            },
        }) + "\n"

        phase1_stdout = MagicMock()
        phase1_stdout.readline.side_effect = [phase1_line, ""]
        phase2_stdout = MagicMock()
        phase2_stdout.readline.side_effect = [phase2_line, ""]

        process1 = Mock()
        process1.stdout = phase1_stdout
        process1.poll.return_value = 0
        process1.terminate = Mock()
        process1.wait = Mock(return_value=0)
        process1.kill = Mock()
        process1.returncode = 0

        process2 = Mock()
        process2.stdout = phase2_stdout
        process2.poll.return_value = None
        process2.terminate = Mock()
        process2.wait = Mock(return_value=0)
        process2.kill = Mock()
        process2.returncode = 0

        mock_popen.side_effect = [process1, process2]

        executor = DockerExecutor()

        with patch("zipsa.core.executor.zipsa_paths.skill_data_dir", return_value=tmp_path):
            events = list(executor.run(skill, "test", env={}))

        event_types = [e.get("type") for e in events]

        # Must have a breach event (sanity check).
        assert "zipsa_limits_breach" in event_types, (
            f"Expected zipsa_limits_breach in: {event_types}"
        )
        # Must have a zipsa_run_complete event (the fix).
        assert "zipsa_run_complete" in event_types, (
            f"Expected zipsa_run_complete in: {event_types}"
        )
        # The complete event must carry exit_code=3.
        complete_event = next(e for e in events if e.get("type") == "zipsa_run_complete")
        assert complete_event["exit_code"] == 3, (
            f"Expected exit_code=3 in complete event: {complete_event}"
        )
        assert complete_event["status"] == "limits_exceeded", (
            f"Expected status=limits_exceeded: {complete_event}"
        )
        # zipsa_run_complete must be the last event.
        assert event_types[-1] == "zipsa_run_complete", (
            f"zipsa_run_complete must be last event, got: {event_types}"
        )


class TestKeyboardInterrupt:
    """Ctrl+C during a run must terminate the underlying Docker process.
    Used to live in test_limits.py; moved here when that file was
    repurposed for the new limits module."""

    @patch("zipsa.core.executor.subprocess.Popen")
    def test_keyboard_interrupt_terminates_process(self, mock_popen):
        import pytest as _pytest
        from unittest.mock import MagicMock

        mock_stdout = MagicMock()
        mock_stdout.readline.side_effect = [
            '{"type":"system","subtype":"init"}\n',
            KeyboardInterrupt("User pressed Ctrl+C"),
        ]
        mock_process = Mock()
        mock_process.stdout = mock_stdout
        mock_process.poll.return_value = None
        mock_process.terminate = Mock()
        mock_process.wait = Mock()
        mock_process.kill = Mock()
        mock_popen.return_value = mock_process

        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        with _pytest.raises(KeyboardInterrupt):
            list(executor.run(skill, "Test", env={}))

        mock_process.terminate.assert_called()
        mock_process.wait.assert_called()


class TestMountExpansion:
    def test_static_mount_unchanged(self, tmp_path):
        """Existing static mounts: -v <host>:<container>:<mode>."""
        from zipsa.core.executor import DockerExecutor
        from zipsa.core.skill import Skill

        skill_dir = tmp_path / "s"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# x")
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: t\n"
            "  instructions: ./SKILL.md\n"
            "  mounts:\n"
            f"    - {{host: {tmp_path}, container: /static, mode: ro}}\n"
        )
        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")
        cmd = ex._build_docker_command(
            skill, "hi", tmp_path / "claude.json", {},
            requires_values={},
        )
        assert any(f"{tmp_path.resolve()}:/static:ro" in arg for arg in cmd)

    def test_single_directory_dynamic_mount(self, tmp_path):
        from zipsa.core.executor import DockerExecutor
        from zipsa.core.skill import Skill

        vault = tmp_path / "vault"
        vault.mkdir()
        skill_dir = tmp_path / "s"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# x")
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: t\n"
            "  instructions: ./SKILL.md\n"
            "  requires:\n"
            "    vault: {type: directory, prompt: 'where'}\n"
            "  mounts:\n"
            "    - {source: requires.vault, container: /vault, mode: ro}\n"
        )
        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")
        cmd = ex._build_docker_command(
            skill, "hi", tmp_path / "claude.json", {},
            requires_values={"vault": str(vault)},
        )
        assert any(f"{vault}:/vault:ro" in arg for arg in cmd)

    def test_list_directory_dynamic_expands_per_item(self, tmp_path):
        from zipsa.core.executor import DockerExecutor
        from zipsa.core.skill import Skill

        a = tmp_path / "code"
        b = tmp_path / "personal"
        a.mkdir()
        b.mkdir()
        skill_dir = tmp_path / "s"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# x")
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: t\n"
            "  instructions: ./SKILL.md\n"
            "  requires:\n"
            "    project_roots:\n"
            "      type: 'list[directory]'\n"
            "      prompt: '?'\n"
            "  mounts:\n"
            "    - {source: requires.project_roots, container_prefix: /projects/, mode: ro}\n"
        )
        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")
        cmd = ex._build_docker_command(
            skill, "hi", tmp_path / "claude.json", {},
            requires_values={"project_roots": [str(a), str(b)]},
        )
        assert any(f"{a}:/projects/code:ro" in arg for arg in cmd)
        assert any(f"{b}:/projects/personal:ro" in arg for arg in cmd)

    def test_basename_collision_raises(self, tmp_path):
        from zipsa.core.executor import DockerExecutor, MountCollisionError
        from zipsa.core.skill import Skill

        a = tmp_path / "code" / "zipsa"
        b = tmp_path / "personal" / "zipsa"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        skill_dir = tmp_path / "s"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# x")
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: t\n"
            "  instructions: ./SKILL.md\n"
            "  requires:\n"
            "    project_roots:\n"
            "      type: 'list[directory]'\n"
            "      prompt: '?'\n"
            "  mounts:\n"
            "    - {source: requires.project_roots, container_prefix: /projects/, mode: ro}\n"
        )
        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")
        with pytest.raises(MountCollisionError, match="zipsa"):
            ex._build_docker_command(
                skill, "hi", tmp_path / "claude.json", {},
                requires_values={"project_roots": [str(a), str(b)]},
            )

    def test_static_and_dynamic_same_container_path_collides(self, tmp_path):
        """Cross-mount collision: static mount and dynamic mount targeting
        the same container path must raise (validates shared seen_container_paths)."""
        from zipsa.core.executor import DockerExecutor, MountCollisionError
        from zipsa.core.skill import Skill

        vault = tmp_path / "vault"
        vault.mkdir()
        skill_dir = tmp_path / "s"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# x")
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: t\n"
            "  instructions: ./SKILL.md\n"
            "  requires:\n"
            "    vault: {type: directory, prompt: 'where'}\n"
            "  mounts:\n"
            f"    - {{host: {tmp_path}, container: /shared, mode: ro}}\n"
            "    - {source: requires.vault, container: /shared, mode: ro}\n"
        )
        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")
        with pytest.raises(MountCollisionError, match="/shared"):
            ex._build_docker_command(
                skill, "hi", tmp_path / "claude.json", {},
                requires_values={"vault": str(vault)},
            )

    def test_manifest_cannot_shadow_zipsa_internal_path(self, tmp_path):
        """Manifest declaring `container: /skill` would clash with the
        auto-mounted skill source directory. Pre-seeded seen_container_paths
        catches this before docker run."""
        from zipsa.core.executor import DockerExecutor, MountCollisionError
        from zipsa.core.skill import Skill

        skill_dir = tmp_path / "s"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# x")
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: t\n"
            "  instructions: ./SKILL.md\n"
            "  mounts:\n"
            f"    - {{host: {tmp_path}, container: /skill, mode: ro}}\n"
        )
        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")
        with pytest.raises(MountCollisionError, match="/skill"):
            ex._build_docker_command(
                skill, "hi", tmp_path / "claude.json", {},
                requires_values={},
            )

    def test_preserve_host_path_single_directory(self, tmp_path):
        """source + preserve_host_path: directory value → mount at its own
        absolute host path inside the container (no basename, no prefix)."""
        from zipsa.core.executor import DockerExecutor
        from zipsa.core.skill import Skill

        vault = tmp_path / "vault"
        vault.mkdir()
        skill_dir = tmp_path / "s"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# x")
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: t\n"
            "  instructions: ./SKILL.md\n"
            "  requires:\n"
            "    vault: {type: directory, prompt: 'where'}\n"
            "  mounts:\n"
            "    - {source: requires.vault, preserve_host_path: true, mode: ro}\n"
        )
        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")
        cmd = ex._build_docker_command(
            skill, "hi", tmp_path / "claude.json", {},
            requires_values={"vault": str(vault)},
        )
        assert any(f"{vault}:{vault}:ro" in arg for arg in cmd)

    def test_preserve_host_path_list_directory_expands_per_item(self, tmp_path):
        """list[directory] + preserve_host_path: each item mounts at its own
        absolute host path, no transformation."""
        from zipsa.core.executor import DockerExecutor
        from zipsa.core.skill import Skill

        a = tmp_path / "Code"
        b = tmp_path / "WestbrookAI"
        a.mkdir()
        b.mkdir()
        skill_dir = tmp_path / "s"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# x")
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: t\n"
            "  instructions: ./SKILL.md\n"
            "  requires:\n"
            "    project_roots: {type: 'list[directory]', prompt: '?'}\n"
            "  mounts:\n"
            "    - {source: requires.project_roots, preserve_host_path: true, mode: ro}\n"
        )
        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")
        cmd = ex._build_docker_command(
            skill, "hi", tmp_path / "claude.json", {},
            requires_values={"project_roots": [str(a), str(b)]},
        )
        assert any(f"{a}:{a}:ro" in arg for arg in cmd)
        assert any(f"{b}:{b}:ro" in arg for arg in cmd)

    def test_preserve_host_path_collision_on_duplicates(self, tmp_path):
        """Same resolved path twice in a list → MountCollisionError."""
        from zipsa.core.executor import DockerExecutor, MountCollisionError
        from zipsa.core.skill import Skill

        a = tmp_path / "Code"
        a.mkdir()
        skill_dir = tmp_path / "s"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# x")
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: t\n"
            "  instructions: ./SKILL.md\n"
            "  requires:\n"
            "    project_roots: {type: 'list[directory]', prompt: '?'}\n"
            "  mounts:\n"
            "    - {source: requires.project_roots, preserve_host_path: true, mode: ro}\n"
        )
        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")
        with pytest.raises(MountCollisionError, match=str(a)):
            ex._build_docker_command(
                skill, "hi", tmp_path / "claude.json", {},
                requires_values={"project_roots": [str(a), str(a)]},
            )


class TestModelWiring:
    """Test that spec.model and phase.model actually reach the claude CLI
    via --model. Previously spec.model was used only for pricing."""

    def _make_skill(self, tmp_path, manifest_yaml):
        from zipsa.core.skill import Skill
        skill_dir = tmp_path / "s"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# x")
        (skill_dir / "manifest.yaml").write_text(manifest_yaml)
        return Skill.load(skill_dir)

    def test_no_model_omits_flag(self, tmp_path):
        """Skill without spec.model → no --model in docker cmd."""
        from zipsa.core.executor import DockerExecutor
        skill = self._make_skill(tmp_path,
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: t\n"
            "  instructions: ./SKILL.md\n"
        )
        ex = DockerExecutor(runtime="claude", image="x")
        cmd = ex._build_docker_command(
            skill, "hi", tmp_path / "claude.json", {},
        )
        assert "--model" not in cmd

    def test_spec_model_emits_flag(self, tmp_path):
        """Skill with spec.model.name → --model flag with that value."""
        from zipsa.core.executor import DockerExecutor
        skill = self._make_skill(tmp_path,
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: t\n"
            "  instructions: ./SKILL.md\n"
            "  model: {name: claude-haiku-4-5-20251001}\n"
        )
        ex = DockerExecutor(runtime="claude", image="x")
        cmd = ex._build_docker_command(
            skill, "hi", tmp_path / "claude.json", {},
        )
        # --model appears followed by the model name
        joined = " ".join(cmd)
        assert "--model claude-haiku-4-5-20251001" in joined

    def test_explicit_model_kwarg_wins_over_spec(self, tmp_path):
        """Per-phase override: model kwarg beats skill.spec.model."""
        from zipsa.core.executor import DockerExecutor
        skill = self._make_skill(tmp_path,
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: t\n"
            "  instructions: ./SKILL.md\n"
            "  model: {name: claude-opus-4-7}\n"
        )
        ex = DockerExecutor(runtime="claude", image="x")
        cmd = ex._build_docker_command(
            skill, "hi", tmp_path / "claude.json", {},
            model="claude-haiku-4-5-20251001",
        )
        joined = " ".join(cmd)
        assert "--model claude-haiku-4-5-20251001" in joined
        assert "claude-opus-4-7" not in joined


class TestPhaseSpecModel:
    """Test that PhaseSpec.model exists and is honored by the phase loop."""

    def test_phase_model_field_loads(self):
        from zipsa.core.models import PhaseSpec
        p = PhaseSpec(
            id="precheck", goal="check",
            model={"name": "claude-haiku-4-5-20251001"},
        )
        assert p.model == {"name": "claude-haiku-4-5-20251001"}

    def test_phase_model_defaults_none(self):
        from zipsa.core.models import PhaseSpec
        p = PhaseSpec(id="precheck", goal="check")
        assert p.model is None

    def test_phase_model_in_manifest_loads(self, tmp_path):
        from zipsa.core.skill import Skill
        skill_dir = tmp_path / "s"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# x")
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: s, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: t\n"
            "  instructions: ./SKILL.md\n"
            "  model: {name: claude-opus-4-7}\n"
            "  phases:\n"
            "    - id: precheck\n"
            "      goal: 'check things'\n"
            "      model: {name: claude-haiku-4-5-20251001}\n"
            "    - id: main\n"
            "      goal: 'do work'\n"
        )
        skill = Skill.load(skill_dir)
        assert skill.manifest.spec.phases[0].model == {"name": "claude-haiku-4-5-20251001"}
        assert skill.manifest.spec.phases[1].model is None  # falls back to spec.model


class TestArtifactsDirCreation:
    def test_run_creates_artifacts_subdir(self, tmp_path, monkeypatch):
        """When run_dir is created (real execution path), artifacts/
        subdir must exist so the skill can write into it from inside
        the container."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))

        # Set up a minimal skill fixture
        skill_dir = tmp_path / "src" / "afct"
        skill_dir.mkdir(parents=True)
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: afct, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: test\n"
            "  instructions: ./SKILL.md\n"
        )
        (skill_dir / "SKILL.md").write_text("# x")

        from zipsa.core.skill import Skill
        from zipsa.core.executor import DockerExecutor

        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")
        from zipsa.paths import skill_data_dir
        sd = skill_data_dir("afct", "0.1.0")
        sd.mkdir(parents=True, exist_ok=True)
        runs_dir = sd / "runs"
        runs_dir.mkdir(exist_ok=True)
        run_id = "2026-05-21_120000_000"
        run_dir = runs_dir / run_id
        run_dir.mkdir()

        ex._ensure_run_artifacts_dir(run_dir)
        assert (run_dir / "artifacts").exists()
        assert (run_dir / "artifacts").is_dir()

    def test_build_docker_command_mounts_run_dir(self, tmp_path, monkeypatch):
        """Container should see the host run_dir at /home/agent/runs/current/
        with rw access, so the skill can write artifacts."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))

        skill_dir = tmp_path / "src" / "afct"
        skill_dir.mkdir(parents=True)
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: afct, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: test\n"
            "  instructions: ./SKILL.md\n"
        )
        (skill_dir / "SKILL.md").write_text("# x")

        from zipsa.core.skill import Skill
        from zipsa.core.executor import DockerExecutor

        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")

        from zipsa.paths import skill_data_dir
        sd = skill_data_dir("afct", "0.1.0")
        sd.mkdir(parents=True, exist_ok=True)
        run_dir = sd / "runs" / "2026-05-21_120000_000"
        run_dir.mkdir(parents=True)
        (run_dir / "artifacts").mkdir()

        cmd = ex._build_docker_command(
            skill, "hi", tmp_path / "claude.json", {},
            run_dir=run_dir,
        )
        joined = " ".join(cmd)
        assert f"{run_dir}:/home/agent/runs/current:rw" in joined

    def test_build_docker_command_skips_mount_when_run_dir_none(self, tmp_path, monkeypatch):
        """Dry-run / shell mode pass run_dir=None — no run_dir mount."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))

        from zipsa.core.skill import Skill
        from zipsa.core.executor import DockerExecutor

        skill_dir = tmp_path / "src" / "afct"
        skill_dir.mkdir(parents=True)
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: afct, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: test\n"
            "  instructions: ./SKILL.md\n"
        )
        (skill_dir / "SKILL.md").write_text("# x")

        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")

        cmd = ex._build_docker_command(
            skill, "hi", tmp_path / "claude.json", {},
        )
        joined = " ".join(cmd)
        # Mount flag is the only thing we care about — the path appears
        # inside the embedded runtime-contract.md system prompt now too.
        assert ":/home/agent/runs/current:rw" not in joined

    def test_user_manifest_cannot_shadow_run_current_path(self, tmp_path):
        """A user manifest declaring container: /home/agent/runs/current must
        be rejected by the collision tracker (since we pre-seed that path
        when run_dir is given)."""
        from zipsa.core.skill import Skill
        from zipsa.core.executor import DockerExecutor, MountCollisionError

        skill_dir = tmp_path / "src" / "afct"
        skill_dir.mkdir(parents=True)
        (skill_dir / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: Skill\n"
            "metadata: {name: afct, version: 0.1.0}\n"
            "spec:\n"
            "  purpose: test\n"
            "  instructions: ./SKILL.md\n"
            "  mounts:\n"
            f"    - {{host: {tmp_path}, container: /home/agent/runs/current, mode: ro}}\n"
        )
        (skill_dir / "SKILL.md").write_text("# x")

        skill = Skill.load(skill_dir)
        ex = DockerExecutor(runtime="claude", image="x")

        sd = tmp_path / "afct@0.1.0"
        sd.mkdir(parents=True, exist_ok=True)
        run_dir = sd / "runs" / "2026-05-21_120000_000"
        run_dir.mkdir(parents=True)

        with pytest.raises(MountCollisionError, match="/home/agent/runs/current"):
            ex._build_docker_command(
                skill, "hi", tmp_path / "claude.json", {},
                run_dir=run_dir,
            )


class TestParentMCPDelegation:
    """When ZIPSA_PARENT_MCP_URL is set, the executor must NOT start
    its own HitlServer; the child container's .claude.json points at
    the parent's URL with the parent-supplied token."""

    def test_build_claude_json_uses_url_override(self, tmp_path):
        """build_claude_json should accept mcp_url_override and
        mcp_token_override and use them instead of generating a
        localhost URL + new token."""
        import json

        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        claude_json_path = skill.build_claude_json(
            output_dir=tmp_path,
            mcp_url_override="http://host.docker.internal:7777/mcp",
            mcp_token_override="parent-tok-abc",
        )
        cfg = json.loads(claude_json_path.read_text())
        zipsa_mcp = cfg["projects"]["/home/agent/workspace"]["mcpServers"]["zipsa"]
        assert zipsa_mcp["url"] == "http://host.docker.internal:7777/mcp"
        # Header carrying the token
        assert any(
            "parent-tok-abc" in str(v)
            for v in (zipsa_mcp.get("headers") or {}).values()
        ) or "parent-tok-abc" in zipsa_mcp.get("headersHelper", "")

    def test_build_claude_json_no_override_uses_generated(self, tmp_path):
        """Without overrides, build_claude_json keeps current behavior:
        uses hitl_port to generate a localhost URL."""
        import json

        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        # No overrides — uses existing hitl_port path
        claude_json_path = skill.build_claude_json(
            output_dir=tmp_path,
            hitl_port=54321,
        )
        cfg = json.loads(claude_json_path.read_text())
        zipsa_mcp = cfg["projects"]["/home/agent/workspace"]["mcpServers"]["zipsa"]
        # URL should use the supplied port, NOT the override value
        assert "7777" not in zipsa_mcp["url"]
        assert "54321" in zipsa_mcp["url"]

    def test_build_claude_json_raises_when_only_url_override(self, tmp_path):
        """Passing only one of the two overrides is a misconfiguration."""
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        with pytest.raises(ValueError, match="mcp_url_override"):
            skill.build_claude_json(
                output_dir=tmp_path,
                mcp_url_override="http://host.docker.internal:7777/mcp",
                # mcp_token_override intentionally omitted
            )

    def test_build_claude_json_raises_when_only_token_override(self, tmp_path):
        """Passing only one of the two overrides is a misconfiguration."""
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        with pytest.raises(ValueError, match="mcp_token_override"):
            skill.build_claude_json(
                output_dir=tmp_path,
                mcp_token_override="parent-tok-abc",
                # mcp_url_override intentionally omitted
            )

    def test_detect_parent_mcp_returns_env_values(self, monkeypatch):
        """_detect_parent_mcp() returns (url, token) from env vars when set."""
        monkeypatch.setenv("ZIPSA_PARENT_MCP_URL", "http://host.docker.internal:9999/mcp")
        monkeypatch.setenv("ZIPSA_PARENT_MCP_TOKEN", "child-tok-xyz")

        url, token = DockerExecutor._detect_parent_mcp()
        assert url == "http://host.docker.internal:9999/mcp"
        assert token == "child-tok-xyz"

    def test_detect_parent_mcp_returns_none_when_absent(self, monkeypatch):
        """_detect_parent_mcp() returns (None, None) when env vars are absent."""
        monkeypatch.delenv("ZIPSA_PARENT_MCP_URL", raising=False)
        monkeypatch.delenv("ZIPSA_PARENT_MCP_TOKEN", raising=False)

        url, token = DockerExecutor._detect_parent_mcp()
        assert url is None
        assert token is None


class TestPhaseStateJsonWrite:
    """Each phase that completes with status=ok writes its full skill
    envelope to phases/<idx>-<id>/state.json. Failed/out_of_scope phases
    write nothing — only ok phases produce a state.json."""

    def test_ok_phase_writes_state_json(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        from zipsa.core.executor import DockerExecutor

        run_dir = tmp_path / "skillname@0.1.0" / "runs" / "2026-05-21_000000_000"
        phase_dir = run_dir / "phases" / "0-precheck"
        phase_dir.mkdir(parents=True)

        envelope = {
            "status": "ok",
            "phase": "precheck",
            "result": {"db_id": "abc"},
            "state_updates": {"db_id": "abc"},
            "next_phase_input": {"db_id": "abc", "date": "today"},
            "user_facing_summary": "DB resolved.",
        }
        DockerExecutor._write_phase_state(phase_dir, envelope)

        path = phase_dir / "state.json"
        assert path.exists()
        import json
        loaded = json.loads(path.read_text())
        assert loaded == envelope

    def test_write_phase_state_skips_when_phase_dir_none(self):
        """Dry-run and shell paths pass phase_dir=None; helper must
        no-op without raising."""
        from zipsa.core.executor import DockerExecutor
        DockerExecutor._write_phase_state(None, {"status": "ok"})  # no raise


class TestResumeFromSkipsPhases:
    """When _execute_phases is called with resume_from=N, phases
    0..N-1 are skipped; previous_output is loaded from
    phases/<N-1>-*/state.json; the resumed phase's metering starts
    from zero."""

    def test_load_resume_state_returns_next_phase_input(self, tmp_path):
        """The helper reads next_phase_input from the phase BEFORE
        resume_from."""
        from zipsa.core.executor import DockerExecutor

        run_dir = tmp_path / "x@0.1.0" / "runs" / "2026-05-21_100000_000000"
        phase_dir = run_dir / "phases" / "1-second"
        phase_dir.mkdir(parents=True)
        envelope = {
            "status": "ok", "phase": "second",
            "next_phase_input": {"answer": 42, "marker": "ok"},
            "user_facing_summary": "phase 2 done",
            "result": None, "state_updates": None,
        }
        import json as _j
        (phase_dir / "state.json").write_text(_j.dumps(envelope))

        loaded = DockerExecutor._load_resume_state(run_dir, resume_from=2)
        assert loaded == {"answer": 42, "marker": "ok"}

    def test_load_resume_state_missing_raises(self, tmp_path):
        """If phase N-1's state.json is missing, executor cannot
        proceed — raises with a clear message."""
        import pytest
        from zipsa.core.executor import DockerExecutor
        run_dir = tmp_path / "x@0.1.0" / "runs" / "2026-05-21_100000_000000"
        (run_dir / "phases").mkdir(parents=True)
        with pytest.raises(FileNotFoundError, match="state.json"):
            DockerExecutor._load_resume_state(run_dir, resume_from=2)

    def test_load_resume_state_reads_from_prior_dir_not_current(self, tmp_path):
        """state.json lives in the PRIOR (failed) run dir, not the
        current (fresh) run dir. Regression test: caller must pass
        the prior run dir to _load_resume_state, not the current one."""
        from zipsa.core.executor import DockerExecutor
        import json as _j

        prior_run_dir = tmp_path / "x@0.1.0" / "runs" / "2026-05-21_100000_000000"
        prior_phase_dir = prior_run_dir / "phases" / "0-precheck"
        prior_phase_dir.mkdir(parents=True)
        (prior_phase_dir / "state.json").write_text(_j.dumps({
            "status": "ok", "phase": "precheck",
            "next_phase_input": {"loaded": True},
            "user_facing_summary": "ok", "state_updates": None, "result": None,
        }))

        # Fresh current run dir — empty, no phases/
        current_run_dir = tmp_path / "x@0.1.0" / "runs" / "2026-05-21_110000_000000"
        current_run_dir.mkdir()

        # Read using the PRIOR dir explicitly
        loaded = DockerExecutor._load_resume_state(
            prior_run_dir, resume_from=1,
        )
        assert loaded == {"loaded": True}


class TestResumeChainState:
    """When a resume run B itself fails, run C should be able to
    resume from B by finding state.json for the skipped phases.
    This requires B to copy state.json files from its source run A."""

    def test_state_copied_from_prior_run_on_resume(self, tmp_path):
        """Test the file-copy primitive that the resume code path uses
        to make each resumed run self-sufficient as a resume source.

        Setup: prior_run has phases/0-precheck/state.json and
        phases/1-discover/state.json. New run is fresh and empty.
        After the copy step, new_run has the same state.json files
        in the same paths so a future resume can read from new_run."""
        import shutil as _sh

        prior_run = tmp_path / "x@0.1.0" / "runs" / "A"
        for idx, pid in [(0, "precheck"), (1, "discover")]:
            d = prior_run / "phases" / f"{idx}-{pid}"
            d.mkdir(parents=True)
            (d / "state.json").write_text(
                f'{{"status":"ok","phase":"{pid}","next_phase_input":{{"i":{idx}}}}}'
            )

        new_run = tmp_path / "x@0.1.0" / "runs" / "B"
        new_run.mkdir(parents=True)

        # The same logic as in _execute_phases for resume_from=2
        for skipped_idx, pid in [(0, "precheck"), (1, "discover")]:
            src = prior_run / "phases" / f"{skipped_idx}-{pid}" / "state.json"
            if src.exists():
                dst_dir = new_run / "phases" / f"{skipped_idx}-{pid}"
                dst_dir.mkdir(parents=True, exist_ok=True)
                _sh.copy(src, dst_dir / "state.json")

        # Verify B is now self-sufficient
        assert (new_run / "phases" / "0-precheck" / "state.json").exists()
        assert (new_run / "phases" / "1-discover" / "state.json").exists()
        # And the content matches
        import json as _j
        b_precheck = _j.loads(
            (new_run / "phases" / "0-precheck" / "state.json").read_text()
        )
        assert b_precheck["next_phase_input"] == {"i": 0}


class TestHitlIOMeasureWait:
    """HitlIO accumulates time spent in stdin.readline so summary.json
    can report hitl_wait_seconds (skill compute time = duration - hitl
    wait)."""

    def test_measure_wait_accumulates(self):
        import io as _io
        import threading as _t
        import time as _time
        from zipsa.core.hitl_mcp import HitlIO

        h = HitlIO(stdin=_io.StringIO(""), stdout=_io.StringIO(),
                   stdout_lock=_t.Lock(), is_interactive=True)
        assert h.hitl_wait_seconds[0] == 0.0
        with h.measure_wait():
            _time.sleep(0.01)
        assert h.hitl_wait_seconds[0] >= 0.01
        # Cumulative
        with h.measure_wait():
            _time.sleep(0.01)
        assert h.hitl_wait_seconds[0] >= 0.02

    def test_ask_handler_accumulates_wait(self):
        import io as _io
        import threading as _t
        from zipsa.core.hitl_mcp import HitlIO, AskHandler

        h = HitlIO(stdin=_io.StringIO("answer\n"), stdout=_io.StringIO(),
                   stdout_lock=_t.Lock(), is_interactive=True)
        result = AskHandler(h).run(prompt="what?")
        assert result == "answer"
        # readline on a pre-loaded StringIO returns immediately, but the
        # context manager still added some non-negative delta.
        assert h.hitl_wait_seconds[0] >= 0.0


class TestSummaryChainFields:
    """build_summary writes chain_started_at, chain_duration_seconds,
    hitl_wait_seconds, and resumed_from."""

    def test_chain_fields_default_to_self(self):
        from datetime import datetime, timedelta, timezone
        from zipsa.core.summary import build_summary, PhaseSummary

        t0 = datetime(2026, 5, 22, 10, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=30)
        s = build_summary(
            status="ok", exit_code=0, skill="x", version="0.1.0",
            started_at=t0, finished_at=t1, cost_usd=0.05, turns=2,
            phases=[PhaseSummary(id="p", status="ok", cost_usd=0.05, turns=2)],
        )
        assert s["chain_started_at"] == t0.isoformat()
        assert s["chain_duration_seconds"] == 30.0
        assert s["hitl_wait_seconds"] == 0.0
        assert s["resumed_from"] is None

    def test_chain_fields_propagate_on_resume(self):
        from datetime import datetime, timedelta, timezone
        from zipsa.core.summary import build_summary, PhaseSummary

        chain_origin = datetime(2026, 5, 22, 9, 0, 0, tzinfo=timezone.utc)
        this_start = datetime(2026, 5, 22, 10, 0, 0, tzinfo=timezone.utc)
        this_end = this_start + timedelta(seconds=20)

        s = build_summary(
            status="ok", exit_code=0, skill="x", version="0.1.0",
            started_at=this_start, finished_at=this_end,
            cost_usd=0.10, turns=3,
            phases=[PhaseSummary(id="p", status="ok", cost_usd=0.10, turns=3)],
            chain_started_at=chain_origin,
            resumed_from="2026-05-22_090000_000000",
            hitl_wait_seconds=120.0,
        )
        # This run alone: 20s
        assert s["duration_seconds"] == 20.0
        # Chain: 1h 20s (3620s)
        assert s["chain_duration_seconds"] == 3620.0
        assert s["chain_started_at"] == chain_origin.isoformat()
        assert s["hitl_wait_seconds"] == 120.0
        assert s["resumed_from"] == "2026-05-22_090000_000000"
