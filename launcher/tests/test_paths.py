"""Tests for zipsa.paths — centralized path resolution."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from zipsa.paths import (
    credentials_dir,
    global_env_file,
    skill_data_dir,
    skill_env_file,
    skill_runs_dir,
    zipsa_home,
)


class TestZipsaHome:
    def test_default_is_dotzip_under_home(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZIPSA_HOME", None)
            assert zipsa_home() == Path.home() / ".zipsa"

    def test_env_var_overrides_default(self, tmp_path):
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            assert zipsa_home() == tmp_path


class TestSkillPaths:
    def test_skill_data_dir(self, tmp_path):
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            result = skill_data_dir("my-skill", "1.2.3")
            assert result == tmp_path / "my-skill@1.2.3"

    def test_skill_runs_dir(self, tmp_path):
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            result = skill_runs_dir("my-skill", "1.2.3")
            assert result == tmp_path / "my-skill@1.2.3" / "runs"

    def test_skill_env_file(self, tmp_path):
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            result = skill_env_file("my-skill", "1.2.3")
            assert result == tmp_path / "my-skill@1.2.3" / ".env"


class TestGlobalPaths:
    def test_global_env_file(self, tmp_path):
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            assert global_env_file() == tmp_path / ".env"

    def test_credentials_dir(self, tmp_path):
        with patch.dict(os.environ, {"ZIPSA_HOME": str(tmp_path)}):
            assert credentials_dir() == tmp_path / "credentials"
