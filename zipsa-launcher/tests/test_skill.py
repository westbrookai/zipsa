"""Tests for Skill loader."""

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
