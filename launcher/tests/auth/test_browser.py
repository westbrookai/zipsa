"""Tests for browser callback server."""

import threading
import time
import urllib.request
import pytest
from zipsa.auth.browser import LocalCallbackServer


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
