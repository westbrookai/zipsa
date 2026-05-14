"""Browser-based OAuth callback server."""

import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

CALLBACK_PORT = 54321


class OAuthCallbackError(Exception):
    """Raised when the OAuth provider returns an error or state mismatch."""


class _CallbackHandler(BaseHTTPRequestHandler):
    """Captures authorization code from OAuth redirect."""

    code: str | None = None
    error: str | None = None
    state: str | None = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = parse_qs(parsed.query)
        self.__class__.code = params.get("code", [None])[0]
        self.__class__.error = params.get("error", [None])[0]
        self.__class__.state = params.get("state", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if self.__class__.error:
            self.wfile.write(
                b"<html><body><h2>Authorization failed. You can close this window.</h2></body></html>"
            )
        else:
            self.wfile.write(
                b"<html><body><h2>Authorization complete! You can close this window.</h2></body></html>"
            )

    def log_message(self, format: str, *args: object) -> None:
        pass  # suppress request logs


class LocalCallbackServer:
    """Blocking HTTP server that waits for one OAuth callback on /callback."""

    def __init__(self, port: int = CALLBACK_PORT):
        self.port = port
        self.code: str | None = None
        self.state: str | None = None

    def wait_for_code(self, timeout: int = 120, expected_state: str | None = None) -> str:
        """Block until /callback?code=... or ?error=... arrives.

        Raises OAuthCallbackError on denied auth, state mismatch, or timeout.
        """
        _CallbackHandler.code = None
        _CallbackHandler.error = None
        _CallbackHandler.state = None

        server = HTTPServer(("localhost", self.port), _CallbackHandler)
        server.timeout = 1

        start = time.time()
        try:
            while _CallbackHandler.code is None and _CallbackHandler.error is None:
                if time.time() - start > timeout:
                    raise TimeoutError(
                        f"OAuth callback timed out after {timeout}s on port {self.port}"
                    )
                server.handle_request()
        finally:
            server.server_close()

        if _CallbackHandler.error:
            raise OAuthCallbackError(
                f"Authorization failed: {_CallbackHandler.error}"
            )

        if expected_state is not None and _CallbackHandler.state != expected_state:
            raise OAuthCallbackError(
                "State mismatch — possible CSRF attack, aborting OAuth flow"
            )

        self.code = _CallbackHandler.code
        self.state = _CallbackHandler.state
        assert self.code is not None
        return self.code


def open_browser_and_wait(
    auth_url: str,
    port: int = CALLBACK_PORT,
    timeout: int = 120,
    expected_state: str | None = None,
) -> str:
    """Open browser at auth_url and block until OAuth callback returns the code."""
    server = LocalCallbackServer(port=port)
    webbrowser.open(auth_url)
    return server.wait_for_code(timeout=timeout, expected_state=expected_state)
