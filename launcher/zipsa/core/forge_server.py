"""Host MCP server for the forge authoring session: exposes path-scoped
exec + run + promote + HITL to the in-container LLM that builds a skill.

Design decisions:
- The server is constructed with the session's staging_path; the promote tool
  injects it so the agent only passes `name`. This keeps the draft location an
  invariant the agent cannot redirect.
- exec/run/promote tool bodies live as instance methods (_exec_impl/_run_impl/
  _promote_impl) so they are callable and testable without start() spinning up
  a uvicorn server. The @mcp.tool registrations are thin wrappers.
- tool_names() returns a literal list matching the registrations; deriving from
  the FastMCP instance is fragile (internal API), so it's documented explicitly.

Follow-up: this is the 3rd copy of the FastMCP/uvicorn boilerplate
(RunServer/CreateServer/ForgeServer) — a shared base is deferred (out of scope).
"""

from __future__ import annotations

import secrets
import socket
import threading
import time
from typing import Optional

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .caller_context import CallerContextMiddleware, CallerInfo
from .hitl_mcp import AskHandler, ChooseHandler, ConfirmHandler, HitlIO, HitlUnattended, ReportHandler
from .hitl_runner import _ALLOWED_HOSTS, _pick_free_port


class ForgeServer:
    def __init__(
        self,
        hitl_io: HitlIO,
        *,
        exec_handler,
        run_handler,
        promote_handler,
        staging_path: str = "",
        caller: "CallerInfo | None" = None,
    ):
        self._io = hitl_io
        self._exec_handler = exec_handler
        self._run_handler = run_handler
        self._promote_handler = promote_handler
        self._staging = staging_path
        self._caller = caller or CallerInfo("forge", "forge")
        self.port = 0
        self.token = ""
        self._tool_names: list[str] = []
        self._thread: Optional[threading.Thread] = None
        self._uvicorn_server: Optional[uvicorn.Server] = None

    def tool_names(self) -> list[str]:
        return list(self._tool_names)

    def _exec_impl(self, *, script, args="", prev=None, mounts=None):
        return self._exec_handler.run(script=script, args=args, prev=prev, mounts=mounts)

    def _run_impl(self, *, args="", mounts=None):
        return self._run_handler.run(args=args, mounts=mounts)

    def _promote_impl(self, *, name):
        return self._promote_handler.run(staging_path=self._staging, name=name)

    def start(self) -> None:
        self.port = _pick_free_port()
        self.token = secrets.token_urlsafe(32)
        mcp = FastMCP(
            "zipsa-forge",
            host="127.0.0.1",
            port=self.port,
            stateless_http=False,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=_ALLOWED_HOSTS,
            ),
        )

        ask_h = AskHandler(self._io)
        confirm_h = ConfirmHandler(self._io)
        choose_h = ChooseHandler(self._io)
        report_h = ReportHandler(self._io)

        @mcp.tool(name="exec")
        def exec_script(script: str, args: str = "", prev: dict | None = None,
                        mounts: list[tuple[str, str]] | None = None) -> dict:
            """Run ONE of the draft's scripts (fast debug)."""
            return self._exec_impl(script=script, args=args, prev=prev, mounts=mounts)

        @mcp.tool(name="run")
        def run_skill(args: str = "",
                      mounts: list[tuple[str, str]] | None = None) -> dict:
            """Test the WHOLE draft through the real run-time (an LLM following
            SKILL.md, calling scripts) — the user's real experience. Use after
            exec-debugging the scripts."""
            return self._run_impl(args=args, mounts=mounts)

        @mcp.tool(name="promote")
        def promote(name: str) -> dict:
            """Finalize: validate kebab-case name, move the draft into
            skills/<name>/. Decide the name LAST, once the user is happy."""
            return self._promote_impl(name=name)

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

        @mcp.tool(name="report")
        def report(message: str) -> str:
            """Emit a NON-BLOCKING progress update to the user (does not wait for a
            reply). Use it to narrate what you're doing — build start, writing
            files, before/after each exec/run test, and especially on an
            error or retry. Prefer this over going silent. For a real question use
            ask/confirm/choose instead."""
            return report_h.run(message)

        self._tool_names = ["exec", "run", "promote", "ask", "confirm", "choose", "report"]
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
            name=f"forge-mcp-{self.port}",
        )
        self._thread.start()

        deadline, step, elapsed = 5.0, 0.05, 0.0
        while elapsed < deadline:
            try:
                # `with` closes the socket even when connect() raises, so a
                # slow-to-bind server doesn't leak one FD per failed probe.
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
                    sk.settimeout(0.5)
                    sk.connect(("127.0.0.1", self.port))
                return
            except OSError:
                time.sleep(step)
                elapsed += step
        raise RuntimeError(f"ForgeServer failed to listen on port {self.port}")

    def stop(self) -> None:
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._uvicorn_server = None
        self._thread = None
