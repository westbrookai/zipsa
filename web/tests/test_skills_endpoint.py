"""Tests for /api/skills — returns the installed-skill catalog as JSON."""

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient


def _make_skill(root: Path, name: str, *, version: str, purpose: str,
                model_name: str | None = None, description: str | None = None) -> None:
    """Write a minimal-but-valid SkillManifest + SKILL.md under root/<name>/."""
    d = root / name
    d.mkdir()
    spec = {
        "purpose": purpose,
        "instructions": "./SKILL.md",  # required by SkillSpec
    }
    if model_name:
        spec["model"] = {"name": model_name}
    metadata = {"name": name, "version": version}
    if description:
        metadata["description"] = description
    (d / "manifest.yaml").write_text(yaml.safe_dump({
        "apiVersion": "zipsa.dev/v1alpha1",
        "kind": "Skill",
        "metadata": metadata,
        "spec": spec,
    }))
    (d / "SKILL.md").write_text(f"# {name}\n\n{purpose}\n")


def _make_run(zipsa_home: Path, skill_name: str, version: str,
              run_id: str, status: str) -> None:
    """Write a fake summary.json under <home>/<name>@<version>/runs/<id>/."""
    run_dir = zipsa_home / f"{skill_name}@{version}" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({"status": status}))


@pytest.fixture
def zipsa_home_env(tmp_path: Path, monkeypatch) -> Path:
    """Point ZIPSA_HOME at a fresh tmp dir — this drives every path
    helper (skills_dir, skill_data_dir, …) so we don't need to monkey-
    patch them individually."""
    monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
    (tmp_path / "skills").mkdir()
    return tmp_path


@pytest.fixture
def fake_skills_root(zipsa_home_env: Path) -> Path:
    root = zipsa_home_env / "skills"
    _make_skill(root, "hello-world", version="0.1.0", purpose="Smoke test.",
                model_name="claude-sonnet-4-6", description="A smoke test")
    _make_skill(root, "weather", version="0.2.0", purpose="Report the weather.")
    return root


@pytest.fixture
def client(fake_skills_root):
    from app import app
    return TestClient(app)


def test_get_skills_returns_installed_skills(client):
    resp = client.get("/api/skills")
    assert resp.status_code == 200
    data = resp.json()
    assert "skills" in data
    names = sorted(s["name"] for s in data["skills"])
    assert names == ["hello-world", "weather"]


def test_skill_entry_shape(client):
    resp = client.get("/api/skills")
    weather = next(s for s in resp.json()["skills"] if s["name"] == "weather")
    assert weather["version"] == "0.2.0"
    assert weather["purpose"] == "Report the weather."
    # Optional fields: model may be None when not pinned by manifest.
    assert weather["model"] is None


def test_skill_entry_with_optional_fields(client):
    """When manifest provides model + description, they surface."""
    resp = client.get("/api/skills")
    hw = next(s for s in resp.json()["skills"] if s["name"] == "hello-world")
    assert hw["model"] == "claude-sonnet-4-6"
    assert hw["description"] == "A smoke test"


def test_empty_skills_root_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
    (tmp_path / "skills").mkdir()
    from app import app
    client = TestClient(app)
    resp = client.get("/api/skills")
    assert resp.status_code == 200
    assert resp.json() == {"skills": []}


def test_missing_skills_root_returns_empty_list(tmp_path, monkeypatch):
    """If skills/ doesn't exist (fresh install), don't crash."""
    monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
    # no skills/ subdir created
    from app import app
    client = TestClient(app)
    resp = client.get("/api/skills")
    assert resp.status_code == 200
    assert resp.json() == {"skills": []}


def test_broken_skill_silently_skipped(client, fake_skills_root):
    """A directory with no manifest.yaml shouldn't crash the endpoint."""
    broken = fake_skills_root / "broken"
    broken.mkdir()
    resp = client.get("/api/skills")
    assert resp.status_code == 200
    names = sorted(s["name"] for s in resp.json()["skills"])
    assert names == ["hello-world", "weather"]
    assert "broken" not in names


def test_run_stats_count_summaries_across_versions(client, zipsa_home_env):
    """total_runs + successful_runs aggregate across every <name>@*
    dir, matching the CLI's `zipsa list` behavior."""
    _make_run(zipsa_home_env, "hello-world", "0.1.0", "r1", "ok")
    _make_run(zipsa_home_env, "hello-world", "0.1.0", "r2", "ok")
    _make_run(zipsa_home_env, "hello-world", "0.1.0", "r3", "failed")
    # A run from a previous version still counts toward the total
    _make_run(zipsa_home_env, "hello-world", "0.0.9", "r0", "ok")
    resp = client.get("/api/skills")
    hw = next(s for s in resp.json()["skills"] if s["name"] == "hello-world")
    assert hw["total_runs"] == 4
    assert hw["successful_runs"] == 3


def test_run_stats_zero_when_never_run(client):
    """A freshly installed skill with no runs/ dir should report zero."""
    resp = client.get("/api/skills")
    weather = next(s for s in resp.json()["skills"] if s["name"] == "weather")
    assert weather["total_runs"] == 0
    assert weather["successful_runs"] == 0


def test_run_stats_skip_unparseable_summary(client, zipsa_home_env):
    """A truncated/garbage summary.json shouldn't crash the endpoint."""
    run_dir = zipsa_home_env / "weather@0.2.0" / "runs" / "rbad"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text("not json {")
    # Plus one good one
    _make_run(zipsa_home_env, "weather", "0.2.0", "rgood", "ok")
    resp = client.get("/api/skills")
    weather = next(s for s in resp.json()["skills"] if s["name"] == "weather")
    assert weather["total_runs"] == 1
    assert weather["successful_runs"] == 1
