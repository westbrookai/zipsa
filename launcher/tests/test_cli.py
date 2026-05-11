"""Tests for CLI commands."""

import json
import os
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, patch as _patch
import pytest
from typer.testing import CliRunner
from zipsa.cli import app, _find_run_dir
from zipsa.paths import SkillNotInstalledError


runner = CliRunner()


class TestRunCommand:
    """Test run command."""

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_basic(self, mock_skill_cls, mock_executor_cls, mock_resolve):
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

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_with_runtime(self, mock_skill_cls, mock_executor_cls, mock_resolve):
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

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_with_env_vars(self, mock_skill_cls, mock_executor_cls, mock_resolve):
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

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_dry_run(self, mock_skill_cls, mock_executor_cls, mock_resolve):
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

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_with_mcp_debug(self, mock_skill_cls, mock_executor_cls, mock_resolve):
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

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.Skill")
    def test_run_invalid_skill(self, mock_skill_cls, mock_resolve):
        """Run with invalid skill should fail."""
        mock_skill_cls.load.side_effect = FileNotFoundError("Not found")

        result = runner.invoke(app, ["run", "nonexistent", "input"])

        assert result.exit_code != 0


class TestValidateCommand:
    """Test validate command."""

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.Skill")
    def test_validate_valid_skill(self, mock_skill_cls, mock_resolve):
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

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.Skill")
    def test_validate_invalid_skill(self, mock_skill_cls, mock_resolve):
        """Validate invalid skill."""
        from pydantic import ValidationError

        mock_skill_cls.load.side_effect = ValidationError.from_exception_data(
            "SkillManifest", [{"type": "missing", "loc": ("spec",), "msg": ""}]
        )

        result = runner.invoke(app, ["validate", "invalid-skill"])

        assert result.exit_code != 0


class TestListCommand:
    """Test list command (installed skills with stats)."""

    def test_list_shows_installed_skills(self, tmp_path):
        """list shows skills from ZIPSA_HOME/skills/."""
        import json as _json
        import yaml
        zipsa_home = tmp_path / ".zipsa"
        skill_dir = zipsa_home / "skills" / "daily-progress"
        skill_dir.mkdir(parents=True)
        (skill_dir / "manifest.yaml").write_text(yaml.dump({
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "daily-progress", "version": "0.1.0"},
            "spec": {"purpose": "Test", "instructions": "./SKILL.md",
                     "mcp": [], "tools": {"builtin": []}},
        }))
        (skill_dir / "SKILL.md").write_text("# Test")
        (skill_dir / "_install.json").write_text(_json.dumps({
            "source": "github:westbrookai/zipsa/skills/daily-progress",
            "ref": "main", "commit_sha": "abc123", "version": "0.1.0",
            "type": "github", "installed_at": "2026-05-11T00:00:00+00:00",
        }))

        with patch.dict(os.environ, {"ZIPSA_HOME": str(zipsa_home)}):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "daily-progress" in result.output
        assert "0.1.0" in result.output

    def test_list_empty_when_no_skills_installed(self, tmp_path):
        """list reports no installed skills."""
        zipsa_home = tmp_path / ".zipsa"
        zipsa_home.mkdir(parents=True)

        with patch.dict(os.environ, {"ZIPSA_HOME": str(zipsa_home)}):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "No installed skills" in result.output

    def test_list_shows_linked_label_for_link_type(self, tmp_path):
        """list shows 'linked' for link-type installs."""
        import json as _json
        import yaml
        zipsa_home = tmp_path / ".zipsa"
        original_dir = tmp_path / "original" / "hello-world"
        original_dir.mkdir(parents=True)
        (original_dir / "manifest.yaml").write_text(yaml.dump({
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "hello-world", "version": "0.1.0"},
            "spec": {"purpose": "Hi", "instructions": "./SKILL.md",
                     "mcp": [], "tools": {"builtin": []}},
        }))
        (original_dir / "SKILL.md").write_text("# Hi")
        (original_dir / "_install.json").write_text(_json.dumps({
            "source": "/some/local/path", "ref": "local",
            "version": "0.1.0", "type": "link",
            "installed_at": "2026-05-11T00:00:00+00:00",
        }))

        skills_dir_path = zipsa_home / "skills"
        skills_dir_path.mkdir(parents=True)
        link_path = skills_dir_path / "hello-world"
        link_path.symlink_to(original_dir)

        with patch.dict(os.environ, {"ZIPSA_HOME": str(zipsa_home)}):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "linked" in result.output.lower()

    def test_list_shows_run_stats(self, tmp_path):
        """list shows run count and success rate from metadata.json files."""
        import yaml
        zipsa_home = tmp_path / ".zipsa"
        skill_dir = zipsa_home / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "manifest.yaml").write_text(yaml.dump({
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "my-skill", "version": "0.1.0"},
            "spec": {"purpose": "Test", "instructions": "./SKILL.md",
                     "mcp": [], "tools": {"builtin": []}},
        }))
        (skill_dir / "SKILL.md").write_text("# Test")
        (skill_dir / "_install.json").write_text(json.dumps({
            "source": "github:test/repo", "ref": "main",
            "version": "0.1.0", "type": "github",
            "installed_at": "2026-05-11T00:00:00+00:00",
        }))

        # Create run history: 2 runs, 1 success, 1 error
        runs_dir = zipsa_home / "my-skill@0.1.0" / "runs"
        for run_id, is_error in [("2026-05-11_120000_00001", False), ("2026-05-11_120100_00002", True)]:
            rd = runs_dir / run_id
            rd.mkdir(parents=True)
            (rd / "metadata.json").write_text(json.dumps({
                "run_id": run_id, "skill_name": "my-skill",
                "skill_version": "0.1.0", "is_error": is_error,
            }))

        with patch.dict(os.environ, {"ZIPSA_HOME": str(zipsa_home)}):
            result = runner.invoke(app, ["list"])

        assert result.exit_code == 0
        assert "my-skill" in result.output
        assert "2 run" in result.output  # "2 runs"
        assert "50%" in result.output


class TestDiscoverCommand:
    """Test discover command (scan directory for skills)."""

    @patch("zipsa.cli.Skill")
    @patch("zipsa.cli.Path")
    def test_discover_skills(self, mock_path_cls, mock_skill_cls):
        """Discover skills in directory."""
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

        result = runner.invoke(app, ["discover", "."])

        assert result.exit_code == 0
        assert "skill-1" in result.stdout
        assert "skill-2" in result.stdout

    @patch("zipsa.cli.Path")
    def test_list_empty_directory(self, mock_path_cls):
        """Discover finds no skills in empty directory."""
        mock_path = Mock()
        mock_path.exists.return_value = True
        mock_path.is_dir.return_value = True
        mock_path.iterdir.return_value = []
        mock_path_cls.return_value = mock_path

        result = runner.invoke(app, ["discover", "."])

        assert result.exit_code == 0
        assert "no skills" in result.stdout.lower() or "0" in result.stdout

    def test_discover_lists_skills_in_directory(self, tmp_path):
        """discover scans a directory for skill manifests."""
        import yaml
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "manifest.yaml").write_text(yaml.dump({
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "my-skill", "version": "0.1.0"},
            "spec": {"purpose": "Test", "instructions": "./SKILL.md",
                     "mcp": [], "tools": {"builtin": []}},
        }))
        (skill_dir / "SKILL.md").write_text("# Test")

        result = runner.invoke(app, ["discover", str(tmp_path)])
        assert result.exit_code == 0
        assert "my-skill" in result.output

    def test_discover_no_skills_found(self, tmp_path):
        result = runner.invoke(app, ["discover", str(tmp_path)])
        assert result.exit_code == 0
        assert "No skills found" in result.output

    def test_discover_nonexistent_dir_exits_nonzero(self, tmp_path):
        """discover exits 1 for non-existent directory."""
        result = runner.invoke(app, ["discover", str(tmp_path / "nonexistent")])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_discover_file_arg_exits_nonzero(self, tmp_path):
        """discover exits 1 when path is a file, not a directory."""
        file_path = tmp_path / "a_file.txt"
        file_path.write_text("content")
        result = runner.invoke(app, ["discover", str(file_path)])
        assert result.exit_code == 1


class TestRunOutputMode:
    """Test --output-mode option on run command."""

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_defaults_to_pretty_mode(self, mock_skill_cls, mock_executor_cls, mock_resolve):
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

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_answer_mode_prints_only_text(self, mock_skill_cls, mock_executor_cls, mock_resolve):
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

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.DockerExecutor")
    @patch("zipsa.cli.Skill")
    def test_run_json_mode_prints_raw_json(self, mock_skill_cls, mock_executor_cls, mock_resolve):
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



class TestFindRunDir:
    """Test _find_run_dir helper for view command run selection."""

    def test_returns_latest_run_when_no_id_given(self, tmp_path):
        runs = tmp_path / "runs"
        older = runs / "2026-05-07_100000_00000"
        newer = runs / "2026-05-08_120000_00000"
        older.mkdir(parents=True)
        newer.mkdir(parents=True)
        (older / "output.jsonl").touch()
        (newer / "output.jsonl").touch()

        result = _find_run_dir(runs)

        assert result == newer

    def test_raises_when_no_runs_exist(self, tmp_path):
        runs = tmp_path / "runs"
        with pytest.raises(ValueError, match="No runs found"):
            _find_run_dir(runs)

    def test_prefix_match_returns_correct_run(self, tmp_path):
        runs = tmp_path / "runs"
        run = runs / "2026-05-08_103540_69123"
        run.mkdir(parents=True)
        (run / "output.jsonl").touch()

        result = _find_run_dir(runs, run_id="2026-05-08_103540")

        assert result == run

    def test_raises_on_ambiguous_prefix(self, tmp_path):
        runs = tmp_path / "runs"
        (runs / "2026-05-08_103540_11111").mkdir(parents=True)
        (runs / "2026-05-08_103540_22222").mkdir(parents=True)

        with pytest.raises(ValueError, match="Ambiguous"):
            _find_run_dir(runs, run_id="2026-05-08_103540")

    def test_raises_when_prefix_matches_nothing(self, tmp_path):
        runs = tmp_path / "runs"
        (runs / "2026-05-08_103540_11111").mkdir(parents=True)

        with pytest.raises(ValueError, match="No run matching"):
            _find_run_dir(runs, run_id="2026-05-09")

    def test_returns_dir_even_when_output_jsonl_missing(self, tmp_path):
        runs = tmp_path / "runs"
        run = runs / "2026-05-08_103540_11111"
        run.mkdir(parents=True)
        # no output.jsonl — _find_run_dir just returns the directory
        # the CLI layer handles missing output.jsonl separately

        result = _find_run_dir(runs)
        assert result == run

    def test_ignores_non_timestamp_directories(self, tmp_path):
        runs = tmp_path / "runs"
        run = runs / "2026-05-08_120000_00000"
        run.mkdir(parents=True)
        (runs / "tmp").mkdir()  # non-timestamp directory

        result = _find_run_dir(runs)
        assert result == run


class TestViewCommand:
    """Test view command for replaying past skill runs."""

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.Skill")
    def test_view_replays_latest_run(self, mock_skill_cls, mock_resolve, tmp_path):
        """view should read output.jsonl from latest run and render it."""
        mock_skill = Mock()
        mock_skill.name = "daily-progress"
        mock_skill.manifest.metadata.version = "0.1.0"
        mock_skill_cls.load.return_value = mock_skill

        # Create a fake run directory
        run_dir = tmp_path / ".zipsa" / "daily-progress@0.1.0" / "runs" / "2026-05-08_120000_00000"
        run_dir.mkdir(parents=True)
        output_jsonl = run_dir / "output.jsonl"
        output_jsonl.write_text(
            '{"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}}\n'
        )

        with patch("zipsa.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(app, ["view", "test-skill"])

        assert result.exit_code == 0
        assert "Done." in result.output

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.Skill")
    def test_view_errors_when_no_runs(self, mock_skill_cls, mock_resolve, tmp_path):
        """view should exit with error when no runs exist."""
        mock_skill = Mock()
        mock_skill.name = "daily-progress"
        mock_skill.manifest.metadata.version = "0.1.0"
        mock_skill_cls.load.return_value = mock_skill

        with patch("zipsa.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(app, ["view", "test-skill"])

        assert result.exit_code == 1
        assert "No runs found" in result.stderr

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.Skill")
    def test_view_specific_run_by_prefix(self, mock_skill_cls, mock_resolve, tmp_path):
        """view with run-id prefix should replay that specific run."""
        mock_skill = Mock()
        mock_skill.name = "daily-progress"
        mock_skill.manifest.metadata.version = "0.1.0"
        mock_skill_cls.load.return_value = mock_skill

        run_dir = tmp_path / ".zipsa" / "daily-progress@0.1.0" / "runs" / "2026-05-08_103540_69123"
        run_dir.mkdir(parents=True)
        (run_dir / "output.jsonl").write_text(
            '{"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello."}]}}\n'
        )

        with patch("zipsa.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(app, ["view", "test-skill", "2026-05-08_103540"])

        assert result.exit_code == 0
        assert "Hello." in result.output

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.Skill")
    def test_view_errors_when_output_jsonl_missing(self, mock_skill_cls, mock_resolve, tmp_path):
        """view should exit with error when output.jsonl is missing."""
        mock_skill = Mock()
        mock_skill.name = "daily-progress"
        mock_skill.manifest.metadata.version = "0.1.0"
        mock_skill_cls.load.return_value = mock_skill

        run_dir = tmp_path / ".zipsa" / "daily-progress@0.1.0" / "runs" / "2026-05-08_120000_00000"
        run_dir.mkdir(parents=True)
        # no output.jsonl

        with patch("zipsa.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(app, ["view", "test-skill"])

        assert result.exit_code == 1
        assert "output.jsonl" in result.stderr


class TestConnectCommand:
    """Test connect command."""

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.OAuthManager")
    @patch("zipsa.cli.Skill")
    def test_connect_all_oauth_servers(self, mock_skill_cls, mock_oauth_manager_cls, mock_resolve):
        """connect authorizes all oauth2 servers in skill."""
        from zipsa.core.models import MCPServerHTTP, MCPServerAuth

        mock_skill = Mock()
        mock_skill.manifest.spec.mcp = [
            MCPServerHTTP(
                name="notion",
                type="http",
                url="https://mcp.notion.com/mcp",
                auth=MCPServerAuth(type="oauth2"),
            )
        ]
        mock_skill_cls.load.return_value = mock_skill

        mock_manager = Mock()
        mock_manager.ensure_credentials.return_value = "tok-123"
        mock_oauth_manager_cls.return_value = mock_manager

        result = runner.invoke(app, ["connect", "daily-progress"])

        assert result.exit_code == 0
        mock_manager.ensure_credentials.assert_called_once_with(
            "notion", "https://mcp.notion.com/mcp"
        )
        assert "notion" in result.stdout

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.OAuthManager")
    @patch("zipsa.cli.Skill")
    def test_connect_specific_server(self, mock_skill_cls, mock_oauth_manager_cls, mock_resolve):
        """connect with server_name only authorizes that server."""
        from zipsa.core.models import MCPServerHTTP, MCPServerAuth

        mock_skill = Mock()
        mock_skill.manifest.spec.mcp = [
            MCPServerHTTP(
                name="notion",
                type="http",
                url="https://mcp.notion.com/mcp",
                auth=MCPServerAuth(type="oauth2"),
            ),
            MCPServerHTTP(
                name="github",
                type="http",
                url="https://api.github.com/mcp",
                auth=MCPServerAuth(type="oauth2"),
            ),
        ]
        mock_skill_cls.load.return_value = mock_skill

        mock_manager = Mock()
        mock_manager.ensure_credentials.return_value = "tok"
        mock_oauth_manager_cls.return_value = mock_manager

        result = runner.invoke(app, ["connect", "daily-progress", "notion"])

        assert result.exit_code == 0
        calls = mock_manager.ensure_credentials.call_args_list
        assert len(calls) == 1
        assert calls[0][0][0] == "notion"

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.Skill")
    def test_connect_no_oauth_servers(self, mock_skill_cls, mock_resolve):
        """connect reports nothing to do if no oauth2 servers."""
        mock_skill = Mock()
        mock_skill.manifest.spec.mcp = []
        mock_skill_cls.load.return_value = mock_skill

        result = runner.invoke(app, ["connect", "daily-progress"])

        assert result.exit_code == 0
        assert "no" in result.stdout.lower() or "0" in result.stdout

    @patch("zipsa.cli.resolve_skill", return_value=Path("/fake/skill"))
    @patch("zipsa.cli.Skill")
    def test_connect_unknown_server_name_exits_nonzero(self, mock_skill_cls, mock_resolve):
        """connect with unknown server_name exits non-zero."""
        from zipsa.core.models import MCPServerHTTP, MCPServerAuth

        mock_skill = Mock()
        mock_skill.manifest.spec.mcp = [
            MCPServerHTTP(
                name="notion",
                type="http",
                url="https://mcp.notion.com/mcp",
                auth=MCPServerAuth(type="oauth2"),
            )
        ]
        mock_skill_cls.load.return_value = mock_skill

        result = runner.invoke(app, ["connect", "daily-progress", "nonexistent"])

        assert result.exit_code != 0


class TestInstallCommand:
    """Test install command."""

    @patch("zipsa.cli.install_from_github")
    def test_install_github_source(self, mock_install):
        """install command with GitHub source calls install_from_github."""
        mock_install.return_value = "daily-progress"
        result = runner.invoke(app, ["install", "westbrookai/zipsa/skills/daily-progress"])
        assert result.exit_code == 0
        mock_install.assert_called_once_with("westbrookai/zipsa/skills/daily-progress", force=False)
        assert "daily-progress" in result.stdout

    @patch("zipsa.cli.install_from_github")
    def test_install_with_force_flag(self, mock_install):
        """install --force passes force=True."""
        mock_install.return_value = "daily-progress"
        result = runner.invoke(app, ["install", "--force", "westbrookai/zipsa/skills/daily-progress"])
        assert result.exit_code == 0
        mock_install.assert_called_once_with("westbrookai/zipsa/skills/daily-progress", force=True)

    def test_install_no_args_exits_nonzero(self):
        """install with no arguments exits 1."""
        result = runner.invoke(app, ["install"])
        assert result.exit_code == 1

    @patch("zipsa.cli.install_from_github")
    def test_install_runtime_error_exits_nonzero(self, mock_install):
        """install exits 1 on RuntimeError (e.g., HTTP 403)."""
        mock_install.side_effect = RuntimeError("Failed to download: HTTP 403 Forbidden")
        result = runner.invoke(app, ["install", "westbrookai/private-repo"])
        assert result.exit_code == 1

    @patch("zipsa.cli.install_local")
    def test_install_mutually_exclusive_flags_exits_nonzero(self, mock_install):
        """install exits 1 when both --path and --link are provided."""
        result = runner.invoke(app, ["install", "--path", "./a", "--link", "./b"])
        assert result.exit_code == 1
        mock_install.assert_not_called()

    @patch("zipsa.cli.install_local")
    def test_install_with_path_flag(self, mock_install):
        """install --path calls install_local with link=False."""
        mock_install.return_value = "my-skill"
        result = runner.invoke(app, ["install", "--path", "./my-skill"])
        assert result.exit_code == 0
        mock_install.assert_called_once_with("./my-skill", link=False, force=False)

    @patch("zipsa.cli.install_local")
    def test_install_with_link_flag(self, mock_install):
        """install --link calls install_local with link=True."""
        mock_install.return_value = "my-skill"
        result = runner.invoke(app, ["install", "--link", "./my-skill"])
        assert result.exit_code == 0
        mock_install.assert_called_once_with("./my-skill", link=True, force=False)

    @patch("zipsa.cli.install_from_github")
    def test_install_file_exists_error_exits_nonzero(self, mock_install):
        """install exits 1 when skill already installed."""
        mock_install.side_effect = FileExistsError("already installed")
        result = runner.invoke(app, ["install", "westbrookai/zipsa/skills/daily-progress"])
        assert result.exit_code == 1
        assert "already installed" in result.output

    @patch("zipsa.cli.install_from_github")
    def test_install_file_not_found_exits_nonzero(self, mock_install):
        """install exits 1 when repo not found."""
        mock_install.side_effect = FileNotFoundError("not found")
        result = runner.invoke(app, ["install", "westbrookai/zipsa/skills/daily-progress"])
        assert result.exit_code == 1


class TestUninstallCommand:
    """Test uninstall command."""

    def test_uninstall_removes_skill_dir(self, tmp_path):
        """uninstall removes ~/.zipsa/skills/<name>/."""
        skill_dir = tmp_path / "skills" / "daily-progress"
        skill_dir.mkdir(parents=True)
        (skill_dir / "_install.json").write_text('{"type": "github"}')

        with patch("zipsa.cli.installed_skill_dir", return_value=skill_dir):
            result = runner.invoke(app, ["uninstall", "daily-progress"])

        assert result.exit_code == 0
        assert not skill_dir.exists()
        assert "daily-progress" in result.output

    def test_uninstall_removes_symlink_only_for_linked_skills(self, tmp_path):
        """uninstall for linked skill removes symlink, not original."""
        original = tmp_path / "original"
        original.mkdir()
        link_path = tmp_path / "skills" / "my-skill"
        link_path.parent.mkdir(parents=True)
        link_path.symlink_to(original)

        with patch("zipsa.cli.installed_skill_dir", return_value=link_path):
            result = runner.invoke(app, ["uninstall", "my-skill"])

        assert result.exit_code == 0
        assert not link_path.exists()
        assert original.exists()

    def test_uninstall_not_installed_exits_nonzero(self, tmp_path):
        """uninstall exits 1 when skill is not installed."""
        non_existent = tmp_path / "skills" / "ghost"
        with patch("zipsa.cli.installed_skill_dir", return_value=non_existent):
            result = runner.invoke(app, ["uninstall", "ghost"])
        assert result.exit_code == 1
        assert "not installed" in result.output

    def test_uninstall_removes_dangling_symlink(self, tmp_path):
        """uninstall removes a dangling symlink (original target deleted)."""
        gone_target = tmp_path / "gone"
        link_path = tmp_path / "skills" / "my-skill"
        link_path.parent.mkdir(parents=True)
        link_path.symlink_to(gone_target)  # dangling symlink
        assert not link_path.exists()  # dangling: target gone
        assert link_path.is_symlink()  # but symlink itself exists

        with patch("zipsa.cli.installed_skill_dir", return_value=link_path):
            result = runner.invoke(app, ["uninstall", "my-skill"])

        assert result.exit_code == 0
        assert not link_path.is_symlink()


class TestNameResolution:
    """Verify all commands reject unknown skill names with exit code 1."""

    @patch("zipsa.cli.resolve_skill")
    def test_run_exits_when_not_installed(self, mock_resolve):
        mock_resolve.side_effect = SkillNotInstalledError("Skill 'ghost' not found.")
        result = runner.invoke(app, ["run", "ghost", "hello"])
        assert result.exit_code == 1
        assert "ghost" in result.output

    @patch("zipsa.cli.resolve_skill")
    def test_validate_exits_when_not_installed(self, mock_resolve):
        mock_resolve.side_effect = SkillNotInstalledError("Skill 'ghost' not found.")
        result = runner.invoke(app, ["validate", "ghost"])
        assert result.exit_code == 1
        assert "ghost" in result.output

    @patch("zipsa.cli.resolve_skill")
    def test_view_exits_when_not_installed(self, mock_resolve):
        mock_resolve.side_effect = SkillNotInstalledError("Skill 'ghost' not found.")
        result = runner.invoke(app, ["view", "ghost"])
        assert result.exit_code == 1
        assert "ghost" in result.output

    @patch("zipsa.cli.resolve_skill")
    def test_connect_exits_when_not_installed(self, mock_resolve):
        mock_resolve.side_effect = SkillNotInstalledError("Skill 'ghost' not found.")
        result = runner.invoke(app, ["connect", "ghost"])
        assert result.exit_code == 1
