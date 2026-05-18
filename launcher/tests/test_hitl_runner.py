"""Tests for HitlServer — port allocation, token, lifecycle."""

import socket
import threading

import pytest

from zipsa.core.hitl_runner import HitlServer
from zipsa.core.hitl_mcp import HitlIO
import io as _io


def _io_pair():
    return HitlIO(
        stdin=_io.StringIO(""),
        stdout=_io.StringIO(),
        stdout_lock=threading.Lock(),
        is_interactive=True,
    )


class TestHitlServerLifecycle:
    def test_start_assigns_port_and_token(self):
        server = HitlServer(_io_pair())
        server.start()
        try:
            assert isinstance(server.port, int) and server.port > 0
            assert isinstance(server.token, str) and len(server.token) >= 32
        finally:
            server.stop()

    def test_port_actually_listening_after_start(self):
        server = HitlServer(_io_pair())
        server.start()
        try:
            # Connect should succeed
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect(("127.0.0.1", server.port))
            s.close()
        finally:
            server.stop()

    def test_stop_releases_port(self):
        server = HitlServer(_io_pair())
        server.start()
        port = server.port
        server.stop()
        # Rebinding should succeed after stop
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.close()

    def test_each_run_gets_different_token(self):
        server1 = HitlServer(_io_pair())
        server1.start()
        token1 = server1.token
        server1.stop()
        server2 = HitlServer(_io_pair())
        server2.start()
        try:
            assert server2.token != token1
        finally:
            server2.stop()


class TestToolsCallable:
    def test_ask_tool_via_http(self):
        """End-to-end: connect to server, call ask via MCP HTTP."""
        import io as _io
        import httpx
        import json

        io_ = HitlIO(
            stdin=_io.StringIO("seoul\n"),
            stdout=_io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        server = HitlServer(io_)
        server.start()
        try:
            # MCP initialize handshake (minimal). FastMCP default path is
            # "/mcp" (no trailing slash); using "/mcp/" yields a 307 redirect.
            url = f"http://127.0.0.1:{server.port}/mcp"
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {server.token}",
            }
            init = {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26",
                           "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
            }
            r = httpx.post(url, json=init, headers=headers, timeout=5.0)
            assert r.status_code == 200
            session_id = r.headers.get("mcp-session-id")
            assert session_id

            # Send initialized notification
            session_headers = {**headers, "mcp-session-id": session_id}
            httpx.post(url, json={
                "jsonrpc": "2.0", "method": "notifications/initialized",
            }, headers=session_headers, timeout=5.0)

            # Call ask
            call = {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "ask", "arguments": {"prompt": "Where?"}},
            }
            r = httpx.post(url, json=call, headers=session_headers, timeout=5.0)
            assert r.status_code == 200
            # Streamable HTTP may return SSE; parse the data line
            body = r.text
            if body.startswith("event:") or "data:" in body:
                for line in body.splitlines():
                    if line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                        break
            else:
                data = r.json()
            content = data["result"]["content"][0]
            assert content["text"] == "seoul"
        finally:
            server.stop()
