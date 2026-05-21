"""Caller-context routing for the HitlServer.

A child skill invoked via run_skill talks to the parent's HitlServer
using a child-specific Bearer token. The server's token map
({token: CallerInfo}) is set by RunSkillHandler when it spawns the
child. This middleware reads the token from each incoming request,
looks up the caller, and stashes it in a contextvar so tool handlers
can route skill-scoped operations (ask_once, recall, remember) to the
correct skill's memory file without parent and child stepping on each
other.

Combines auth + routing: unknown/missing tokens return 401. Replaces
the separate _BearerAuthMiddleware that HitlServer currently uses
(swap happens in T3).
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


@dataclass(frozen=True)
class CallerInfo:
    skill: str
    version: str


current_caller: ContextVar[Optional[CallerInfo]] = ContextVar(
    "current_caller", default=None,
)


class CallerContextMiddleware(BaseHTTPMiddleware):
    """Auth + caller-routing middleware.

    Extracts Bearer token, resolves to CallerInfo, sets the contextvar
    for the request's duration. Unknown or missing tokens → 401.
    """

    def __init__(self, app, token_map: dict[str, CallerInfo]) -> None:
        super().__init__(app)
        self._token_map = token_map

    async def dispatch(self, request, call_next):
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        token = auth[len("Bearer "):].strip()
        if not token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        caller = self._token_map.get(token)
        if caller is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        reset_token = current_caller.set(caller)
        try:
            return await call_next(request)
        finally:
            current_caller.reset(reset_token)
