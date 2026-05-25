"""Tests for /api/skills/{name}/files/{filename} — raw view of a skill's
manifest.yaml or SKILL.md from the browser. Strictly an allowlist —
arbitrary file reads must 404 to keep the endpoint from becoming a
path-traversal hole."""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient


def _make_skill(root: Path, name: str) -> None:
    d = root / name
    d.mkdir()
    (d / "manifest.yaml").write_text(yaml.safe_dump({
        "apiVersion": "zipsa.dev/v1alpha1",
        "kind": "Skill",
        "metadata": {"name": name, "version": "0.1.0"},
        "spec": {"purpose": "Test.", "instructions": "./SKILL.md"},
    }))
    (d / "SKILL.md").write_text(f"# {name}\n\nDo the thing.\n")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
    (tmp_path / "skills").mkdir()
    _make_skill(tmp_path / "skills", "demo")
    from app import app
    return TestClient(app)


def test_get_manifest_returns_raw_yaml(client):
    resp = client.get("/api/skills/demo/files/manifest.yaml")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "apiVersion: zipsa.dev/v1alpha1" in resp.text
    assert "name: demo" in resp.text


def test_get_skill_md_returns_raw_markdown(client):
    resp = client.get("/api/skills/demo/files/SKILL.md")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "# demo" in resp.text


def test_unknown_filename_is_404(client):
    """Allowlist guards against path traversal and arbitrary reads."""
    resp = client.get("/api/skills/demo/files/.env")
    assert resp.status_code == 404


def test_path_traversal_is_404(client):
    """`../../etc/passwd`-style paths must not escape the skill dir."""
    resp = client.get("/api/skills/demo/files/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code == 404


def test_unknown_skill_is_404(client):
    resp = client.get("/api/skills/never-installed/files/manifest.yaml")
    assert resp.status_code == 404


# -- /view/skills/{name}/files/{filename} (Jinja shell) -------------------


def test_view_route_renders_html_shell(client):
    """The view route returns an HTML page that fetches the raw API and
    renders MD client-side via marked.js. Smoke test: 200 HTML with the
    expected file URL embedded."""
    resp = client.get("/view/skills/demo/files/SKILL.md")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    # The raw-API URL the JS fetches must be on the page
    assert "/api/skills/demo/files/SKILL.md" in body
    # marked.js CDN included for the rendering
    assert "marked" in body


def test_view_route_works_for_manifest_too(client):
    resp = client.get("/view/skills/demo/files/manifest.yaml")
    assert resp.status_code == 200
    assert "/api/skills/demo/files/manifest.yaml" in resp.text
