"""Tests for OAuthManager."""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from zipsa.auth.oauth import OAuthManager, _generate_pkce, _get_discovery_url, _build_auth_url


class TestHelpers:
    """Test OAuth helper functions."""

    def test_get_discovery_url_strips_path(self):
        """Discovery URL is at server root, not MCP path."""
        url = _get_discovery_url("https://mcp.notion.com/mcp")
        assert url == "https://mcp.notion.com/.well-known/oauth-authorization-server"

    def test_get_discovery_url_no_double_slash(self):
        """Discovery URL has no double slash."""
        url = _get_discovery_url("https://mcp.notion.com/")
        assert "/.well-known/" in url
        assert "//" not in url.replace("https://", "")

    def test_generate_pkce_returns_verifier_and_challenge(self):
        """PKCE generates verifier and S256 challenge."""
        verifier, challenge = _generate_pkce()
        import base64, hashlib
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        assert challenge == expected

    def test_generate_pkce_unique_each_call(self):
        """Each PKCE call produces different values."""
        v1, c1 = _generate_pkce()
        v2, c2 = _generate_pkce()
        assert v1 != v2
        assert c1 != c2

    def test_build_auth_url_contains_required_params(self):
        """Auth URL has response_type, client_id, redirect_uri, PKCE, state."""
        url = _build_auth_url(
            authorization_endpoint="https://auth.example.com/authorize",
            client_id="cid-123",
            redirect_uri="http://localhost:54321/callback",
            code_challenge="challenge-abc",
            state="state-xyz",
        )
        assert "response_type=code" in url
        assert "client_id=cid-123" in url
        assert "redirect_uri=" in url
        assert "code_challenge=challenge-abc" in url
        assert "code_challenge_method=S256" in url
        assert "state=state-xyz" in url


class TestOAuthManager:
    """Test OAuthManager.ensure_credentials()."""

    def _mock_storage(self, creds, client_info=None):
        storage = AsyncMock()
        storage.load.return_value = creds
        storage.load_client_info.return_value = client_info
        storage.save = AsyncMock()
        storage.save_client_info = AsyncMock()
        return storage

    def test_returns_existing_valid_token(self):
        """Returns cached token if not expired."""
        creds = {
            "client_id": "cid",
            "access_token": "valid-token",
            "expires_at": int(time.time()) + 3600,
        }
        storage = self._mock_storage(creds)
        manager = OAuthManager()

        with patch("zipsa.auth.oauth.FileTokenStorage", return_value=storage):
            token = manager.ensure_credentials("notion", "https://mcp.notion.com/mcp")

        assert token == "valid-token"
        storage.save.assert_not_called()

    def test_returns_token_with_zero_expires_at(self):
        """Token with expires_at=0 is treated as non-expiring."""
        creds = {
            "client_id": "cid",
            "access_token": "forever-token",
            "expires_at": 0,
        }
        storage = self._mock_storage(creds)
        manager = OAuthManager()

        with patch("zipsa.auth.oauth.FileTokenStorage", return_value=storage):
            token = manager.ensure_credentials("notion", "https://mcp.notion.com/mcp")

        assert token == "forever-token"

    def test_refreshes_expired_token(self):
        """Calls refresh when token is expired and refresh_token present."""
        creds = {
            "client_id": "cid",
            "access_token": "old-token",
            "refresh_token": "ref-tok",
            "expires_at": int(time.time()) - 10,
        }
        new_creds = {**creds, "access_token": "new-token", "expires_at": int(time.time()) + 3600}
        storage = self._mock_storage(creds)
        manager = OAuthManager()

        with patch("zipsa.auth.oauth.FileTokenStorage", return_value=storage), \
             patch.object(manager, "_refresh", new=AsyncMock(return_value=new_creds)):
            token = manager.ensure_credentials("notion", "https://mcp.notion.com/mcp")

        assert token == "new-token"
        storage.save.assert_called_once_with(new_creds)

    def test_falls_through_to_full_flow_when_no_token(self):
        """Runs full OAuth flow when no credentials exist."""
        storage = self._mock_storage(None)
        new_token = "fresh-token"
        manager = OAuthManager()

        with patch("zipsa.auth.oauth.FileTokenStorage", return_value=storage), \
             patch.object(manager, "_full_flow", new=AsyncMock(return_value=new_token)):
            token = manager.ensure_credentials("notion", "https://mcp.notion.com/mcp")

        assert token == new_token

    def test_full_flow_on_refresh_failure(self):
        """Falls back to full OAuth flow when refresh fails."""
        creds = {
            "client_id": "cid",
            "access_token": "old-token",
            "refresh_token": "ref-tok",
            "expires_at": int(time.time()) - 10,
        }
        storage = self._mock_storage(creds)
        manager = OAuthManager()

        with patch("zipsa.auth.oauth.FileTokenStorage", return_value=storage), \
             patch.object(manager, "_refresh", new=AsyncMock(side_effect=Exception("refresh failed"))), \
             patch.object(manager, "_full_flow", new=AsyncMock(return_value="fallback-token")):
            token = manager.ensure_credentials("notion", "https://mcp.notion.com/mcp")

        assert token == "fallback-token"
