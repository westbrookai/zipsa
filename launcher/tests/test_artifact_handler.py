"""Tests for ArtifactHandler — host-side reader for skill-written artifacts."""

import json
import pytest

from zipsa.core.artifact_handler import ArtifactHandler
from zipsa import paths as zipsa_paths


def _make_artifact(tmp_path, monkeypatch, skill, version, run_id, name, content):
    monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
    artifacts_dir = zipsa_paths.skill_run_artifacts_dir(skill, version, run_id)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    path = artifacts_dir / name
    if isinstance(content, (dict, list)):
        path.write_text(json.dumps(content))
    else:
        path.write_text(content)
    return path


class TestArtifactHandler:
    def test_reads_json_artifact(self, tmp_path, monkeypatch):
        _make_artifact(
            tmp_path, monkeypatch,
            "agenthud-report", "0.1.0", "2026-05-21_120000_000",
            "agenthud-report.json", {"sessions": 3, "projects": ["a", "b"]},
        )
        result = ArtifactHandler().run(
            skill="agenthud-report",
            version="0.1.0",
            run_id="2026-05-21_120000_000",
            name="agenthud-report.json",
        )
        assert result["name"] == "agenthud-report.json"
        assert result["content"] == {"sessions": 3, "projects": ["a", "b"]}
        assert result["size"] > 0

    def test_reads_text_artifact(self, tmp_path, monkeypatch):
        _make_artifact(
            tmp_path, monkeypatch,
            "x-post", "0.1.0", "r1",
            "draft.txt", "hello world",
        )
        result = ArtifactHandler().run(
            skill="x-post", version="0.1.0", run_id="r1", name="draft.txt",
        )
        assert result["content"] == "hello world"

    def test_missing_artifact_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        zipsa_paths.skill_run_artifacts_dir("x", "0.1.0", "r1").mkdir(parents=True)
        with pytest.raises(RuntimeError, match="ARTIFACT_NOT_FOUND"):
            ArtifactHandler().run(skill="x", version="0.1.0", run_id="r1", name="nope.json")

    def test_path_traversal_dotdot_rejected(self, tmp_path, monkeypatch):
        _make_artifact(
            tmp_path, monkeypatch,
            "victim", "0.1.0", "r1", "innocent.txt", "ok",
        )
        with pytest.raises(RuntimeError, match="ARTIFACT_BAD_NAME"):
            ArtifactHandler().run(
                skill="victim", version="0.1.0", run_id="r1",
                name="../../../etc/passwd",
            )

    def test_path_traversal_absolute_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        with pytest.raises(RuntimeError, match="ARTIFACT_BAD_NAME"):
            ArtifactHandler().run(
                skill="x", version="0.1.0", run_id="r1", name="/etc/passwd",
            )

    def test_path_with_subdir_rejected(self, tmp_path, monkeypatch):
        """`name` must be a flat filename, no slashes."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        with pytest.raises(RuntimeError, match="ARTIFACT_BAD_NAME"):
            ArtifactHandler().run(
                skill="x", version="0.1.0", run_id="r1", name="sub/file.json",
            )

    def test_too_large_artifact_rejected(self, tmp_path, monkeypatch):
        path = _make_artifact(
            tmp_path, monkeypatch,
            "big", "0.1.0", "r1", "huge.txt", "x" * 100,
        )
        # Overwrite with >10MB content
        path.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
        with pytest.raises(RuntimeError, match="ARTIFACT_TOO_LARGE"):
            ArtifactHandler().run(
                skill="big", version="0.1.0", run_id="r1", name="huge.txt",
            )

    def test_bad_json_rejected(self, tmp_path, monkeypatch):
        _make_artifact(
            tmp_path, monkeypatch,
            "broken", "0.1.0", "r1", "bad.json", "{not valid json",
        )
        with pytest.raises(RuntimeError, match="ARTIFACT_BAD_JSON"):
            ArtifactHandler().run(
                skill="broken", version="0.1.0", run_id="r1", name="bad.json",
            )
