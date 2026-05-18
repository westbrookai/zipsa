# Sign in with X — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "provider registry" to zipsa's auth layer so users can run `zipsa connect x` and have `ZIPSA_TOKEN_X` injected into any skill that declares `auth_providers: [x]`.

**Architecture:** New `auth/providers.py` holds a `Provider` dataclass + `PROVIDERS` dict (X hardcoded for v1). `OAuthManager` gains `ensure_credentials_provider(provider)` that runs PKCE against the provider's hardcoded endpoints (skipping discover + DCR). `SkillSpec` gains an `auth_providers: list[str]` field; the executor's existing `_ensure_oauth_credentials` is extended to iterate providers alongside MCP OAuth servers. `cli.py connect` falls through to the registry when the name isn't a known MCP server.

**Tech Stack:** Python 3.10+, httpx, pydantic v2, pytest. No new runtime dependencies.

---

## File map

| File | Role |
|---|---|
| `launcher/zipsa/auth/providers.py` (new) | `Provider` dataclass + frozen `PROVIDERS` dict |
| `launcher/zipsa/auth/oauth.py` | Add `ensure_credentials_provider`; reuse existing PKCE/browser helpers |
| `launcher/zipsa/core/models.py` | Add `SkillSpec.auth_providers: list[str]` with validator |
| `launcher/zipsa/core/executor.py` | `_ensure_oauth_credentials` also handles `spec.auth_providers` |
| `launcher/zipsa/cli.py` | `connect` falls through to `PROVIDERS[name]` if name isn't an MCP server |
| `launcher/tests/test_providers.py` (new) | Registry + Provider dataclass tests |
| `launcher/tests/test_oauth.py` | `ensure_credentials_provider` happy path + refresh + no-creds-noninteractive |
| `launcher/tests/test_models.py` | `auth_providers` valid + unknown-name rejected |
| `launcher/tests/test_executor.py` | Provider token injection |
| `launcher/tests/test_cli.py` | `connect` fallback path |

---

## Task 1: Provider registry

**Files:**
- Create: `launcher/zipsa/auth/providers.py`
- Test: `launcher/tests/test_providers.py`

- [ ] **Step 1: Write the failing test**

```python
# launcher/tests/test_providers.py
"""Provider registry tests."""

import pytest
from zipsa.auth.providers import PROVIDERS, Provider, get_provider, UnknownProvider


class TestProviderRegistry:
    def test_x_provider_present(self):
        p = get_provider("x")
        assert isinstance(p, Provider)
        assert p.name == "x"
        assert p.token_env_var == "ZIPSA_TOKEN_X"

    def test_x_provider_endpoints_are_https(self):
        p = get_provider("x")
        assert p.authorization_endpoint.startswith("https://")
        assert p.token_endpoint.startswith("https://")

    def test_x_provider_required_scopes(self):
        p = get_provider("x")
        # Posting requires tweet.write; refresh requires offline.access
        assert "tweet.write" in p.scopes
        assert "offline.access" in p.scopes

    def test_unknown_provider_raises(self):
        with pytest.raises(UnknownProvider) as exc:
            get_provider("not-a-real-thing")
        assert "not-a-real-thing" in str(exc.value)
        # Error should hint at what IS available
        assert "x" in str(exc.value)

    def test_provider_is_frozen(self):
        p = get_provider("x")
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            p.name = "mutated"  # type: ignore
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_providers.py -v`
Expected: `ImportError` or `ModuleNotFoundError: No module named 'zipsa.auth.providers'`

- [ ] **Step 3: Implement the registry**

```python
# launcher/zipsa/auth/providers.py
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
    """A non-MCP OAuth target.

    Attributes:
        name: registry key; also the argument to `zipsa connect <name>`.
        client_id: public OAuth client id of the zipsa-owned app.
        authorization_endpoint: where to send the user for consent.
        token_endpoint: where to exchange code / refresh tokens.
        scopes: space-joined when forming the auth URL.
        token_env_var: env var name to inject into skill containers.
        display_handle_endpoint: optional GET-able URL whose JSON we
            shallow-parse to fetch a username for the connect success
            message. Best-effort: failures don't fail connect.
    """

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_providers.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add launcher/zipsa/auth/providers.py launcher/tests/test_providers.py
git commit -m "feat(auth): add provider registry for non-MCP OAuth targets

X is the first provider. Client ID is a placeholder — replaced at
release time with the real value from the westbrookai/zipsa X app."
```

---

## Task 2: `SkillSpec.auth_providers` field

**Files:**
- Modify: `launcher/zipsa/core/models.py:146-160`
- Test: `launcher/tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `launcher/tests/test_models.py`:

```python
class TestAuthProviders:
    """SkillSpec should accept and validate auth_providers."""

    def _spec(self, **overrides):
        from zipsa.core.models import SkillSpec
        data = {
            "purpose": "test",
            "instructions": "./SKILL.md",
        }
        data.update(overrides)
        return SkillSpec.model_validate(data)

    def test_empty_auth_providers_is_default(self):
        spec = self._spec()
        assert spec.auth_providers == []

    def test_known_provider_accepted(self):
        spec = self._spec(auth_providers=["x"])
        assert spec.auth_providers == ["x"]

    def test_unknown_provider_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc:
            self._spec(auth_providers=["not-real"])
        msg = str(exc.value)
        assert "not-real" in msg
        # The validator should hint at what's available
        assert "x" in msg

    def test_duplicate_providers_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc:
            self._spec(auth_providers=["x", "x"])
        assert "duplicate" in str(exc.value).lower()
```

(If `pytest` is not imported at the top of `test_models.py`, add `import pytest` to the imports.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::TestAuthProviders -v`
Expected: 4 failures — `auth_providers` attribute doesn't exist.

- [ ] **Step 3: Implement**

In `launcher/zipsa/core/models.py`, add to imports:

```python
from ..auth.providers import PROVIDERS
```

Add a field to `SkillSpec` (after `state_schema`):

```python
class SkillSpec(BaseModel):
    """Skill specification."""

    purpose: str
    instructions: str
    model: Optional[dict] = None
    tools: SkillTools = Field(default_factory=SkillTools)
    mcp: list[MCPServer] = Field(default_factory=list)
    mounts: list[SkillMount] = Field(default_factory=list)
    limits: Optional[SkillLimits] = None
    config: dict = Field(default_factory=dict)
    network: Optional[dict] = None
    phases: list[PhaseSpec] = Field(default_factory=list)
    state_schema: dict = Field(default_factory=dict)
    auth_providers: list[str] = Field(default_factory=list)

    @field_validator("auth_providers")
    @classmethod
    def _check_providers(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError("duplicate provider names in auth_providers")
        unknown = [p for p in v if p not in PROVIDERS]
        if unknown:
            available = ", ".join(sorted(PROVIDERS.keys()))
            raise ValueError(
                f"unknown auth provider(s): {unknown}. Available: {available}"
            )
        return v
```

(`field_validator` should already be imported; if not, add `from pydantic import field_validator`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py::TestAuthProviders -v`
Expected: 4 passed

- [ ] **Step 5: Run full suite — no regressions**

Run: `uv run pytest`
Expected: 394 passed (390 baseline + 5 from task 1 - 1 due to model change + 4 from task 2 — adjust if count differs but no FAILED).

- [ ] **Step 6: Commit**

```bash
git add launcher/zipsa/core/models.py launcher/tests/test_models.py
git commit -m "feat(models): SkillSpec.auth_providers with registry-backed validation"
```

---

## Task 3: `OAuthManager.ensure_credentials_provider`

**Files:**
- Modify: `launcher/zipsa/auth/oauth.py`
- Test: `launcher/tests/test_oauth.py` (create if missing)

- [ ] **Step 1: Write the failing test**

```python
# launcher/tests/test_oauth.py — add (or create file)
"""OAuth manager tests."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

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

            token_resp = AsyncMock()
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

            refresh_resp = AsyncMock()
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
        # POST went to provider's token endpoint, not via discovery
        post_kwargs = mock_client.post.call_args
        assert post_kwargs.args[0] == "https://example.test/token"
        body = post_kwargs.kwargs["data"]
        assert body["grant_type"] == "refresh_token"
        assert body["refresh_token"] == "ref-old"
        assert body["client_id"] == "fake-client-id"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_oauth.py::TestEnsureCredentialsProvider -v`
Expected: 3 failures — `AttributeError: 'OAuthManager' object has no attribute 'ensure_credentials_provider'`.

- [ ] **Step 3: Implement**

In `launcher/zipsa/auth/oauth.py`, add the method on `OAuthManager` (anywhere after `ensure_credentials`):

```python
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
            response.raise_for_status()
            tokens = response.json()

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
```

Add the forward import at the top of `oauth.py` (after existing imports):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .providers import Provider
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_oauth.py::TestEnsureCredentialsProvider -v`
Expected: 3 passed

- [ ] **Step 5: Run full suite**

Run: `uv run pytest`
Expected: green; coverage didn't drop materially.

- [ ] **Step 6: Commit**

```bash
git add launcher/zipsa/auth/oauth.py launcher/tests/test_oauth.py
git commit -m "feat(oauth): ensure_credentials_provider for registry-based PKCE flow"
```

---

## Task 4: Executor injects provider tokens

**Files:**
- Modify: `launcher/zipsa/core/executor.py` (`_ensure_oauth_credentials` and surrounding)
- Test: `launcher/tests/test_executor.py`

- [ ] **Step 1: Locate the current method**

Read `launcher/zipsa/core/executor.py` around the line containing `def _ensure_oauth_credentials`. The current implementation only iterates `skill.manifest.spec.mcp`. We'll add a parallel block for `skill.manifest.spec.auth_providers`.

- [ ] **Step 2: Write the failing test**

Add to `launcher/tests/test_executor.py`:

```python
class TestAuthProvidersInjection:
    """Skills declaring auth_providers get the provider token in env."""

    def test_provider_token_injected(self, tmp_path):
        from zipsa.core.executor import DockerExecutor
        from unittest.mock import patch

        executor = DockerExecutor()
        # Use a fixture manifest with auth_providers: [x]
        skill_dir = Path(__file__).parent / "fixtures/manifests/with-auth-provider.yaml"
        skill = Skill.load(skill_dir)
        env = {}

        with patch("zipsa.core.executor.OAuthManager") as mgr_cls:
            mgr = mgr_cls.return_value
            mgr.ensure_credentials_provider.return_value = "tok-injected"

            executor._ensure_oauth_credentials(skill, env)

        assert env["ZIPSA_TOKEN_X"] == "tok-injected"
        mgr.ensure_credentials_provider.assert_called_once()

    def test_no_provider_no_injection(self, tmp_path):
        from zipsa.core.executor import DockerExecutor
        from unittest.mock import patch

        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        env = {}

        with patch("zipsa.core.executor.OAuthManager") as mgr_cls:
            mgr = mgr_cls.return_value
            executor._ensure_oauth_credentials(skill, env)

        assert "ZIPSA_TOKEN_X" not in env
        mgr.ensure_credentials_provider.assert_not_called()

    def test_existing_token_in_env_skipped(self, tmp_path):
        """If user already exported ZIPSA_TOKEN_X, don't run OAuth flow."""
        from zipsa.core.executor import DockerExecutor
        from unittest.mock import patch

        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/with-auth-provider.yaml"
        skill = Skill.load(skill_dir)
        env = {"ZIPSA_TOKEN_X": "pre-set"}

        with patch("zipsa.core.executor.OAuthManager") as mgr_cls:
            mgr = mgr_cls.return_value
            executor._ensure_oauth_credentials(skill, env)

        assert env["ZIPSA_TOKEN_X"] == "pre-set"
        mgr.ensure_credentials_provider.assert_not_called()
```

Create the fixture `launcher/tests/fixtures/manifests/with-auth-provider.yaml`:

```yaml
apiVersion: zipsa.dev/v1alpha1
kind: Skill
metadata:
  name: with-auth-provider
  version: 1.0.0
spec:
  purpose: Test skill declaring an auth provider.
  instructions: ./SKILL.md
  auth_providers:
    - x
  tools:
    builtin: []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_executor.py::TestAuthProvidersInjection -v`
Expected: 3 failures.

- [ ] **Step 4: Implement**

In `launcher/zipsa/core/executor.py`, at the top add:

```python
from ..auth.providers import get_provider
```

Extend `_ensure_oauth_credentials`:

```python
    def _ensure_oauth_credentials(self, skill: "Skill", env: dict[str, str]) -> None:
        """Inject ZIPSA_TOKEN_<NAME> for oauth2 MCP servers and auth_providers."""
        # Existing MCP OAuth path (unchanged from current implementation)
        oauth_servers = [
            s for s in skill.manifest.spec.mcp
            if s.type == "http" and getattr(s, "auth", None) and s.auth.type == "oauth2"
        ]

        provider_names = skill.manifest.spec.auth_providers

        if not oauth_servers and not provider_names:
            return

        manager = OAuthManager()
        print("Checking credentials...")

        for server in oauth_servers:
            token_var = f"ZIPSA_TOKEN_{server.name.upper().replace('-', '_')}"
            if token_var in env:
                print(f"  {server.name}: token already set")
                continue
            token = manager.ensure_credentials(server.name, server.url)
            env[token_var] = token
            print(f"  {server.name}: authorized")

        for name in provider_names:
            provider = get_provider(name)
            if provider.token_env_var in env:
                print(f"  {provider.name}: token already set")
                continue
            token = manager.ensure_credentials_provider(provider)
            env[provider.token_env_var] = token
            print(f"  {provider.name}: authorized")
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_executor.py::TestAuthProvidersInjection -v`
Expected: 3 passed.

Run full suite to catch regressions: `uv run pytest`. Expected: green.

- [ ] **Step 6: Commit**

```bash
git add launcher/zipsa/core/executor.py launcher/tests/test_executor.py launcher/tests/fixtures/manifests/with-auth-provider.yaml
git commit -m "feat(executor): inject auth_providers tokens into skill containers"
```

---

## Task 5: `zipsa connect <provider>` fallback

**Files:**
- Modify: `launcher/zipsa/cli.py` (around the `connect` command)
- Test: `launcher/tests/test_cli.py`

- [ ] **Step 1: Read the current connect command**

Read `launcher/zipsa/cli.py` lines 517 onward (the `connect` function). Note how it looks up MCP servers across installed skills and what it does on success.

- [ ] **Step 2: Write the failing test**

Add to `launcher/tests/test_cli.py`:

```python
class TestConnectProviderFallback:
    """`zipsa connect <name>` should fall through to PROVIDERS when name
    isn't a known MCP server in any installed skill."""

    def test_connect_provider_runs_oauth(self):
        from unittest.mock import patch
        from typer.testing import CliRunner
        from zipsa.cli import app

        runner = CliRunner()
        with patch("zipsa.cli.OAuthManager") as mgr_cls, \
             patch("zipsa.cli.get_provider") as get_p:
            # Simulate the provider lookup
            from zipsa.auth.providers import PROVIDERS
            get_p.return_value = PROVIDERS["x"]

            mgr = mgr_cls.return_value
            mgr.ensure_credentials_provider.return_value = "tok-xyz"

            result = runner.invoke(app, ["connect", "x"])

        assert result.exit_code == 0, result.output
        mgr.ensure_credentials_provider.assert_called_once()
        assert "x" in result.output.lower()

    def test_connect_unknown_name_fails_gracefully(self):
        from typer.testing import CliRunner
        from zipsa.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["connect", "definitely-not-a-thing"])

        assert result.exit_code != 0
        # Should mention what IS available (the registry hint)
        assert "x" in result.output
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::TestConnectProviderFallback -v`
Expected: failures.

- [ ] **Step 4: Implement**

In `launcher/zipsa/cli.py`, add to imports:

```python
from .auth.providers import PROVIDERS, get_provider, UnknownProvider
```

Modify the `connect` function. Find the block that looks up MCP servers across installed skills. After the MCP search, if no `matched_server` was found, fall through to the provider registry:

```python
    # ... existing MCP lookup code that produces `matched_server` or None ...

    if matched_server is not None:
        manager = OAuthManager()
        manager.ensure_credentials(matched_server.name, matched_server.url)
        typer.echo(f"Connected to {matched_server.name}")
        return

    # Fall through: maybe it's a registered provider
    try:
        provider = get_provider(name)
    except UnknownProvider as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)

    manager = OAuthManager()
    manager.ensure_credentials_provider(provider)
    typer.echo(f"Connected to {provider.name}")
```

(Exact placement depends on the existing structure of `connect` — the implementer should preserve the current MCP behavior and only add the provider fallback at the no-match point. If `connect` currently exits with an error when no match is found, replace that error with the provider lookup.)

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_cli.py::TestConnectProviderFallback -v`
Expected: 2 passed.

Full suite: `uv run pytest`. Expected: green.

- [ ] **Step 6: Commit**

```bash
git add launcher/zipsa/cli.py launcher/tests/test_cli.py
git commit -m "feat(cli): connect falls through to provider registry for non-MCP targets"
```

---

## Task 6: Register the X app + replace placeholder Client ID (release prerequisite)

This is a one-time manual action by a westbrookai maintainer. It does
NOT produce code in this PR — it's a deployment step that must happen
before the PR is merged so end users get a working `zipsa connect x`.

- [ ] **Step 1: Create the X App**

1. Go to [developer.x.com](https://developer.x.com) and sign in with the westbrookai X account.
2. Create a new App named `zipsa`.
3. Enable OAuth 2.0 in the App settings.
4. App type: **Native App** (public client, PKCE-only, no client secret).
5. Add callback URL: `http://localhost:54321/callback`
   (the value of `CALLBACK_URI` in `launcher/zipsa/auth/browser.py`).
6. Permissions: `Read and Write` (minimum required for posting tweets).
7. Save and copy the generated **Client ID**.

- [ ] **Step 2: Replace the placeholder**

Edit `launcher/zipsa/auth/providers.py`:

```python
_X_CLIENT_ID = "<actual client id from step 1>"
```

- [ ] **Step 3: Commit**

```bash
git add launcher/zipsa/auth/providers.py
git commit -m "chore(auth): set real X Client ID from westbrookai zipsa app"
```

- [ ] **Step 4: Smoke test (manual)**

```bash
zipsa connect x
# Browser opens; authorize. Expect "Connected to x" in terminal.
ls -la ~/.zipsa/credentials/x.json
# Expect file present, 0600.
```

If the smoke test fails, do NOT merge — the X app config is likely wrong (callback URL mismatch is the most common cause).

---

## Wrap-up

After all 6 tasks:

- [ ] Full suite: `uv run pytest`. Expected: green, 4 new test classes added.
- [ ] Manual: `zipsa connect x` end-to-end (requires task 6 to be done).
- [ ] PR description references this plan and links the spec.
- [ ] Push branch, open PR against `main`. After merge, kick off the `bip-daily-x` plan (separate document).
