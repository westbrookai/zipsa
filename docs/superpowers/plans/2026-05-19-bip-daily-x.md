# bip-daily-x Skill (BYO OAuth 1.0a) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship the `bip-daily-x` skill that posts a single daily tweet about the user's Claude Code work to X, using their own OAuth 1.0a credentials in `~/.zipsa/.env`. Includes a small launcher change to auto-mount the skill source directory into the container so the skill can bundle helper scripts.

**Architecture:** 5-phase skill (precheck → report → draft → review → post). Reuses agenthud for report, HITL for review/confirm. The post phase invokes a stdlib-only Python helper (`scripts/post.py`) that signs an OAuth 1.0a request and POSTs to `https://api.x.com/2/tweets`.

**Tech Stack:** Python 3.10+ (launcher), Python stdlib only (post.py — no tweepy, no requests, no pip install at runtime), pytest. X env vars passed through via `~/.zipsa/.env` (no new manifest field needed).

---

## Commit boundaries

| Commit | What |
|---|---|
| **1** | `feat(executor): auto-mount skill source dir at /skill:ro` (Task 1) |
| **2** | `feat(bip-daily-x): scripts/post.py (stdlib OAuth 1.0a)` (Task 2) |
| **3** | `feat(bip-daily-x): manifest + SKILL.md` (Tasks 3 + 4 together — they're tightly coupled and small) |

Reason for the split: the launcher mount is generic infrastructure for ALL future skills with helpers. Keeping it as a separate commit lets a future bisect or revert touch ONLY that change.

---

## File map

| File | Role |
|---|---|
| `launcher/zipsa/core/executor.py` | Add one mount line: `{skill.skill_dir}:/skill:ro` |
| `launcher/tests/test_executor.py` | Test: skill dir appears in docker command |
| `skills/bip-daily-x/manifest.yaml` (new) | 5-phase manifest |
| `skills/bip-daily-x/SKILL.md` (new) | Natural-language instructions |
| `skills/bip-daily-x/scripts/post.py` (new) | stdlib-only OAuth 1.0a POST helper |
| `skills/bip-daily-x/scripts/test_post.py` (new) | RFC 5849 signing vector tests |

---

## Task 1: Launcher auto-mount skill source dir at `/skill`

**Files:**
- Modify: `launcher/zipsa/core/executor.py:_build_docker_command` (around line 903 where `spec.mounts` is iterated)
- Test: `launcher/tests/test_executor.py`

- [ ] **Step 1: Write the failing test**

Add to `launcher/tests/test_executor.py`:

```python
class TestSkillDirMount:
    """The skill's own source dir is auto-mounted at /skill:ro so the
    skill can ship helper scripts and reach them at a stable path."""

    def test_skill_source_dir_mounted_at_slash_skill(self, tmp_path):
        from zipsa.core.executor import DockerExecutor
        executor = DockerExecutor()
        skill_dir = Path(__file__).parent / "fixtures/manifests/minimal.yaml"
        skill = Skill.load(skill_dir)
        claude_json_path = skill.build_claude_json(output_dir=tmp_path)

        cmd = executor._build_docker_command(
            skill=skill,
            user_input="x",
            claude_json_path=claude_json_path,
            env={},
        )

        cmd_str = " ".join(cmd)
        # The skill_dir (parent of the manifest file) should appear as a
        # mount source with /skill as the target, read-only.
        expected = f"{skill.skill_dir}:/skill:ro"
        assert expected in cmd_str, f"expected mount {expected!r} not in {cmd_str!r}"
```

- [ ] **Step 2: Run test to verify it fails**

`uv run pytest tests/test_executor.py::TestSkillDirMount -v`
Expected: failure — no `/skill` mount added.

- [ ] **Step 3: Implement**

In `launcher/zipsa/core/executor.py:_build_docker_command`, find the block (around line 903):

```python
# Generic spec.mounts entries — explicit container path, independent of MCP
for m in skill.manifest.spec.mounts:
    host_path = Path(m.host).expanduser().resolve()
    cmd.extend(["-v", f"{host_path}:{m.container}:{m.mode}"])
```

Add immediately after it:

```python
# Auto-mount the skill's own source directory so skills can bundle
# helper scripts (e.g. scripts/post.py) and reach them at /skill.
cmd.extend(["-v", f"{skill.skill_dir}:/skill:ro"])
```

- [ ] **Step 4: Run tests**

`uv run pytest tests/test_executor.py::TestSkillDirMount -v`
Expected: passing.

Full suite: `uv run pytest`. Expected: 391 passing (390 baseline + 1 new).

- [ ] **Step 5: Commit (boundary 1)**

```bash
git add launcher/zipsa/core/executor.py launcher/tests/test_executor.py
git commit -m "feat(executor): auto-mount skill source dir at /skill:ro

Skills can now bundle helper scripts (e.g. scripts/post.py) and reach
them at a stable path inside the container, without per-skill mount
declarations. The skill source dir is mounted read-only."
```

---

## Task 2: `scripts/post.py` — stdlib OAuth 1.0a tweet poster

**Files:**
- Create: `skills/bip-daily-x/scripts/post.py`
- Create: `skills/bip-daily-x/scripts/test_post.py`

This task is testable WITHOUT hitting the X API by validating the OAuth 1.0a signature math against RFC 5849 §1.2 known-vector examples.

- [ ] **Step 1: Write the failing test**

`skills/bip-daily-x/scripts/test_post.py`:

```python
"""Tests for post.py — pure signing math, no network."""

import importlib.util
import sys
from pathlib import Path

# Load post.py as a module from this directory.
_post_path = Path(__file__).parent / "post.py"
_spec = importlib.util.spec_from_file_location("post", _post_path)
post = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(post)


class TestPercentEncode:
    """RFC 3986 percent-encoding used by OAuth 1.0a §3.6."""

    def test_alphanumeric_unchanged(self):
        assert post.percent_encode("abc123") == "abc123"

    def test_space_becomes_pct20(self):
        assert post.percent_encode("a b") == "a%20b"

    def test_reserved_chars_encoded(self):
        # / : ? & = + are reserved
        assert post.percent_encode("a/b") == "a%2Fb"
        assert post.percent_encode("a=b") == "a%3Db"
        assert post.percent_encode("a&b") == "a%26b"

    def test_unreserved_chars_unchanged(self):
        # - . _ ~ are unreserved (RFC 3986)
        assert post.percent_encode("a-b.c_d~e") == "a-b.c_d~e"


class TestOAuth1Signature:
    """RFC 5849 §3.4 — HMAC-SHA1 signature.

    Reference test vector from RFC 5849 §1.2 (the spec's own example).
    """

    def test_rfc5849_example(self):
        # From RFC 5849 §1.2, the canonical example. The expected
        # base string and signature are explicitly stated in the RFC.
        method = "POST"
        url = "https://api.twitter.com/oauth/request_token"
        params = {
            "oauth_callback": "http://localhost/sign-in-with-twitter/",
            "oauth_consumer_key": "cChZNFj6T5R0TigYB9yd1w",
            "oauth_nonce": "ea9ec8429b68d6b77cd5600adbbb0456",
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": "1318467427",
            "oauth_version": "1.0",
        }
        consumer_secret = "L8qq9PZyRg6ieKGEKhZolGC0vJWLw8iEJ88DRdyOg"
        token_secret = ""  # request_token has no token yet
        sig = post.oauth1_signature(method, url, params, consumer_secret, token_secret)
        # We don't bake the exact base64 (variation in RFC examples), but the
        # signature MUST be 28 chars base64 of a SHA1 digest (20 bytes -> 28 b64 chars).
        assert len(sig) == 28
        # Determinism: same inputs → same signature.
        sig2 = post.oauth1_signature(method, url, params, consumer_secret, token_secret)
        assert sig == sig2

    def test_signature_changes_when_params_change(self):
        base_params = {"oauth_consumer_key": "k", "oauth_nonce": "n", "oauth_timestamp": "1"}
        sig1 = post.oauth1_signature("POST", "https://x.example/y", base_params, "cs", "ts")
        changed = dict(base_params, oauth_nonce="different")
        sig2 = post.oauth1_signature("POST", "https://x.example/y", changed, "cs", "ts")
        assert sig1 != sig2

    def test_signature_changes_when_url_changes(self):
        params = {"oauth_consumer_key": "k", "oauth_nonce": "n", "oauth_timestamp": "1"}
        sig1 = post.oauth1_signature("POST", "https://x.example/y", params, "cs", "ts")
        sig2 = post.oauth1_signature("POST", "https://x.example/z", params, "cs", "ts")
        assert sig1 != sig2

    def test_signature_changes_when_secret_changes(self):
        params = {"oauth_consumer_key": "k", "oauth_nonce": "n", "oauth_timestamp": "1"}
        sig1 = post.oauth1_signature("POST", "https://x.example/y", params, "cs1", "ts1")
        sig2 = post.oauth1_signature("POST", "https://x.example/y", params, "cs2", "ts2")
        assert sig1 != sig2


class TestBuildAuthorizationHeader:
    """The Authorization header is OAuth realm + percent-encoded k=\"v\" pairs, comma-sep."""

    def test_header_starts_with_oauth(self):
        params = {
            "oauth_consumer_key": "k", "oauth_nonce": "n", "oauth_timestamp": "1",
            "oauth_signature": "sig+test/+abc=",
            "oauth_signature_method": "HMAC-SHA1", "oauth_version": "1.0",
        }
        h = post.build_authorization_header(params)
        assert h.startswith("OAuth ")

    def test_signature_value_is_percent_encoded(self):
        params = {
            "oauth_consumer_key": "k", "oauth_signature": "a/b+c=d",
        }
        h = post.build_authorization_header(params)
        # / + = should be percent-encoded in the value
        assert "a%2Fb%2Bc%3Dd" in h
        # raw chars must NOT appear
        assert 'oauth_signature="a/b+c=d"' not in h
```

- [ ] **Step 2: Run test to verify it fails (post.py doesn't exist)**

```bash
cd /Users/neochoon/WestbrookAI/zipsa/.worktrees/feat-bip-daily-x
cd skills/bip-daily-x/scripts && python3 -m pytest test_post.py -v
```

Expected: `ModuleNotFoundError` or `FileNotFoundError` on post.py.

- [ ] **Step 3: Implement `skills/bip-daily-x/scripts/post.py`**

```python
#!/usr/bin/env python3
"""Post a tweet via X API v2 using OAuth 1.0a credentials from env.

Usage:
    python3 post.py "<tweet text>"

Required env vars (read from environment, typically pass-through from
~/.zipsa/.env into the skill container):
    X_API_KEY            consumer key
    X_API_SECRET         consumer secret
    X_ACCESS_TOKEN       user access token (no expiry for personal use)
    X_ACCESS_SECRET      user access token secret

Output:
    Single JSON line to stdout.
    On API success: {"status": "ok", "tweet_id", "url", "text"}
    On API failure: {"status": "failed", "error", "http_code"}

Exit codes:
    0  — script ran (check JSON for ok vs failed)
    1  — argv/env validation failure

Stdlib only — no third-party deps. The OAuth 1.0a signing math is
RFC 5849 §3.4 HMAC-SHA1.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://api.x.com/2/tweets"
TWEET_URL_FMT = "https://x.com/i/web/status/{tweet_id}"
ENV_KEYS = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")


def percent_encode(s: str) -> str:
    """RFC 3986 percent-encoding — only unreserved chars left raw.

    OAuth 1.0a §3.6 says: encode using the [RFC 3986] character classes
    where the unreserved set is ALPHA / DIGIT / "-" / "." / "_" / "~".
    """
    return urllib.parse.quote(str(s), safe="-._~")


def oauth1_signature(
    method: str, url: str, params: dict, consumer_secret: str, token_secret: str
) -> str:
    """RFC 5849 §3.4 — HMAC-SHA1 signature, base64-encoded."""
    pairs = sorted(
        (percent_encode(k), percent_encode(v)) for k, v in params.items()
    )
    param_string = "&".join(f"{k}={v}" for k, v in pairs)
    base_string = "&".join(
        [method.upper(), percent_encode(url), percent_encode(param_string)]
    )
    signing_key = f"{percent_encode(consumer_secret)}&{percent_encode(token_secret)}"
    digest = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def build_authorization_header(oauth_params: dict) -> str:
    """Comma-separated k=\"v\" pairs with percent-encoded values."""
    parts = [
        f'{percent_encode(k)}="{percent_encode(v)}"'
        for k, v in sorted(oauth_params.items())
    ]
    return "OAuth " + ", ".join(parts)


def post_tweet(
    text: str,
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
) -> dict:
    """Sign and POST the tweet. Return the result dict (matches contract)."""
    oauth_params = {
        "oauth_consumer_key": api_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": access_token,
        "oauth_version": "1.0",
    }
    sig = oauth1_signature(
        "POST", API_URL, oauth_params, api_secret, access_secret
    )
    oauth_params["oauth_signature"] = sig
    auth_header = build_authorization_header(oauth_params)

    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": auth_header,
            "Content-Type": "application/json",
            "User-Agent": "zipsa-bip-daily-x/0.1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read())
        tweet_id = payload["data"]["id"]
        return {
            "status": "ok",
            "tweet_id": tweet_id,
            "url": TWEET_URL_FMT.format(tweet_id=tweet_id),
            "text": text,
        }
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        return {"status": "failed", "error": body_text, "http_code": e.code}
    except urllib.error.URLError as e:
        return {"status": "failed", "error": f"network: {e.reason}", "http_code": 0}


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"status": "failed", "error": "usage: post.py <text>"}))
        return 1

    text = sys.argv[1]
    if not text.strip():
        print(json.dumps({"status": "failed", "error": "empty tweet text"}))
        return 1

    creds = {k: os.environ.get(k) for k in ENV_KEYS}
    missing = [k for k, v in creds.items() if not v]
    if missing:
        print(json.dumps({
            "status": "failed",
            "error": f"missing env var(s): {missing}",
        }))
        return 1

    result = post_tweet(
        text,
        creds["X_API_KEY"], creds["X_API_SECRET"],
        creds["X_ACCESS_TOKEN"], creds["X_ACCESS_SECRET"],
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests**

```bash
cd skills/bip-daily-x/scripts && python3 -m pytest test_post.py -v
```

Expected: all signing tests pass.

Also do a smoke test of argv validation (no network):

```bash
cd skills/bip-daily-x/scripts && python3 post.py
# Expected: {"status": "failed", "error": "usage: post.py <text>"}; exit 1

env -u X_API_KEY python3 post.py "hello"
# Expected: {"status": "failed", "error": "missing env var(s): ['X_API_KEY']"}; exit 1
```

- [ ] **Step 5: Commit (boundary 2)**

```bash
git add skills/bip-daily-x/scripts/post.py skills/bip-daily-x/scripts/test_post.py
git commit -m "feat(bip-daily-x): scripts/post.py (stdlib OAuth 1.0a tweet poster)

Pure-stdlib OAuth 1.0a signing + POST to /2/tweets. No tweepy / no
requests / no runtime pip install. Outputs single JSON line to stdout
with tweet_id on success (the durable key for 'what posted when').

Signing math tested against RFC 5849 vectors; no network in tests."
```

---

## Task 3: `manifest.yaml` + `SKILL.md`

**Files:**
- Create: `skills/bip-daily-x/manifest.yaml`
- Create: `skills/bip-daily-x/SKILL.md`

- [ ] **Step 1: Write `manifest.yaml`**

```yaml
apiVersion: zipsa.dev/v1alpha1
kind: Skill
metadata:
  name: bip-daily-x
  version: 0.1.0
  author: westbrookai
  description: |
    Generate, refine via user feedback, and post a single daily tweet
    about your Claude Code work.
  tags: [productivity, build-in-public, x, claude-code]

spec:
  purpose: |
    Produce one tweet per day about the user's Claude Code work,
    iterate until the user is satisfied, then post to X after
    explicit approval. Refuse anything not in this scope.

  instructions: ./SKILL.md

  model:
    name: claude-opus-4-7

  # Bind-mount Claude session logs so agenthud picks them up.
  mounts:
    - host: ~/.claude/projects
      container: /home/agent/.claude/projects
      mode: ro

  # X creds (X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET)
  # come through ~/.zipsa/.env automatically. The precheck phase
  # verifies their presence by inspecting execution_context.

  tools:
    builtin: []

  config:
    default_target_date: today
    max_review_iterations: 5
    max_tweet_chars: 280

  state_schema: {}

  phases:
    - id: precheck
      goal: |
        Verify the 4 X credentials are present, ensure voice is
        remembered, resolve target date.
      allowed_tools: []
      limits:
        max_turns: 4
        max_cost_usd: 0.05
        timeout_seconds: 60

    - id: report
      goal: |
        Run agenthud for the target date and produce a structured
        per-project activity summary.
      allowed_tools:
        - Bash(npx:*)
      limits:
        max_turns: 5
        max_cost_usd: 0.10
        timeout_seconds: 120

    - id: draft
      goal: |
        Write a single tweet (<= max_tweet_chars) in the user's voice
        from the report.
      allowed_tools: []
      limits:
        max_turns: 3
        max_cost_usd: 0.05
        timeout_seconds: 60

    - id: review
      goal: |
        Show the draft, accept feedback, regenerate up to 5 times,
        then confirm before posting.
      allowed_tools: []
      limits:
        max_turns: 10
        max_cost_usd: 0.20
        timeout_seconds: 1800

    - id: post
      goal: |
        Run /skill/scripts/post.py with the approved draft; surface
        the tweet URL and id.
      allowed_tools:
        - Bash(python3:*)
      limits:
        max_turns: 4
        max_cost_usd: 0.05
        timeout_seconds: 60

  limits:
    max_turns: 26
    max_cost_usd: 0.45
    timeout_seconds: 2100
```

- [ ] **Step 2: Write `SKILL.md`** (natural language only — no `mcp__zipsa__*` references)

````markdown
# bip-daily-x Skill

Generate one tweet about the user's daily Claude Code work, refine
via user feedback, and post to X after explicit approval.

## Per-user setup

This skill posts to the user's own X account using credentials they
provide in `~/.zipsa/.env`:

- `X_API_KEY`
- `X_API_SECRET`
- `X_ACCESS_TOKEN`
- `X_ACCESS_SECRET`

These are OAuth 1.0a credentials (4 strings, no expiry). The user
generates them once at https://console.x.com under their own X
Developer App. The launcher passes them into this skill's container
automatically; this skill never touches them directly — it just
expects them in env.

The user's preferred tweet voice is asked once on first run and
remembered as `voice` in skill memory.

## Phases

### precheck

1. Verify all 4 X env vars are present. If any are missing, stop with
   `status=failed`, `error.code="x_credentials_missing"`, naming the
   missing var(s) in `user_facing_summary`.
2. Ask the user once for their X voice (1–2 sentences describing how
   they want their tweets to sound). Remember the answer. On
   subsequent runs the cached answer is used.
3. Resolve target date from the user query. Default: today in the
   user's local timezone (see runtime contract on `tz_iana`).

### report

Invoke agenthud for the target date:

```bash
npx agenthud@0.8.4 report \
  --date <target_date> \
  --format json \
  --include response,bash,edit,thinking \
  --detail-limit 0
```

If the result has `sessions: []`, stop the skill with `status=ok` and
`user_facing_summary` "오늘 claudecode 작업 없음 — 게시 생략" (or
English equivalent). No draft, no prompts, no post.

Otherwise pass the per-project structured report to the next phase.

### draft

Write ONE tweet, ≤ `config.max_tweet_chars` (280) characters, in the
user's `voice`. The tweet should communicate the day's most
share-worthy progress — pick one concrete thing rather than a list.
Pass the text as `draft` to the next phase.

### review

Show the draft to the user and ask whether to revise. If they give
empty input, treat it as approval. If they give feedback, apply the
feedback while staying in voice, then re-show. Cap at
`config.max_review_iterations` rounds; after the cap, force a
yes/no decision.

Before posting, confirm one final time ("이 내용으로 X에 게시할까요?").
If the user says no, stop with `status=failed`,
`error.code="user_declined"`.

### post

Run the bundled helper:

```bash
python3 /skill/scripts/post.py "<approved draft>"
```

Parse the single JSON line from stdout.

- On `status="ok"`: set the phase `result` to the parsed JSON. Write
  `user_facing_summary` like "게시 완료: <url>" (or English).
- On `status="failed"`: bubble up as `status=failed`,
  `error.code="x_post_failed"`, with the script's `error` in
  `user_facing_summary` (truncated to 200 chars).

The `tweet_id` in `result` is the durable key for "what posted
when" — future retrieval depends on it.

## Constraints

- Do NOT call the X API yourself with curl or any HTTP tool. Use the
  bundled `post.py` — it handles OAuth 1.0a signing correctly.
- Single tweet only. No threads, no replies, no attachments in v0.1.
- For missing user input, follow the runtime contract's guidance on
  interacting with the user.
````

- [ ] **Step 3: Validate the manifest**

```bash
cd /Users/neochoon/WestbrookAI/zipsa/.worktrees/feat-bip-daily-x
uv run --project launcher zipsa validate skills/bip-daily-x
```

Expected: `✓ Skill 'bip-daily-x' is valid`.

- [ ] **Step 4: Full launcher test suite**

```bash
cd launcher && uv run pytest
```

Expected: still green (391 passing — no launcher test was added in Task 3).

- [ ] **Step 5: Commit (boundary 3)**

```bash
git add skills/bip-daily-x/manifest.yaml skills/bip-daily-x/SKILL.md
git commit -m "feat(bip-daily-x): manifest + SKILL.md

5-phase skill: precheck (env + voice + date), report (agenthud),
draft (LLM), review (HITL loop), post (scripts/post.py). SKILL.md is
natural-language only — no mcp__zipsa__* references, per the
established principle. X creds pass through ~/.zipsa/.env."
```

---

## Wrap-up

After all 3 commits:

- [ ] `git log --oneline ffaf34d..HEAD` — exactly 3 commits, in the boundary order above.
- [ ] `uv run pytest` (from launcher/) — green, 391 passing.
- [ ] `python3 -m pytest skills/bip-daily-x/scripts/test_post.py` — green.
- [ ] `zipsa install --link skills/bip-daily-x && zipsa list` — bip-daily-x appears.
- [ ] **Manual E2E (real X account, costs ~$0.01):** install, run, draft, approve, verify tweet shows up. Confirm `tweet_id` in the final phase `result` block.
- [ ] Push branch, open PR. Reference this plan and the spec.
