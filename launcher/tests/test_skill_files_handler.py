"""Tests for SkillFilesHandler — writes skill-builder's draft to
~/.zipsa/staging/<name>/.

Capability: only three filenames allowed (the author SKILL.md plus the
two zipsa-dist files). Any other filename, a name with path separators,
or a target outside ~/.zipsa/staging/ is rejected — this is the
authority the MCP tool holds, and we want it tight.
"""

from pathlib import Path

import pytest

from zipsa.core.skill_files_handler import SkillFilesHandler


@pytest.fixture
def zipsa_home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
    return tmp_path


class TestSkillFilesHandler:
    def test_writes_author_skill_md(self, zipsa_home):
        h = SkillFilesHandler()
        result = h.write(
            name="my-skill",
            files={"SKILL.md": "# my-skill\n\nDo the thing.\n"},
        )
        out = zipsa_home / "staging" / "my-skill" / "SKILL.md"
        assert out.read_text().startswith("# my-skill")
        assert result["written_files"] == ["SKILL.md"]
        assert Path(result["path"]) == (zipsa_home / "staging" / "my-skill").resolve()

    def test_writes_zipsa_dist_files(self, zipsa_home):
        """The two zipsa-dist files land under the zipsa-dist/ subdir,
        which is created if missing."""
        h = SkillFilesHandler()
        h.write(
            name="my-skill",
            files={
                "zipsa-dist/manifest.yaml": "apiVersion: zipsa.dev/v1alpha1\n",
                "zipsa-dist/instruction.md": "Agent text.\n",
            },
        )
        base = zipsa_home / "staging" / "my-skill" / "zipsa-dist"
        assert (base / "manifest.yaml").read_text().startswith("apiVersion")
        assert (base / "instruction.md").read_text() == "Agent text.\n"

    def test_writes_full_set(self, zipsa_home):
        h = SkillFilesHandler()
        result = h.write(
            name="my-skill",
            files={
                "SKILL.md": "# my-skill\n",
                "zipsa-dist/manifest.yaml": "apiVersion: zipsa.dev/v1alpha1\n",
                "zipsa-dist/instruction.md": "Agent text.\n",
            },
        )
        assert sorted(result["written_files"]) == [
            "SKILL.md",
            "zipsa-dist/instruction.md",
            "zipsa-dist/manifest.yaml",
        ]

    def test_overwrites_existing_files(self, zipsa_home):
        """skill-builder iterates — re-writing must replace, not append."""
        h = SkillFilesHandler()
        h.write(name="my-skill", files={"SKILL.md": "v1\n"})
        h.write(name="my-skill", files={"SKILL.md": "v2 different content\n"})
        out = zipsa_home / "staging" / "my-skill" / "SKILL.md"
        assert out.read_text() == "v2 different content\n"

    def test_rejects_unknown_filename(self, zipsa_home):
        """Allowlist: only the three filenames. README.md, config.json,
        etc. all 404 — the agent must use SKILL.md / zipsa-dist/*."""
        h = SkillFilesHandler()
        with pytest.raises(RuntimeError, match="SKILL_FILE_BAD_NAME"):
            h.write(name="my-skill", files={"README.md": "..."})

    def test_rejects_path_traversal_in_name(self, zipsa_home):
        h = SkillFilesHandler()
        for bad_name in ("../other", "a/b", "..", "abs/path"):
            with pytest.raises(RuntimeError, match="SKILL_NAME_BAD"):
                h.write(name=bad_name, files={"SKILL.md": "x"})

    def test_rejects_empty_name(self, zipsa_home):
        h = SkillFilesHandler()
        with pytest.raises(RuntimeError, match="SKILL_NAME_BAD"):
            h.write(name="", files={"SKILL.md": "x"})

    def test_rejects_absolute_path_in_filename(self, zipsa_home):
        h = SkillFilesHandler()
        with pytest.raises(RuntimeError, match="SKILL_FILE_BAD_NAME"):
            h.write(name="my-skill", files={"/etc/passwd": "x"})

    def test_rejects_traversal_in_filename(self, zipsa_home):
        h = SkillFilesHandler()
        with pytest.raises(RuntimeError, match="SKILL_FILE_BAD_NAME"):
            h.write(name="my-skill", files={"zipsa-dist/../other.md": "x"})

    def test_rejects_empty_files_dict(self, zipsa_home):
        h = SkillFilesHandler()
        with pytest.raises(RuntimeError, match="SKILL_FILES_EMPTY"):
            h.write(name="my-skill", files={})

    def test_rejects_non_string_content(self, zipsa_home):
        h = SkillFilesHandler()
        with pytest.raises(RuntimeError, match="SKILL_FILE_BAD_CONTENT"):
            h.write(name="my-skill", files={"SKILL.md": 42})
