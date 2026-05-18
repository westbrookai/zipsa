"""HitlServer — runs an HTTP MCP server in a daemon thread for one
zipsa run. Owns port allocation, per-run Bearer token, and start/stop
lifecycle. Tool wiring is added in a later task; for now the server
exposes the bare framework so port/token can be asserted in tests."""

from __future__ import annotations

import secrets
import socket
import threading
from typing import Optional

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .hitl_mcp import HitlIO


class _BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, expected_token: str) -> None:
        super().__init__(app)
        self._expected = expected_token

    async def dispatch(self, request, call_next):
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {self._expected}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def _pick_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class HitlServer:
    """HTTP MCP server (FastMCP) bound to 127.0.0.1:<random-port>."""

    def __init__(self, io_: HitlIO) -> None:
        self._io = io_
        self.port: int = 0
        self.token: str = ""
        self._thread: Optional[threading.Thread] = None
        self._uvicorn_server: Optional[uvicorn.Server] = None

    def start(self) -> None:
        self.port = _pick_free_port()
        self.token = secrets.token_urlsafe(32)

        mcp = FastMCP("zipsa", host="127.0.0.1", port=self.port,
                      stateless_http=False)

        from .hitl_mcp import AskHandler, ConfirmHandler, ChooseHandler, HitlUnattended

        ask_h = AskHandler(self._io)
        confirm_h = ConfirmHandler(self._io)
        choose_h = ChooseHandler(self._io)

        @mcp.tool()
        def ask(prompt: str) -> str:
            """Ask the user a free-text question and return their reply."""
            try:
                return ask_h.run(prompt=prompt)
            except HitlUnattended as e:
                raise RuntimeError(f"HITL_UNATTENDED: {e}") from e

        @mcp.tool()
        def confirm(message: str, default: bool | None = None) -> bool:
            """Ask the user a yes/no question."""
            try:
                return confirm_h.run(message=message, default=default)
            except HitlUnattended as e:
                raise RuntimeError(f"HITL_UNATTENDED: {e}") from e

        @mcp.tool()
        def choose(prompt: str, options: list[str]) -> str:
            """Ask the user to choose one of the given options."""
            try:
                return choose_h.run(prompt=prompt, options=options)
            except HitlUnattended as e:
                raise RuntimeError(f"HITL_UNATTENDED: {e}") from e

        app = mcp.streamable_http_app()
        app.add_middleware(_BearerAuthMiddleware, expected_token=self.token)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.port,
            log_level="error",
            access_log=False,
        )
        self._uvicorn_server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._uvicorn_server.run,
            daemon=True,
            name=f"hitl-mcp-{self.port}",
        )
        self._thread.start()

        # Wait until the server actually accepts connections
        deadline = 5.0
        step = 0.05
        elapsed = 0.0
        while elapsed < deadline:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                s.connect(("127.0.0.1", self.port))
                s.close()
                return
            except OSError:
                threading.Event().wait(step)
                elapsed += step
        raise RuntimeError(f"HitlServer failed to listen on port {self.port}")

    def stop(self) -> None:
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._uvicorn_server = None
        self._thread = None
