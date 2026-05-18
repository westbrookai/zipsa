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


class TestConfirm:
    @pytest.mark.parametrize("text,expected", [
        ("y\n", True),
        ("yes\n", True),
        ("Y\n", True),
        ("n\n", False),
        ("no\n", False),
        ("N\n", False),
    ])
    def test_y_n_yes_no_case_insensitive(self, text, expected):
        io_ = make_io(text)
        handler = ConfirmHandler(io_)
        assert handler.run(message="Proceed?") is expected

    def test_default_true_on_blank(self):
        io_ = make_io("\n")
        handler = ConfirmHandler(io_)
        assert handler.run(message="OK?", default=True) is True

    def test_default_false_on_blank(self):
        io_ = make_io("\n")
        handler = ConfirmHandler(io_)
        assert handler.run(message="OK?", default=False) is False

    def test_no_default_on_blank_reprompts(self):
        # Blank then "y" — should re-prompt once and accept "y"
        io_ = make_io("\ny\n")
        handler = ConfirmHandler(io_)
        assert handler.run(message="OK?") is True

    def test_bad_input_reprompts_then_gives_up(self):
        # 3 bad attempts → ValueError
        io_ = make_io("maybe\nperhaps\nidk\n")
        handler = ConfirmHandler(io_)
        with pytest.raises(ValueError):
            handler.run(message="OK?")

    def test_unattended_raises(self):
        io_ = HitlIO(
            stdin=io.StringIO(""),
            stdout=io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=False,
        )
        with pytest.raises(HitlUnattended):
            ConfirmHandler(io_).run(message="OK?")


class TestChoose:
    OPTIONS = ["alpha", "beta", "gamma"]

    def test_by_index(self):
        io_ = make_io("2\n")
        handler = ChooseHandler(io_)
        assert handler.run(prompt="Pick", options=self.OPTIONS) == "beta"

    def test_by_exact_match(self):
        io_ = make_io("gamma\n")
        handler = ChooseHandler(io_)
        assert handler.run(prompt="Pick", options=self.OPTIONS) == "gamma"

    def test_case_insensitive_match(self):
        io_ = make_io("Beta\n")
        handler = ChooseHandler(io_)
        assert handler.run(prompt="Pick", options=self.OPTIONS) == "beta"

    def test_out_of_range_reprompts(self):
        io_ = make_io("99\n1\n")
        handler = ChooseHandler(io_)
        assert handler.run(prompt="Pick", options=self.OPTIONS) == "alpha"

    def test_options_listed_in_prompt(self):
        io_ = make_io("1\n")
        handler = ChooseHandler(io_)
        handler.run(prompt="Pick", options=self.OPTIONS)
        out = io_.stdout.getvalue()
        assert "1) alpha" in out
        assert "2) beta" in out
        assert "3) gamma" in out

    def test_max_retries(self):
        io_ = make_io("foo\nbar\nbaz\n")
        handler = ChooseHandler(io_)
        with pytest.raises(ValueError):
            handler.run(prompt="Pick", options=self.OPTIONS)

    def test_empty_options_rejected(self):
        io_ = make_io("\n")
        handler = ChooseHandler(io_)
        with pytest.raises(ValueError, match="empty"):
            handler.run(prompt="Pick", options=[])

    def test_unattended_raises(self):
        io_ = HitlIO(
            stdin=io.StringIO(""),
            stdout=io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=False,
        )
        with pytest.raises(HitlUnattended):
            ChooseHandler(io_).run(prompt="Pick", options=["a", "b"])


from zipsa.core.memory_store import MemoryStore
from zipsa.core.hitl_mcp import (
    RecallHandler, RememberHandler, ForgetHandler, ListMemoryHandler,
)


def _store_pair(tmp_path):
    return (
        MemoryStore(tmp_path / "skill.json"),
        MemoryStore(tmp_path / "global.json"),
    )


class TestRecall:
    def test_skill_scope_default(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        skill.set("k", "skill-value")
        global_.set("k", "global-value")
        h = RecallHandler(skill, global_)
        assert h.run(key="k") == "skill-value"

    def test_global_scope_explicit(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        global_.set("lang", "ko")
        h = RecallHandler(skill, global_)
        assert h.run(key="lang", scope="global") == "ko"

    def test_missing_returns_none(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        h = RecallHandler(skill, global_)
        assert h.run(key="absent") is None

    def test_invalid_scope_raises(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        h = RecallHandler(skill, global_)
        with pytest.raises(ValueError, match="scope"):
            h.run(key="k", scope="bogus")


class TestRemember:
    def test_skill_scope_default(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        h = RememberHandler(skill, global_)
        h.run(key="k", value="v")
        assert skill.get("k") == "v"
        assert global_.get("k") is None

    def test_global_scope(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        h = RememberHandler(skill, global_)
        h.run(key="lang", value="ko", scope="global")
        assert global_.get("lang") == "ko"
        assert skill.get("lang") is None

    def test_invalid_scope_raises(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        h = RememberHandler(skill, global_)
        with pytest.raises(ValueError, match="scope"):
            h.run(key="k", value="v", scope="bogus")


class TestForget:
    def test_removes_existing_returns_true(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        skill.set("k", "v")
        h = ForgetHandler(skill, global_)
        assert h.run(key="k") is True
        assert skill.get("k") is None

    def test_missing_returns_false(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        h = ForgetHandler(skill, global_)
        assert h.run(key="never") is False

    def test_global_scope(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        global_.set("k", "v")
        h = ForgetHandler(skill, global_)
        assert h.run(key="k", scope="global") is True
        assert global_.get("k") is None

    def test_invalid_scope_raises(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        h = ForgetHandler(skill, global_)
        with pytest.raises(ValueError, match="scope"):
            h.run(key="k", scope="bogus")


class TestListMemory:
    def test_skill_scope_default(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        skill.set("a", 1)
        skill.set("b", 2)
        global_.set("g", 3)
        h = ListMemoryHandler(skill, global_)
        assert sorted(h.run()) == ["a", "b"]

    def test_global_scope(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        global_.set("x", 1)
        global_.set("y", 2)
        h = ListMemoryHandler(skill, global_)
        assert sorted(h.run(scope="global")) == ["x", "y"]

    def test_invalid_scope_raises(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        h = ListMemoryHandler(skill, global_)
        with pytest.raises(ValueError, match="scope"):
            h.run(scope="bogus")


from zipsa.core.hitl_mcp import AskOnceHandler


class TestAskOnce:
    def test_returns_cached_without_asking(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        skill.set("workspace", "Westbrook HQ")
        # stdin is empty — proves ask never runs
        io_ = HitlIO(
            stdin=io.StringIO(""),
            stdout=io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        ask = AskHandler(io_)
        recall = RecallHandler(skill, global_)
        remember = RememberHandler(skill, global_)
        h = AskOnceHandler(ask, recall, remember)
        assert h.run(key="workspace", prompt="?") == "Westbrook HQ"

    def test_asks_and_stores_when_missing(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        io_ = HitlIO(
            stdin=io.StringIO("Westbrook HQ\n"),
            stdout=io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        ask = AskHandler(io_)
        recall = RecallHandler(skill, global_)
        remember = RememberHandler(skill, global_)
        h = AskOnceHandler(ask, recall, remember)
        result = h.run(key="workspace", prompt="어느 workspace?")
        assert result == "Westbrook HQ"
        # Stored in skill scope
        assert skill.get("workspace") == "Westbrook HQ"

    def test_subsequent_call_returns_cached(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        io_ = HitlIO(
            stdin=io.StringIO("Westbrook HQ\n"),
            stdout=io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        ask = AskHandler(io_)
        recall = RecallHandler(skill, global_)
        remember = RememberHandler(skill, global_)
        h = AskOnceHandler(ask, recall, remember)
        h.run(key="ws", prompt="?")  # first call — asks
        # Second call must return cached — even with empty stdin
        io2 = HitlIO(
            stdin=io.StringIO(""),
            stdout=io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        ask2 = AskHandler(io2)
        h2 = AskOnceHandler(ask2, recall, remember)
        assert h2.run(key="ws", prompt="?") == "Westbrook HQ"

    def test_global_scope(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        io_ = HitlIO(
            stdin=io.StringIO("ko\n"),
            stdout=io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        ask = AskHandler(io_)
        recall = RecallHandler(skill, global_)
        remember = RememberHandler(skill, global_)
        h = AskOnceHandler(ask, recall, remember)
        h.run(key="lang", prompt="?", scope="global")
        assert global_.get("lang") == "ko"
        assert skill.get("lang") is None

    def test_unattended_propagates(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        io_ = HitlIO(
            stdin=io.StringIO(""),
            stdout=io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=False,
        )
        ask = AskHandler(io_)
        recall = RecallHandler(skill, global_)
        remember = RememberHandler(skill, global_)
        h = AskOnceHandler(ask, recall, remember)
        with pytest.raises(HitlUnattended):
            h.run(key="never_cached", prompt="?")

    def test_invalid_scope_raises(self, tmp_path):
        skill, global_ = _store_pair(tmp_path)
        io_ = HitlIO(
            stdin=io.StringIO("x\n"),
            stdout=io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        ask = AskHandler(io_)
        recall = RecallHandler(skill, global_)
        remember = RememberHandler(skill, global_)
        h = AskOnceHandler(ask, recall, remember)
        with pytest.raises(ValueError, match="scope"):
            h.run(key="k", prompt="?", scope="bogus")
