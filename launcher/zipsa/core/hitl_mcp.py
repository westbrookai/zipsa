"""HITL tool handlers (ask/confirm/choose).

The handlers are decoupled from the MCP transport via a small HitlIO
dataclass, so unit tests can drive them with in-memory streams without
sockets or a real server.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TextIO


PROMPT_OPEN = "──── User input needed ────"
PROMPT_CLOSE = "──── Resuming ────"


class HitlUnattended(Exception):
    """Raised when a tool is invoked but the run is non-interactive."""


@dataclass
class HitlIO:
    stdin: TextIO
    stdout: TextIO
    stdout_lock: threading.Lock
    is_interactive: bool


class AskHandler:
    def __init__(self, io_: HitlIO) -> None:
        self._io = io_

    def run(self, prompt: str) -> str:
        if not self._io.is_interactive:
            raise HitlUnattended("ask called in non-interactive run")
        with self._io.stdout_lock:
            self._io.stdout.write(f"\n{PROMPT_OPEN}\n[ask] {prompt}\n> ")
            self._io.stdout.flush()
            answer = self._io.stdin.readline()
            self._io.stdout.write(f"{PROMPT_CLOSE}\n")
            self._io.stdout.flush()
        return answer.strip()


class ConfirmHandler:
    def __init__(self, io_: HitlIO) -> None:
        self._io = io_

    def run(self, message: str, default: bool | None = None) -> bool:
        raise NotImplementedError  # Implemented in Task 3


class ChooseHandler:
    def __init__(self, io_: HitlIO) -> None:
        self._io = io_

    def run(self, prompt: str, options: list[str]) -> str:
        raise NotImplementedError  # Implemented in Task 4
