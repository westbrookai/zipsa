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
from pathlib import Path
from unittest.mock import patch, MagicMock
import yaml
from zipsa.installer import install_from_github, install_local


def _make_fake_tarball(subpath: str, skill_name: str = "test-skill", version: str = "0.1.0") -> bytes:
    """Build an in-memory tarball mimicking a GitHub API tarball."""
    buf = io.BytesIO()
    manifest_content = yaml.dump({
        "apiVersion": "zipsa.dev/v1alpha1",
        "kind": "SkillManifest",
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

    def test_install_from_github_replaces_broken_entry(self, tmp_path):
        """install_from_github replaces a broken existing entry transparently
        without requiring --force, matching the local --link path behavior."""
        tarball = _make_fake_tarball("skills/test-skill")
        sha = "abc1234def5678abcdef"

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir(parents=True)
        # Place a dangling symlink (broken entry) at the skill's destination.
        broken_entry = skills_dir / "test-skill"
        broken_entry.symlink_to(tmp_path / "gone-source")  # target never created

        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            with patch("urllib.request.urlopen") as mock_open:
                mock_open.side_effect = [
                    self._mock_commit_response(sha),
                    self._mock_response(tarball),
                ]
                # Should succeed without force=True even though entry exists.
                name = install_from_github("westbrookai/zipsa/skills/test-skill")

        assert name == "test-skill"
        # Broken symlink was removed and replaced with a real directory.
        assert not broken_entry.is_symlink()
        assert broken_entry.is_dir()
        assert (broken_entry / "manifest.yaml").exists()

    def test_install_from_github_healthy_existing_still_errors_without_force(self, tmp_path):
        """Regression: github install over a healthy existing entry still
        raises FileExistsError without --force."""
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


def _make_local_skill(base: Path, name: str = "my-skill", version: str = "0.1.0") -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "manifest.yaml").write_text(yaml.dump({
        "apiVersion": "zipsa.dev/v1alpha1",
        "kind": "SkillManifest",
        "metadata": {"name": name, "version": version},
        "spec": {
            "purpose": "Local test skill",
            "instructions": "./SKILL.md",
            "mcp": [],
            "tools": {"builtin": []},
        },
    }))
    (skill_dir / "SKILL.md").write_text("# Instructions")
    return skill_dir


class TestInstallLocal:
    def test_install_path_copies_files(self, tmp_path):
        """--path installs a copy of the local skill."""
        src = _make_local_skill(tmp_path / "src")
        dest_home = tmp_path / "home"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(dest_home)}):
            name = install_local(str(src), link=False)

        assert name == "my-skill"
        installed = dest_home / "skills" / "my-skill"
        assert installed.exists()
        assert not installed.is_symlink()
        assert (installed / "manifest.yaml").exists()

    def test_install_link_creates_symlink(self, tmp_path):
        """--link installs a symlink to the local skill."""
        src = _make_local_skill(tmp_path / "src")
        dest_home = tmp_path / "home"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(dest_home)}):
            name = install_local(str(src), link=True)

        assert name == "my-skill"
        installed = dest_home / "skills" / "my-skill"
        assert installed.is_symlink()
        assert installed.resolve() == src.resolve()

    def test_install_local_writes_install_json(self, tmp_path):
        """install_local writes _install.json with type=copy."""
        src = _make_local_skill(tmp_path / "src")
        dest_home = tmp_path / "home"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(dest_home)}):
            install_local(str(src), link=False)

        meta = json.loads(
            (dest_home / "skills" / "my-skill" / "_install.json").read_text()
        )
        assert meta["type"] == "copy"
        assert meta["version"] == "0.1.0"
        assert "commit_sha" not in meta

    def test_install_link_writes_install_json_with_link_type(self, tmp_path):
        src = _make_local_skill(tmp_path / "src")
        dest_home = tmp_path / "home"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(dest_home)}):
            install_local(str(src), link=True)

        meta = json.loads(
            (dest_home / "skills" / "my-skill" / "_install.json").read_text()
        )
        assert meta["type"] == "link"

    def test_install_local_raises_if_already_installed(self, tmp_path):
        src = _make_local_skill(tmp_path / "src")
        dest_home = tmp_path / "home"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(dest_home)}):
            install_local(str(src), link=False)
            with pytest.raises(FileExistsError, match="already installed"):
                install_local(str(src), link=False)

    def test_install_local_force_replaces(self, tmp_path):
        src = _make_local_skill(tmp_path / "src")
        dest_home = tmp_path / "home"

        with patch.dict(os.environ, {"ZIPSA_HOME": str(dest_home)}):
            install_local(str(src), link=False)
            name = install_local(str(src), link=False, force=True)

        assert name == "my-skill"

    def test_install_local_path_not_found_raises(self, tmp_path):
        dest_home = tmp_path / "home"
        with patch.dict(os.environ, {"ZIPSA_HOME": str(dest_home)}):
            with pytest.raises(FileNotFoundError):
                install_local(str(tmp_path / "nonexistent"), link=False)

    def test_install_local_dangling_symlink_force_overwrites(self, tmp_path):
        """force=True replaces a dangling symlink without raising FileExistsError."""
        nonexistent_target = tmp_path / "gone"
        dest_home = tmp_path / "home"
        dest_home.mkdir()
        (dest_home / "skills").mkdir()
        dangling = dest_home / "skills" / "my-skill"
        dangling.symlink_to(nonexistent_target)  # dangling symlink

        src = _make_local_skill(tmp_path / "src")
        with patch.dict(os.environ, {"ZIPSA_HOME": str(dest_home)}):
            name = install_local(str(src), link=False, force=True)
        assert name == "my-skill"
        assert not dangling.is_symlink()
        assert dangling.is_dir()

    def test_install_local_raises_when_no_manifest(self, tmp_path):
        """install_local raises FileNotFoundError if directory has no manifest.yaml."""
        src = tmp_path / "empty-skill"
        src.mkdir()
        dest_home = tmp_path / "home"
        with patch.dict(os.environ, {"ZIPSA_HOME": str(dest_home)}):
            with pytest.raises(FileNotFoundError, match="manifest"):
                install_local(str(src), link=False)
