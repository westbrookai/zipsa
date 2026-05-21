"""Tests for caller-context routing primitive."""

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from zipsa.core.caller_context import (
    CallerInfo,
    CallerContextMiddleware,
    current_caller,
)


def _build_app(token_map: dict[str, CallerInfo]) -> Starlette:
    async def endpoint(request):
        caller = current_caller.get()
        if caller is None:
            return JSONResponse({"caller": None})
        return JSONResponse({"skill": caller.skill, "version": caller.version})

    app = Starlette(routes=[Route("/", endpoint)])
    app.add_middleware(CallerContextMiddleware, token_map=token_map)
    return app


class TestCallerContext:
    def test_middleware_resolves_token_to_caller(self):
        token_map = {"tok-alice": CallerInfo(skill="alice", version="1.0.0")}
        client = TestClient(_build_app(token_map))
        r = client.get("/", headers={"Authorization": "Bearer tok-alice"})
        assert r.status_code == 200
        assert r.json() == {"skill": "alice", "version": "1.0.0"}

    def test_unknown_token_returns_401(self):
        client = TestClient(_build_app({}))
        r = client.get("/", headers={"Authorization": "Bearer mystery"})
        assert r.status_code == 401
        assert r.json() == {"error": "unauthorized"}

    def test_missing_auth_header_returns_401(self):
        client = TestClient(_build_app({}))
        r = client.get("/")
        assert r.status_code == 401

    def test_malformed_auth_header_returns_401(self):
        """Non-Bearer scheme or no token after 'Bearer ' → 401."""
        client = TestClient(_build_app({}))
        r1 = client.get("/", headers={"Authorization": "Basic abc"})
        assert r1.status_code == 401
        r2 = client.get("/", headers={"Authorization": "Bearer "})
        assert r2.status_code == 401

    def test_contextvar_isolates_concurrent_requests(self):
        """Two sequential requests with different tokens each see their
        own caller. (TestClient is synchronous, so contextvar leak from
        request 1 would show up in request 2's response.)"""
        token_map = {
            "tok-a": CallerInfo(skill="alice", version="1.0.0"),
            "tok-b": CallerInfo(skill="bob", version="2.0.0"),
        }
        client = TestClient(_build_app(token_map))
        r_a = client.get("/", headers={"Authorization": "Bearer tok-a"})
        r_b = client.get("/", headers={"Authorization": "Bearer tok-b"})
        r_a2 = client.get("/", headers={"Authorization": "Bearer tok-a"})
        assert r_a.json()["skill"] == "alice"
        assert r_b.json()["skill"] == "bob"
        assert r_a2.json()["skill"] == "alice"

    def test_contextvar_default_is_none(self):
        """Outside of a request, current_caller is None."""
        assert current_caller.get() is None
