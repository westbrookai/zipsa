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
            "kind: Skill\n"
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
            "kind: Skill\n"
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
            "kind: Skill\n"
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
