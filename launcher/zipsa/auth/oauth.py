"""OAuth 2.1 manager: discovery, DCR, PKCE, exchange, refresh."""

import asyncio
import base64
import hashlib
import secrets
import time
from typing import TYPE_CHECKING
from urllib.parse import urlencode, urlparse

import httpx

from .browser import open_browser_and_wait, CALLBACK_PORT, OAuthCallbackError
from .storage import FileTokenStorage

if TYPE_CHECKING:
    from .providers import Provider

CALLBACK_URI = f"http://localhost:{CALLBACK_PORT}/callback"
_TOKEN_EXPIRY_BUFFER_S = 60  # refresh if expiring within this many seconds


def _get_discovery_url(server_url: str) -> str:
    """Build /.well-known/oauth-authorization-server URL from MCP server URL."""
    parsed = urlparse(server_url)
    return f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-authorization-server"


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _build_auth_url(
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    scope: str = "read write",
) -> str:
    """Build authorization URL with PKCE and state."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": scope,
    }
    return f"{authorization_endpoint}?{urlencode(params)}"


class OAuthManager:
    """Manages OAuth 2.1 credentials for HTTP MCP servers."""

    def ensure_credentials(self, server_name: str, server_url: str) -> str:
        """Return a valid access token, running OAuth flow if needed."""
        return asyncio.run(self._ensure(server_name, server_url))

    async def _ensure(self, server_name: str, server_url: str) -> str:
        storage = FileTokenStorage(server_name)
        creds = await storage.load()

        if creds and creds.get("access_token"):
            expires_at = creds.get("expires_at", 0)
            if expires_at == 0 or expires_at > time.time() + _TOKEN_EXPIRY_BUFFER_S:
                return creds["access_token"]

            if creds.get("refresh_token"):
                try:
                    new_creds = await self._refresh(server_url, creds)
                    await storage.save(new_creds)
                    return new_creds["access_token"]
                except Exception:
                    pass  # fall through to full OAuth flow

        return await self._full_flow(server_name, server_url, storage)

    async def _full_flow(
        self, server_name: str, server_url: str, storage: FileTokenStorage
    ) -> str:
        """Run complete browser OAuth flow: discover -> register -> PKCE -> exchange."""
        async with httpx.AsyncClient() as client:
            meta = await self._discover(client, server_url)

            client_info = await storage.load_client_info()
            if not client_info:
                client_info = await self._register(client, meta["registration_endpoint"])
                await storage.save_client_info(client_info)

            code_verifier, code_challenge = _generate_pkce()
            state = secrets.token_urlsafe(16)
            auth_url = _build_auth_url(
                meta["authorization_endpoint"],
                client_info["client_id"],
                CALLBACK_URI,
                code_challenge,
                state,
            )

            code = open_browser_and_wait(auth_url, expected_state=state)
            tokens = await self._exchange(
                client, meta["token_endpoint"], client_info, code, code_verifier
            )

        creds = {
            "client_id": client_info["client_id"],
            "client_secret": client_info.get("client_secret"),
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token"),
            "expires_at": (
                int(time.time()) + tokens["expires_in"]
                if tokens.get("expires_in")
                else 0
            ),
            "scope": tokens.get("scope"),
        }
        await storage.save(creds)
        return creds["access_token"]

    async def _discover(self, client: httpx.AsyncClient, server_url: str) -> dict:
        """Fetch OAuth server metadata from /.well-known/oauth-authorization-server."""
        discovery_url = _get_discovery_url(server_url)
        response = await client.get(discovery_url)
        response.raise_for_status()
        return response.json()

    async def _register(self, client: httpx.AsyncClient, registration_endpoint: str) -> dict:
        """Register zipsa as OAuth client via RFC 7591 Dynamic Client Registration."""
        response = await client.post(
            registration_endpoint,
            json={
                "client_name": "zipsa",
                "redirect_uris": [CALLBACK_URI],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )
        response.raise_for_status()
        return response.json()

    async def _exchange(
        self,
        client: httpx.AsyncClient,
        token_endpoint: str,
        client_info: dict,
        code: str,
        code_verifier: str,
    ) -> dict:
        """Exchange authorization code for tokens."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CALLBACK_URI,
            "client_id": client_info["client_id"],
            "code_verifier": code_verifier,
        }
        if client_info.get("client_secret"):
            data["client_secret"] = client_info["client_secret"]
        response = await client.post(token_endpoint, data=data)
        response.raise_for_status()
        return response.json()

    async def _refresh(self, server_url: str, creds: dict) -> dict:
        """Refresh access token using refresh_token grant."""
        async with httpx.AsyncClient() as client:
            meta = await self._discover(client, server_url)
            data = {
                "grant_type": "refresh_token",
                "refresh_token": creds["refresh_token"],
                "client_id": creds["client_id"],
            }
            if creds.get("client_secret"):
                data["client_secret"] = creds["client_secret"]
            response = await client.post(meta["token_endpoint"], data=data)
            response.raise_for_status()
            tokens = response.json()

        return {
            **creds,
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token", creds["refresh_token"]),
            "expires_at": (
                int(time.time()) + tokens["expires_in"]
                if tokens.get("expires_in")
                else 0
            ),
        }

    def ensure_credentials_provider(self, provider: "Provider") -> str:
        """Return a valid access token for a registry Provider.

        Like ensure_credentials() but skips OAuth discovery + DCR;
        uses the provider's hardcoded endpoints and client_id.
        """
        return asyncio.run(self._ensure_provider(provider))

    async def _ensure_provider(self, provider: "Provider") -> str:
        storage = FileTokenStorage(provider.name)
        creds = await storage.load()

        if creds and creds.get("access_token"):
            expires_at = creds.get("expires_at", 0)
            if expires_at == 0 or expires_at > time.time() + _TOKEN_EXPIRY_BUFFER_S:
                return creds["access_token"]

            if creds.get("refresh_token"):
                try:
                    new_creds = await self._refresh_provider(provider, creds)
                    await storage.save(new_creds)
                    return new_creds["access_token"]
                except Exception:
                    pass  # fall through to full flow

        return await self._full_flow_provider(provider, storage)

    async def _full_flow_provider(
        self, provider: "Provider", storage: FileTokenStorage
    ) -> str:
        async with httpx.AsyncClient() as client:
            code_verifier, code_challenge = _generate_pkce()
            state = secrets.token_urlsafe(16)
            scope = " ".join(provider.scopes)
            auth_url = _build_auth_url(
                provider.authorization_endpoint,
                provider.client_id,
                CALLBACK_URI,
                code_challenge,
                state,
                scope=scope,
            )

            code = open_browser_and_wait(auth_url, expected_state=state)

            data = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": CALLBACK_URI,
                "client_id": provider.client_id,
                "code_verifier": code_verifier,
            }
            response = await client.post(provider.token_endpoint, data=data)
            await response.raise_for_status()
            tokens = await response.json()

        creds = {
            "client_id": provider.client_id,
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token"),
            "expires_at": (
                int(time.time()) + tokens["expires_in"]
                if tokens.get("expires_in")
                else 0
            ),
            "scope": tokens.get("scope"),
        }
        await storage.save(creds)
        return creds["access_token"]

    async def _refresh_provider(
        self, provider: "Provider", creds: dict
    ) -> dict:
        async with httpx.AsyncClient() as client:
            data = {
                "grant_type": "refresh_token",
                "refresh_token": creds["refresh_token"],
                "client_id": provider.client_id,
            }
            response = await client.post(provider.token_endpoint, data=data)
            await response.raise_for_status()
            tokens = await response.json()

        return {
            **creds,
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token", creds["refresh_token"]),
            "expires_at": (
                int(time.time()) + tokens["expires_in"]
                if tokens.get("expires_in")
                else 0
            ),
        }
