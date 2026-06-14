"""Tests for PromoteSkillHandler — host-side body of mcp__zipsa__promote.

When the authoring conversation lands on a name, the agent calls
mcp__zipsa__promote(staging_path, name). The handler (host-side)
validates the slug, checks the destination is free, and moves the
staging dir into the repo's skills/<name>/ — the only step that touches
the repo.
"""

from __future__ import annotations

from pathlib import Path

from zipsa.core.promote_skill_handler import PromoteSkillHandler


def _staging(home: Path, temp_id: str) -> Path:
    d = home / "staging" / temp_id / "zipsa-dist"
    d.mkdir(parents=True)
    (d / "1.do.py").write_text("print('{}')\n")
    (home / "staging" / temp_id / "SKILL.md").write_text("# draft\n")
    return home / "staging" / temp_id


class TestPromoteSkillHandler:
    def test_moves_staging_into_skills(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        staging = _staging(tmp_path / "home", "abc123")
        dest_root = tmp_path / "repo" / "skills"
        dest_root.mkdir(parents=True)

        out = PromoteSkillHandler(dest_root=dest_root).run(
            staging_path=str(staging), name="umbrella-reminder",
        )

        assert out["status"] == "ok"
        final = dest_root / "umbrella-reminder"
        assert out["path"] == str(final)
        assert (final / "zipsa-dist" / "1.do.py").exists()
        assert not staging.exists()  # moved, not copied

    def test_bad_slug_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        staging = _staging(tmp_path / "home", "abc123")
        dest_root = tmp_path / "repo" / "skills"
        dest_root.mkdir(parents=True)

        for bad in ["Umbrella", "with space", "under_score", "-leading", "백"]:
            out = PromoteSkillHandler(dest_root=dest_root).run(
                staging_path=str(staging), name=bad,
            )
            assert out["status"] == "failed", bad
            assert out["error"]["code"] == "promote_bad_name", bad
        # nothing moved
        assert staging.exists()

    def test_collision_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        staging = _staging(tmp_path / "home", "abc123")
        dest_root = tmp_path / "repo" / "skills"
        (dest_root / "taken").mkdir(parents=True)

        out = PromoteSkillHandler(dest_root=dest_root).run(
            staging_path=str(staging), name="taken",
        )

        assert out["status"] == "failed"
        assert out["error"]["code"] == "promote_name_taken"
        assert staging.exists()

    def test_path_outside_staging_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        outside = tmp_path / "elsewhere" / "x"
        outside.mkdir(parents=True)
        dest_root = tmp_path / "repo" / "skills"
        dest_root.mkdir(parents=True)

        out = PromoteSkillHandler(dest_root=dest_root).run(
            staging_path=str(outside), name="ok-name",
        )

        assert out["status"] == "failed"
        assert out["error"]["code"] == "promote_path_outside_staging"

    def test_missing_staging_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        (tmp_path / "home").mkdir()
        gone = tmp_path / "home" / "staging" / "gone"
        dest_root = tmp_path / "repo" / "skills"
        dest_root.mkdir(parents=True)

        out = PromoteSkillHandler(dest_root=dest_root).run(
            staging_path=str(gone), name="ok-name",
        )

        assert out["status"] == "failed"
        assert out["error"]["code"] == "promote_staging_not_found"
