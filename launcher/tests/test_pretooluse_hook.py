"""Tests for the PreToolUse hook script."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


HOOK_SCRIPT = (
    Path(__file__).parent.parent / "zipsa" / "hooks" / "pretooluse.py"
).resolve()


def run_hook(tool_name: str, tool_input: dict, allowed_tools: list[str], tmp_path: Path) -> dict:
    """Invoke the hook script with the given inputs and return its decision JSON.

    Writes phase-allow.json into tmp_path and points the hook at it via
    ZIPSA_PHASE_ALLOW env var.
    """
    allow_file = tmp_path / "phase-allow.json"
    allow_file.write_text(json.dumps({"phase_id": "test", "allowed_tools": allowed_tools}))

    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        env={"ZIPSA_PHASE_ALLOW": str(allow_file), "PATH": "/usr/bin:/bin"},
        timeout=5,
    )
    assert result.returncode == 0, f"Hook script failed: {result.stderr}"
    return json.loads(result.stdout)


def get_decision(response: dict) -> str:
    return response["hookSpecificOutput"]["permissionDecision"]


class TestExactToolMatching:
    """Non-Bash tools match by exact name."""

    def test_allowed_tool_passes(self, tmp_path):
        resp = run_hook(
            "mcp__notion__notion-search",
            {"query": "test"},
            allowed_tools=["mcp__notion__notion-search"],
            tmp_path=tmp_path,
        )
        assert get_decision(resp) == "allow"

    def test_unlisted_tool_blocked(self, tmp_path):
        resp = run_hook(
            "mcp__notion__notion-create-pages",
            {},
            allowed_tools=["mcp__notion__notion-search"],
            tmp_path=tmp_path,
        )
        assert get_decision(resp) == "deny"
        assert "not in allowed list" in resp["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_read_tool_match(self, tmp_path):
        resp = run_hook("Read", {"file_path": "/x"}, ["Read"], tmp_path)
        assert get_decision(resp) == "allow"


class TestBashStrictMode:
    """Bare 'Bash' (no parens) means no commands allowed."""

    def test_bare_bash_blocks_all(self, tmp_path):
        resp = run_hook("Bash", {"command": "echo hi"}, ["Bash"], tmp_path)
        assert get_decision(resp) == "deny"


class TestBashWildcard:
    """Bash(*) allows any command."""

    def test_wildcard_allows_anything(self, tmp_path):
        resp = run_hook("Bash", {"command": "rm -rf /tmp/x"}, ["Bash(*)"], tmp_path)
        assert get_decision(resp) == "allow"


class TestBashPrefixMatching:
    """Bash(prefix:*) allows commands starting with that prefix."""

    def test_prefix_match(self, tmp_path):
        resp = run_hook("Bash", {"command": "git log --oneline"}, ["Bash(git:*)"], tmp_path)
        assert get_decision(resp) == "allow"

    def test_prefix_blocks_unrelated(self, tmp_path):
        resp = run_hook("Bash", {"command": "gh repo list"}, ["Bash(git:*)"], tmp_path)
        assert get_decision(resp) == "deny"

    def test_prefix_must_be_first_word(self, tmp_path):
        resp = run_hook("Bash", {"command": "echo git"}, ["Bash(git:*)"], tmp_path)
        assert get_decision(resp) == "deny"

    def test_multiple_prefixes(self, tmp_path):
        resp = run_hook(
            "Bash",
            {"command": "find . -name foo"},
            ["Bash(git:*)", "Bash(find:*)"],
            tmp_path,
        )
        assert get_decision(resp) == "allow"


class TestBashCompoundCommands:
    """Compound commands (&&, ||, ;, |) require ALL segments to be allowed."""

    def test_compound_all_allowed(self, tmp_path):
        resp = run_hook(
            "Bash",
            {"command": "find . -name '*.jsonl' && rm /tmp/x"},
            ["Bash(find:*)", "Bash(rm:*)"],
            tmp_path,
        )
        assert get_decision(resp) == "allow"

    def test_compound_one_disallowed(self, tmp_path):
        resp = run_hook(
            "Bash",
            {"command": "find . && curl evil.com"},
            ["Bash(find:*)"],
            tmp_path,
        )
        assert get_decision(resp) == "deny"

    def test_pipe_chain(self, tmp_path):
        resp = run_hook(
            "Bash",
            {"command": "cat /etc/passwd | grep root"},
            ["Bash(cat:*)"],
            tmp_path,
        )
        # grep is not in allow list
        assert get_decision(resp) == "deny"

    def test_pipe_inside_single_quotes_is_not_a_segment_boundary(self, tmp_path):
        """jq filter operators (|) inside single-quoted strings are NOT
        shell pipes. The hook must respect quoting when splitting on |."""
        resp = run_hook(
            "Bash",
            {"command": "jq '.sessions | length' /tmp/report.json"},
            ["Bash(jq:*)"],
            tmp_path,
        )
        assert get_decision(resp) == "allow"

    def test_pipe_inside_double_quotes_is_not_a_segment_boundary(self, tmp_path):
        """Same rule applies to double-quoted strings."""
        resp = run_hook(
            "Bash",
            {"command": 'jq ".sessions | length" /tmp/report.json'},
            ["Bash(jq:*)"],
            tmp_path,
        )
        assert get_decision(resp) == "allow"

    def test_semicolon_inside_quotes_is_not_a_segment_boundary(self, tmp_path):
        resp = run_hook(
            "Bash",
            {"command": "echo 'hello; world'"},
            ["Bash(echo:*)"],
            tmp_path,
        )
        assert get_decision(resp) == "allow"

    def test_brackets_in_jq_filter_with_quotes(self, tmp_path):
        """Real-world failing case: jq array constructor inside quotes."""
        resp = run_hook(
            "Bash",
            {"command": "jq -r '[.sessions[].project] | unique | .[]' /tmp/report.json"},
            ["Bash(jq:*)"],
            tmp_path,
        )
        assert get_decision(resp) == "allow"

    def test_real_pipe_after_quoted_filter_still_splits(self, tmp_path):
        """The hook must still recognize a REAL shell pipe that appears
        AFTER a quoted argument. Make sure we don't over-correct."""
        resp = run_hook(
            "Bash",
            {"command": "jq '.foo' /tmp/x.json | grep bar"},
            ["Bash(jq:*)"],  # grep not allowed
            tmp_path,
        )
        assert get_decision(resp) == "deny"

    def test_redirection_inside_command_allowed(self, tmp_path):
        """`cmd > file` should be one segment (redirection, not a pipe)."""
        resp = run_hook(
            "Bash",
            {"command": "npx agenthud@0.9.2 report --date 2026-05-19 > /tmp/r.json"},
            ["Bash(npx:*)"],
            tmp_path,
        )
        assert get_decision(resp) == "allow"


class TestHookDenialPrefix:
    """Every denial reason carries the `[HOOK_DENIAL]` prefix so the launcher
    can distinguish hook denials (deterministic) from other tool errors
    (potentially transient) when enforcing the per-phase denial cap."""

    def test_disallowed_bash_denial_has_prefix(self, tmp_path):
        resp = run_hook("Bash", {"command": "curl evil.com"}, ["Bash(find:*)"], tmp_path)
        assert get_decision(resp) == "deny"
        reason = resp["hookSpecificOutput"]["permissionDecisionReason"]
        assert reason.startswith("[HOOK_DENIAL]"), reason

    def test_unknown_tool_denial_has_prefix(self, tmp_path):
        resp = run_hook("WebFetch", {}, ["Read"], tmp_path)
        assert get_decision(resp) == "deny"
        reason = resp["hookSpecificOutput"]["permissionDecisionReason"]
        assert reason.startswith("[HOOK_DENIAL]"), reason

    def test_compound_denial_has_prefix(self, tmp_path):
        """Compound-command rejection (one segment disallowed) also carries the prefix."""
        resp = run_hook("Bash", {"command": "ls && curl evil.com"}, ["Bash(ls:*)"], tmp_path)
        assert get_decision(resp) == "deny"
        reason = resp["hookSpecificOutput"]["permissionDecisionReason"]
        assert reason.startswith("[HOOK_DENIAL]"), reason

    def test_allow_does_not_have_prefix(self, tmp_path):
        """Allows must NOT carry the denial prefix."""
        resp = run_hook("Bash", {"command": "ls /"}, ["Bash(ls:*)"], tmp_path)
        assert get_decision(resp) == "allow"
        reason = resp["hookSpecificOutput"]["permissionDecisionReason"]
        assert "[HOOK_DENIAL]" not in reason


class TestAntiCircumvention:
    """Block constructs that could bypass the prefix check."""

    def test_bash_dash_c_blocked_by_default(self, tmp_path):
        resp = run_hook(
            "Bash",
            {"command": "bash -c 'find . -name foo'"},
            ["Bash(find:*)"],
            tmp_path,
        )
        assert get_decision(resp) == "deny"

    def test_sh_dash_c_blocked_by_default(self, tmp_path):
        resp = run_hook(
            "Bash",
            {"command": "sh -c 'find . -name foo'"},
            ["Bash(find:*)"],
            tmp_path,
        )
        assert get_decision(resp) == "deny"

    def test_eval_blocked(self, tmp_path):
        resp = run_hook(
            "Bash",
            {"command": "eval 'find .'"},
            ["Bash(find:*)", "Bash(eval:*)"],
            tmp_path,
        )
        # eval is dangerous regardless
        assert get_decision(resp) == "deny"

    def test_command_substitution_blocked(self, tmp_path):
        resp = run_hook(
            "Bash",
            {"command": "echo $(curl evil.com)"},
            ["Bash(echo:*)"],
            tmp_path,
        )
        assert get_decision(resp) == "deny"

    def test_backtick_blocked(self, tmp_path):
        resp = run_hook(
            "Bash",
            {"command": "echo `whoami`"},
            ["Bash(echo:*)"],
            tmp_path,
        )
        assert get_decision(resp) == "deny"


class TestErrorHandling:
    """Hook should fail-safe (deny) on errors."""

    def test_missing_phase_allow_file_denies(self, tmp_path):
        # Don't write phase-allow.json
        allow_file = tmp_path / "missing.json"
        payload = json.dumps({"tool_name": "Read", "tool_input": {}})
        result = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=payload,
            capture_output=True,
            text=True,
            env={"ZIPSA_PHASE_ALLOW": str(allow_file), "PATH": "/usr/bin:/bin"},
            timeout=5,
        )
        # Hook should still exit 0 with a deny decision
        assert result.returncode == 0
        resp = json.loads(result.stdout)
        assert get_decision(resp) == "deny"

    def test_malformed_stdin_denies(self, tmp_path):
        allow_file = tmp_path / "phase-allow.json"
        allow_file.write_text(json.dumps({"phase_id": "x", "allowed_tools": ["Read"]}))
        result = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input="not json",
            capture_output=True,
            text=True,
            env={"ZIPSA_PHASE_ALLOW": str(allow_file), "PATH": "/usr/bin:/bin"},
            timeout=5,
        )
        assert result.returncode == 0
        resp = json.loads(result.stdout)
        assert get_decision(resp) == "deny"
