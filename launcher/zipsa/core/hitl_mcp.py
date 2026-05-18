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
    _YES = {"y", "yes"}
    _NO = {"n", "no"}
    _MAX_RETRIES = 3

    def __init__(self, io_: HitlIO) -> None:
        self._io = io_

    def run(self, message: str, default: bool | None = None) -> bool:
        if not self._io.is_interactive:
            raise HitlUnattended("confirm called in non-interactive run")
        suffix = "[Y/n]" if default is True else "[y/N]" if default is False else "[y/n]"
        with self._io.stdout_lock:
            self._io.stdout.write(f"\n{PROMPT_OPEN}\n[confirm] {message} {suffix}\n")
            for _ in range(self._MAX_RETRIES):
                self._io.stdout.write("> ")
                self._io.stdout.flush()
                line = self._io.stdin.readline().strip().lower()
                if line == "" and default is not None:
                    self._io.stdout.write(f"{PROMPT_CLOSE}\n")
                    self._io.stdout.flush()
                    return default
                if line in self._YES:
                    self._io.stdout.write(f"{PROMPT_CLOSE}\n")
                    self._io.stdout.flush()
                    return True
                if line in self._NO:
                    self._io.stdout.write(f"{PROMPT_CLOSE}\n")
                    self._io.stdout.flush()
                    return False
                self._io.stdout.write("(enter y or n)\n")
            self._io.stdout.write(f"{PROMPT_CLOSE}\n")
            self._io.stdout.flush()
        raise ValueError("confirm: too many invalid answers")


class ChooseHandler:
    _MAX_RETRIES = 3

    def __init__(self, io_: HitlIO) -> None:
        self._io = io_

    def run(self, prompt: str, options: list[str]) -> str:
        if not self._io.is_interactive:
            raise HitlUnattended("choose called in non-interactive run")
        if not options:
            raise ValueError("choose: empty options list")
        lowered = [o.lower() for o in options]
        with self._io.stdout_lock:
            self._io.stdout.write(f"\n{PROMPT_OPEN}\n[choose] {prompt}\n")
            for i, opt in enumerate(options, 1):
                self._io.stdout.write(f"  {i}) {opt}\n")
            for _ in range(self._MAX_RETRIES):
                self._io.stdout.write("> ")
                self._io.stdout.flush()
                line = self._io.stdin.readline().strip()
                if line.isdigit():
                    idx = int(line)
                    if 1 <= idx <= len(options):
                        self._io.stdout.write(f"{PROMPT_CLOSE}\n")
                        self._io.stdout.flush()
                        return options[idx - 1]
                elif line.lower() in lowered:
                    self._io.stdout.write(f"{PROMPT_CLOSE}\n")
                    self._io.stdout.flush()
                    return options[lowered.index(line.lower())]
                self._io.stdout.write("(enter the number or the exact option text)\n")
            self._io.stdout.write(f"{PROMPT_CLOSE}\n")
            self._io.stdout.flush()
        raise ValueError("choose: too many invalid answers")


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
