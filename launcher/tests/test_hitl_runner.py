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


class TestAuth:
    def _make_running_server(self):
        import io as _io
        io_ = HitlIO(
            stdin=_io.StringIO(""),
            stdout=_io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        server = HitlServer(io_)
        server.start()
        return server

    def test_missing_token_rejected(self):
        import httpx
        server = self._make_running_server()
        try:
            r = httpx.post(
                f"http://127.0.0.1:{server.port}/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                headers={"Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream"},
                timeout=5.0,
            )
            assert r.status_code == 401
        finally:
            server.stop()

    def test_wrong_token_rejected(self):
        import httpx
        server = self._make_running_server()
        try:
            r = httpx.post(
                f"http://127.0.0.1:{server.port}/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                headers={"Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream",
                         "Authorization": "Bearer wrong"},
                timeout=5.0,
            )
            assert r.status_code == 401
        finally:
            server.stop()


class TestMemoryToolsWired:
    """End-to-end: HitlServer exposes memory tools over HTTP MCP."""

    def test_remember_then_recall_via_http(self, tmp_path):
        import io as _io
        import httpx
        import json
        from zipsa.core.memory_store import MemoryStore

        io_ = HitlIO(
            stdin=_io.StringIO(""),
            stdout=_io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        skill = MemoryStore(tmp_path / "skill.json")
        global_ = MemoryStore(tmp_path / "global.json")
        server = HitlServer(io_, skill_store=skill, global_store=global_)
        server.start()
        try:
            url = f"http://127.0.0.1:{server.port}/mcp"
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {server.token}",
            }
            # initialize
            init = {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26",
                           "capabilities": {},
                           "clientInfo": {"name": "test", "version": "0"}},
            }
            r = httpx.post(url, json=init, headers=headers, timeout=5.0)
            assert r.status_code == 200
            session_id = r.headers["mcp-session-id"]
            session_headers = {**headers, "mcp-session-id": session_id}

            httpx.post(url, json={
                "jsonrpc": "2.0", "method": "notifications/initialized",
            }, headers=session_headers, timeout=5.0)

            # remember
            call = {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "remember",
                           "arguments": {"key": "workspace", "value": "WBrk HQ"}},
            }
            r = httpx.post(url, json=call, headers=session_headers, timeout=5.0)
            assert r.status_code == 200
            # recall
            call = {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "recall", "arguments": {"key": "workspace"}},
            }
            r = httpx.post(url, json=call, headers=session_headers, timeout=5.0)
            assert r.status_code == 200
            body = r.text
            if "data:" in body:
                for line in body.splitlines():
                    if line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                        break
            else:
                data = r.json()
            text = data["result"]["content"][0]["text"]
            # MCP serializes returned strings as JSON in text content
            assert "WBrk HQ" in text
        finally:
            server.stop()


class TestAskOnceWired:
    """End-to-end: ask_once caches first answer and returns it on second call."""

    def test_ask_once_full_cycle_via_http(self, tmp_path):
        import io as _io
        import httpx
        import json
        from zipsa.core.memory_store import MemoryStore

        io_ = HitlIO(
            stdin=_io.StringIO("Westbrook HQ\n"),  # consumed on first ask
            stdout=_io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        skill = MemoryStore(tmp_path / "skill.json")
        global_ = MemoryStore(tmp_path / "global.json")
        server = HitlServer(io_, skill_store=skill, global_store=global_)
        server.start()
        try:
            url = f"http://127.0.0.1:{server.port}/mcp"
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {server.token}",
            }
            init = {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-03-26",
                           "capabilities": {},
                           "clientInfo": {"name": "test", "version": "0"}},
            }
            r = httpx.post(url, json=init, headers=headers, timeout=5.0)
            assert r.status_code == 200
            session_id = r.headers["mcp-session-id"]
            session_headers = {**headers, "mcp-session-id": session_id}
            httpx.post(url, json={
                "jsonrpc": "2.0", "method": "notifications/initialized",
            }, headers=session_headers, timeout=5.0)

            def call_ask_once(req_id):
                call = {
                    "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                    "params": {"name": "ask_once",
                               "arguments": {"key": "workspace",
                                             "prompt": "Where?"}},
                }
                r = httpx.post(url, json=call, headers=session_headers, timeout=5.0)
                assert r.status_code == 200
                body = r.text
                if "data:" in body:
                    for line in body.splitlines():
                        if line.startswith("data:"):
                            return json.loads(line[5:].strip())
                return r.json()

            # First call: consumes stdin, stores, returns "Westbrook HQ"
            data1 = call_ask_once(req_id=2)
            text1 = data1["result"]["content"][0]["text"]
            assert "Westbrook HQ" in text1

            # Second call: no more stdin (would block ask if it ran), but
            # ask_once must recall and return cached value
            data2 = call_ask_once(req_id=3)
            text2 = data2["result"]["content"][0]["text"]
            assert "Westbrook HQ" in text2
        finally:
            server.stop()


class TestGetArtifactMCP:
    """End-to-end: get_artifact MCP tool reads artifacts from disk."""

    def _mcp_session(self, server):
        """Return (url, session_headers) after completing MCP initialize handshake."""
        import httpx
        url = f"http://127.0.0.1:{server.port}/mcp"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {server.token}",
        }
        init = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
        r = httpx.post(url, json=init, headers=headers, timeout=5.0)
        assert r.status_code == 200
        session_id = r.headers["mcp-session-id"]
        session_headers = {**headers, "mcp-session-id": session_id}
        httpx.post(url, json={
            "jsonrpc": "2.0", "method": "notifications/initialized",
        }, headers=session_headers, timeout=5.0)
        return url, session_headers

    def _parse_mcp_response(self, r):
        import json
        body = r.text
        if "data:" in body:
            for line in body.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
        return r.json()

    def test_get_artifact_returns_text_content(self, tmp_path, monkeypatch):
        """get_artifact reads a text artifact from disk and returns it via MCP."""
        import io as _io
        import httpx
        import json

        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))

        # Create a fake artifact on disk
        artifacts_dir = (
            tmp_path / "my-skill@0.1.0" / "runs" / "2026-05-21_120000_000" / "artifacts"
        )
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "summary.txt").write_text("hello artifact")

        io_ = HitlIO(
            stdin=_io.StringIO(""),
            stdout=_io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        server = HitlServer(io_)
        server.start()
        try:
            url, session_headers = self._mcp_session(server)
            call = {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {
                    "name": "get_artifact",
                    "arguments": {
                        "skill": "my-skill",
                        "version": "0.1.0",
                        "run_id": "2026-05-21_120000_000",
                        "name": "summary.txt",
                    },
                },
            }
            r = httpx.post(url, json=call, headers=session_headers, timeout=5.0)
            assert r.status_code == 200
            data = self._parse_mcp_response(r)
            # MCP text/event-stream wraps the return dict in content[0].text as JSON
            text = data["result"]["content"][0]["text"]
            parsed = json.loads(text) if text.startswith("{") else text
            if isinstance(parsed, dict):
                assert parsed["name"] == "summary.txt"
                assert parsed["content"] == "hello artifact"
            else:
                assert "hello artifact" in text
        finally:
            server.stop()

    def test_get_artifact_returns_json_content(self, tmp_path, monkeypatch):
        """get_artifact parses .json artifacts and returns the object via MCP."""
        import io as _io
        import httpx
        import json

        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))

        artifacts_dir = (
            tmp_path / "report-skill@0.2.0" / "runs" / "2026-05-21_090000_000" / "artifacts"
        )
        artifacts_dir.mkdir(parents=True)
        payload = {"score": 42, "items": ["a", "b"]}
        (artifacts_dir / "result.json").write_text(json.dumps(payload))

        io_ = HitlIO(
            stdin=_io.StringIO(""),
            stdout=_io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        server = HitlServer(io_)
        server.start()
        try:
            url, session_headers = self._mcp_session(server)
            call = {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {
                    "name": "get_artifact",
                    "arguments": {
                        "skill": "report-skill",
                        "version": "0.2.0",
                        "run_id": "2026-05-21_090000_000",
                        "name": "result.json",
                    },
                },
            }
            r = httpx.post(url, json=call, headers=session_headers, timeout=5.0)
            assert r.status_code == 200
            data = self._parse_mcp_response(r)
            text = data["result"]["content"][0]["text"]
            # The MCP layer serialises the returned dict as JSON text
            outer = json.loads(text) if isinstance(text, str) else text
            # outer may be {"name": ..., "size": ..., "content": ...}
            if isinstance(outer, dict) and "content" in outer:
                assert outer["content"] == payload
            else:
                # Content embedded in text directly
                assert "42" in text
        finally:
            server.stop()

    def test_get_artifact_not_found_returns_error(self, tmp_path, monkeypatch):
        """get_artifact raises an MCP tool error when the artifact is missing."""
        import io as _io
        import httpx

        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))

        io_ = HitlIO(
            stdin=_io.StringIO(""),
            stdout=_io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        server = HitlServer(io_)
        server.start()
        try:
            url, session_headers = self._mcp_session(server)
            call = {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {
                    "name": "get_artifact",
                    "arguments": {
                        "skill": "no-skill",
                        "version": "9.9.9",
                        "run_id": "2026-05-21_000000_000",
                        "name": "missing.txt",
                    },
                },
            }
            r = httpx.post(url, json=call, headers=session_headers, timeout=5.0)
            assert r.status_code == 200
            data = self._parse_mcp_response(r)
            # MCP tool errors surface as isError=true in the result
            result = data.get("result", data)
            assert result.get("isError") is True
            # Error text should mention ARTIFACT_NOT_FOUND
            error_text = str(result.get("content", ""))
            assert "ARTIFACT_NOT_FOUND" in error_text
        finally:
            server.stop()
