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
