"""OAuth manager tests."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zipsa.auth.oauth import OAuthManager
from zipsa.auth.providers import Provider


@pytest.fixture
def fake_provider():
    return Provider(
        name="fake",
        client_id="fake-client-id",
        authorization_endpoint="https://example.test/authorize",
        token_endpoint="https://example.test/token",
        scopes=["read", "write", "offline.access"],
        token_env_var="ZIPSA_TOKEN_FAKE",
        display_handle_endpoint=None,
    )


class TestEnsureCredentialsProvider:
    def test_full_flow_with_no_creds(self, fake_provider, tmp_path):
        """No stored creds → open browser → exchange code → store + return token."""
        with patch("zipsa.auth.oauth.FileTokenStorage") as storage_cls, \
             patch("zipsa.auth.oauth.open_browser_and_wait", return_value="auth-code-abc") as browser, \
             patch("zipsa.auth.oauth.httpx.AsyncClient") as client_cls:
            storage = AsyncMock()
            storage.load.return_value = None
            storage_cls.return_value = storage

            token_resp = MagicMock()
            token_resp.json.return_value = {
                "access_token": "tok-1",
                "refresh_token": "ref-1",
                "expires_in": 3600,
                "scope": "read write offline.access",
            }
            token_resp.raise_for_status.return_value = None
            mock_client = AsyncMock()
            mock_client.post.return_value = token_resp
            client_cls.return_value.__aenter__.return_value = mock_client

            mgr = OAuthManager()
            token = mgr.ensure_credentials_provider(fake_provider)

        assert token == "tok-1"
        browser.assert_called_once()
        auth_url = browser.call_args.args[0]
        assert "client_id=fake-client-id" in auth_url
        assert "code_challenge=" in auth_url
        assert "scope=read+write+offline.access" in auth_url or \
               "scope=read%20write%20offline.access" in auth_url
        storage.save.assert_awaited_once()
        saved = storage.save.await_args.args[0]
        assert saved["access_token"] == "tok-1"
        assert saved["refresh_token"] == "ref-1"

    def test_uses_cached_valid_token(self, fake_provider):
        """Stored creds still valid → return without browser."""
        with patch("zipsa.auth.oauth.FileTokenStorage") as storage_cls, \
             patch("zipsa.auth.oauth.open_browser_and_wait") as browser:
            storage = AsyncMock()
            storage.load.return_value = {
                "access_token": "cached-tok",
                "expires_at": time.time() + 3600,
            }
            storage_cls.return_value = storage

            mgr = OAuthManager()
            token = mgr.ensure_credentials_provider(fake_provider)

        assert token == "cached-tok"
        browser.assert_not_called()

    def test_refresh_when_near_expiry(self, fake_provider):
        """Stored creds expiring soon → refresh → return new token."""
        with patch("zipsa.auth.oauth.FileTokenStorage") as storage_cls, \
             patch("zipsa.auth.oauth.open_browser_and_wait") as browser, \
             patch("zipsa.auth.oauth.httpx.AsyncClient") as client_cls:
            storage = AsyncMock()
            storage.load.return_value = {
                "access_token": "old-tok",
                "refresh_token": "ref-old",
                "client_id": "fake-client-id",
                "expires_at": time.time() + 10,  # within buffer
            }
            storage_cls.return_value = storage

            refresh_resp = MagicMock()
            refresh_resp.json.return_value = {
                "access_token": "new-tok",
                "refresh_token": "ref-new",
                "expires_in": 3600,
            }
            refresh_resp.raise_for_status.return_value = None
            mock_client = AsyncMock()
            mock_client.post.return_value = refresh_resp
            client_cls.return_value.__aenter__.return_value = mock_client

            mgr = OAuthManager()
            token = mgr.ensure_credentials_provider(fake_provider)

        assert token == "new-tok"
        browser.assert_not_called()
        post_kwargs = mock_client.post.call_args
        assert post_kwargs.args[0] == "https://example.test/token"
        body = post_kwargs.kwargs["data"]
        assert body["grant_type"] == "refresh_token"
        assert body["refresh_token"] == "ref-old"
        assert body["client_id"] == "fake-client-id"

    def test_placeholder_client_id_raises_before_browser(self):
        """Calling ensure_credentials_provider with a REPLACE_ME client_id
        should fail loudly, not silently send a bad request to the provider."""
        bad_provider = Provider(
            name="bad",
            client_id="REPLACE_ME_BEFORE_RELEASE",
            authorization_endpoint="https://example.test/authorize",
            token_endpoint="https://example.test/token",
            scopes=["read"],
            token_env_var="ZIPSA_TOKEN_BAD",
            display_handle_endpoint=None,
        )

        with patch("zipsa.auth.oauth.FileTokenStorage") as storage_cls, \
             patch("zipsa.auth.oauth.open_browser_and_wait") as browser:
            storage = AsyncMock()
            storage.load.return_value = None
            storage_cls.return_value = storage

            mgr = OAuthManager()
            with pytest.raises(RuntimeError, match="placeholder client_id"):
                mgr.ensure_credentials_provider(bad_provider)

        browser.assert_not_called()
