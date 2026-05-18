"""Auth providers registry — non-MCP OAuth targets.

For MCP servers we discover endpoints via /.well-known and register
clients dynamically (RFC 7591). Some services don't support either
(e.g. X / Twitter). We pre-register one app per service, hardcode the
public Client ID + endpoints here, and run the standard OAuth 2.0
PKCE flow against them.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class UnknownProvider(KeyError):
    """Raised when a provider name is not in the registry."""


@dataclass(frozen=True)
class Provider:
    """A non-MCP OAuth target."""

    name: str
    client_id: str
    authorization_endpoint: str
    token_endpoint: str
    scopes: list[str] = field(default_factory=list)
    token_env_var: str = ""
    display_handle_endpoint: str | None = None


# Placeholder client_id — replaced at release time with the real
# Client ID from the westbrookai/zipsa X app. See plan task 6.
_X_CLIENT_ID = "REPLACE_ME_BEFORE_RELEASE"

PROVIDERS: dict[str, Provider] = {
    "x": Provider(
        name="x",
        client_id=_X_CLIENT_ID,
        authorization_endpoint="https://x.com/i/oauth2/authorize",
        token_endpoint="https://api.x.com/2/oauth2/token",
        scopes=["tweet.write", "tweet.read", "users.read", "offline.access"],
        token_env_var="ZIPSA_TOKEN_X",
        display_handle_endpoint="https://api.x.com/2/users/me",
    ),
}


def get_provider(name: str) -> Provider:
    """Look up a provider by name; raise UnknownProvider if not found."""
    try:
        return PROVIDERS[name]
    except KeyError:
        available = ", ".join(sorted(PROVIDERS.keys()))
        raise UnknownProvider(
            f"Unknown auth provider {name!r}. Available: {available}"
        ) from None
