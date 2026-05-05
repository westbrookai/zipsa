"""Tests for Pydantic manifest models."""

import pytest
from pydantic import ValidationError
from zipsa.core.models import (
    SkillManifest,
    SkillMetadata,
    MCPServerStdio,
    MCPServerHTTP,
    SkillTools,
    VolumeMount,
)


class TestSkillMetadata:
    """Test SkillMetadata model."""

    def test_minimal_metadata(self):
        """Minimal metadata with only required fields."""
        data = {
            "name": "test-skill",
            "version": "1.0.0",
        }
        metadata = SkillMetadata(**data)
        assert metadata.name == "test-skill"
        assert metadata.version == "1.0.0"
        assert metadata.author is None
        assert metadata.description is None
        assert metadata.tags == []

    def test_full_metadata(self):
        """Metadata with all fields."""
        data = {
            "name": "weather",
            "version": "0.1.0",
            "author": "westbrookai",
            "description": "Weather skill",
            "tags": ["weather", "utility"],
        }
        metadata = SkillMetadata(**data)
        assert metadata.name == "weather"
        assert metadata.tags == ["weather", "utility"]


class TestMCPServers:
    """Test MCP server models."""

    def test_stdio_server_minimal(self):
        """Stdio server with minimal fields."""
        data = {
            "name": "filesystem",
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        }
        server = MCPServerStdio(**data)
        assert server.name == "filesystem"
        assert server.type == "stdio"
        assert server.command == "npx"
        assert server.mount is None

    def test_stdio_server_with_mount(self):
        """Stdio server with mount configuration."""
        data = {
            "name": "sessions",
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem@0.5.0"],
            "mount": {
                "host": "~/.claude/projects",
                "container": "/host-claude-projects",
                "mode": "ro",
            },
        }
        server = MCPServerStdio(**data)
        assert server.mount is not None
        assert server.mount.host == "~/.claude/projects"
        assert server.mount.mode == "ro"

    def test_http_server(self):
        """HTTP MCP server."""
        data = {
            "name": "notion",
            "type": "http",
            "url": "https://mcp.notion.com/mcp",
            "connection": "notion",
        }
        server = MCPServerHTTP(**data)
        assert server.type == "http"
        assert server.url == "https://mcp.notion.com/mcp"


class TestSkillManifest:
    """Test full SkillManifest model."""

    def test_minimal_manifest(self):
        """Minimal valid manifest."""
        data = {
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {
                "name": "test",
                "version": "1.0.0",
            },
            "spec": {
                "purpose": "Test skill",
                "instructions": "./SKILL.md",
            },
        }
        manifest = SkillManifest.model_validate(data)
        assert manifest.kind == "Skill"
        assert manifest.metadata.name == "test"
        assert manifest.spec.purpose == "Test skill"

    def test_manifest_with_mcp_stdio(self):
        """Manifest with MCP stdio server."""
        data = {
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
            },
        }
        manifest = SkillManifest.model_validate(data)
        assert len(manifest.spec.mcp) == 1
        assert manifest.spec.mcp[0].name == "filesystem"

    def test_manifest_with_tools(self):
        """Manifest with tool whitelist."""
        data = {
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {
                "purpose": "Test",
                "instructions": "./SKILL.md",
                "tools": {
                    "builtin": ["WebFetch", "Bash"],
                    "mcp": ["filesystem:read_file", "notion:create-page"],
                },
            },
        }
        manifest = SkillManifest.model_validate(data)
        assert "WebFetch" in manifest.spec.tools.builtin
        assert "filesystem:read_file" in manifest.spec.tools.mcp

    def test_invalid_kind(self):
        """Invalid kind should fail validation."""
        data = {
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Invalid",
            "metadata": {"name": "test", "version": "1.0.0"},
            "spec": {"purpose": "Test", "instructions": "./SKILL.md"},
        }
        with pytest.raises(ValidationError):
            SkillManifest.model_validate(data)

    def test_missing_required_field(self):
        """Missing required field should fail validation."""
        data = {
            "apiVersion": "zipsa.dev/v1alpha1",
            "kind": "Skill",
            # Missing metadata
            "spec": {"purpose": "Test", "instructions": "./SKILL.md"},
        }
        with pytest.raises(ValidationError):
            SkillManifest.model_validate(data)
