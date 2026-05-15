"""Tests for the development overlay system (ZIPSA_DEV_OVERLAY)."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

from zipsa.core.dev_overlay import DevOverlay, load_dev_overlay


class TestLoadDevOverlay:
    """load_dev_overlay() reads ZIPSA_DEV_OVERLAY and returns a DevOverlay."""

    def test_returns_none_when_env_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZIPSA_DEV_OVERLAY", None)
            assert load_dev_overlay() is None

    def test_returns_none_when_env_empty_string(self):
        with patch.dict(os.environ, {"ZIPSA_DEV_OVERLAY": ""}):
            assert load_dev_overlay() is None

    def test_loads_valid_yaml(self, tmp_path):
        overlay_file = tmp_path / "overlay.yaml"
        overlay_file.write_text(yaml.dump({
            "description": "agenthud dev",
            "mounts": ["/host/path:/container/path:rw"],
            "preamble": "cd /container/path && npm link",
            "env": {"FOO": "bar"},
        }))
        with patch.dict(os.environ, {"ZIPSA_DEV_OVERLAY": str(overlay_file)}):
            overlay = load_dev_overlay()
        assert overlay is not None
        assert overlay.description == "agenthud dev"
        assert overlay.mounts == ["/host/path:/container/path:rw"]
        assert overlay.env == {"FOO": "bar"}

    def test_expands_user_in_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        overlay_file = tmp_path / "overlay.yaml"
        overlay_file.write_text(yaml.dump({"mounts": []}))
        with patch.dict(os.environ, {"ZIPSA_DEV_OVERLAY": "~/overlay.yaml"}):
            overlay = load_dev_overlay()
        assert overlay is not None

    def test_raises_when_file_missing(self, tmp_path):
        missing = tmp_path / "nope.yaml"
        with patch.dict(os.environ, {"ZIPSA_DEV_OVERLAY": str(missing)}):
            with pytest.raises(FileNotFoundError):
                load_dev_overlay()

    def test_raises_on_unknown_field(self, tmp_path):
        overlay_file = tmp_path / "overlay.yaml"
        overlay_file.write_text(yaml.dump({
            "mounts": [],
            "typo_field": "oops",   # not a real field
        }))
        with patch.dict(os.environ, {"ZIPSA_DEV_OVERLAY": str(overlay_file)}):
            with pytest.raises(ValidationError):
                load_dev_overlay()


class TestDevOverlayDefaults:
    """An empty overlay file should produce sensible defaults."""

    def test_empty_yaml_yields_empty_overlay(self, tmp_path):
        overlay_file = tmp_path / "overlay.yaml"
        overlay_file.write_text("{}")
        with patch.dict(os.environ, {"ZIPSA_DEV_OVERLAY": str(overlay_file)}):
            overlay = load_dev_overlay()
        assert overlay.mounts == []
        assert overlay.env == {}
        assert overlay.preamble_str == ""
        assert overlay.description is None


class TestDevOverlayPreamble:
    """Preamble can be a string or list; preamble_str joins lists with &&."""

    def test_string_preamble_passthrough(self):
        o = DevOverlay(preamble="echo hi")
        assert o.preamble_str == "echo hi"

    def test_list_preamble_joined_with_and(self):
        o = DevOverlay(preamble=["cd /x", "npm link", "echo done"])
        assert o.preamble_str == "cd /x && npm link && echo done"

    def test_empty_list_preamble(self):
        o = DevOverlay(preamble=[])
        assert o.preamble_str == ""


class TestDevOverlayMountValidation:
    """Mount entries must be 'host:container[:mode]' strings."""

    def test_accepts_two_part_mount(self):
        o = DevOverlay(mounts=["/host:/container"])
        assert o.mounts == ["/host:/container"]

    def test_accepts_three_part_mount(self):
        o = DevOverlay(mounts=["/host:/container:ro"])
        assert o.mounts == ["/host:/container:ro"]

    def test_rejects_missing_colon(self):
        with pytest.raises(ValidationError):
            DevOverlay(mounts=["/just-host"])

    def test_rejects_empty_string(self):
        with pytest.raises(ValidationError):
            DevOverlay(mounts=[""])
