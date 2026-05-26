"""Tests for SkillCatalogHandler — list installed skills as JSON.

Used by the upcoming skill-builder MCP tool list_skills_catalog so the
authoring agent can say things like "weather already exists, do you
want to recreate it?" or pick existing atomic skills as children.
"""

import json
from pathlib import Path

import pytest
import yaml

from zipsa.core.skill_catalog_handler import SkillCatalogHandler


def _write_skill(skills_root: Path, name: str, *, version: str,
                 purpose: str, description: str | None = None) -> None:
    """Write a minimal legacy-layout skill into skills_root/<name>/."""
    d = skills_root / name
    d.mkdir()
    metadata = {"name": name, "version": version}
    if description:
        metadata["description"] = description
    (d / "manifest.yaml").write_text(yaml.safe_dump({
        "apiVersion": "zipsa.dev/v1alpha1",
        "kind": "SkillManifest",
        "metadata": metadata,
        "spec": {"purpose": purpose, "instructions": "./SKILL.md"},
    }))
    (d / "SKILL.md").write_text(f"# {name}\n")


def _write_run_summary(home: Path, name: str, version: str, run_id: str,
                       status: str) -> None:
    """Write a summary.json under ~/.zipsa/<name>@<version>/runs/<id>/."""
    run_dir = home / f"{name}@{version}" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({"status": status}))


@pytest.fixture
def zipsa_home_env(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
    (tmp_path / "skills").mkdir()
    return tmp_path


class TestSkillCatalogHandler:
    def test_lists_installed_skills(self, zipsa_home_env):
        _write_skill(zipsa_home_env / "skills", "weather",
                     version="0.5.0", purpose="Report current weather.")
        _write_skill(zipsa_home_env / "skills", "hello-world",
                     version="0.1.0", purpose="Smoke test.")
        result = SkillCatalogHandler().run()
        names = sorted(s["name"] for s in result["skills"])
        assert names == ["hello-world", "weather"]

    def test_skill_entry_includes_purpose_and_version(self, zipsa_home_env):
        _write_skill(zipsa_home_env / "skills", "weather",
                     version="0.5.0", purpose="Report current weather.",
                     description="Weather reporting skill")
        result = SkillCatalogHandler().run()
        weather = next(s for s in result["skills"] if s["name"] == "weather")
        assert weather["version"] == "0.5.0"
        assert weather["purpose"] == "Report current weather."
        assert weather["description"] == "Weather reporting skill"

    def test_aggregates_run_stats_across_versions(self, zipsa_home_env):
        """Matches `zipsa list` behavior: runs from any <name>@* count."""
        _write_skill(zipsa_home_env / "skills", "weather",
                     version="0.5.0", purpose="Weather.")
        _write_run_summary(zipsa_home_env, "weather", "0.5.0", "r1", "ok")
        _write_run_summary(zipsa_home_env, "weather", "0.5.0", "r2", "ok")
        _write_run_summary(zipsa_home_env, "weather", "0.5.0", "r3", "failed")
        _write_run_summary(zipsa_home_env, "weather", "0.4.9", "r0", "ok")
        result = SkillCatalogHandler().run()
        weather = next(s for s in result["skills"] if s["name"] == "weather")
        assert weather["total_runs"] == 4
        assert weather["successful_runs"] == 3

    def test_never_run_skill_reports_zero(self, zipsa_home_env):
        _write_skill(zipsa_home_env / "skills", "hello-world",
                     version="0.1.0", purpose="Smoke.")
        result = SkillCatalogHandler().run()
        hw = next(s for s in result["skills"] if s["name"] == "hello-world")
        assert hw["total_runs"] == 0
        assert hw["successful_runs"] == 0

    def test_broken_manifest_is_skipped(self, zipsa_home_env):
        """Bad manifest shouldn't crash the tool — agent gets the others."""
        _write_skill(zipsa_home_env / "skills", "weather",
                     version="0.5.0", purpose="Weather.")
        broken = zipsa_home_env / "skills" / "broken"
        broken.mkdir()
        # no manifest.yaml
        result = SkillCatalogHandler().run()
        names = sorted(s["name"] for s in result["skills"])
        assert names == ["weather"]

    def test_empty_skills_dir_returns_empty_list(self, zipsa_home_env):
        result = SkillCatalogHandler().run()
        assert result == {"skills": []}

    def test_missing_skills_dir_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        # no skills/ subdir at all
        result = SkillCatalogHandler().run()
        assert result == {"skills": []}
