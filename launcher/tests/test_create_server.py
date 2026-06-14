"""Tests for CreateServer — the focused host MCP server for `zipsa create`.

Exposes exactly two tools (exec, promote) backed by injected handlers,
behind the same Bearer-token auth the legacy run path uses. Handlers
are injected so these tests never touch docker or the filesystem move.
"""

from __future__ import annotations

import io as _io
import json
import socket
import threading

import httpx
import pytest

from zipsa.core.create_server import CreateServer
from zipsa.core.hitl_mcp import HitlIO


def _io_(stdin_text: str = ""):
    return HitlIO(
        stdin=_io.StringIO(stdin_text),
        stdout=_io.StringIO(),
        stdout_lock=threading.Lock(),
        is_interactive=True,
    )


class _FakeHandler:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self.reply


def _mcp_call(server, name, arguments):
    """Minimal MCP initialize → tools/call over streamable HTTP."""
    url = f"http://127.0.0.1:{server.port}/mcp"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {server.token}",
    }
    init = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "t", "version": "0"}},
    }
    r = httpx.post(url, json=init, headers=headers, timeout=5.0)
    assert r.status_code == 200, r.text
    sid = r.headers["mcp-session-id"]
    sh = {**headers, "mcp-session-id": sid}
    httpx.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"},
               headers=sh, timeout=5.0)
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}
    r = httpx.post(url, json=call, headers=sh, timeout=5.0)
    assert r.status_code == 200, r.text
    body = r.text
    if "data:" in body:
        for line in body.splitlines():
            if line.startswith("data:"):
                data = json.loads(line[5:].strip())
                break
    else:
        data = r.json()
    return data


class TestLifecycle:
    def test_start_assigns_port_and_token(self):
        s = CreateServer(_io_(), _FakeHandler({}), _FakeHandler({}))
        s.start()
        try:
            assert isinstance(s.port, int) and s.port > 0
            assert isinstance(s.token, str) and len(s.token) >= 32
        finally:
            s.stop()

    def test_listening_then_released(self):
        s = CreateServer(_io_(), _FakeHandler({}), _FakeHandler({}))
        s.start()
        port = s.port
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c.settimeout(2.0)
        c.connect(("127.0.0.1", port))
        c.close()
        s.stop()
        b = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        b.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        b.bind(("127.0.0.1", port))
        b.close()


class TestTools:
    def test_exec_tool_delegates_to_handler(self):
        exec_h = _FakeHandler({"status": "ok", "result": {"hi": 1}})
        s = CreateServer(_io_(), exec_h, _FakeHandler({}))
        s.start()
        try:
            data = _mcp_call(s, "exec",
                             {"staging_path": "/x/staging/a", "args": "q"})
            text = data["result"]["content"][0]["text"]
            assert json.loads(text)["result"] == {"hi": 1}
            assert exec_h.calls == [{"staging_path": "/x/staging/a", "args": "q"}]
        finally:
            s.stop()

    def test_promote_tool_delegates_to_handler(self):
        promote_h = _FakeHandler({"status": "ok", "path": "/repo/skills/foo"})
        s = CreateServer(_io_(), _FakeHandler({}), promote_h)
        s.start()
        try:
            data = _mcp_call(s, "promote",
                             {"staging_path": "/x/staging/a", "name": "foo"})
            text = data["result"]["content"][0]["text"]
            assert json.loads(text)["path"] == "/repo/skills/foo"
            assert promote_h.calls == [{"staging_path": "/x/staging/a", "name": "foo"}]
        finally:
            s.stop()

    def test_ask_tool_routes_to_host_terminal(self):
        """The conversation channel: ask reads the host user's reply via
        HitlIO (claude runs headless, talks back over MCP)."""
        s = CreateServer(_io_("seoul\n"), _FakeHandler({}), _FakeHandler({}))
        s.start()
        try:
            data = _mcp_call(s, "ask", {"prompt": "Which city?"})
            assert data["result"]["content"][0]["text"] == "seoul"
        finally:
            s.stop()


class TestAuth:
    def test_missing_token_rejected(self):
        s = CreateServer(_io_(), _FakeHandler({}), _FakeHandler({}))
        s.start()
        try:
            r = httpx.post(
                f"http://127.0.0.1:{s.port}/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                headers={"Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream"},
                timeout=5.0,
            )
            assert r.status_code == 401
        finally:
            s.stop()
