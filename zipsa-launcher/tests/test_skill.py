"""Tests for Skill loader."""

import json
import pytest
from pathlib import Path
from zipsa.core.skill import Skill


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
        """Stdio MCP server should be in config."""
        manifest_path = Path(__file__).parent / "fixtures/manifests/with-mcp.yaml"
        skill = Skill.load(manifest_path)

        config = skill.build_mcp_config()

        assert "filesystem" in config["mcpServers"]
        assert config["mcpServers"]["filesystem"]["command"] == "npx"
        assert config["mcpServers"]["filesystem"]["args"] == [
            "-y",
            "@modelcontextprotocol/server-filesystem",
            "/workspace",
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
        """MCP tools should be converted to mcp__server__method format."""
        manifest_path = Path(__file__).parent / "fixtures/manifests/with-mcp.yaml"
        skill = Skill.load(manifest_path)

        tools = skill.get_allowed_tools()

        # Should have builtin tools
        assert "WebFetch" in tools
        assert "Bash" in tools

        # Should have MCP tools with correct format
        assert "mcp__filesystem__read_file" in tools
        assert "mcp__notion__create-page" in tools

    def test_get_allowed_tools_order(self):
        """Tools should be in order: builtin first, then mcp."""
        manifest_path = Path(__file__).parent / "fixtures/manifests/with-mcp.yaml"
        skill = Skill.load(manifest_path)

        tools = skill.get_allowed_tools().split(",")

        # First two should be builtin
        assert tools[0] == "WebFetch"
        assert tools[1] == "Bash"

        # Rest should be MCP
        assert tools[2].startswith("mcp__")


class TestClaudeJson:
    """Test .claude.json generation."""

    def test_build_claude_json_creates_file(self, tmp_path):
        """Should create .claude.json file in .zipsa directory."""
        # Create minimal manifest in tmp directory
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        zipsa_dir = skill_dir / ".zipsa"

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
        claude_json_path = skill.build_claude_json()

        # Both files should exist
        assert claude_json_path.exists()
        assert claude_json_path == zipsa_dir / ".claude.json"

        claude_json_org_path = zipsa_dir / ".claude.json.org"
        assert claude_json_org_path.exists()

        # Both should have same content initially
        assert claude_json_path.read_text() == claude_json_org_path.read_text()

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
        claude_json_path = skill.build_claude_json()

        # Parse and check structure
        config = json.loads(claude_json_path.read_text())

        assert config["hasCompletedOnboarding"] is True
        assert "/workspace" in config["projects"]
        assert config["projects"]["/workspace"]["hasTrustDialogAccepted"] is True
        assert "mcpServers" in config["projects"]["/workspace"]

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
        claude_json_path = skill.build_claude_json()

        config = json.loads(claude_json_path.read_text())
        mcp_servers = config["projects"]["/workspace"]["mcpServers"]

        assert "filesystem" in mcp_servers
        assert mcp_servers["filesystem"]["command"] == "npx"
        assert mcp_servers["filesystem"]["args"] == [
            "-y",
            "@modelcontextprotocol/server-filesystem",
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
        claude_json_path = skill.build_claude_json()

        config = json.loads(claude_json_path.read_text())
        mcp_servers = config["projects"]["/workspace"]["mcpServers"]

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
        claude_json_path = skill.build_claude_json()

        config = json.loads(claude_json_path.read_text())
        mcp_servers = config["projects"]["/workspace"]["mcpServers"]

        assert "github" in mcp_servers
        assert mcp_servers["github"]["type"] == "http"
        assert mcp_servers["github"]["url"] == "https://api.githubcopilot.com/mcp"
        assert "headersHelper" in mcp_servers["github"]
        assert mcp_servers["github"]["headersHelper"] == "echo \"{\\\"Authorization\\\": \\\"Bearer $TOKEN\\\"}\""
