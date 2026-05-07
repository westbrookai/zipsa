# MCP OAuth Authentication Design

## Goal

`zipsa run` transparently handles OAuth authentication for HTTP MCP servers before launching the skill. If credentials are missing, it performs the OAuth flow. If a token is expired, it refreshes automatically.

## Background

HTTP MCP servers (`type: http`) may require OAuth 2.1-based authentication. The naive approach — letting Claude Code inside Docker handle OAuth — is structurally broken:
- The container's OAuth callback server binds to a random port
- The host browser cannot reach the container's localhost

Therefore **zipsa performs OAuth on the host** and injects the resulting token into Docker at runtime.

## Standards

- RFC 8414 — OAuth Authorization Server Metadata (discovery)
- RFC 7591 — OAuth 2.0 Dynamic Client Registration (no pre-registration required)
- RFC 7636 — PKCE (Proof Key for Code Exchange)
- RFC 6749 — OAuth 2.0 (refresh_token grant)

`mcp.notion.com/mcp` confirmed to support all of the above:
```json
{
  "registration_endpoint": "https://mcp.notion.com/register",
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "code_challenge_methods_supported": ["S256"]
}
```

## Implementation Library

Use the official MCP Python SDK (`mcp` package), specifically `mcp.client.auth.OAuthClientProvider`. Do not reimplement OAuth from scratch.

```
pip install mcp
```

`OAuthClientProvider` handles:
- `/.well-known/oauth-authorization-server` discovery
- Dynamic Client Registration (POST /register)
- PKCE code_challenge generation and verification
- Authorization code → access_token exchange
- Token refresh via refresh_token

## Credential Storage

```
~/.zipsa/credentials/
  notion.json
  github.json
  ...
```

File format:
```json
{
  "client_id": "dynamically-registered-id",
  "client_secret": "...",
  "access_token": "...",
  "refresh_token": "...",
  "expires_at": 1234567890,
  "scope": "..."
}
```

File permissions: `600` (owner read/write only).

## Architecture

### `zipsa run` execution flow

```
zipsa run skills/daily-progress "..."
  ↓
Extract HTTP MCP servers from manifest
  ↓
For each server with auth.type == oauth2:
  Does ~/.zipsa/credentials/<server>.json exist?
    → No:  run OAuth flow (same logic as zipsa connect)
    → Yes: is access_token expired?
              → Yes: refresh via refresh_token
                       → refresh fails: re-run full OAuth flow
              → No:  use as-is
  ↓
All credentials ready
  ↓
Inject tokens as env vars → Docker run
```

### `zipsa connect` command (explicit auth management)

```bash
zipsa connect skills/daily-progress          # auth all HTTP servers in skill
zipsa connect skills/daily-progress notion   # auth specific server only
```

Uses the same internal logic as the `zipsa run` pre-flight step.
Use this to pre-authorize before a run, or to force re-authorization.

### Token injection into Docker

The executor reads credentials before launching Docker and injects tokens as environment variables:

```python
# executor auto-generates per server
env["ZIPSA_TOKEN_NOTION"] = credential["access_token"]
```

The manifest `headersHelper` reads this env var:

```yaml
# manifest.yaml
- name: notion
  type: http
  url: https://mcp.notion.com/mcp
  auth:
    type: oauth2
  headersHelper: >
    echo "{\"Authorization\": \"Bearer $ZIPSA_TOKEN_NOTION\"}"
```

When `auth.type: oauth2` is declared, the executor also auto-generates the `headersHelper` if not explicitly set, so skill authors do not need to write it.

## Manifest Schema

Add `auth` field to HTTP MCP server definitions:

```yaml
mcp:
  - name: notion
    type: http
    url: https://mcp.notion.com/mcp
    auth:
      type: oauth2        # oauth2 | none
    allowed_tools:
      - notion-search
      - notion-create-pages
```

`auth` not set: executor skips OAuth pre-flight for this server.
`auth.type: none`: explicitly skip OAuth (no-op).

## Component Breakdown

### `zipsa/auth/oauth.py`
- `OAuthManager` class
- `ensure_credentials(server_name, server_url) -> str` — returns valid access_token
- Wraps `mcp.client.auth.OAuthClientProvider`
- Reads/writes credential files
- Checks token expiry and triggers refresh

### `zipsa/auth/storage.py`
- `FileTokenStorage` — persists tokens to `~/.zipsa/credentials/<name>.json`
- Implements `mcp.client.auth.TokenStorage` protocol

### `zipsa/auth/browser.py`
- `open_browser_and_wait(auth_url, redirect_uri) -> str` — returns authorization code
- `webbrowser.open()` + local HTTP callback server on a fixed port chosen by zipsa
- The redirect_uri is passed to `OAuthClientProvider` so it is included in the auth URL

### `zipsa/core/executor.py` changes
- `run()` calls `_ensure_oauth_credentials(skill)` before building the Docker command
- Injects tokens as env vars into the `env` dict

### `zipsa/cli.py` addition
- `zipsa connect <skill_dir> [server_name]` command

## UX

```
$ zipsa run skills/daily-progress "summarize today's work"

Loaded skill: daily-progress
Checking credentials...
  notion: missing → starting OAuth flow
  Opening browser for Notion authorization...
  Waiting for callback on http://localhost:54321/callback ...
  ✓ notion: authorized (expires in 3600s)
Running skill...
```

```
$ zipsa run skills/daily-progress "summarize today's work"  # second run

Loaded skill: daily-progress
Checking credentials...
  notion: ✓ valid
Running skill...
```

```
$ zipsa connect skills/daily-progress notion

Re-authorizing notion...
Opening browser...
✓ notion: re-authorized
```

## Out of Scope

- zipsa acting as an OAuth authorization server
- GUI / TUI for credential management
- Team-shared credentials
- Automatic key rotation for API key-based servers

## Design Decisions

**Q: Should `auth` be auto-discovered (try `/.well-known/`) or require explicit declaration?**
→ Explicit declaration required. Skill authors must declare intent clearly. Auto-discovery silently changes behavior when a server adds OAuth support.

**Q: Should `headersHelper` be auto-generated when `auth.type: oauth2`?**
→ Yes. The executor generates `echo '{"Authorization": "Bearer $ZIPSA_TOKEN_<NAME>"}'` automatically. Skill authors may override by providing an explicit `headersHelper`.

**Q: What happens when token refresh fails?**
→ Automatically trigger a full OAuth re-authorization flow (same as first-time auth).

**Q: What env var name convention for injected tokens?**
→ `ZIPSA_TOKEN_<SERVER_NAME_UPPER>` (e.g., `ZIPSA_TOKEN_NOTION`, `ZIPSA_TOKEN_GITHUB`).
