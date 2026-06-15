import io, threading, socket
from unittest.mock import MagicMock
from zipsa.core.hitl_mcp import HitlIO
from zipsa.core.forge_server import ForgeServer


def _io():
    return HitlIO(stdin=io.StringIO(""), stdout=io.StringIO(),
                  stdout_lock=threading.Lock(), is_interactive=False)


class TestForgeServer:
    def test_start_stop_and_tools(self):
        s = ForgeServer(_io(), exec_handler=MagicMock(),
                        run_handler=MagicMock(), promote_handler=MagicMock())
        s.start()
        try:
            assert s.port > 0 and s.token
            socket.create_connection(("127.0.0.1", s.port), timeout=2).close()
            tools = set(s.tool_names())
            assert {"exec", "run", "promote", "ask", "confirm", "choose"} == tools
        finally:
            s.stop()

    def test_promote_tool_passes_only_name(self):
        promote = MagicMock(); promote.run.return_value = {"status": "ok"}
        s = ForgeServer(_io(), exec_handler=MagicMock(),
                        run_handler=MagicMock(), promote_handler=promote,
                        staging_path="/x/staging/draft-1")
        # the promote tool injects the staging path the server was built with
        s._promote_impl(name="weather")  # test hook calling the tool body
        promote.run.assert_called_once_with(
            staging_path="/x/staging/draft-1", name="weather")
