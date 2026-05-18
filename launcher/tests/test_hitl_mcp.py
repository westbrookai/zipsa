"""Tests for HITL tool handlers (ask/confirm/choose) — IO-injected so no
sockets or real stdin/stdout involved."""

import io
import threading
import pytest

from zipsa.core.hitl_mcp import (
    HitlIO, AskHandler, ConfirmHandler, ChooseHandler, HitlUnattended,
)


def make_io(stdin_text: str):
    """Build a HitlIO with pre-seeded stdin and a buffer for stdout."""
    return HitlIO(
        stdin=io.StringIO(stdin_text),
        stdout=io.StringIO(),
        stdout_lock=threading.Lock(),
        is_interactive=True,
    )


class TestAsk:
    def test_returns_user_line(self):
        io_ = make_io("seoul\n")
        handler = AskHandler(io_)
        result = handler.run(prompt="Where?")
        assert result == "seoul"

    def test_strips_trailing_newline_and_whitespace(self):
        io_ = make_io("  seoul  \n")
        handler = AskHandler(io_)
        assert handler.run(prompt="Where?") == "seoul"

    def test_prompt_text_appears_in_stdout(self):
        io_ = make_io("seoul\n")
        handler = AskHandler(io_)
        handler.run(prompt="Where?")
        out = io_.stdout.getvalue()
        assert "Where?" in out
        assert "User input needed" in out
        assert "Resuming" in out

    def test_unattended_raises_hitl_unattended(self):
        io_ = HitlIO(
            stdin=io.StringIO(""),
            stdout=io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=False,
        )
        handler = AskHandler(io_)
        with pytest.raises(HitlUnattended):
            handler.run(prompt="Where?")
