"""CreateServer — focused host MCP server for `zipsa create`.

Exposes exactly two tools to the authoring container: `exec` (test the
draft via the real host `zipsa exec`) and `promote` (name + move into
the repo). Deliberately NOT the full HitlServer surface — the authoring
agent needs only these two.

Reuses the legacy run path's building blocks (free-port picker, allowed
host list, Bearer-token middleware) but stays a separate class so the
DockerExecutor run path is untouched. Handlers are injected so the
server is testable without docker or real filesystem moves.
"""

from __future__ import annotations

import secrets
import socket
import threading
from typing import Optional

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .caller_context import CallerContextMiddleware, CallerInfo
from .hitl_runner import _ALLOWED_HOSTS, _pick_free_port


class CreateServer:
    def __init__(
        self,
        exec_handler,
        promote_handler,
        caller: "CallerInfo | None" = None,
    ) -> None:
        self._exec_handler = exec_handler
        self._promote_handler = promote_handler
        self._caller = caller or CallerInfo("skill-builder", "create")
        self.port: int = 0
        self.token: str = ""
        self._thread: Optional[threading.Thread] = None
        self._uvicorn_server: Optional[uvicorn.Server] = None

    def start(self) -> None:
        self.port = _pick_free_port()
        self.token = secrets.token_urlsafe(32)

        mcp = FastMCP(
            "zipsa-create",
            host="127.0.0.1",
            port=self.port,
            stateless_http=False,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=_ALLOWED_HOSTS,
            ),
        )

        exec_handler = self._exec_handler
        promote_handler = self._promote_handler

        @mcp.tool(name="exec")
        def exec_skill(staging_path: str, args: str = "") -> dict:
            """Test the draft skill: run it through the real `zipsa exec`
            (docker mode, a fresh runtime container per phase) and return
            the result. `staging_path` is the skill dir; `args` is an
            optional user_query to exercise it with."""
            return exec_handler.run(staging_path=staging_path, args=args)

        @mcp.tool(name="promote")
        def promote_skill(staging_path: str, name: str) -> dict:
            """Finalize the skill: validate `name` (kebab-case) and move
            the draft from staging into the repo's skills/<name>/. Call
            this only once the user is happy and a name is agreed."""
            return promote_handler.run(staging_path=staging_path, name=name)

        app = mcp.streamable_http_app()
        app.add_middleware(
            CallerContextMiddleware, token_map={self.token: self._caller},
        )
        config = uvicorn.Config(
            app, host="0.0.0.0", port=self.port,
            log_level="error", access_log=False,
        )
        self._uvicorn_server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._uvicorn_server.run,
            daemon=True,
            name=f"create-mcp-{self.port}",
        )
        self._thread.start()

        deadline, step, elapsed = 5.0, 0.05, 0.0
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
        raise RuntimeError(f"CreateServer failed to listen on port {self.port}")

    def stop(self) -> None:
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._uvicorn_server = None
        self._thread = None
