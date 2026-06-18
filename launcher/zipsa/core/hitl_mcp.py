"""HITL tool handlers (ask/confirm/choose/report).

Design decisions:
- Handlers are decoupled from the MCP transport via a small HitlIO
  dataclass, so unit tests can drive them with in-memory streams without
  sockets or a real server.
- All input goes through HitlIO.read_answer()/drain() so the three blocking
  handlers share one path: read_answer gathers a multi-line paste into one
  answer (D1), drain discards a stray trailing paste so it can't leak into
  the next prompt.
- confirm/choose are lossless and non-fatal (D2): an unrecognized answer is
  returned verbatim to the agent (confirm returns str "yes"/"no"/raw, choose
  returns the chosen option or the raw text) rather than looping or raising.

Gotchas:
- read_answer/drain gather under a temporarily non-blocking fd and read via
  stdin.readline(), not raw os.read — a TextIOWrapper may pull a whole
  multi-line burst into its own decoded buffer in one underlying read, which
  fd-level reads would miss.
- When stdin has no real fileno() (io.StringIO in tests) the gather is
  skipped and read_answer falls back to a single readline().
"""

from __future__ import annotations

import os
import select
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TextIO


PROMPT_OPEN = "──── User input needed ────"
PROMPT_CLOSE = "──── Resuming ────"
REPORT_OPEN = "──── report ────"   # distinct from PROMPT_OPEN so a relay
                                   # can tell progress from a question


class HitlUnattended(Exception):
    """Raised when a tool is invoked but the run is non-interactive."""


@dataclass
class HitlIO:
    stdin: TextIO
    stdout: TextIO
    stdout_lock: threading.Lock
    is_interactive: bool
    # Single-element list so handlers can mutate by reference; we don't
    # want a property or recursive @dataclass field with default_factory
    # for a float wrapper. List wins for simplicity.
    hitl_wait_seconds: list = field(default_factory=lambda: [0.0])

    @contextmanager
    def measure_wait(self):
        """Bracket a stdin.readline call to accumulate wait time.

        Usage:
            with self._io.measure_wait():
                answer = self._io.stdin.readline()
        """
        t0 = time.monotonic()
        try:
            yield
        finally:
            self.hitl_wait_seconds[0] += time.monotonic() - t0

    def _fd(self) -> int | None:
        """The stdin fd if it is a real, selectable fd, else None.

        Returns None for io.StringIO and other non-fd streams (tests), so
        callers fall back to today's single-readline() behaviour.
        """
        try:
            fd = self.stdin.fileno()
        except (AttributeError, OSError, ValueError):
            return None
        try:
            select.select([fd], [], [], 0)
        except (OSError, ValueError):
            return None
        return fd

    def _gather_pending(self, fd: int) -> str:
        """Read all immediately-available input through stdin without blocking.

        Puts the fd in non-blocking mode for the duration so that, once the
        pending burst is consumed, the next read returns at once (empty or
        BlockingIOError) instead of waiting for more input. Reads via
        stdin.readline() — not raw os.read — so any lines a TextIOWrapper
        already pulled into its own buffer (a multi-line paste arrives in one
        underlying read) are gathered too; raw fd reads would miss those.
        Restores the original blocking flag afterwards.
        """
        flags = os.get_blocking(fd)
        parts: list[str] = []
        try:
            os.set_blocking(fd, False)
            while True:
                try:
                    line = self.stdin.readline()
                except BlockingIOError:
                    break
                # None: non-blocking read with nothing available (would block).
                # "": EOF. Either way, stop.
                if not line:
                    break
                parts.append(line)
        finally:
            os.set_blocking(fd, flags)
        return "".join(parts)

    def read_answer(self) -> str:
        """Read one line, then gather any further immediately-available lines.

        A multi-line paste arrives as one burst, so lines 2..N are pending
        within microseconds of the first; we read them all and return the
        joined, stripped block (D1). A genuine single-line answer sees nothing
        pending and returns at once (the non-blocking gather returns empty).
        When stdin has no real fd (StringIO, non-fd streams) we fall back to
        a single readline().
        """
        with self.measure_wait():
            first = self.stdin.readline()
            fd = self._fd()
            if fd is None:
                return first.strip()
            rest = self._gather_pending(fd)
        return (first + rest).strip()

    def drain(self) -> None:
        """Discard any immediately-available pending input (best-effort).

        Used by confirm/choose after they capture their answer so a stray
        multi-line paste can't leak into the next prompt. No-op when stdin
        has no real fd.
        """
        fd = self._fd()
        if fd is not None:
            self._gather_pending(fd)


class AskHandler:
    def __init__(self, io_: HitlIO) -> None:
        self._io = io_

    def run(self, prompt: str) -> str:
        if not self._io.is_interactive:
            raise HitlUnattended("ask called in non-interactive run")
        with self._io.stdout_lock:
            self._io.stdout.write(f"\n{PROMPT_OPEN}\n[ask] {prompt}\n> ")
            self._io.stdout.flush()
            answer = self._io.read_answer()
            self._io.stdout.write(f"{PROMPT_CLOSE}\n")
            self._io.stdout.flush()
        return answer


class ConfirmHandler:
    # Small, documented English synonym sets. Non-English yes/no (그래, 네,
    # 아니) deliberately fall through to "freeform returned to the agent"
    # rather than being hardcoded — the agent interprets the literal text.
    _YES = {"y", "yes", "yeah", "yep", "ok", "okay", "sure", "true"}
    _NO = {"n", "no", "nope", "nah", "false"}

    def __init__(self, io_: HitlIO) -> None:
        self._io = io_

    def run(self, message: str, default: bool | None = None) -> str:
        """Ask a yes/no question; return "yes"/"no" or the raw freeform text.

        Lossless and non-fatal (D2-A): a clean yes/no synonym maps to
        "yes"/"no"; an empty line uses the default (or, with no default,
        re-prompts once then returns ""); anything else is returned verbatim
        so the agent can treat it as a correction. Never raises ValueError.
        """
        if not self._io.is_interactive:
            raise HitlUnattended("confirm called in non-interactive run")
        suffix = "[Y/n]" if default is True else "[y/N]" if default is False else "[y/n]"
        with self._io.stdout_lock:
            self._io.stdout.write(f"\n{PROMPT_OPEN}\n[confirm] {message} {suffix}\n")
            # Bounded loop: only re-prompt on an *empty* line. A non-empty
            # answer is always honoured (mapped or returned verbatim).
            for attempt in range(2):
                self._io.stdout.write("> ")
                self._io.stdout.flush()
                answer = self._io.read_answer()
                self._io.drain()
                lowered = answer.lower()
                if answer == "":
                    if default is not None:
                        result = "yes" if default else "no"
                        break
                    if attempt == 0:
                        continue  # re-prompt once
                    result = ""  # still empty, no default — give up, don't raise
                    break
                if lowered in self._YES:
                    result = "yes"
                    break
                if lowered in self._NO:
                    result = "no"
                    break
                result = answer  # freeform correction → hand to the agent
                break
            self._io.stdout.write(f"{PROMPT_CLOSE}\n")
            self._io.stdout.flush()
        return result


class ChooseHandler:
    def __init__(self, io_: HitlIO) -> None:
        self._io = io_

    def run(self, prompt: str, options: list[str]) -> str:
        """Offer a closed set; return the chosen option or the raw freeform.

        Lossless and non-fatal (D2-A): an in-range number or an exact
        (case-insensitive) option text maps to that option; an empty line
        re-prompts once; anything else — a non-option correction or new
        instruction — is returned verbatim so the agent can act on it.
        Never raises (except for an empty options list, a caller bug).
        """
        if not self._io.is_interactive:
            raise HitlUnattended("choose called in non-interactive run")
        if not options:
            raise ValueError("choose: empty options list")
        lowered = [o.lower() for o in options]
        with self._io.stdout_lock:
            self._io.stdout.write(f"\n{PROMPT_OPEN}\n[choose] {prompt}\n")
            for i, opt in enumerate(options, 1):
                self._io.stdout.write(f"  {i}) {opt}\n")
            # Bounded loop: only re-prompt on an *empty* line. Any non-empty
            # answer is honoured (mapped to an option or returned verbatim).
            for attempt in range(2):
                self._io.stdout.write("> ")
                self._io.stdout.flush()
                line = self._io.read_answer()
                self._io.drain()
                if line == "" and attempt == 0:
                    continue  # re-prompt once
                if line.isdigit():
                    idx = int(line)
                    if 1 <= idx <= len(options):
                        result = options[idx - 1]
                        break
                if line.lower() in lowered:
                    result = options[lowered.index(line.lower())]
                    break
                result = line  # non-option freeform → hand to the agent
                break
            self._io.stdout.write(f"{PROMPT_CLOSE}\n")
            self._io.stdout.flush()
        return result


class ReportHandler:
    def __init__(self, io_: HitlIO) -> None:
        self._io = io_

    def run(self, message: str) -> str:
        # Write-only: no stdin read, no measure_wait, no HitlUnattended.
        # A report is just output; valid attended OR unattended.
        with self._io.stdout_lock:
            self._io.stdout.write(f"\n{REPORT_OPEN}\n[report] {message}\n")
            self._io.stdout.flush()
        return "ok"


from typing import Any

from .memory_store import MemoryStore


_VALID_SCOPES = ("skill", "global")


def _pick_store(scope: str, skill: MemoryStore, global_: MemoryStore) -> MemoryStore:
    if scope == "skill":
        return skill
    if scope == "global":
        return global_
    raise ValueError(f"scope must be one of {_VALID_SCOPES!r}, got {scope!r}")


class RecallHandler:
    def __init__(self, skill_store: MemoryStore, global_store: MemoryStore) -> None:
        self._skill = skill_store
        self._global = global_store

    def run(self, key: str, scope: str = "skill") -> Any | None:
        return _pick_store(scope, self._skill, self._global).get(key)


class RememberHandler:
    def __init__(self, skill_store: MemoryStore, global_store: MemoryStore) -> None:
        self._skill = skill_store
        self._global = global_store

    def run(self, key: str, value: Any, scope: str = "skill") -> None:
        _pick_store(scope, self._skill, self._global).set(key, value)


class ForgetHandler:
    def __init__(self, skill_store: MemoryStore, global_store: MemoryStore) -> None:
        self._skill = skill_store
        self._global = global_store

    def run(self, key: str, scope: str = "skill") -> bool:
        return _pick_store(scope, self._skill, self._global).delete(key)


class ListMemoryHandler:
    def __init__(self, skill_store: MemoryStore, global_store: MemoryStore) -> None:
        self._skill = skill_store
        self._global = global_store

    def run(self, scope: str = "skill") -> list[str]:
        return _pick_store(scope, self._skill, self._global).keys()


class AskOnceHandler:
    """Composite: recall first; if missing, ask + remember + return.

    The "ask the user once, cache forever" pattern. Replaces the
    three-call sequence (recall → ask → remember) with a single
    primitive that can't be misused (no way to forget the remember step).
    """

    def __init__(
        self,
        ask: AskHandler,
        recall: RecallHandler,
        remember: RememberHandler,
    ) -> None:
        self._ask = ask
        self._recall = recall
        self._remember = remember

    def run(self, key: str, prompt: str, scope: str = "skill") -> str:
        if scope not in _VALID_SCOPES:
            raise ValueError(
                f"scope must be one of {_VALID_SCOPES!r}, got {scope!r}"
            )
        existing = self._recall.run(key=key, scope=scope)
        if existing is not None:
            return existing
        answer = self._ask.run(prompt=prompt)
        self._remember.run(key=key, value=answer, scope=scope)
        return answer
