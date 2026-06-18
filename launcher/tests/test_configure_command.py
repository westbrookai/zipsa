"""Integration tests for the `zipsa configure` command."""

import pytest
import yaml
from pathlib import Path
from typer.testing import CliRunner

from zipsa.cli import app


runner = CliRunner()


def _install_demo_skill(tmp_path: Path, requires_block: str) -> Path:
    """Create a fixture skill linked into ZIPSA_HOME/skills."""
    src = tmp_path / "src" / "demo"
    src.mkdir(parents=True)
    (src / "manifest.yaml").write_text(
        "apiVersion: zipsa.dev/v1alpha1\n"
        "kind: Skill\n"
        "metadata: {name: demo, version: 0.1.0}\n"
        "spec:\n"
        "  purpose: test\n"
        "  instructions: ./SKILL.md\n"
        + requires_block
    )
    (src / "SKILL.md").write_text("# demo")
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "demo").symlink_to(src)
    return src


def _install_exec_skill(tmp_path: Path, package_yaml: str, name: str = "exec-demo") -> Path:
    """Create an exec-format fixture skill linked into ZIPSA_HOME/skills."""
    src = tmp_path / "src" / name
    (src / "scripts").mkdir(parents=True)
    (src / "zipsa").mkdir(parents=True)
    (src / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Demo exec. Use when testing.\n---\n\n# {name}\n"
    )
    (src / "zipsa" / "package.yaml").write_text(package_yaml)
    (src / "scripts" / "1.fetch.py").write_text("print('{}')\n")
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / name).symlink_to(src)
    return src


class TestConfigureExecSkill:
    def test_exec_requires_prompts_and_saves(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        roots = tmp_path / "Code"
        roots.mkdir()
        _install_exec_skill(
            tmp_path,
            "version: 0.3.0\n"
            "requires:\n"
            "  project_roots:\n"
            "    type: list[directory]\n"
            "    prompt: 'where?'\n"
            "    container_prefix: /projects/\n",
        )
        result = runner.invoke(app, ["configure", "exec-demo"], input=f"{roots}\n\n")
        assert result.exit_code == 0, result.output
        saved = yaml.safe_load((tmp_path / "exec-demo@0.3.0" / "requires.yaml").read_text())
        assert saved == {"project_roots": [str(roots.resolve())]}

    def test_exec_no_requires_no_op(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_exec_skill(tmp_path, "version: 0.3.0\n")
        result = runner.invoke(app, ["configure", "exec-demo"])
        assert result.exit_code == 0
        assert "no required configuration" in result.output.lower()


class TestConfigureCommand:
    def test_first_run_saves_values(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        code_dir = tmp_path / "Code"
        code_dir.mkdir()
        _install_demo_skill(tmp_path,
            "  requires:\n"
            "    project_roots:\n"
            "      type: list[directory]\n"
            "      prompt: 'where?'\n"
        )
        # Simulate input: one path, then empty line
        result = runner.invoke(app, ["configure", "demo"], input=f"{code_dir}\n\n")
        assert result.exit_code == 0, result.output
        saved = yaml.safe_load((tmp_path / "demo@0.1.0" / "requires.yaml").read_text())
        assert saved == {"project_roots": [str(code_dir.resolve())]}

    def test_no_tty_exits_4(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_demo_skill(tmp_path,
            "  requires:\n"
            "    name:\n"
            "      type: string\n"
            "      prompt: 'name?'\n"
        )
        # When no input is provided (empty stdin / EOF), configure exits 4.
        # CliRunner provides an empty stdin when no input= is given.
        result = runner.invoke(app, ["configure", "demo"])
        assert result.exit_code == 4

    def test_unknown_skill_exits_1(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        (tmp_path / "skills").mkdir()
        result = runner.invoke(app, ["configure", "nope"])
        assert result.exit_code == 1
        assert "not installed" in result.output.lower()

    def test_skill_without_requires_no_op(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_demo_skill(tmp_path, "")  # no requires block
        result = runner.invoke(app, ["configure", "demo"])
        assert result.exit_code == 0
        assert "no required configuration" in result.output.lower()

    def test_string_type_collects_one_line(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_demo_skill(tmp_path,
            "  requires:\n"
            "    name:\n"
            "      type: string\n"
            "      prompt: 'name?'\n"
        )
        result = runner.invoke(app, ["configure", "demo"], input="hello\n")
        assert result.exit_code == 0, result.output
        saved = yaml.safe_load((tmp_path / "demo@0.1.0" / "requires.yaml").read_text())
        assert saved == {"name": "hello"}

    def test_directory_type_validates_existence(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_demo_skill(tmp_path,
            "  requires:\n"
            "    home:\n"
            "      type: directory\n"
            "      prompt: 'home?'\n"
        )
        # First input: nonexistent path → re-prompt. Second: valid → save.
        valid = tmp_path / "valid"
        valid.mkdir()
        result = runner.invoke(app, ["configure", "demo"], input=f"/no/such/dir\n{valid}\n")
        assert result.exit_code == 0, result.output
        assert "does not exist" in result.output.lower()
        saved = yaml.safe_load((tmp_path / "demo@0.1.0" / "requires.yaml").read_text())
        assert saved == {"home": str(valid.resolve())}

    def test_three_failed_attempts_exits_1(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_demo_skill(tmp_path,
            "  requires:\n"
            "    home:\n"
            "      type: directory\n"
            "      prompt: 'home?'\n"
        )
        # 3 invalid attempts → exit 1
        result = runner.invoke(app, ["configure", "demo"], input="/no\n/no\n/no\n")
        assert result.exit_code == 1
        # No file created
        assert not (tmp_path / "demo@0.1.0" / "requires.yaml").exists()

    def test_existing_values_enter_keeps_them(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        _install_demo_skill(tmp_path,
            "  requires:\n"
            "    name:\n"
            "      type: string\n"
            "      prompt: 'name?'\n"
        )
        (tmp_path / "demo@0.1.0").mkdir(parents=True)
        (tmp_path / "demo@0.1.0" / "requires.yaml").write_text("name: original\n")
        # Press enter → keep
        result = runner.invoke(app, ["configure", "demo"], input="\n")
        assert result.exit_code == 0, result.output
        saved = yaml.safe_load((tmp_path / "demo@0.1.0" / "requires.yaml").read_text())
        assert saved == {"name": "original"}
