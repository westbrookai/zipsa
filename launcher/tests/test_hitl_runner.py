"""Tests for HitlServer — port allocation, token, lifecycle."""

import socket
import threading

import pytest

from zipsa.core.hitl_runner import HitlServer, _bind_free_socket
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
        from zipsa.core.caller_context import CallerInfo

        io_ = HitlIO(
            stdin=_io.StringIO("seoul\n"),
            stdout=_io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        server = HitlServer(io_, primary_caller=CallerInfo("test", "0"))
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
        from zipsa.core.caller_context import CallerInfo

        io_ = HitlIO(
            stdin=_io.StringIO(""),
            stdout=_io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        skill = MemoryStore(tmp_path / "skill.json")
        global_ = MemoryStore(tmp_path / "global.json")
        server = HitlServer(io_, skill_store=skill, global_store=global_,
                            primary_caller=CallerInfo("test", "0"))
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
        from zipsa.core.caller_context import CallerInfo

        io_ = HitlIO(
            stdin=_io.StringIO("Westbrook HQ\n"),  # consumed on first ask
            stdout=_io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        skill = MemoryStore(tmp_path / "skill.json")
        global_ = MemoryStore(tmp_path / "global.json")
        server = HitlServer(io_, skill_store=skill, global_store=global_,
                            primary_caller=CallerInfo("test", "0"))
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

        from zipsa.core.caller_context import CallerInfo
        io_ = HitlIO(
            stdin=_io.StringIO(""),
            stdout=_io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        server = HitlServer(io_, primary_caller=CallerInfo("test", "0"))
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
            outer = json.loads(text)
            assert outer["name"] == "summary.txt"
            assert outer["content"] == "hello artifact"
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

        from zipsa.core.caller_context import CallerInfo
        io_ = HitlIO(
            stdin=_io.StringIO(""),
            stdout=_io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        server = HitlServer(io_, primary_caller=CallerInfo("test", "0"))
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
            outer = json.loads(text)
            assert outer["name"] == "result.json"
            assert outer["content"] == payload
        finally:
            server.stop()

    def test_get_artifact_not_found_returns_error(self, tmp_path, monkeypatch):
        """get_artifact raises an MCP tool error when the artifact is missing."""
        import io as _io
        import httpx

        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))

        from zipsa.core.caller_context import CallerInfo
        io_ = HitlIO(
            stdin=_io.StringIO(""),
            stdout=_io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        server = HitlServer(io_, primary_caller=CallerInfo("test", "0"))
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


class TestPerCallerMemoryRouting:
    """When two registered callers (parent skill, child skill) invoke
    memory tools on the same HitlServer, each must read/write its own
    skill's memory file — never the other's."""

    def test_recall_returns_caller_specific_store(self, tmp_path, monkeypatch):
        """Alice's remember(key='color', value='red') must NOT be visible
        to Bob's recall(key='color')."""
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path))
        import io as _io2
        import httpx
        import json as _json
        from zipsa.core.hitl_runner import HitlServer
        from zipsa.core.hitl_mcp import HitlIO
        from zipsa.core.caller_context import CallerInfo

        io_ = HitlIO(
            stdin=_io2.StringIO(""),
            stdout=_io2.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=False,
        )
        server = HitlServer(io_, primary_caller=CallerInfo("alice", "1.0.0"))
        server.start()
        try:
            # Register Bob as a second caller
            bob_token = "tok-bob-xyz"
            server.register_caller(bob_token, CallerInfo("bob", "2.0.0"))

            url = f"http://127.0.0.1:{server.port}/mcp"

            def mcp_session(token: str):
                h = {
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "Authorization": f"Bearer {token}",
                }
                init = {
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"},
                    },
                }
                r = httpx.post(url, json=init, headers=h, timeout=5.0)
                sid = r.headers["mcp-session-id"]
                sh = {**h, "mcp-session-id": sid}
                httpx.post(
                    url,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    headers=sh,
                    timeout=5.0,
                )
                return sh

            def call_tool(sh, name, args):
                payload = {
                    "jsonrpc": "2.0", "id": 99, "method": "tools/call",
                    "params": {"name": name, "arguments": args},
                }
                r = httpx.post(url, json=payload, headers=sh, timeout=5.0)
                body = r.text
                if "data:" in body:
                    for line in body.splitlines():
                        if line.startswith("data:"):
                            return _json.loads(line[5:].strip())
                return r.json()

            sh_alice = mcp_session(server.token)
            sh_bob = mcp_session(bob_token)

            # Alice: remember
            call_tool(sh_alice, "remember", {"key": "color", "value": "red"})
            # Alice: recall sees own value
            r1 = call_tool(sh_alice, "recall", {"key": "color"})
            text_a = r1["result"]["content"][0]["text"]
            assert text_a == "red"

            # Bob: recall sees nothing (his memory is empty)
            r2 = call_tool(sh_bob, "recall", {"key": "color"})
            # The recall handler returns None for missing keys.
            # MCP serializes None as either empty content list, null, or empty string.
            content_b = r2["result"]["content"]
            if content_b:
                text_b = content_b[0]["text"]
                assert text_b in ("", "null", None)
            # else: empty content list is also acceptable (MCP omits null values)

            # Check the memory files themselves are separate
            from zipsa import paths as zp
            alice_mem = zp.resolve_skill_memory_path("alice")
            bob_mem = zp.resolve_skill_memory_path("bob")
            assert alice_mem.exists()
            assert "red" in alice_mem.read_text()
            # bob's memory file might not exist (no remember was called) or be empty
            if bob_mem.exists():
                assert "red" not in bob_mem.read_text()
        finally:
            server.stop()


class TestAskOnceDefault:
    """ask_once `default` resolves empty input and unattended runs."""

    def _make_server(self, tmp_path, stdin_text, interactive):
        import io as _io
        from zipsa.core.memory_store import MemoryStore
        from zipsa.core.caller_context import CallerInfo
        io_ = HitlIO(
            stdin=_io.StringIO(stdin_text),
            stdout=_io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=interactive,
        )
        skill = MemoryStore(tmp_path / "skill.json")
        global_ = MemoryStore(tmp_path / "global.json")
        server = HitlServer(io_, skill_store=skill, global_store=global_,
                            primary_caller=CallerInfo("test", "0"))
        return server, skill

    def _session(self, server):
        import httpx
        url = f"http://127.0.0.1:{server.port}/mcp"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {server.token}",
        }
        init = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}},
        }
        r = httpx.post(url, json=init, headers=headers, timeout=5.0)
        assert r.status_code == 200
        sid = r.headers["mcp-session-id"]
        sh = {**headers, "mcp-session-id": sid}
        httpx.post(url, json={"jsonrpc": "2.0",
                              "method": "notifications/initialized"},
                   headers=sh, timeout=5.0)
        return url, sh

    def _call(self, url, sh, arguments, req_id=2):
        import httpx
        call = {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                "params": {"name": "ask_once", "arguments": arguments}}
        r = httpx.post(url, json=call, headers=sh, timeout=5.0)
        assert r.status_code == 200
        return r

    def _text(self, r):
        import json
        body = r.text
        if "data:" in body:
            for line in body.splitlines():
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    break
        else:
            data = r.json()
        return data["result"]["content"][0]["text"]

    def test_empty_input_uses_default(self, tmp_path):
        server, skill = self._make_server(tmp_path, "\n", interactive=True)
        server.start()
        try:
            url, sh = self._session(server)
            r = self._call(url, sh, {"key": "db", "prompt": "DB?",
                                     "default": "zipsa-daily-log"})
            assert "zipsa-daily-log" in self._text(r)
            assert skill.get("db") == "zipsa-daily-log"
        finally:
            server.stop()

    def test_empty_input_no_default_stores_empty(self, tmp_path):
        server, skill = self._make_server(tmp_path, "\n", interactive=True)
        server.start()
        try:
            url, sh = self._session(server)
            self._call(url, sh, {"key": "db", "prompt": "DB?"})
            assert skill.get("db") == ""   # documents current behavior
        finally:
            server.stop()

    def test_nonempty_input_ignores_default(self, tmp_path):
        server, skill = self._make_server(tmp_path, "my-db\n", interactive=True)
        server.start()
        try:
            url, sh = self._session(server)
            r = self._call(url, sh, {"key": "db", "prompt": "DB?",
                                     "default": "zipsa-daily-log"})
            assert "my-db" in self._text(r)
            assert skill.get("db") == "my-db"
        finally:
            server.stop()

    def test_cache_hit_ignores_default(self, tmp_path):
        server, skill = self._make_server(tmp_path, "", interactive=True)
        skill.set("db", "cached-db")
        server.start()
        try:
            url, sh = self._session(server)
            r = self._call(url, sh, {"key": "db", "prompt": "DB?",
                                     "default": "zipsa-daily-log"})
            assert "cached-db" in self._text(r)
        finally:
            server.stop()

    def test_noninteractive_uses_default(self, tmp_path):
        server, skill = self._make_server(tmp_path, "", interactive=False)
        server.start()
        try:
            url, sh = self._session(server)
            r = self._call(url, sh, {"key": "db", "prompt": "DB?",
                                     "default": "zipsa-daily-log"})
            assert "zipsa-daily-log" in self._text(r)
            assert skill.get("db") == "zipsa-daily-log"
        finally:
            server.stop()

    def test_noninteractive_no_default_raises_unattended(self, tmp_path):
        server, skill = self._make_server(tmp_path, "", interactive=False)
        server.start()
        try:
            url, sh = self._session(server)
            r = self._call(url, sh, {"key": "db", "prompt": "DB?"})
            assert "HITL_UNATTENDED" in r.text
        finally:
            server.stop()


class TestBindFreeSocket:
    """Unit tests for _bind_free_socket helper."""

    def test_returns_open_listening_socket(self):
        """Helper returns an open socket bound to a nonzero port."""
        s = _bind_free_socket()
        try:
            port = s.getsockname()[1]
            assert port > 0
        finally:
            s.close()

    def test_getsockname_port_matches(self):
        """The returned socket's port is consistent across two getsockname calls."""
        s = _bind_free_socket()
        try:
            port1 = s.getsockname()[1]
            port2 = s.getsockname()[1]
            assert port1 == port2
            assert port1 > 0
        finally:
            s.close()

    def test_two_calls_return_different_ports(self):
        """Two calls must return sockets on different ports (no TOCTOU window)."""
        s1 = _bind_free_socket()
        s2 = _bind_free_socket()
        try:
            port1 = s1.getsockname()[1]
            port2 = s2.getsockname()[1]
            assert port1 != port2
        finally:
            s1.close()
            s2.close()

    def test_socket_is_listening(self):
        """The returned socket must accept connections (i.e. is listening)."""
        s = _bind_free_socket()
        try:
            port = s.getsockname()[1]
            # If listen() was called, connect should succeed
            with socket.create_connection(("127.0.0.1", port), timeout=2.0):
                pass
        finally:
            s.close()


class TestConcurrentStartNoConflict:
    """Regression test for the TOCTOU fix: 10 servers started concurrently
    must all get distinct ports and all become reachable."""

    def _make_io(self):
        return HitlIO(
            stdin=_io.StringIO(""),
            stdout=_io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=False,
        )

    def test_concurrent_hitl_servers_all_distinct_ports(self):
        """Start 10 RunServers from threads simultaneously; every server
        must bind a unique port and be reachable."""
        from zipsa.core.run_server import RunServer
        from unittest.mock import MagicMock

        n = 10
        servers = [RunServer(self._make_io(), MagicMock()) for _ in range(n)]
        errors = []

        def start_server(srv):
            try:
                srv.start()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=start_server, args=(s,)) for s in servers]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        try:
            assert not errors, f"Server start errors: {errors}"
            ports = [s.port for s in servers]
            # All ports must be positive
            assert all(p > 0 for p in ports), f"Some ports are zero: {ports}"
            # All ports must be distinct
            assert len(set(ports)) == n, f"Port collisions detected: {ports}"
            # All servers must be reachable
            for srv in servers:
                with socket.create_connection(("127.0.0.1", srv.port), timeout=2.0):
                    pass
        finally:
            for srv in servers:
                try:
                    srv.stop()
                except Exception:
                    pass
