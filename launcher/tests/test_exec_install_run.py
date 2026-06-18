"""TDD tests for exec-skill install-by-name + run-by-name (#157).

Covers:
  D3 — _is_exec_format detects exec layout (SKILL.md + scripts/ or zipsa-dist/,
        no manifest.yaml)
  D1 — install --link / install_local for exec-format skills
  D2 — run <name> resolves an installed exec skill to run_skill_llm
  D4 — zipsa <name> shortcut routes exec skills through run -> run_skill_llm
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from zipsa.cli import _is_exec_format, app
from zipsa.paths import SkillNotInstalledError

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures — small on-disk skill layouts
# ---------------------------------------------------------------------------


def _make_exec_skill_scripts(root: Path, name: str = "my-exec", version: str = "0.1.0") -> Path:
    """Exec skill: SKILL.md + scripts/ + zipsa/package.yaml, no manifest.yaml."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A test exec skill\n---\n\n# Body\n"
    )
    (root / "scripts").mkdir()
    (root / "scripts" / "1.run.py").write_text("# step 1\n")
    (root / "zipsa").mkdir()
    (root / "zipsa" / "package.yaml").write_text(f"version: {version}\n")
    return root


def _make_exec_skill_zipsa_dist(root: Path, name: str = "legacy-exec", version: str = "0.2.0") -> Path:
    """Exec skill: SKILL.md + zipsa-dist/ + zipsa/package.yaml, no manifest.yaml."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A legacy exec skill\n---\n\n# Body\n"
    )
    (root / "zipsa-dist").mkdir()
    (root / "zipsa-dist" / "1.run.py").write_text("# step 1\n")
    (root / "zipsa").mkdir()
    (root / "zipsa" / "package.yaml").write_text(f"version: {version}\n")
    return root


def _make_legacy_skill(root: Path, name: str = "my-legacy", version: str = "1.0.0") -> Path:
    """Legacy skill: SKILL.md + scripts/ + manifest.yaml."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    (root / "scripts").mkdir()
    (root / "scripts" / "1.run.py").write_text("# step 1\n")
    (root / "manifest.yaml").write_text(
        f"metadata:\n  name: {name}\n  version: {version}\n"
        "spec:\n  purpose: A legacy test skill\n"
    )
    return root


# ---------------------------------------------------------------------------
# D3 — _is_exec_format
# ---------------------------------------------------------------------------


class TestIsExecFormat:
    """_is_exec_format correctly identifies exec vs legacy skill layouts."""

    def test_true_for_skill_md_plus_scripts(self, tmp_path):
        """SKILL.md + scripts/ + no manifest.yaml → exec format."""
        skill_dir = tmp_path / "my-skill"
        _make_exec_skill_scripts(skill_dir)
        assert _is_exec_format(skill_dir) is True

    def test_true_for_skill_md_plus_zipsa_dist(self, tmp_path):
        """SKILL.md + zipsa-dist/ + no manifest.yaml → exec format."""
        skill_dir = tmp_path / "my-skill"
        _make_exec_skill_zipsa_dist(skill_dir)
        assert _is_exec_format(skill_dir) is True

    def test_false_when_manifest_present_with_scripts(self, tmp_path):
        """SKILL.md + scripts/ + manifest.yaml → legacy (not exec)."""
        skill_dir = tmp_path / "hybrid"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: hybrid\n---\n")
        (skill_dir / "scripts").mkdir()
        (skill_dir / "manifest.yaml").write_text("metadata:\n  name: hybrid\n")
        assert _is_exec_format(skill_dir) is False

    def test_false_when_manifest_present_with_zipsa_dist(self, tmp_path):
        """SKILL.md + zipsa-dist/ + manifest.yaml → legacy (not exec)."""
        skill_dir = tmp_path / "hybrid"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: hybrid\n---\n")
        (skill_dir / "zipsa-dist").mkdir()
        (skill_dir / "manifest.yaml").write_text("metadata:\n  name: hybrid\n")
        assert _is_exec_format(skill_dir) is False

    def test_false_when_zipsa_dist_has_nested_manifest(self, tmp_path):
        """SKILL.md + zipsa-dist/manifest.yaml (new-structure legacy) → not exec."""
        skill_dir = tmp_path / "new-struct"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: new-struct\n---\n")
        (skill_dir / "zipsa-dist").mkdir()
        # new-structure legacy: manifest inside zipsa-dist/
        (skill_dir / "zipsa-dist" / "manifest.yaml").write_text(
            "metadata:\n  name: new-struct\n  version: 1.0.0\n"
        )
        assert _is_exec_format(skill_dir) is False

    def test_false_when_no_skill_md(self, tmp_path):
        """scripts/ alone (no SKILL.md) → not exec format."""
        skill_dir = tmp_path / "no-skill-md"
        skill_dir.mkdir()
        (skill_dir / "scripts").mkdir()
        assert _is_exec_format(skill_dir) is False

    def test_false_when_neither_scripts_nor_zipsa_dist(self, tmp_path):
        """SKILL.md alone → not exec format."""
        skill_dir = tmp_path / "skill-md-only"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: x\n---\n")
        assert _is_exec_format(skill_dir) is False


# ---------------------------------------------------------------------------
# D1 — install exec branch
# ---------------------------------------------------------------------------


class TestInstallExecSkill:
    """install command routes exec-format skills through load_exec_skill."""

    def test_install_link_exec_skill(self, tmp_path, monkeypatch):
        """install --link <exec-dir> → installs to skills/<name>, install.json has version."""
        src = _make_exec_skill_scripts(tmp_path / "my-exec", version="0.3.0")
        skills_home = tmp_path / ".zipsa"
        monkeypatch.setenv("ZIPSA_HOME", str(skills_home))

        result = runner.invoke(app, ["install", "--link", str(src)])

        assert result.exit_code == 0, f"install failed:\n{result.output}\n{result.exception}"
        dest = skills_home / "skills" / "my-exec"
        assert dest.exists() or dest.is_symlink(), "skill not installed"
        install_json = dest / "_install.json"
        assert install_json.exists(), "_install.json not written"
        meta = json.loads(install_json.read_text())
        assert meta["version"] == "0.3.0"

    def test_install_link_resolves_by_name(self, tmp_path, monkeypatch):
        """After install --link, resolve_skill(name) finds the installed dir."""
        from zipsa.paths import resolve_skill

        src = _make_exec_skill_scripts(tmp_path / "named-exec", name="named-exec", version="1.0.0")
        skills_home = tmp_path / ".zipsa"
        monkeypatch.setenv("ZIPSA_HOME", str(skills_home))

        result = runner.invoke(app, ["install", "--link", str(src)])
        assert result.exit_code == 0, result.output

        found = resolve_skill("named-exec")
        assert found.exists()

    def test_install_exec_missing_version_raises_error(self, tmp_path, monkeypatch):
        """install of an exec skill missing version → clear error, no partial install."""
        src = tmp_path / "bad-exec"
        src.mkdir()
        (src / "SKILL.md").write_text("---\nname: bad-exec\n---\n")
        (src / "scripts").mkdir()
        (src / "scripts" / "1.run.py").write_text("# step 1\n")
        (src / "zipsa").mkdir()
        # version field missing from package.yaml
        (src / "zipsa" / "package.yaml").write_text("author: nobody\n")

        skills_home = tmp_path / ".zipsa"
        monkeypatch.setenv("ZIPSA_HOME", str(skills_home))

        result = runner.invoke(app, ["install", "--link", str(src)])
        assert result.exit_code != 0
        assert "version" in result.output.lower() or "error" in result.output.lower()
        # Must NOT create a partial install entry
        dest = skills_home / "skills" / "bad-exec"
        assert not dest.exists(), "partial install created despite error"

    def test_install_exec_missing_name_raises_error(self, tmp_path, monkeypatch):
        """install of an exec skill missing name → clear error, no partial install."""
        src = tmp_path / "no-name"
        src.mkdir()
        # SKILL.md has no name field in frontmatter
        (src / "SKILL.md").write_text("---\ndescription: missing name\n---\n")
        (src / "scripts").mkdir()
        (src / "scripts" / "1.run.py").write_text("# step 1\n")
        (src / "zipsa").mkdir()
        (src / "zipsa" / "package.yaml").write_text("version: 1.0.0\n")

        skills_home = tmp_path / ".zipsa"
        monkeypatch.setenv("ZIPSA_HOME", str(skills_home))

        result = runner.invoke(app, ["install", "--link", str(src)])
        assert result.exit_code != 0
        assert "name" in result.output.lower() or "error" in result.output.lower()

    def test_install_exec_copy(self, tmp_path, monkeypatch):
        """install --path <exec-dir> (copy mode) works for exec skills."""
        src = _make_exec_skill_scripts(tmp_path / "copy-exec", name="copy-exec", version="2.0.0")
        skills_home = tmp_path / ".zipsa"
        monkeypatch.setenv("ZIPSA_HOME", str(skills_home))

        result = runner.invoke(app, ["install", "--path", str(src)])
        assert result.exit_code == 0, result.output
        dest = skills_home / "skills" / "copy-exec"
        assert dest.is_dir()
        meta = json.loads((dest / "_install.json").read_text())
        assert meta["version"] == "2.0.0"


# ---------------------------------------------------------------------------
# D2 — run resolves an installed exec skill to run_skill_llm
# ---------------------------------------------------------------------------


class TestRunExecByName:
    """run <name> dispatches to run_skill_llm for installed exec skills."""

    def test_run_installed_exec_by_name(self, tmp_path, monkeypatch):
        """run <name> for installed exec skill calls run_skill_llm with resolved dir."""
        src = _make_exec_skill_scripts(tmp_path / "exec-skill", name="exec-skill")
        skills_home = tmp_path / ".zipsa"
        monkeypatch.setenv("ZIPSA_HOME", str(skills_home))

        # Install first
        install_result = runner.invoke(app, ["install", "--link", str(src)])
        assert install_result.exit_code == 0, install_result.output

        # Now run by name — mock run_skill_llm
        with patch("zipsa.cli.run_skill_llm", return_value=0) as mock_run:
            result = runner.invoke(app, ["run", "exec-skill", "hello"])

        assert result.exit_code == 0, f"run failed:\n{result.output}\n{result.exception}"
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        # First positional arg is the resolved skill dir
        resolved_dir = call_args[0][0]
        assert resolved_dir.exists(), f"resolved dir does not exist: {resolved_dir}"
        assert _is_exec_format(resolved_dir), "resolved dir is not exec format"

    def test_run_installed_exec_does_not_call_skill_load(self, tmp_path, monkeypatch):
        """run <name> for exec skill must NOT call Skill.load."""
        src = _make_exec_skill_scripts(tmp_path / "pure-exec", name="pure-exec")
        skills_home = tmp_path / ".zipsa"
        monkeypatch.setenv("ZIPSA_HOME", str(skills_home))

        install_result = runner.invoke(app, ["install", "--link", str(src)])
        assert install_result.exit_code == 0, install_result.output

        with patch("zipsa.cli.run_skill_llm", return_value=0):
            with patch("zipsa.cli.Skill") as mock_skill_cls:
                result = runner.invoke(app, ["run", "pure-exec", "query"])

        assert result.exit_code == 0
        mock_skill_cls.load.assert_not_called()

    def test_run_explicit_exec_path_regression(self, tmp_path, monkeypatch):
        """run ./path/to/exec-skill (explicit path) still dispatches to run_skill_llm."""
        skill_dir = _make_exec_skill_scripts(tmp_path / "explicit-exec")
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / ".zipsa"))

        with patch("zipsa.cli.run_skill_llm", return_value=0) as mock_run:
            result = runner.invoke(app, ["run", str(skill_dir), "test query"])

        assert result.exit_code == 0
        mock_run.assert_called_once()

    def test_run_installed_legacy_skill_uses_docker_executor(self, tmp_path, monkeypatch):
        """run <name> for a legacy manifest skill still uses DockerExecutor."""
        from zipsa.core.skill import Skill

        skills_home = tmp_path / ".zipsa"
        skills_dir = skills_home / "skills"
        skills_dir.mkdir(parents=True)
        monkeypatch.setenv("ZIPSA_HOME", str(skills_home))

        # Create a minimal legacy skill in skills dir directly
        legacy_dir = skills_dir / "my-legacy"
        _make_legacy_skill(legacy_dir, name="my-legacy")

        # Write _install.json so check_install passes (in case it's checked)
        (legacy_dir / "_install.json").write_text(
            json.dumps({"version": "1.0.0", "type": "link"})
        )

        mock_skill = MagicMock()
        mock_skill.name = "my-legacy"
        mock_skill.manifest.metadata.version = "1.0.0"
        mock_skill.manifest.spec.children = []
        mock_skill.manifest.spec.requires = {}
        mock_skill.manifest.spec.default_query = None

        with patch("zipsa.cli.Skill") as mock_skill_cls, \
             patch("zipsa.cli.DockerExecutor") as mock_exec_cls, \
             patch("zipsa.cli.run_skill_llm") as mock_run_llm:

            mock_skill_cls.load.return_value = mock_skill
            mock_executor = MagicMock()
            mock_executor.run.return_value = iter([
                {"type": "zipsa_run_complete", "status": "ok", "exit_code": 0},
            ])
            mock_exec_cls.return_value = mock_executor

            result = runner.invoke(app, ["run", "my-legacy", "query"])

        assert result.exit_code == 0
        mock_exec_cls.assert_called_once()  # DockerExecutor was used
        mock_run_llm.assert_not_called()    # LLM runner was NOT used

    def test_run_not_installed_raises_skill_not_installed(self, tmp_path, monkeypatch):
        """run <name> for an unknown skill exits non-zero with a clear error."""
        skills_home = tmp_path / ".zipsa"
        monkeypatch.setenv("ZIPSA_HOME", str(skills_home))

        result = runner.invoke(app, ["run", "nonexistent-skill", "query"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# D4 — zipsa <name> shortcut routes exec skills through run → run_skill_llm
# ---------------------------------------------------------------------------


class TestSkillNameShortcut:
    """zipsa <name> shortcut works for installed exec skills."""

    def test_shortcut_exec_skill_routes_to_run_skill_llm(self, tmp_path, monkeypatch):
        """zipsa <name> for installed exec skill → run_skill_llm (via run rewrite)."""
        src = _make_exec_skill_scripts(tmp_path / "shortcut-exec", name="shortcut-exec")
        skills_home = tmp_path / ".zipsa"
        monkeypatch.setenv("ZIPSA_HOME", str(skills_home))

        install_result = runner.invoke(app, ["install", "--link", str(src)])
        assert install_result.exit_code == 0, install_result.output

        # The shortcut fires in main() by rewriting sys.argv.
        # We test via the app directly (same as run <name>), because
        # _rewrite_argv_for_skill_shortcut is tested by existing tests;
        # what we need to verify here is that once rewritten to "run",
        # an installed exec skill dispatches to run_skill_llm.
        with patch("zipsa.cli.run_skill_llm", return_value=0) as mock_run:
            result = runner.invoke(app, ["run", "shortcut-exec"])

        assert result.exit_code == 0
        mock_run.assert_called_once()
