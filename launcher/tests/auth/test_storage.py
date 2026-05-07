"""Tests for FileTokenStorage."""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import patch
from zipsa.auth.storage import FileTokenStorage


class TestFileTokenStorage:
    """Test token persistence."""

    def test_load_returns_none_when_file_missing(self, tmp_path):
        """Returns None when credentials file doesn't exist."""
        with patch("zipsa.auth.storage.CREDENTIALS_DIR", tmp_path):
            storage = FileTokenStorage("notion")
            result = asyncio.run(storage.load())
        assert result is None

    def test_save_and_load_roundtrip(self, tmp_path):
        """Saved credentials can be loaded back."""
        creds = {
            "client_id": "test-client",
            "access_token": "tok-abc",
            "refresh_token": "ref-xyz",
            "expires_at": 9999999999,
            "scope": "read write",
        }
        with patch("zipsa.auth.storage.CREDENTIALS_DIR", tmp_path):
            storage = FileTokenStorage("notion")
            asyncio.run(storage.save(creds))
            loaded = asyncio.run(storage.load())
        assert loaded == creds

    def test_save_sets_file_permissions_600(self, tmp_path):
        """Credentials file is saved with 600 permissions."""
        creds = {"access_token": "tok"}
        with patch("zipsa.auth.storage.CREDENTIALS_DIR", tmp_path):
            storage = FileTokenStorage("notion")
            asyncio.run(storage.save(creds))
            mode = (tmp_path / "notion.json").stat().st_mode & 0o777
        assert mode == 0o600

    def test_save_creates_parent_directory(self, tmp_path):
        """Creates credentials directory if it doesn't exist."""
        creds_dir = tmp_path / "credentials"
        with patch("zipsa.auth.storage.CREDENTIALS_DIR", creds_dir):
            storage = FileTokenStorage("notion")
            asyncio.run(storage.save({"access_token": "tok"}))
        assert creds_dir.exists()
        assert (creds_dir / "notion.json").exists()

    def test_load_client_info_returns_none_when_no_client_id(self, tmp_path):
        """Returns None when stored creds have no client_id."""
        creds = {"access_token": "tok"}
        with patch("zipsa.auth.storage.CREDENTIALS_DIR", tmp_path):
            storage = FileTokenStorage("notion")
            asyncio.run(storage.save(creds))
            info = asyncio.run(storage.load_client_info())
        assert info is None

    def test_load_client_info_returns_id_and_secret(self, tmp_path):
        """Returns client_id and client_secret from stored creds."""
        creds = {
            "client_id": "cid",
            "client_secret": "csec",
            "access_token": "tok",
        }
        with patch("zipsa.auth.storage.CREDENTIALS_DIR", tmp_path):
            storage = FileTokenStorage("notion")
            asyncio.run(storage.save(creds))
            info = asyncio.run(storage.load_client_info())
        assert info == {"client_id": "cid", "client_secret": "csec"}

    def test_save_client_info_merges_with_existing(self, tmp_path):
        """Saving client info merges with existing credentials."""
        creds = {"access_token": "tok"}
        with patch("zipsa.auth.storage.CREDENTIALS_DIR", tmp_path):
            storage = FileTokenStorage("notion")
            asyncio.run(storage.save(creds))
            asyncio.run(storage.save_client_info({"client_id": "cid"}))
            loaded = asyncio.run(storage.load())
        assert loaded["access_token"] == "tok"
        assert loaded["client_id"] == "cid"

    def test_file_name_uses_server_name(self, tmp_path):
        """Credentials file is named after server."""
        with patch("zipsa.auth.storage.CREDENTIALS_DIR", tmp_path):
            storage = FileTokenStorage("github")
            asyncio.run(storage.save({"access_token": "tok"}))
        assert (tmp_path / "github.json").exists()
