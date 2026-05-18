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

from .hitl_mcp import HitlIO


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

        mcp = FastMCP("zipsa", host="127.0.0.1", port=self.port)
        app = mcp.streamable_http_app()
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
