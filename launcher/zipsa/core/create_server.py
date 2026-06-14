"""CreateServer — focused host MCP server for `zipsa create`.

The authoring agent runs headless (`claude -p`) inside the runtime
container and reaches back here over HTTP MCP for everything it needs:

- ask / confirm / choose — converse with the host user (HITL), exactly
  the mechanism the legacy `zipsa run` path uses (routed to the host
  terminal via HitlIO).
- exec — test the draft via the real host `zipsa exec` (per-phase
  container).
- promote — name + move the draft into the repo.

Deliberately NOT the full HitlServer surface (no memory / run_skill /
write_skill_files). Reuses the legacy run path's building blocks
(free-port picker, allowed-host list, Bearer-token middleware, HITL
handlers) but stays a separate class so DockerExecutor is untouched.
Handlers are injected so the server is testable without docker or real
filesystem moves.
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
from .hitl_mcp import AskHandler, ChooseHandler, ConfirmHandler, HitlIO, HitlUnattended
from .hitl_runner import _ALLOWED_HOSTS, _pick_free_port


class CreateServer:
    def __init__(
        self,
        hitl_io: HitlIO,
        exec_handler,
        promote_handler,
        caller: "CallerInfo | None" = None,
    ) -> None:
        self._io = hitl_io
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
        ask_h = AskHandler(self._io)
        confirm_h = ConfirmHandler(self._io)
        choose_h = ChooseHandler(self._io)

        @mcp.tool()
        def ask(prompt: str) -> str:
            """Ask the host user a free-text question and return their reply."""
            try:
                return ask_h.run(prompt=prompt)
            except HitlUnattended as e:
                raise RuntimeError(f"HITL_UNATTENDED: {e}") from e

        @mcp.tool()
        def confirm(message: str, default: bool | None = None) -> bool:
            """Ask the host user a yes/no question."""
            try:
                return confirm_h.run(message=message, default=default)
            except HitlUnattended as e:
                raise RuntimeError(f"HITL_UNATTENDED: {e}") from e

        @mcp.tool()
        def choose(prompt: str, options: list[str]) -> str:
            """Ask the host user to choose one of the given options."""
            try:
                return choose_h.run(prompt=prompt, options=options)
            except HitlUnattended as e:
                raise RuntimeError(f"HITL_UNATTENDED: {e}") from e

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
