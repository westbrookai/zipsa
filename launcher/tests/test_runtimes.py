"""Tests for runtime plugins."""

import json
from pathlib import Path
from zipsa.runtimes.claude import ClaudeRuntime


class TestClaudeRuntime:
    """Test Claude Code runtime."""

    def test_runtime_name(self):
        """Runtime name should be 'claude'."""
        runtime = ClaudeRuntime()
        assert runtime.name == "claude"

    def test_build_command(self):
        """Build command should generate correct Claude Code CLI."""
        runtime = ClaudeRuntime()

        cmd = runtime.build_command(
            skill_name="test-skill",
            user_input="Hello world",
            system_prompt="You are a test agent.",
            allowed_tools="Read,Write",
            workspace=Path("/workspace"),
            env={"TEST": "value"},
        )

        # Check command structure
        assert cmd[0] == "claude"
        assert "--print" in cmd
        assert "Hello world" in cmd
        assert "--append-system-prompt" in cmd
        assert "You are a test agent." in cmd
        assert "--allowedTools" in cmd
        assert "Read,Write" in cmd
        # MCP config is now loaded from .claude.json (not --mcp-config option)
        assert "--mcp-config" not in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "--output-format=stream-json" in cmd

    def test_parse_output_json(self):
        """Parse valid JSON lines."""
        runtime = ClaudeRuntime()

        lines = [
            '{"type": "system", "session_id": "abc123"}',
            '{"type": "assistant", "content": "Hello"}',
        ]

        output = list(runtime.parse_output(iter(lines)))

        assert len(output) == 2
        assert output[0]["type"] == "system"
        assert output[1]["content"] == "Hello"

    def test_parse_output_plain_text(self):
        """Parse plain text (non-JSON) lines."""
        runtime = ClaudeRuntime()

        lines = [
            "Some plain text output",
            "Another line",
        ]

        output = list(runtime.parse_output(iter(lines)))

        assert len(output) == 2
        assert output[0]["type"] == "text"
        assert output[0]["content"] == "Some plain text output"

    def test_parse_output_mixed(self):
        """Parse mixed JSON and plain text."""
        runtime = ClaudeRuntime()

        lines = [
            '{"type": "system"}',
            "Plain text line",
            '{"type": "result"}',
        ]

        output = list(runtime.parse_output(iter(lines)))

        assert len(output) == 3
        assert output[0]["type"] == "system"
        assert output[1]["type"] == "text"
        assert output[2]["type"] == "result"

    def test_supports_mcp(self):
        """Claude runtime should support MCP."""
        runtime = ClaudeRuntime()
        assert runtime.supports_mcp() is True

    def test_build_command_no_debug_by_default(self):
        """Build command should not include debug flags by default."""
        runtime = ClaudeRuntime()

        cmd = runtime.build_command(
            skill_name="test-skill",
            user_input="Hello",
            system_prompt="System",
            allowed_tools="Read",
            workspace=Path("/workspace"),
            env={},
        )

        assert "--debug" not in cmd
        assert "--debug-file" not in cmd

    def test_build_command_with_mcp_debug_file(self):
        """Build command should include --debug and --debug-file when mcp_debug_file is set."""
        runtime = ClaudeRuntime()

        cmd = runtime.build_command(
            skill_name="test-skill",
            user_input="Hello",
            system_prompt="System",
            allowed_tools="Read",
            workspace=Path("/workspace"),
            env={},
            mcp_debug_file="/home/agent/mcp-debug.log",
        )

        assert "--debug" in cmd
        assert "--debug-file" in cmd
        assert "/home/agent/mcp-debug.log" in cmd

    def test_build_command_with_extra_dirs(self):
        """Build command should include --add-dir for each extra dir."""
        runtime = ClaudeRuntime()

        cmd = runtime.build_command(
            skill_name="test-skill",
            user_input="Hello",
            system_prompt="System",
            allowed_tools="Read",
            workspace=Path("/workspace"),
            env={},
            extra_dirs=["/host-claude-projects", "/mnt/docs"],
        )

        assert cmd.count("--add-dir") == 2
        assert "/host-claude-projects" in cmd
        assert "/mnt/docs" in cmd

    def test_build_command_no_extra_dirs_by_default(self):
        """Build command should not include --add-dir when extra_dirs is empty."""
        runtime = ClaudeRuntime()

        cmd = runtime.build_command(
            skill_name="test-skill",
            user_input="Hello",
            system_prompt="System",
            allowed_tools="Read",
            workspace=Path("/workspace"),
            env={},
        )

        assert "--add-dir" not in cmd
