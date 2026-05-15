"""Tests for Skill loader."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch
from zipsa.core.skill import Skill
from zipsa.paths import zipsa_home


class TestSkillLoad:
    """Test Skill loading from various sources."""

    def test_load_from_directory(self):
        """Load skill from directory path."""
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        assert skill.name == "test-skill"
        assert skill.manifest.metadata.version == "1.0.0"

    def test_load_from_manifest_file(self):
        """Load skill from direct manifest.yaml path."""
        manifest_path = Path(__file__).parent / "fixtures/skills/test-skill/manifest.yaml"
        skill = Skill.load(manifest_path)

        assert skill.name == "test-skill"

    def test_load_nonexistent_manifest(self):
        """Loading nonexistent manifest should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            Skill.load("/nonexistent/path")

    def test_skill_dir_is_absolute_when_loaded_with_relative_path(self, tmp_path, monkeypatch):
        """skill_dir must be absolute even when loaded with a relative path."""
        # Set up a skill in tmp_path
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        import shutil
        target = tmp_path / "test-skill"
        shutil.copytree(skill_dir, target)

        # Change cwd so relative path resolves correctly
        monkeypatch.chdir(tmp_path)
        skill = Skill.load("test-skill")

        assert skill.skill_dir.is_absolute()

    def test_skill_dir_attribute(self):
        """Skill should store its directory path."""
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        assert skill.skill_dir == skill_dir


class TestSkillInstructions:
    """Test SKILL.md loading."""

    def test_instructions_lazy_load(self):
        """Instructions should be loaded on first access."""
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        # Should not be loaded yet
        assert skill._instructions is None

        # Access should trigger load
        instructions = skill.instructions
        assert "Test Skill" in instructions
        assert "Hello from test skill" in instructions

        # Should be cached
        assert skill._instructions is not None

    def test_instructions_caching(self):
        """Multiple accesses should use cached value."""
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        instructions1 = skill.instructions
        instructions2 = skill.instructions

        # Should be same object (cached)
        assert instructions1 is instructions2


class TestMCPConfig:
    """Test MCP config generation."""

    def test_build_mcp_config_empty(self):
        """Skill without MCP servers should generate empty config."""
        manifest_path = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(manifest_path)

        config = skill.build_mcp_config()
        assert config == {"mcpServers": {}}

    def test_build_mcp_config_stdio(self):
        """Stdio MCP server should be in config (passthrough, no auto-appended path)."""
        manifest_path = Path(__file__).parent / "fixtures/manifests/with-mcp.yaml"
        skill = Skill.load(manifest_path)

        config = skill.build_mcp_config()

        assert "filesystem" in config["mcpServers"]
        assert config["mcpServers"]["filesystem"]["command"] == "npx"
        assert config["mcpServers"]["filesystem"]["args"] == [
            "-y",
            "@modelcontextprotocol/server-filesystem",
        ]

    def test_build_mcp_config_http(self):
        """HTTP MCP server should be in config."""
        manifest_path = Path(__file__).parent / "fixtures/manifests/with-mcp.yaml"
        skill = Skill.load(manifest_path)

        config = skill.build_mcp_config()

        assert "notion" in config["mcpServers"]
        assert config["mcpServers"]["notion"]["type"] == "http"
        assert config["mcpServers"]["notion"]["url"] == "https://mcp.notion.com/mcp"


class TestAllowedTools:
    """Test allowed tools string generation."""

    def test_get_allowed_tools_empty(self):
        """Skill without tools should return empty string."""
        manifest_path = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(manifest_path)

        tools = skill.get_allowed_tools()
        assert tools == ""

    def test_get_allowed_tools_builtin_only(self):
        """Skill with only builtin tools."""
        skill_dir = Path(__file__).parent / "fixtures/skills/test-skill"
        skill = Skill.load(skill_dir)

        tools = skill.get_allowed_tools()
        assert tools == "Read,Write"

    def test_get_allowed_tools_mcp_format(self):
        """MCP server allowed_tools become mcp__server__method format."""
        manifest_path = Path(__file__).parent / "fixtures/manifests/with-mcp.yaml"
        skill = Skill.load(manifest_path)

        tools = skill.get_allowed_tools()

        # Should have builtin tools
        assert "WebFetch" in tools
        assert "Bash(*)" in tools

        # MCP tools from server.allowed_tools
        assert "mcp__filesystem__read_file" in tools
        assert "mcp__notion__create-page" in tools

    def test_get_allowed_tools_order(self):
        """Tools should be in order: builtin first, then mcp."""
        manifest_path = Path(__file__).parent / "fixtures/manifests/with-mcp.yaml"
        skill = Skill.load(manifest_path)

        tools = skill.get_allowed_tools().split(",")

        assert tools[0] == "WebFetch"
        assert tools[1] == "Bash(*)"
        assert tools[2].startswith("mcp__")

    def test_get_allowed_tools_empty_allowed_tools(self):
        """Server with no allowed_tools contributes nothing to the list."""
        manifest_path = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(manifest_path)

        tools = skill.get_allowed_tools()
        assert tools == ""


class TestClaudeJson:
    """Test .claude.json generation."""

    def test_build_claude_json_creates_file(self, tmp_path):
        """Should create .claude.json file in output_dir."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        output_dir = tmp_path / "skill-data"

        manifest = {
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {
                "purpose": "Test",
                "instructions": "./SKILL.md",
                "mcp": [],
                "tools": {"builtin": [], "mcp": []},
            },
        }

        import yaml
        (skill_dir / "manifest.yaml").write_text(yaml.dump(manifest))
        (skill_dir / "SKILL.md").write_text("Test instructions")

        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=output_dir)

        assert claude_json_path.exists()
        assert claude_json_path == output_dir / ".claude.json"
        assert (output_dir / ".claude.json.org").exists()
        assert claude_json_path.read_text() == (output_dir / ".claude.json.org").read_text()

    def test_build_claude_json_structure(self, tmp_path):
        """Should have correct structure with onboarding and projects."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()

        manifest = {
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {
                "purpose": "Test",
                "instructions": "./SKILL.md",
                "mcp": [],
                "tools": {"builtin": [], "mcp": []},
            },
        }

        import yaml
        (skill_dir / "manifest.yaml").write_text(yaml.dump(manifest))
        (skill_dir / "SKILL.md").write_text("Test instructions")

        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path / "skill-data")

        # Parse and check structure
        config = json.loads(claude_json_path.read_text())

        assert config["hasCompletedOnboarding"] is True
        assert "/home/agent/workspace" in config["projects"]
        assert config["projects"]["/home/agent/workspace"]["hasTrustDialogAccepted"] is True
        assert "mcpServers" in config["projects"]["/home/agent/workspace"]

    def test_build_claude_json_includes_pretooluse_hook(self, tmp_path):
        """The generated .claude.json must register the zipsa PreToolUse hook."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()

        manifest = {
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {"purpose": "Test", "instructions": "./SKILL.md"},
        }
        import yaml
        (skill_dir / "manifest.yaml").write_text(yaml.dump(manifest))
        (skill_dir / "SKILL.md").write_text("Test")

        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path / "skill-data")
        config = json.loads(claude_json_path.read_text())

        project = config["projects"]["/home/agent/workspace"]
        assert "hooks" in project
        pretool_hooks = project["hooks"]["PreToolUse"]
        assert len(pretool_hooks) == 1
        assert pretool_hooks[0]["matcher"] == "*"
        commands = [h["command"] for h in pretool_hooks[0]["hooks"]]
        assert "/zipsa-hooks/pretooluse.py" in commands

    def test_build_claude_json_with_stdio_mcp(self, tmp_path):
        """Stdio MCP servers should be in /workspace project mcpServers."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()

        manifest = {
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {
                "purpose": "Test",
                "instructions": "./SKILL.md",
                "mcp": [
                    {
                        "name": "filesystem",
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                    }
                ],
                "tools": {"builtin": [], "mcp": []},
            },
        }

        import yaml
        (skill_dir / "manifest.yaml").write_text(yaml.dump(manifest))
        (skill_dir / "SKILL.md").write_text("Test instructions")

        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path / "skill-data")

        config = json.loads(claude_json_path.read_text())
        mcp_servers = config["projects"]["/home/agent/workspace"]["mcpServers"]

        assert "filesystem" in mcp_servers
        assert mcp_servers["filesystem"]["command"] == "npx"
        assert mcp_servers["filesystem"]["args"] == [
            "-y",
            "@modelcontextprotocol/server-filesystem",
        ]

    def test_build_claude_json_stdio_mount_appends_container_path(self, tmp_path):
        """Stdio server with mount should have container path auto-appended to args."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()

        manifest = {
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {
                "purpose": "Test",
                "instructions": "./SKILL.md",
                "mcp": [
                    {
                        "name": "sessions",
                        "type": "stdio",
                        "command": "npx",
                        "args": ["@modelcontextprotocol/server-filesystem@2025.11.25"],
                        "mount": {"host": "~/.claude/projects", "mode": "ro"},
                    }
                ],
                "tools": {"builtin": [], "mcp": []},
            },
        }

        import yaml
        (skill_dir / "manifest.yaml").write_text(yaml.dump(manifest))
        (skill_dir / "SKILL.md").write_text("Test instructions")

        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path / "skill-data")

        config = json.loads(claude_json_path.read_text())
        mcp_servers = config["projects"]["/home/agent/workspace"]["mcpServers"]

        assert mcp_servers["sessions"]["args"] == [
            "@modelcontextprotocol/server-filesystem@2025.11.25",
            "/home/agent/workspace/sessions",
        ]

    def test_build_claude_json_with_http_mcp(self, tmp_path):
        """HTTP MCP servers should be in /workspace project mcpServers."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()

        manifest = {
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {
                "purpose": "Test",
                "instructions": "./SKILL.md",
                "mcp": [
                    {
                        "name": "notion",
                        "type": "http",
                        "url": "https://mcp.notion.com/mcp",
                    }
                ],
                "tools": {"builtin": [], "mcp": []},
            },
        }

        import yaml
        (skill_dir / "manifest.yaml").write_text(yaml.dump(manifest))
        (skill_dir / "SKILL.md").write_text("Test instructions")

        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path / "skill-data")

        config = json.loads(claude_json_path.read_text())
        mcp_servers = config["projects"]["/home/agent/workspace"]["mcpServers"]

        assert "notion" in mcp_servers
        assert mcp_servers["notion"]["type"] == "http"
        assert mcp_servers["notion"]["url"] == "https://mcp.notion.com/mcp"

    def test_build_claude_json_with_headers_helper(self, tmp_path):
        """HTTP MCP server with headersHelper should include it in config."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()

        manifest = {
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {
                "purpose": "Test",
                "instructions": "./SKILL.md",
                "mcp": [
                    {
                        "name": "github",
                        "type": "http",
                        "url": "https://api.githubcopilot.com/mcp",
                        "headersHelper": "echo \"{\\\"Authorization\\\": \\\"Bearer $TOKEN\\\"}\"",
                        "env": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
                    }
                ],
                "tools": {"builtin": [], "mcp": []},
            },
        }

        import yaml
        (skill_dir / "manifest.yaml").write_text(yaml.dump(manifest))
        (skill_dir / "SKILL.md").write_text("Test instructions")

        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path / "skill-data")

        config = json.loads(claude_json_path.read_text())
        mcp_servers = config["projects"]["/home/agent/workspace"]["mcpServers"]

        assert "github" in mcp_servers
        assert mcp_servers["github"]["type"] == "http"
        assert mcp_servers["github"]["url"] == "https://api.githubcopilot.com/mcp"
        assert "headersHelper" in mcp_servers["github"]
        assert mcp_servers["github"]["headersHelper"] == "echo \"{\\\"Authorization\\\": \\\"Bearer $TOKEN\\\"}\""

    def test_build_claude_json_uses_default_home_dir(self, tmp_path):
        """build_claude_json with no args should write to ZIPSA_HOME/<name>@<version>/."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        manifest = {
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "my-skill", "version": "2.0.0"},
            "spec": {
                "purpose": "Test",
                "instructions": "./SKILL.md",
                "mcp": [],
                "tools": {"builtin": [], "mcp": []},
            },
        }
        import yaml
        (skill_dir / "manifest.yaml").write_text(yaml.dump(manifest))
        (skill_dir / "SKILL.md").write_text("Test instructions")

        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json()

        expected_dir = zipsa_home() / "my-skill@2.0.0"
        assert claude_json_path == expected_dir / ".claude.json"
        assert claude_json_path.exists()
        assert (expected_dir / ".claude.json.org").exists()

    def test_build_claude_json_uses_custom_output_dir(self, tmp_path):
        """build_claude_json with output_dir should write there."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        manifest = {
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "my-skill", "version": "1.0.0"},
            "spec": {
                "purpose": "Test",
                "instructions": "./SKILL.md",
                "mcp": [],
                "tools": {"builtin": [], "mcp": []},
            },
        }
        import yaml
        (skill_dir / "manifest.yaml").write_text(yaml.dump(manifest))
        (skill_dir / "SKILL.md").write_text("Test instructions")

        output_dir = tmp_path / "custom-output"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=output_dir)

        assert claude_json_path == output_dir / ".claude.json"
        assert claude_json_path.exists()
        assert (output_dir / ".claude.json.org").exists()


def _make_skill(tmp_path: Path, mcp_entries: str) -> Skill:
    """Helper: create a minimal skill with given mcp yaml block."""
    import yaml
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test\n")
    (skill_dir / "manifest.yaml").write_text(f"""
apiVersion: zipsa.dev/v1alpha1
kind: Skill
metadata:
  name: test-skill
  version: 1.0.0
spec:
  purpose: Test
  instructions: ./SKILL.md
  mcp:
{mcp_entries}
  tools:
    builtin: []
""")
    return Skill.load(skill_dir)


class TestBuildClaudeJsonOauth:
    """Test auto headersHelper generation for oauth2 servers."""

    def test_oauth2_auto_generates_headers_helper(self, tmp_path):
        """oauth2 server without explicit headersHelper gets auto-generated one."""
        skill = _make_skill(tmp_path, """
    - name: notion
      type: http
      url: https://mcp.notion.com/mcp
      auth:
        type: oauth2
""")
        claude_json_path = skill.build_claude_json(output_dir=tmp_path)
        config = json.loads(claude_json_path.read_text())
        notion = config["projects"]["/home/agent/workspace"]["mcpServers"]["notion"]
        assert "headersHelper" in notion
        assert "ZIPSA_TOKEN_NOTION" in notion["headersHelper"]
        assert "Authorization" in notion["headersHelper"]
        assert "Bearer" in notion["headersHelper"]
        # Must use double quotes so shell expands $ZIPSA_TOKEN_NOTION
        assert notion["headersHelper"].startswith('echo "')
        assert "'" not in notion["headersHelper"]

    def test_oauth2_explicit_headers_helper_not_overridden(self, tmp_path):
        """oauth2 server with explicit headersHelper keeps it unchanged."""
        skill = _make_skill(tmp_path, """
    - name: notion
      type: http
      url: https://mcp.notion.com/mcp
      auth:
        type: oauth2
      headersHelper: 'echo {"X-Custom": "value"}'
""")
        claude_json_path = skill.build_claude_json(output_dir=tmp_path)
        config = json.loads(claude_json_path.read_text())
        notion = config["projects"]["/home/agent/workspace"]["mcpServers"]["notion"]
        assert notion["headersHelper"] == 'echo {"X-Custom": "value"}'

    def test_no_auth_no_headers_helper(self, tmp_path):
        """HTTP server without auth gets no auto-generated headersHelper."""
        skill = _make_skill(tmp_path, """
    - name: api
      type: http
      url: https://api.example.com/mcp
""")
        claude_json_path = skill.build_claude_json(output_dir=tmp_path)
        config = json.loads(claude_json_path.read_text())
        api = config["projects"]["/home/agent/workspace"]["mcpServers"]["api"]
        assert "headersHelper" not in api

    def test_token_var_uppercased_server_name(self, tmp_path):
        """Token env var uses uppercased server name with hyphens replaced by underscores."""
        skill = _make_skill(tmp_path, """
    - name: github-copilot
      type: http
      url: https://api.example.com/mcp
      auth:
        type: oauth2
""")
        claude_json_path = skill.build_claude_json(output_dir=tmp_path)
        config = json.loads(claude_json_path.read_text())
        server = config["projects"]["/home/agent/workspace"]["mcpServers"]["github-copilot"]
        assert "ZIPSA_TOKEN_GITHUB_COPILOT" in server["headersHelper"]
