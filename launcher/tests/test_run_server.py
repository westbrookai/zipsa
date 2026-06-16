import threading, socket
from unittest.mock import MagicMock
from zipsa.core.hitl_mcp import HitlIO
from zipsa.core.run_server import RunServer


def _io():
    import io
    return HitlIO(stdin=io.StringIO(""), stdout=io.StringIO(),
                  stdout_lock=threading.Lock(), is_interactive=False)


class TestRunServer:
    def test_start_assigns_port_token_then_stops(self):
        s = RunServer(_io(), MagicMock())
        s.start()
        try:
            assert s.port > 0 and len(s.token) > 0
            sock = socket.create_connection(("127.0.0.1", s.port), timeout=2)
            sock.close()
        finally:
            s.stop()

    def test_no_promote_tool_registered(self):
        s = RunServer(_io(), MagicMock())
        s.start()
        try:
            tools = s.tool_names()
            assert "exec" in tools
            assert {"ask", "confirm", "choose", "report"} <= set(tools)
            assert "promote" not in tools
        finally:
            s.stop()
