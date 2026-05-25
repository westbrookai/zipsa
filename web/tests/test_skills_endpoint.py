"""Tests for /api/skills — returns the installed-skill catalog as JSON."""

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


@pytest.fixture
def fake_skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    _make_skill(root, "hello-world", version="0.1.0", purpose="Smoke test.",
                model_name="claude-sonnet-4-6", description="A smoke test")
    _make_skill(root, "weather", version="0.2.0", purpose="Report the weather.")
    return root


@pytest.fixture
def client(fake_skills_root, monkeypatch):
    monkeypatch.setattr("zipsa.paths.skills_dir", lambda: fake_skills_root)
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
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr("zipsa.paths.skills_dir", lambda: empty)
    from app import app
    client = TestClient(app)
    resp = client.get("/api/skills")
    assert resp.status_code == 200
    assert resp.json() == {"skills": []}


def test_missing_skills_root_returns_empty_list(tmp_path, monkeypatch):
    nonexistent = tmp_path / "nope"
    monkeypatch.setattr("zipsa.paths.skills_dir", lambda: nonexistent)
    from app import app
    client = TestClient(app)
    resp = client.get("/api/skills")
    assert resp.status_code == 200
    assert resp.json() == {"skills": []}


def test_broken_skill_silently_skipped(fake_skills_root, monkeypatch):
    """A directory with no manifest.yaml shouldn't crash the endpoint;
    it should just be omitted from the list."""
    broken = fake_skills_root / "broken"
    broken.mkdir()
    # No manifest.yaml — Skill.load will fail
    monkeypatch.setattr("zipsa.paths.skills_dir", lambda: fake_skills_root)
    from app import app
    client = TestClient(app)
    resp = client.get("/api/skills")
    assert resp.status_code == 200
    names = sorted(s["name"] for s in resp.json()["skills"])
    assert names == ["hello-world", "weather"]
    assert "broken" not in names
