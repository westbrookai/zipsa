#!/usr/bin/env python3
"""Post a tweet via X API v2 using OAuth 1.0a credentials from env.

Usage:
    python3 post.py "<tweet text>"

Required env vars:
    X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET

Output: single JSON line to stdout.
    On API success: {"status": "ok", "tweet_id", "url", "text"}
    On API failure: {"status": "failed", "error", "http_code"}

Exit codes:
    0  — script ran (check JSON for ok vs failed)
    1  — argv/env validation failure

Stdlib only — no third-party deps. RFC 5849 §3.4 HMAC-SHA1.
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

    OAuth 1.0a §3.6: unreserved = ALPHA / DIGIT / "-" / "." / "_" / "~".
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
    """Comma-separated k="v" pairs with percent-encoded values."""
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
    """Sign and POST the tweet. Return result dict matching contract."""
    # OAuth 1.0a signature base string (RFC 5849 §3.4.1.3) only
    # includes form-encoded ("application/x-www-form-urlencoded") body
    # parameters. X API v2 takes a JSON body, so the JSON body is
    # correctly NOT included in the signature — only the six oauth_*
    # fields below are signed. If you ever change this to a
    # form-encoded endpoint, you MUST add the body params to oauth_params
    # before signing.
    oauth_params = {
        "oauth_consumer_key": api_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": access_token,
        "oauth_version": "1.0",
    }
    sig = oauth1_signature("POST", API_URL, oauth_params, api_secret, access_secret)
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
    if len(sys.argv) == 2 and sys.argv[1] == "--check-env":
        creds = {k: os.environ.get(k) for k in ENV_KEYS}
        missing = [k for k, v in creds.items() if not v]
        if missing:
            print(json.dumps({"status": "failed", "error": f"missing env var(s): {missing}"}))
            return 1
        print(json.dumps({"status": "ok", "message": "all X env vars present"}))
        return 0

    if len(sys.argv) != 2:
        print(json.dumps({"status": "failed", "error": "usage: post.py <text> | --check-env"}))
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
