"""Output renderer for skill execution events."""

import json
import sys
from enum import Enum
from typing import Iterator


class OutputMode(str, Enum):
    pretty = "pretty"
    answer = "answer"
    json = "json"


def render(events: Iterator[dict], mode: OutputMode) -> None:
    """Render an event stream to stdout according to the given mode."""
    if mode == OutputMode.json:
        for event in events:
            print(json.dumps(event, ensure_ascii=False))
        return

    turn = 0
    for event in events:
        line = _format(event, mode, turn)
        if line is not None:
            if isinstance(line, tuple):
                output, turn = line
            else:
                output = line
            print(output)


def _format(event: dict, mode: OutputMode, turn: int) -> "str | tuple[str, int] | None":
    """Format a single event. Returns (output, new_turn) or output string or None."""
    return None  # placeholder — implemented in later tasks
