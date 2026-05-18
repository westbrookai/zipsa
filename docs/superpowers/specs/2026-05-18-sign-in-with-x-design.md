# Sign in with X — Design

**Date:** 2026-05-18
**Status:** Draft — pending user approval
**Scope:** Extend zipsa's OAuth infrastructure so users can authorize a
shared X (Twitter) app via `zipsa connect x`, then have an
`ZIPSA_TOKEN_X` access token injected into skill containers exactly
like the existing Notion OAuth flow.

**Out of scope (separate spec):** the `bip-daily-x` skill itself.
This spec ships first; the skill consumes the auth.

---

## Goal

One sentence: **A user runs `zipsa connect x`, completes a browser
"Sign in with X" flow, and any zipsa skill that needs to call the X
API gets a fresh access token injected as `ZIPSA_TOKEN_X` without
touching `developer.x.com` or copy-pasting tokens.**

Today's `OAuthManager` only handles HTTP MCP servers that expose
`.well-known/oauth-authorization-server` + RFC 7591 DCR (Notion). X
exposes neither — we (the zipsa maintainers) register one X App once,
hardcode its public Client ID into the launcher, and run the standard
OAuth 2.0 PKCE flow per user.

## Why this approach

We considered three alternatives:

- **Manual token paste.** User goes to developer.x.com, makes their own
  app, generates an access token, pastes into `~/.zipsa/.env`. Zero
  launcher work but ~30 minutes of friction per user and a long-term
  obstacle to anyone trying the BIP skill.
- **Per-user X app.** Each user dynamically registers their own X app.
  X doesn't support DCR (confirmed against current docs — no
  `.well-known` metadata, no public registration endpoint), so this
  isn't possible.
- **Shared X app + PKCE (chosen).** We register one X App as
  westbrookai/zipsa. Users go through "Sign in with X" against our
  Client ID. This is the standard pattern for desktop/CLI apps and the
  only option that gives the BIP skill the low-friction UX it needs to
  be worth shipping.

## User-facing flow

```
$ zipsa connect x
Opening https://x.com/i/oauth2/authorize?...
[browser opens, user clicks "Authorize app"]
[redirected to http://localhost:<port>/callback]
✓ Connected to X as @<handle>
```

Subsequent skill runs that declare an X dependency get the token
injected automatically. If the access token has expired, the launcher
refreshes it via `refresh_token` before starting the container; if the
refresh fails, the next `zipsa run …` prints a one-line prompt to
re-authorize.

## Architecture

### Provider registry (new concept)

Today `OAuthManager` assumes the auth target is an MCP server and
derives endpoints from the server URL. Going forward we'll have
non-MCP auth targets too (X now; GitHub, LinkedIn later). Introduce a
**provider registry** that names each external auth target and
declares how to talk to it.

```python
# zipsa/auth/providers.py (new)

@dataclass(frozen=True)
class Provider:
    name: str                          # "x"
    client_id: str                     # public, baked in
    authorization_endpoint: str
    token_endpoint: str
    scopes: list[str]
    token_env_var: str                 # "ZIPSA_TOKEN_X"
    display_handle_endpoint: str | None  # GET /users/me for connect-success UI

PROVIDERS: dict[str, Provider] = {
    "x": Provider(
        name="x",
        client_id="<westbrookai/zipsa X app Client ID>",
        authorization_endpoint="https://x.com/i/oauth2/authorize",
        token_endpoint="https://api.x.com/2/oauth2/token",
        scopes=["tweet.write", "tweet.read", "users.read", "offline.access"],
        token_env_var="ZIPSA_TOKEN_X",
        display_handle_endpoint="https://api.x.com/2/users/me",
    ),
}
```

MCP-server-based auth (Notion) keeps its current code path. The
registry is the union of "named providers" + "discovered MCP servers."
`zipsa connect <name>` looks up `name` in either; if both, manifest
servers win.

### `OAuthManager` extension

Refactor into two methods that share PKCE + token storage + browser
launch:

- `ensure_credentials_mcp(server_name, server_url)` — today's behavior
  (discover, DCR-register, PKCE, exchange).
- `ensure_credentials_provider(provider: Provider)` — skip discovery
  and DCR; use the provider's hardcoded endpoints and Client ID; run
  PKCE flow as a public client (no `client_secret`); store tokens
  under `~/.zipsa/credentials/<provider.name>.json`.

The shared logic (PKCE generation, browser callback wait, token
storage round-trip, refresh) becomes private helpers. Both entry
points return an access token.

### `zipsa connect <name>` command

`cli.py:connect` today only checks installed skills' MCP servers.
Update it to fall through to the provider registry: if `<name>` isn't
a known MCP server, try `PROVIDERS[<name>]`. If found, run
`ensure_credentials_provider`. On success, optionally fetch
`display_handle_endpoint` to print "Connected to X as @<handle>" — if
the call fails, just print "Connected to X" and move on (don't fail
the connect).

### Token injection into containers

`executor._ensure_oauth_credentials` today iterates over MCP servers
of type `http` with `auth.type=oauth2`. Add a parallel path: skills can
declare a provider dependency in the manifest, and the executor will
ensure that provider's token before starting the container.

Manifest addition (forward-compatible — no existing skill changes):

```yaml
spec:
  # ... existing fields ...
  auth_providers:                      # new top-level field
    - x                                # name from PROVIDERS registry
```

For each declared provider, the executor:
1. Looks up `PROVIDERS[provider_name]`.
2. Calls `OAuthManager.ensure_credentials_provider(provider)` —
   refreshes if needed, runs full flow if no creds exist (interactive),
   or fails fast with a helpful message if non-interactive.
3. Injects `provider.token_env_var=<access_token>` into the
   container's env.

## File changes

| File | Change |
|---|---|
| `launcher/zipsa/auth/providers.py` | **new** — `Provider` dataclass + `PROVIDERS` dict |
| `launcher/zipsa/auth/oauth.py` | refactor `_full_flow` into shared PKCE bits; add `ensure_credentials_provider` |
| `launcher/zipsa/auth/storage.py` | minor — `FileTokenStorage(name)` already file-per-name; no change expected |
| `launcher/zipsa/auth/browser.py` | none expected; reuses existing callback server |
| `launcher/zipsa/cli.py` | `connect` falls through to `PROVIDERS` lookup |
| `launcher/zipsa/core/executor.py` | `_ensure_oauth_credentials` handles `auth_providers` list |
| `launcher/zipsa/core/models.py` | add `auth_providers: list[str] = []` to `SkillSpec` |

## What's stored on disk

`~/.zipsa/credentials/x.json` (mode 0600):

```json
{
  "client_id": "<our public client id>",
  "access_token": "...",
  "refresh_token": "...",
  "expires_at": 1735689600,
  "scope": "tweet.write tweet.read users.read offline.access"
}
```

Same shape as today's Notion creds file. No new storage format.

## Non-interactive behavior

If a skill needs `ZIPSA_TOKEN_X` and there are no stored creds (or the
refresh fails) and the launcher can't open a browser (`stdin not a
tty`, or `ZIPSA_NONINTERACTIVE=1`), abort with
`error.code="oauth_unauthorized"` and a message naming the connect
command:

```
Not connected to X. Run: zipsa connect x
```

This mirrors today's behavior for Notion.

## YAGNI / out of scope for this PR

- **Provider registry as user config.** Hardcoded in source for v1.
  Loading providers from a YAML file is a v2 question.
- **Multi-account.** One stored set of creds per provider; logging in
  again replaces them. No `--account` flag.
- **GitHub / LinkedIn / Mastodon providers.** Added later — the
  registry is the seam for them; no need to predict their shape now.
- **`zipsa disconnect x`.** Useful but not required for the BIP skill
  to ship. Add when there's a second reason to want it.
- **Scope upgrades.** If a future skill needs additional scopes, the
  user re-runs `zipsa connect x` and re-authorizes. No diff/migration
  logic in v1.

## Test plan

Unit tests:
- `Provider` dataclass round-trip; `PROVIDERS["x"]` lookup.
- `ensure_credentials_provider` happy path (mock browser callback,
  mock token endpoint).
- Refresh path: stored creds near expiry → refresh → save.
- Non-interactive abort: no creds + no TTY → raises with
  `oauth_unauthorized`.

Manifest validation:
- Adding `auth_providers: ["x"]` to a fixture skill loads without error.
- Unknown provider name fails validation with a clear message.

Executor integration:
- Skill with `auth_providers: ["x"]` gets `ZIPSA_TOKEN_X` in its env
  (mock OAuthManager).
- Skill without `auth_providers` gets no token env var.

CLI:
- `zipsa connect x` with creds present and valid → reports already
  connected (no new flow).
- `zipsa connect x` with no creds → invokes provider flow (mocked).

## Open questions

- **Client ID storage in source.** The Client ID is public (PKCE-only
  public client, no secret), so committing it to a public repo is
  safe. We should still confirm by reading X's developer terms —
  v1 spec assumes "safe to commit." Worst case: load from
  `ZIPSA_X_CLIENT_ID` env at startup with the source as default.

- **Callback port collision.** Existing `CALLBACK_PORT` is fixed. If
  zipsa is open in two terminals running `connect` simultaneously the
  second will fail. Out of scope for v1 — acceptable failure mode.
