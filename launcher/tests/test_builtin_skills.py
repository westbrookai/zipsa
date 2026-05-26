"""Tests for built-in skill discovery.

Built-in skills live inside the launcher Python package
(`zipsa/builtin_skills/<name>/`). They behave like installed skills
for `Skill.load` / `zipsa run` / `zipsa list`, but ship with the
launcher (no separate install step) and are tagged "(built-in)" so
the user can distinguish.

Strict policy: a user can't install a skill that collides with a
built-in name — the installer rejects with a clear error so the user
explicitly chooses to fork to a different name.
"""

import json
from pathlib import Path

import pytest
import yaml

from zipsa import paths as zipsa_paths
from zipsa.paths import (
    SkillNotInstalledError,
    builtin_skill_dir,
    builtin_skills_root,
    is_builtin_skill,
    resolve_skill,
)


def _write_skill(root: Path, name: str, *, version: str = "0.1.0") -> Path:
    """Write a minimal new-structure skill at root/<name>/."""
    d = root / name
    dist = d / "zipsa-dist"
    dist.mkdir(parents=True)
    (dist / "manifest.yaml").write_text(yaml.safe_dump({
        "apiVersion": "zipsa.dev/v1alpha1",
        "kind": "Skill",
        "metadata": {"name": name, "version": version},
        "spec": {"purpose": "Test.", "instructions": "./instruction.md"},
    }))
    (dist / "instruction.md").write_text(f"Agent text for {name}.\n")
    (d / "SKILL.md").write_text(f"# {name}\n")
    return d


@pytest.fixture
def fake_builtin_root(tmp_path: Path, monkeypatch) -> Path:
    """Re-point `builtin_skills_root` at a tmp dir so tests can create
    fake built-ins without touching the real package data dir."""
    root = tmp_path / "_builtins"
    root.mkdir()
    monkeypatch.setattr(
        "zipsa.paths.builtin_skills_root", lambda: root,
    )
    return root


@pytest.fixture
def zipsa_home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "_home"))
    (tmp_path / "_home" / "skills").mkdir(parents=True)
    return tmp_path / "_home"


# ─── builtin_skill_dir + is_builtin_skill ─────────────────────────────


class TestBuiltinHelpers:
    def test_builtin_skill_dir_returns_path_when_present(self, fake_builtin_root):
        _write_skill(fake_builtin_root, "demo")
        assert builtin_skill_dir("demo") == fake_builtin_root / "demo"

    def test_builtin_skill_dir_returns_none_when_absent(self, fake_builtin_root):
        assert builtin_skill_dir("missing") is None

    def test_builtin_skill_dir_returns_none_for_non_dir(self, fake_builtin_root):
        # A file with the same name shouldn't trick the helper.
        (fake_builtin_root / "demo").write_text("not a dir")
        assert builtin_skill_dir("demo") is None

    def test_is_builtin_skill_true_when_present(self, fake_builtin_root):
        _write_skill(fake_builtin_root, "demo")
        assert is_builtin_skill("demo") is True

    def test_is_builtin_skill_false_when_absent(self, fake_builtin_root):
        assert is_builtin_skill("demo") is False


# ─── resolve_skill fallback ────────────────────────────────────────────


class TestResolveSkillFallback:
    def test_installed_takes_precedence_over_builtin(
        self, fake_builtin_root, zipsa_home
    ):
        """If the same name exists in both places (would normally be
        prevented by installer guard, but defense-in-depth here),
        installed wins so the user's overrides apply."""
        _write_skill(fake_builtin_root, "demo")
        _write_skill(zipsa_home / "skills", "demo")
        resolved = resolve_skill("demo")
        assert resolved == zipsa_home / "skills" / "demo"

    def test_falls_back_to_builtin_when_not_installed(
        self, fake_builtin_root, zipsa_home
    ):
        _write_skill(fake_builtin_root, "demo")
        # nothing in installed
        resolved = resolve_skill("demo")
        assert resolved == fake_builtin_root / "demo"

    def test_raises_when_neither_present(self, fake_builtin_root, zipsa_home):
        with pytest.raises(SkillNotInstalledError):
            resolve_skill("never-existed")

    def test_load_full_skill_from_builtin_path(self, fake_builtin_root, zipsa_home):
        """End-to-end: resolve_skill → Skill.load reads zipsa-dist/manifest."""
        from zipsa.core.skill import Skill
        _write_skill(fake_builtin_root, "demo")
        skill = Skill.load(resolve_skill("demo"))
        assert skill.name == "demo"
        assert skill.manifest.metadata.version == "0.1.0"


# ─── installer rejects builtin name conflict ───────────────────────────


class TestInstallerNameConflict:
    def test_install_local_rejects_builtin_name(
        self, fake_builtin_root, zipsa_home, tmp_path
    ):
        """User can't shadow a built-in by installing a same-named skill.
        Forces user to fork with a different name."""
        from zipsa.installer import install_local
        _write_skill(fake_builtin_root, "skill-builder")
        # Write a fresh skill the user wants to install — same name
        src = tmp_path / "user-source"
        _write_skill(src.parent, src.name)
        # Need to rename it to "skill-builder" so manifest name conflicts
        src_skill = tmp_path / "my-fork"
        _write_skill(src_skill.parent, "my-fork")
        # Override the manifest's metadata.name to collide
        manifest = src_skill / "zipsa-dist" / "manifest.yaml"
        data = yaml.safe_load(manifest.read_text())
        data["metadata"]["name"] = "skill-builder"
        manifest.write_text(yaml.safe_dump(data))
        with pytest.raises(ValueError, match="built-in"):
            install_local(str(src_skill))


# ─── list_installed surfaces built-ins with tag ─────────────────────────


class TestListIncludesBuiltins:
    def test_zipsa_list_shows_builtin_tag(
        self, fake_builtin_root, zipsa_home, capsys
    ):
        """`zipsa list` should enumerate built-in skills alongside
        installed ones, marked '(built-in)' so the user sees what's
        available out of box."""
        from typer.testing import CliRunner
        from zipsa.cli import app
        _write_skill(fake_builtin_root, "skill-builder")
        _write_skill(zipsa_home / "skills", "weather")
        runner = CliRunner()
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        # Both skills shown
        assert "skill-builder" in result.stdout
        assert "weather" in result.stdout
        # built-in tag on the builtin entry, not on the user one
        # Find the line containing 'skill-builder' and assert (built-in) marker
        sb_line = next(
            line for line in result.stdout.splitlines() if "skill-builder" in line
        )
        assert "built-in" in sb_line.lower()
        weather_line = next(
            line for line in result.stdout.splitlines() if "weather@" in line
        )
        assert "built-in" not in weather_line.lower()
