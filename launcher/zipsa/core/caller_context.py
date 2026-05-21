"""Caller-context routing for the HitlServer.

A child skill invoked via run_skill talks to the parent's HitlServer
using a child-specific Bearer token. The server's token map
({token: CallerInfo}) is set by RunSkillHandler when it spawns the
child. This middleware reads the token from each incoming request,
looks up the caller, and stashes it in a contextvar so tool handlers
can route skill-scoped operations (ask_once, recall, remember) to the
correct skill's memory file without parent and child stepping on each
other.

Combines auth + routing: unknown/missing tokens return 401.

Implemented as PURE ASGI middleware (not BaseHTTPMiddleware): MCP's
StreamableHTTP transport uses long-lived SSE streams, and starlette's
BaseHTTPMiddleware buffers request bodies in a way that triggers
ClientDisconnect on streaming endpoints (observed: child container's
initialize POST fails with ClientDisconnect when the middleware uses
BaseHTTPMiddleware). Pure ASGI lets us inspect headers without
touching the body stream.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CallerInfo:
    skill: str
    version: str
    # depth + trace are populated by RunSkillHandler so it can enforce
    # cycle/depth caps in-process: every run_skill call goes through the
    # same parent HitlServer, so env-var-based propagation does NOT
    # accumulate (P0's os.environ never gets new DEPTH set). Default 0/()
    # for top-level callers (no chain yet).
    depth: int = 0
    trace: tuple[str, ...] = ()


current_caller: ContextVar[Optional[CallerInfo]] = ContextVar(
    "current_caller", default=None,
)


class CallerContextMiddleware:
    """Pure ASGI middleware: auth + caller-routing.

    Extracts Bearer token from request headers, resolves to CallerInfo,
    sets the contextvar for the duration of the request/response cycle.
    Unknown or missing tokens → 401 (returned as a complete HTTP
    response without forwarding to the inner app).
    """

    def __init__(self, app, token_map: "dict[str, CallerInfo]") -> None:
        self._app = app
        self._token_map = token_map

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # ASGI headers: list[tuple[bytes, bytes]], lowercased names.
        auth = ""
        for k, v in scope.get("headers", []):
            if k == b"authorization":
                auth = v.decode("latin-1")
                break

        caller = None
        if auth.startswith("Bearer "):
            token = auth[len("Bearer "):].strip()
            if token:
                caller = self._token_map.get(token)

        if caller is None:
            await _send_401(send)
            return

        reset_token = current_caller.set(caller)
        try:
            await self._app(scope, receive, send)
        finally:
            current_caller.reset(reset_token)


async def _send_401(send) -> None:
    body = json.dumps({"error": "unauthorized"}).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("latin-1")),
        ],
    })
    await send({"type": "http.response.body", "body": body})
