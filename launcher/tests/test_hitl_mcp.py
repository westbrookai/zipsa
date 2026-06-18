"""Tests for HITL tool handlers (ask/confirm/choose/report) — IO-injected so no
sockets or real stdin/stdout involved."""

import io
import threading
import pytest

from zipsa.core.hitl_mcp import (
    HitlIO, AskHandler, ConfirmHandler, ChooseHandler, HitlUnattended,
    ReportHandler, REPORT_OPEN,
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
    # D2-A: confirm returns str ("yes"/"no"/raw freeform), never bool, never raises.
    @pytest.mark.parametrize("text,expected", [
        ("y\n", "yes"),
        ("yes\n", "yes"),
        ("yeah\n", "yes"),
        ("yep\n", "yes"),
        ("ok\n", "yes"),
        ("sure\n", "yes"),
        ("true\n", "yes"),
        ("Y\n", "yes"),
        ("YES\n", "yes"),
        ("n\n", "no"),
        ("no\n", "no"),
        ("nope\n", "no"),
        ("nah\n", "no"),
        ("false\n", "no"),
        ("N\n", "no"),
        ("NO\n", "no"),
    ])
    def test_synonyms_map_to_yes_no(self, text, expected):
        io_ = make_io(text)
        handler = ConfirmHandler(io_)
        assert handler.run(message="Proceed?") == expected

    def test_default_true_on_blank(self):
        io_ = make_io("\n")
        handler = ConfirmHandler(io_)
        assert handler.run(message="OK?", default=True) == "yes"

    def test_default_false_on_blank(self):
        io_ = make_io("\n")
        handler = ConfirmHandler(io_)
        assert handler.run(message="OK?", default=False) == "no"

    def test_no_default_on_blank_reprompts(self):
        # Blank then "y" — should re-prompt once and accept "y"
        io_ = make_io("\ny\n")
        handler = ConfirmHandler(io_)
        assert handler.run(message="OK?") == "yes"

    def test_no_default_two_blanks_returns_empty_never_raises(self):
        # Empty, then still empty: re-prompt once, then return "" (never raise).
        io_ = make_io("\n\n")
        handler = ConfirmHandler(io_)
        assert handler.run(message="OK?") == ""

    def test_freeform_returned_verbatim(self):
        # The smoking gun: a freeform correction must reach the agent verbatim.
        io_ = make_io("actually json not markdown\n")
        handler = ConfirmHandler(io_)
        assert handler.run(message="Markdown OK?") == "actually json not markdown"

    def test_non_english_freeform_returned_verbatim(self):
        io_ = make_io("그래\n")
        handler = ConfirmHandler(io_)
        assert handler.run(message="진행할까요?") == "그래"

    def test_never_raises_on_unrecognized(self):
        io_ = make_io("maybe\n")
        handler = ConfirmHandler(io_)
        # Must NOT raise ValueError; returns the raw text.
        assert handler.run(message="OK?") == "maybe"

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

    def test_out_of_range_returns_verbatim(self):
        # An out-of-range number is not a valid option → return it verbatim.
        io_ = make_io("99\n")
        handler = ChooseHandler(io_)
        assert handler.run(prompt="Pick", options=self.OPTIONS) == "99"

    def test_options_listed_in_prompt(self):
        io_ = make_io("1\n")
        handler = ChooseHandler(io_)
        handler.run(prompt="Pick", options=self.OPTIONS)
        out = io_.stdout.getvalue()
        assert "1) alpha" in out
        assert "2) beta" in out
        assert "3) gamma" in out

    def test_freeform_returned_verbatim(self):
        # D2-A: non-option freeform must be returned, not looped or raised.
        io_ = make_io("report but output is JSON, not markdown\n")
        handler = ChooseHandler(io_)
        result = handler.run(prompt="Pick", options=self.OPTIONS)
        assert result == "report but output is JSON, not markdown"

    def test_empty_then_option_reprompts(self):
        # Empty line re-prompts once, then accepts the real answer.
        io_ = make_io("\n1\n")
        handler = ChooseHandler(io_)
        assert handler.run(prompt="Pick", options=self.OPTIONS) == "alpha"

    def test_never_raises_on_freeform(self):
        io_ = make_io("none of these\n")
        handler = ChooseHandler(io_)
        # Must NOT raise.
        assert handler.run(prompt="Pick", options=self.OPTIONS) == "none of these"

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


class TestReadAnswerAndDrain:
    """HitlIO.read_answer (D1 gather) + drain — the centralized input path."""

    def test_read_answer_stringio_returns_single_line(self):
        # StringIO has no real fileno() → fallback to single readline().
        io_ = make_io("line1\nline2\nline3\n")
        assert io_.read_answer() == "line1"

    def test_read_answer_pipe_gathers_multiline_burst(self):
        import os
        r, w = os.pipe()
        os.write(w, b"l1\nl2\nl3\n")
        os.close(w)
        rf = os.fdopen(r, "r")
        io_ = HitlIO(
            stdin=rf,
            stdout=io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        try:
            assert io_.read_answer() == "l1\nl2\nl3"
        finally:
            rf.close()

    def test_drain_is_noop_on_stringio(self):
        io_ = make_io("leftover1\nleftover2\n")
        # Not selectable → no-op, must not raise and must not consume.
        io_.drain()
        assert io_.stdin.readline().strip() == "leftover1"

    def test_drain_discards_pending_pipe_lines(self):
        import os
        r, w = os.pipe()
        os.write(w, b"stale1\nstale2\n")
        rf = os.fdopen(r, "r")
        io_ = HitlIO(
            stdin=rf,
            stdout=io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        try:
            io_.drain()
            # After drain, write a fresh line and confirm it is what we read.
            os.write(w, b"fresh\n")
            os.close(w)
            assert io_.read_answer() == "fresh"
        finally:
            rf.close()

    def test_blocking_flag_restored_after_gather(self):
        # The gather temporarily flips stdin to non-blocking; the finally
        # in _gather_pending must restore it. A leaked non-blocking stdin is
        # the worst latent failure mode — guard it explicitly against future
        # refactors that might drop the restore.
        import os
        r, w = os.pipe()
        os.write(w, b"l1\nl2\n")
        os.close(w)
        rf = os.fdopen(r, "r")
        io_ = HitlIO(
            stdin=rf,
            stdout=io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        try:
            assert os.get_blocking(r) is True  # default: blocking
            io_.read_answer()
            assert os.get_blocking(r) is True  # restored after read_answer
            io_.drain()
            assert os.get_blocking(r) is True  # restored after drain
        finally:
            rf.close()


class TestAskMultiline:
    """D1: ask returns a whole pasted block via the centralized read_answer."""

    def test_pipe_paste_returned_as_one_answer(self):
        import os
        r, w = os.pipe()
        os.write(w, b"first\nsecond\nthird\n")
        os.close(w)
        rf = os.fdopen(r, "r")
        io_ = HitlIO(
            stdin=rf,
            stdout=io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=True,
        )
        try:
            handler = AskHandler(io_)
            assert handler.run(prompt="Paste?") == "first\nsecond\nthird"
        finally:
            rf.close()


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


class _RaisesOnRead:
    """Fake stdin that fails if readline() is ever called."""
    def readline(self):
        raise AssertionError("ReportHandler must not read stdin")


class TestReport:
    def _make_io(self, *, is_interactive: bool = True) -> HitlIO:
        return HitlIO(
            stdin=_RaisesOnRead(),
            stdout=io.StringIO(),
            stdout_lock=threading.Lock(),
            is_interactive=is_interactive,
        )

    def test_returns_ok(self):
        io_ = self._make_io()
        handler = ReportHandler(io_)
        assert handler.run("hello") == "ok"

    def test_writes_message_to_stdout(self):
        io_ = self._make_io()
        handler = ReportHandler(io_)
        handler.run("build started")
        out = io_.stdout.getvalue()
        assert "[report] build started" in out

    def test_writes_report_open_marker(self):
        io_ = self._make_io()
        handler = ReportHandler(io_)
        handler.run("any message")
        assert REPORT_OPEN in io_.stdout.getvalue()

    def test_does_not_read_stdin(self):
        # stdin is _RaisesOnRead — if readline() is called, the test fails
        io_ = self._make_io()
        handler = ReportHandler(io_)
        handler.run("should not read stdin")  # must not raise

    def test_works_unattended(self):
        # report must NOT raise HitlUnattended even when is_interactive=False
        io_ = self._make_io(is_interactive=False)
        handler = ReportHandler(io_)
        assert handler.run("progress update") == "ok"
