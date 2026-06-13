"""Tests for `zipsa create` — author a skill via a separate claude -p run.

The whole point of `create` (vs the assistant hand-authoring) is that
authoring happens in a fresh, observable, reproducible claude process.
These tests pin the subprocess wiring; the actual authoring quality is
the LLM's job, verified by E2E.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from zipsa.create import (
    build_create_prompt,
    find_repo_root,
    run_create,
)


def _make_repo(root: Path) -> Path:
    (root / ".claude" / "skills" / "zipsa-skill-builder").mkdir(parents=True)
    (root / ".claude" / "skills" / "zipsa-skill-builder" / "SKILL.md").write_text(
        "# zipsa-skill-builder\n"
    )
    (root / "skills").mkdir()
    (root / "skills" / "AUTHORING.md").write_text("# authoring\n")
    return root


class TestFindRepoRoot:
    def test_finds_root_from_subdir(self, tmp_path):
        root = _make_repo(tmp_path / "repo")
        deep = root / "launcher" / "zipsa"
        deep.mkdir(parents=True)

        assert find_repo_root(deep) == root

    def test_finds_root_at_self(self, tmp_path):
        root = _make_repo(tmp_path / "repo")

        assert find_repo_root(root) == root

    def test_returns_none_when_no_skill(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()

        assert find_repo_root(plain) is None


class TestBuildCreatePrompt:
    def test_prompt_carries_intent_path_and_workflow(self, tmp_path):
        skill_path = tmp_path / "skills" / "umbrella-reminder"

        prompt = build_create_prompt("8am umbrella alert", skill_path)

        assert "8am umbrella alert" in prompt
        assert str(skill_path) in prompt
        assert "zipsa-skill-builder" in prompt
        assert "AUTHORING.md" in prompt
        # Must instruct the self-test loop
        assert "zipsa exec" in prompt
        assert "--local" in prompt

    def test_custom_zipsa_cmd_woven_in(self, tmp_path):
        prompt = build_create_prompt(
            "x", tmp_path / "s", zipsa_cmd="/venv/python -m zipsa exec",
        )
        assert "/venv/python -m zipsa exec" in prompt


class TestRunCreate:
    @patch("zipsa.create.subprocess.run")
    def test_invokes_claude_headless_with_bypass(self, mock_run, tmp_path):
        root = _make_repo(tmp_path / "repo")
        skill_path = root / "skills" / "foo"
        mock_run.return_value.returncode = 0

        rc = run_create("do a thing", skill_path, root=root)

        assert rc == 0
        argv = mock_run.call_args.args[0]
        assert argv[0] == "claude"
        assert "-p" in argv
        assert "--permission-mode" in argv
        assert "bypassPermissions" in argv
        # The prompt is passed as an arg
        assert any("do a thing" in a for a in argv)
        # claude runs from the repo root so it discovers the skill + docs
        assert mock_run.call_args.kwargs["cwd"] == root

    @patch("zipsa.create.subprocess.run")
    def test_stdio_inherited_for_observability(self, mock_run, tmp_path):
        """No capture_output — claude's progress streams to the user's
        terminal. That visibility is the reason create exists."""
        root = _make_repo(tmp_path / "repo")
        mock_run.return_value.returncode = 0

        run_create("x", root / "skills" / "foo", root=root)

        kwargs = mock_run.call_args.kwargs
        assert not kwargs.get("capture_output")

    @patch("zipsa.create.subprocess.run")
    def test_propagates_claude_exit_code(self, mock_run, tmp_path):
        root = _make_repo(tmp_path / "repo")
        mock_run.return_value.returncode = 2

        rc = run_create("x", root / "skills" / "foo", root=root)

        assert rc == 2

    @patch("zipsa.create.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_claude_raises(self, mock_run, tmp_path):
        root = _make_repo(tmp_path / "repo")

        with pytest.raises(FileNotFoundError):
            run_create("x", root / "skills" / "foo", root=root)

    @patch("zipsa.create.subprocess.run")
    def test_injects_claude_auth_from_env_file(self, mock_run, tmp_path):
        """The spawned claude is headless — it needs
        CLAUDE_CODE_OAUTH_TOKEN in its env (the host login isn't picked
        up). Same token the container LLM phases get via --env-file."""
        root = _make_repo(tmp_path / "repo")
        env_file = tmp_path / "zipsa.env"
        env_file.write_text(
            "# comment\nCLAUDE_CODE_OAUTH_TOKEN=tok-123\nOTHER=val\n"
        )
        mock_run.return_value.returncode = 0

        run_create(
            "x", root / "skills" / "foo", root=root, env_file=env_file,
        )

        env = mock_run.call_args.kwargs["env"]
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-123"
        assert env["OTHER"] == "val"
        # Inherits the rest of the environment too (PATH etc.)
        assert "PATH" in env
