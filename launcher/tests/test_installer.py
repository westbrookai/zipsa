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
