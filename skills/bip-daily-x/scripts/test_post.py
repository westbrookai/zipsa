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
    """RFC 5849 §3.4 — HMAC-SHA1 signature."""

    def test_rfc5849_signature_pinned(self):
        """Pinned exact signature for RFC 5849 §1.2 inputs. The RFC itself
        only documents the base string for this example, not the final
        signature, so this is our own pin — computed once externally with
        the documented algorithm. If our impl silently miscomputes (wrong
        base string, wrong key derivation, etc.), this test catches it."""
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
        token_secret = ""  # request_token step has no token yet
        sig = post.oauth1_signature(method, url, params, consumer_secret, token_secret)
        assert sig == "F1Li3tvehgcraF8DMJ7OyxO4w9Y="

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
    """OAuth Authorization header: comma-separated k=\"v\" with percent-encoded values."""

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
        # raw chars must NOT appear in the value
        assert 'oauth_signature="a/b+c=d"' not in h
