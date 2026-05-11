"""Tests for zipsa.installer — source parsing, download, local install."""

import pytest
from zipsa.installer import GitHubSource, parse_github_source


class TestParseGithubSource:
    def test_user_repo(self):
        s = parse_github_source("westbrookai/zipsa")
        assert s.user == "westbrookai"
        assert s.repo == "zipsa"
        assert s.subpath == ""
        assert s.ref == "HEAD"

    def test_user_repo_subpath(self):
        s = parse_github_source("westbrookai/zipsa/skills/daily-progress")
        assert s.user == "westbrookai"
        assert s.repo == "zipsa"
        assert s.subpath == "skills/daily-progress"
        assert s.ref == "HEAD"

    def test_with_ref(self):
        s = parse_github_source("westbrookai/zipsa@v0.1.0")
        assert s.ref == "v0.1.0"
        assert s.subpath == ""

    def test_subpath_with_ref(self):
        s = parse_github_source("westbrookai/zipsa/skills/daily-progress@main")
        assert s.subpath == "skills/daily-progress"
        assert s.ref == "main"

    def test_explicit_github_scheme(self):
        s = parse_github_source("github:westbrookai/zipsa/skills/daily-progress")
        assert s.user == "westbrookai"
        assert s.repo == "zipsa"
        assert s.subpath == "skills/daily-progress"

    def test_https_github_url(self):
        s = parse_github_source("https://github.com/westbrookai/zipsa")
        assert s.user == "westbrookai"
        assert s.repo == "zipsa"
        assert s.subpath == ""

    def test_https_github_url_with_tree(self):
        s = parse_github_source("https://github.com/westbrookai/zipsa/tree/main/skills/daily-progress")
        assert s.user == "westbrookai"
        assert s.repo == "zipsa"
        assert s.ref == "main"
        assert s.subpath == "skills/daily-progress"

    def test_invalid_source_raises(self):
        with pytest.raises(ValueError, match="Invalid"):
            parse_github_source("notavalidformat")

    def test_only_one_part_raises(self):
        with pytest.raises(ValueError, match="Invalid"):
            parse_github_source("westbrookai")

    def test_empty_ref_raises(self):
        with pytest.raises(ValueError, match="Invalid"):
            parse_github_source("westbrookai/zipsa@")


import io
import json
import os
import tarfile
from unittest.mock import patch, MagicMock
import yaml
from zipsa.installer import install_from_github


def _make_fake_tarball(subpath: str, skill_name: str = "test-skill", version: str = "0.1.0") -> bytes:
    """Build an in-memory tarball mimicking a GitHub API tarball."""
    buf = io.BytesIO()
    manifest_content = yaml.dump({
        "apiVersion": "zipsa.dev/v1alpha1",
        "kind": "Skill",
        "metadata": {"name": skill_name, "version": version},
        "spec": {
            "purpose": "Test skill",
            "instructions": "./SKILL.md",
            "mcp": [],
            "tools": {"builtin": []},
        },
    }).encode()
    skill_md_content = b"# Test skill instructions"

    root = f"westbrookai-zipsa-abc1234"
    prefix = f"{root}/{subpath}/" if subpath else f"{root}/"

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in [
            (f"{prefix}manifest.yaml", manifest_content),
            (f"{prefix}SKILL.md", skill_md_content),
        ]:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class TestInstallFromGithub:
    def _mock_response(self, tarball: bytes):
        resp = MagicMock()
        resp.read.return_value = tarball
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def _mock_commit_response(self, sha: str = "abc1234def5678"):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"sha": sha}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_install_from_github_creates_skill_dir(self, tmp_path):
        """install_from_github downloads and installs skill to skills_dir."""
        tarball = _make_fake_tarball("skills/test-skill")
        sha = "abc1234def5678abcdef"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            with patch("urllib.request.urlopen") as mock_open:
                mock_open.side_effect = [
                    self._mock_commit_response(sha),
                    self._mock_response(tarball),
                ]
                name = install_from_github("westbrookai/zipsa/skills/test-skill")

        assert name == "test-skill"
        skill_dir = tmp_path / "skills" / "test-skill"
        assert skill_dir.exists()
        assert (skill_dir / "manifest.yaml").exists()
        assert (skill_dir / "SKILL.md").exists()

    def test_install_from_github_writes_install_json(self, tmp_path):
        """install_from_github writes _install.json with commit_sha and version."""
        tarball = _make_fake_tarball("skills/test-skill")
        sha = "abc1234def5678abcdef"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            with patch("urllib.request.urlopen") as mock_open:
                mock_open.side_effect = [
                    self._mock_commit_response(sha),
                    self._mock_response(tarball),
                ]
                install_from_github("westbrookai/zipsa/skills/test-skill")

        install_json = tmp_path / "skills" / "test-skill" / "_install.json"
        assert install_json.exists()
        meta = json.loads(install_json.read_text())
        assert meta["commit_sha"] == sha
        assert meta["version"] == "0.1.0"
        assert meta["type"] == "github"
        assert "installed_at" in meta

    def test_install_from_github_fails_if_already_installed(self, tmp_path):
        """install_from_github raises FileExistsError if skill already installed."""
        tarball = _make_fake_tarball("skills/test-skill")
        sha = "abc1234def5678abcdef"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            with patch("urllib.request.urlopen") as mock_open:
                mock_open.side_effect = [
                    self._mock_commit_response(sha),
                    self._mock_response(tarball),
                ]
                install_from_github("westbrookai/zipsa/skills/test-skill")

            with pytest.raises(FileExistsError, match="already installed"):
                with patch("urllib.request.urlopen") as mock_open:
                    mock_open.side_effect = [
                        self._mock_commit_response(sha),
                        self._mock_response(tarball),
                    ]
                    install_from_github("westbrookai/zipsa/skills/test-skill")

    def test_install_from_github_force_overwrites(self, tmp_path):
        """install_from_github with force=True replaces existing installation."""
        tarball = _make_fake_tarball("skills/test-skill")
        sha = "abc1234def5678abcdef"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            with patch("urllib.request.urlopen") as mock_open:
                mock_open.side_effect = [
                    self._mock_commit_response(sha),
                    self._mock_response(tarball),
                    self._mock_commit_response(sha),
                    self._mock_response(tarball),
                ]
                install_from_github("westbrookai/zipsa/skills/test-skill")
                name = install_from_github("westbrookai/zipsa/skills/test-skill", force=True)

        assert name == "test-skill"

    def test_install_raises_file_not_found_on_github_404(self, tmp_path):
        """install_from_github raises FileNotFoundError on HTTP 404."""
        import urllib.error
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            with patch("urllib.request.urlopen") as mock_open:
                mock_open.side_effect = urllib.error.HTTPError(
                    url=None, code=404, msg="Not Found", hdrs=None, fp=None
                )
                with pytest.raises(FileNotFoundError):
                    install_from_github("westbrookai/nonexistent/skills/test-skill")

    def test_install_raises_runtime_error_on_github_non_404(self, tmp_path):
        """install_from_github raises RuntimeError on non-404 HTTP errors."""
        import urllib.error
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            with patch("urllib.request.urlopen") as mock_open:
                mock_open.side_effect = urllib.error.HTTPError(
                    url=None, code=403, msg="Forbidden", hdrs=None, fp=None
                )
                with pytest.raises(RuntimeError):
                    install_from_github("westbrookai/private-repo/skills/test-skill")

    def test_install_raises_when_no_manifest_in_tarball(self, tmp_path):
        """install_from_github raises FileNotFoundError when tarball lacks manifest.yaml."""
        # Create a tarball with no manifest.yaml
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            content = b"# Just a readme"
            info = tarfile.TarInfo(name="westbrookai-zipsa-abc1234/README.md")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        empty_tarball = buf.getvalue()
        sha = "abc1234def5678abcdef"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            with patch("urllib.request.urlopen") as mock_open:
                mock_commit = MagicMock()
                mock_commit.read.return_value = json.dumps({"sha": sha}).encode()
                mock_commit.__enter__ = lambda s: s
                mock_commit.__exit__ = MagicMock(return_value=False)

                mock_tarball = MagicMock()
                mock_tarball.read.return_value = empty_tarball
                mock_tarball.__enter__ = lambda s: s
                mock_tarball.__exit__ = MagicMock(return_value=False)

                mock_open.side_effect = [mock_commit, mock_tarball]
                with pytest.raises(FileNotFoundError, match="manifest"):
                    install_from_github("westbrookai/zipsa/skills/nonexistent")
