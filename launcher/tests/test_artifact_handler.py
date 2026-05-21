"""Tests for ArtifactHandler — host-side reader for skill-written artifacts."""

import json
import pytest

from zipsa.core.artifact_handler import ArtifactHandler
from zipsa import paths as zipsa_paths


def _make_artifact(tmp_path, monkeypatch, skill, version, run_id, *, name, content):
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
            name="agenthud-report.json", content={"sessions": 3, "projects": ["a", "b"]},
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
            name="draft.txt", content="hello world",
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
            "victim", "0.1.0", "r1", name="innocent.txt", content="ok",
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
            "big", "0.1.0", "r1", name="huge.txt", content="x" * 100,
        )
        path.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
        with pytest.raises(RuntimeError, match="ARTIFACT_TOO_LARGE"):
            ArtifactHandler().run(
                skill="big", version="0.1.0", run_id="r1", name="huge.txt",
            )

    def test_bad_json_rejected(self, tmp_path, monkeypatch):
        _make_artifact(
            tmp_path, monkeypatch,
            "broken", "0.1.0", "r1", name="bad.json", content="{not valid json",
        )
        with pytest.raises(RuntimeError, match="ARTIFACT_BAD_JSON"):
            ArtifactHandler().run(
                skill="broken", version="0.1.0", run_id="r1", name="bad.json",
            )

    def test_null_byte_in_name_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        with pytest.raises(RuntimeError, match="ARTIFACT_BAD_NAME"):
            ArtifactHandler().run(
                skill="x", version="0.1.0", run_id="r1", name="a\x00b.json",
            )

    def test_skill_field_traversal_rejected(self, tmp_path, monkeypatch):
        """`skill` is interpolated directly into the path. A traversal
        payload must be caught up-front (per-segment), not just by the
        ZIPSA_HOME containment guard — otherwise the audit log would
        claim `skill="../../etc"` returned data legitimately."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        with pytest.raises(RuntimeError, match="ARTIFACT_BAD_NAME.*skill"):
            ArtifactHandler().run(
                skill="../../etc", version="0.1.0", run_id="r1", name="passwd",
            )

    def test_run_id_traversal_to_sibling_skill_rejected(self, tmp_path, monkeypatch):
        """The historical bug: run_id='../../victim/runs/real' resolved to
        a different skill's artifacts dir but stayed under ZIPSA_HOME, so
        the containment guard let it through. Per-segment validation
        catches this before the path is even constructed."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        # Set up a "victim" skill with a real artifact
        victim_run = tmp_path / "victim@1.0.0" / "runs" / "r_v"
        (victim_run / "artifacts").mkdir(parents=True)
        (victim_run / "artifacts" / "secret.json").write_text('{"top":"secret"}')

        with pytest.raises(RuntimeError, match="ARTIFACT_BAD_NAME.*run_id"):
            ArtifactHandler().run(
                skill="attacker", version="0.1.0",
                run_id="../../victim@1.0.0/runs/r_v",
                name="secret.json",
            )

    def test_version_field_traversal_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        with pytest.raises(RuntimeError, match="ARTIFACT_BAD_NAME.*version"):
            ArtifactHandler().run(
                skill="x", version="../bad", run_id="r1", name="a.json",
            )

    def test_too_long_name_rejected(self, tmp_path, monkeypatch):
        """A 256-char name would crash with OSError [Errno 63] File name
        too long on most filesystems. The agent's contract only knows
        ARTIFACT_* error codes, so surface this cleanly."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        long_name = "a" * 256
        with pytest.raises(RuntimeError, match="ARTIFACT_BAD_NAME.*name"):
            ArtifactHandler().run(
                skill="x", version="0.1.0", run_id="r1", name=long_name,
            )

    def test_max_length_name_accepted(self, tmp_path, monkeypatch):
        """Exactly 255 chars (POSIX NAME_MAX) is the boundary — accept it.
        The file just won't exist, so we expect ARTIFACT_NOT_FOUND, not
        ARTIFACT_BAD_NAME."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        # Need a real-ish skill dir or NOT_FOUND won't get reached
        from zipsa import paths as zp
        zp.skill_run_artifacts_dir("x", "0.1.0", "r1").mkdir(parents=True)
        max_name = "a" * 255
        with pytest.raises(RuntimeError, match="ARTIFACT_NOT_FOUND"):
            ArtifactHandler().run(
                skill="x", version="0.1.0", run_id="r1", name=max_name,
            )
