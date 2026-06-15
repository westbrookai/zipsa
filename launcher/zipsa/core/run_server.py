"""Host MCP server for the run-time: exposes exec + HITL to the in-container
LLM that runs a skill. Sibling of CreateServer, minus promote.

Design decisions:
- Registers exactly exec + ask + confirm + choose; no promote, no memory, no
  skill-builder tools. Keeping the surface minimal prevents the skill LLM from
  calling tools it should not have access to.
- tool_names() returns a literal list matching the @mcp.tool registrations;
  deriving from the FastMCP instance at test time is fragile (internal API),
  so we document the registration explicitly here instead.
- CallerContextMiddleware with a per-server token enforces auth; the exec
  handler is injected so RunServer is testable without Docker or real scripts.
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


class RunServer:
    def __init__(self, hitl_io: HitlIO, exec_handler, caller: "CallerInfo | None" = None):
        self._io = hitl_io
        self._exec_handler = exec_handler
        self._caller = caller or CallerInfo("run", "run")
        self.port = 0
        self.token = ""
        self._tool_names: list[str] = []
        self._thread: Optional[threading.Thread] = None
        self._uvicorn_server: Optional[uvicorn.Server] = None

    def tool_names(self) -> list[str]:
        return list(self._tool_names)

    def start(self) -> None:
        self.port = _pick_free_port()
        self.token = secrets.token_urlsafe(32)
        mcp = FastMCP(
            "zipsa-run",
            host="127.0.0.1",
            port=self.port,
            stateless_http=False,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=_ALLOWED_HOSTS,
            ),
        )

        exec_handler = self._exec_handler
        ask_h = AskHandler(self._io)
        confirm_h = ConfirmHandler(self._io)
        choose_h = ChooseHandler(self._io)

        @mcp.tool(name="exec")
        def exec_script(script: str, args: str = "", prev: dict | None = None) -> dict:
            """Run ONE of this skill's scripts and return its result."""
            return exec_handler.run(script=script, args=args, prev=prev)

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

        self._tool_names = ["exec", "ask", "confirm", "choose"]
        app = mcp.streamable_http_app()
        app.add_middleware(
            CallerContextMiddleware, token_map={self.token: self._caller},
        )
        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=self.port,
            log_level="error",
            access_log=False,
        )
        self._uvicorn_server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._uvicorn_server.run,
            daemon=True,
            name=f"run-mcp-{self.port}",
        )
        self._thread.start()

        deadline, step, elapsed = 5.0, 0.05, 0.0
        while elapsed < deadline:
            try:
                sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sk.settimeout(0.5)
                sk.connect(("127.0.0.1", self.port))
                sk.close()
                return
            except OSError:
                threading.Event().wait(step)
                elapsed += step
        raise RuntimeError(f"RunServer failed to listen on port {self.port}")

    def stop(self) -> None:
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._uvicorn_server = None
        self._thread = None
