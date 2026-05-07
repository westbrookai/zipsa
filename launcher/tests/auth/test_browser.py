"""Tests for browser callback server."""

import threading
import time
import urllib.request
import pytest
from zipsa.auth.browser import LocalCallbackServer, OAuthCallbackError


class TestLocalCallbackServer:
    """Test local HTTP callback server."""

    def test_captures_authorization_code(self):
        """Server captures ?code= from callback request."""
        server = LocalCallbackServer(port=54399)
        t = threading.Thread(target=server.wait_for_code, kwargs={"timeout": 5})
        t.start()
        time.sleep(0.1)  # let server start
        urllib.request.urlopen("http://localhost:54399/callback?code=test-code&state=abc")
        t.join(timeout=3)
        assert server.code == "test-code"
        assert server.state == "abc"

    def test_returns_success_page(self):
        """Callback response is 200 with human-readable message."""
        server = LocalCallbackServer(port=54398)
        t = threading.Thread(target=server.wait_for_code, kwargs={"timeout": 5})
        t.start()
        time.sleep(0.1)
        response = urllib.request.urlopen("http://localhost:54398/callback?code=c&state=s")
        body = response.read().decode()
        t.join(timeout=3)
        assert response.status == 200
        assert "close" in body.lower() or "complete" in body.lower()

    def test_timeout_raises(self):
        """Raises TimeoutError if no callback arrives within timeout."""
        server = LocalCallbackServer(port=54397)
        with pytest.raises(TimeoutError):
            server.wait_for_code(timeout=1)

    def test_ignores_requests_to_non_callback_paths(self):
        """Requests to non-/callback paths are ignored (404) and don't set code."""
        import threading, time, urllib.request, urllib.error
        server = LocalCallbackServer(port=54396)
        t = threading.Thread(target=server.wait_for_code, kwargs={"timeout": 3})
        t.start()
        time.sleep(0.1)
        # This should 404 and NOT terminate the wait loop
        try:
            urllib.request.urlopen("http://localhost:54396/favicon.ico?code=evil-code")
        except urllib.error.HTTPError:
            pass  # 404 expected
        # Now send the real callback
        urllib.request.urlopen("http://localhost:54396/callback?code=real-code&state=abc")
        t.join(timeout=3)
        assert server.code == "real-code"

    def test_error_response_raises_oauth_callback_error(self):
        """?error= in callback raises OAuthCallbackError instead of hanging."""
        import threading, time, urllib.request
        from zipsa.auth.browser import OAuthCallbackError
        server = LocalCallbackServer(port=54395)
        exc_holder = []

        def run():
            try:
                server.wait_for_code(timeout=5)
            except OAuthCallbackError as e:
                exc_holder.append(e)

        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.1)
        urllib.request.urlopen("http://localhost:54395/callback?error=access_denied&state=s")
        t.join(timeout=3)
        assert len(exc_holder) == 1
        assert "access_denied" in str(exc_holder[0])

    def test_state_mismatch_raises_oauth_callback_error(self):
        """State mismatch raises OAuthCallbackError."""
        import threading, time, urllib.request
        from zipsa.auth.browser import OAuthCallbackError
        server = LocalCallbackServer(port=54394)
        exc_holder = []

        def run():
            try:
                server.wait_for_code(timeout=5, expected_state="correct-state")
            except OAuthCallbackError as e:
                exc_holder.append(e)

        t = threading.Thread(target=run)
        t.start()
        time.sleep(0.1)
        urllib.request.urlopen("http://localhost:54394/callback?code=c&state=wrong-state")
        t.join(timeout=3)
        assert len(exc_holder) == 1
        assert "CSRF" in str(exc_holder[0]) or "mismatch" in str(exc_holder[0]).lower()
