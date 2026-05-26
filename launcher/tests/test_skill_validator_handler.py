"""Tests for SkillValidatorHandler — validates a skill directory and
returns a structured result.

skill-builder calls this right after writing the draft so it can either
confirm "✓ valid, ready to install" to the author or feed pydantic
errors back into its own iteration loop. Mirrors what `zipsa validate`
does at the CLI but returns JSON instead of printing.
"""

from pathlib import Path

import pytest
import yaml

from zipsa.core.skill_validator_handler import SkillValidatorHandler


def _write_legacy_skill(root: Path, *, name: str = "demo", broken: bool = False) -> Path:
    """Write a legacy-layout skill. If broken=True, manifest is missing a
    required field so pydantic complains."""
    d = root / name
    d.mkdir()
    spec = {"purpose": "test"}
    if not broken:
        spec["instructions"] = "./SKILL.md"
    (d / "manifest.yaml").write_text(yaml.safe_dump({
        "apiVersion": "zipsa.dev/v1alpha1",
        "kind": "Skill",
        "metadata": {"name": name, "version": "0.1.0"},
        "spec": spec,
    }))
    (d / "SKILL.md").write_text("# demo\n")
    return d


def _write_new_skill(root: Path, *, name: str = "demo") -> Path:
    """Write a new zipsa-dist/ layout skill."""
    d = root / name
    dist = d / "zipsa-dist"
    dist.mkdir(parents=True)
    (dist / "manifest.yaml").write_text(yaml.safe_dump({
        "apiVersion": "zipsa.dev/v1alpha1",
        "kind": "Skill",
        "metadata": {"name": name, "version": "0.1.0"},
        "spec": {"purpose": "test", "instructions": "./instruction.md"},
    }))
    (dist / "instruction.md").write_text("Agent text.\n")
    (d / "SKILL.md").write_text("# demo (author)\n")
    return d


@pytest.fixture
def zipsa_home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
    return tmp_path


class TestSkillValidatorHandler:
    def test_valid_legacy_skill_returns_ok(self, zipsa_home):
        staging = zipsa_home / "staging"
        staging.mkdir()
        skill_dir = _write_legacy_skill(staging)
        result = SkillValidatorHandler().validate(path=str(skill_dir))
        assert result["ok"] is True
        assert result["name"] == "demo"
        assert result["version"] == "0.1.0"
        assert result["errors"] == []

    def test_valid_new_structure_skill_returns_ok(self, zipsa_home):
        staging = zipsa_home / "staging"
        staging.mkdir()
        skill_dir = _write_new_skill(staging)
        result = SkillValidatorHandler().validate(path=str(skill_dir))
        assert result["ok"] is True
        assert result["name"] == "demo"

    def test_missing_manifest_returns_errors(self, zipsa_home):
        staging = zipsa_home / "staging" / "demo"
        staging.mkdir(parents=True)
        # No manifest anywhere.
        result = SkillValidatorHandler().validate(path=str(staging))
        assert result["ok"] is False
        assert result["errors"]
        assert any("manifest" in e.lower() for e in result["errors"])

    def test_pydantic_validation_failure_returns_structured_errors(self, zipsa_home):
        staging = zipsa_home / "staging"
        staging.mkdir()
        skill_dir = _write_legacy_skill(staging, broken=True)
        result = SkillValidatorHandler().validate(path=str(skill_dir))
        assert result["ok"] is False
        # The missing `instructions` field should be cited in the errors
        assert result["errors"]
        assert any("instructions" in e.lower() for e in result["errors"])

    def test_path_outside_zipsa_home_rejected(self, zipsa_home, tmp_path):
        """Defense in depth — the MCP tool's authority is to validate
        ZIPSA_HOME-resident skills (typically staging). Validating an
        arbitrary host path is out of scope and a potential info leak."""
        outside = tmp_path.parent / "outside"
        outside.mkdir(exist_ok=True)
        with pytest.raises(RuntimeError, match="SKILL_PATH_OUTSIDE_HOME"):
            SkillValidatorHandler().validate(path=str(outside))

    def test_nonexistent_path_returns_errors(self, zipsa_home):
        staging = zipsa_home / "staging" / "ghost"
        # Don't create it. Path doesn't exist but is inside ZIPSA_HOME.
        result = SkillValidatorHandler().validate(path=str(staging))
        assert result["ok"] is False
        assert result["errors"]
