"""Tests for the install-health detection helper."""

from pathlib import Path

import pytest

from zipsa.core.install_health import InstallHealth, check_install


class TestHealthyDetection:
    def test_real_directory_with_valid_manifest_is_ok(self, tmp_path):
        d = tmp_path / "skill-a"
        d.mkdir()
        (d / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: SkillManifest\n"
            "metadata:\n"
            "  name: skill-a\n"
            "  version: 1.0.0\n"
            "spec:\n"
            "  purpose: Test.\n"
            "  instructions: ./SKILL.md\n"
        )
        h = check_install(d)
        assert isinstance(h, InstallHealth)
        assert h.ok is True
        assert h.reason is None

    def test_valid_symlink_with_valid_manifest_is_ok(self, tmp_path):
        src = tmp_path / "src" / "skill-a"
        src.mkdir(parents=True)
        (src / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: SkillManifest\n"
            "metadata:\n"
            "  name: skill-a\n"
            "  version: 1.0.0\n"
            "spec:\n"
            "  purpose: Test.\n"
            "  instructions: ./SKILL.md\n"
        )
        link = tmp_path / "installed" / "skill-a"
        link.parent.mkdir()
        link.symlink_to(src)
        h = check_install(link)
        assert h.ok is True


class TestNewStructureDetection:
    """check_install should also recognize `zipsa-dist/manifest.yaml`
    (the layout skill-builder writes). Legacy root-level manifest.yaml
    keeps working — tested in TestHealthyDetection above."""

    @staticmethod
    def _write_new_layout(root: Path, name: str = "skill-a") -> None:
        dist = root / "zipsa-dist"
        dist.mkdir(parents=True)
        (dist / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: SkillManifest\n"
            "metadata:\n"
            f"  name: {name}\n"
            "  version: 1.0.0\n"
            "spec:\n"
            "  purpose: Test.\n"
            "  instructions: ./instruction.md\n"
        )
        (dist / "instruction.md").write_text("agent text\n")

    def test_new_structure_is_ok(self, tmp_path):
        d = tmp_path / "skill-a"
        d.mkdir()
        self._write_new_layout(d)
        h = check_install(d)
        assert h.ok is True
        assert h.reason is None

    def test_new_structure_via_symlink_is_ok(self, tmp_path):
        src = tmp_path / "src" / "skill-a"
        src.mkdir(parents=True)
        self._write_new_layout(src)
        link = tmp_path / "installed" / "skill-a"
        link.parent.mkdir()
        link.symlink_to(src)
        h = check_install(link)
        assert h.ok is True

    def test_neither_layout_reports_missing(self, tmp_path):
        d = tmp_path / "skill-a"
        d.mkdir()
        # no manifest in either location
        h = check_install(d)
        assert h.ok is False
        assert "manifest.yaml" in h.reason


class TestBrokenDetection:
    def test_dangling_symlink_reports_linked_source_missing(self, tmp_path):
        gone = tmp_path / "removed-source"
        link = tmp_path / "skill-a"
        link.symlink_to(gone)  # gone never created
        h = check_install(link)
        assert h.ok is False
        assert "Linked source missing" in h.reason
        assert str(gone) in h.reason

    def test_directory_without_manifest_reports_missing_manifest(self, tmp_path):
        d = tmp_path / "skill-a"
        d.mkdir()
        # no manifest.yaml
        h = check_install(d)
        assert h.ok is False
        assert "manifest.yaml not found" in h.reason

    def test_invalid_manifest_reports_invalid(self, tmp_path):
        d = tmp_path / "skill-a"
        d.mkdir()
        (d / "manifest.yaml").write_text("not: { valid yaml: : :")
        h = check_install(d)
        assert h.ok is False
        assert "Invalid manifest" in h.reason

    def test_manifest_failing_pydantic_validation_reports_invalid(self, tmp_path):
        d = tmp_path / "skill-a"
        d.mkdir()
        (d / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: SkillManifest\n"
            "metadata: {}  # missing required fields\n"
            "spec: {}\n"
        )
        h = check_install(d)
        assert h.ok is False
        assert "Invalid manifest" in h.reason

    def test_symlink_to_dir_without_manifest_reports_missing_manifest(self, tmp_path):
        src = tmp_path / "src" / "skill-a"
        src.mkdir(parents=True)
        # source exists but has no manifest
        link = tmp_path / "installed" / "skill-a"
        link.parent.mkdir()
        link.symlink_to(src)
        h = check_install(link)
        assert h.ok is False
        assert "manifest.yaml not found" in h.reason

    def test_dangling_relative_symlink_reports_resolved_path(self, tmp_path):
        """Relative symlink targets should be resolved to absolute paths in
        the error message — otherwise users see '../foo' which is meaningless
        out of context."""
        link = tmp_path / "skill-a"
        link.symlink_to("../gone-relative")  # relative target
        h = check_install(link)
        assert h.ok is False
        assert "Linked source missing" in h.reason
        # The absolute resolved path should appear, not the bare relative one
        expected_resolved = (tmp_path / ".." / "gone-relative").resolve()
        assert str(expected_resolved) in h.reason


class TestRequiresStatus:
    def test_skill_without_requires_reports_zero(self, tmp_path):
        from zipsa.core.install_health import check_install
        d = tmp_path / "s"
        d.mkdir()
        (d / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: SkillManifest\n"
            "metadata: {name: s, version: 1.0.0}\n"
            "spec: {purpose: x, instructions: ./SKILL.md}\n"
        )
        (d / "SKILL.md").write_text("# x")
        h = check_install(d)
        assert h.requires_total == 0
        assert h.requires_set == 0

    def test_skill_with_requires_no_values(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        d = tmp_path / "skills" / "s"
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: SkillManifest\n"
            "metadata: {name: s, version: 1.0.0}\n"
            "spec:\n"
            "  purpose: x\n"
            "  instructions: ./SKILL.md\n"
            "  requires:\n"
            "    a: {type: string, prompt: 'a?'}\n"
            "    b: {type: string, prompt: 'b?'}\n"
        )
        (d / "SKILL.md").write_text("# x")
        from zipsa.core.install_health import check_install
        h = check_install(d)
        assert h.requires_total == 2
        assert h.requires_set == 0

    def test_skill_with_requires_some_values_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        d = tmp_path / "skills" / "s"
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text(
            "apiVersion: zipsa.dev/v1alpha1\n"
            "kind: SkillManifest\n"
            "metadata: {name: s, version: 1.0.0}\n"
            "spec:\n"
            "  purpose: x\n"
            "  instructions: ./SKILL.md\n"
            "  requires:\n"
            "    a: {type: string, prompt: 'a?'}\n"
            "    b: {type: string, prompt: 'b?'}\n"
        )
        (d / "SKILL.md").write_text("# x")
        # Save only a
        (tmp_path / "s@1.0.0").mkdir()
        (tmp_path / "s@1.0.0" / "requires.yaml").write_text("a: hello\n")
        from zipsa.core.install_health import check_install
        h = check_install(d)
        assert h.requires_total == 2
        assert h.requires_set == 1
