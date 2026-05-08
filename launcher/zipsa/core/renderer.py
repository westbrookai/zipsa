"""Output renderer for skill execution events."""

import json
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
    for event in events:
        _format(event, mode)


def _format(event: dict, mode: OutputMode) -> None:
    """Format a single event. Placeholder — implemented in Task 2."""
    return
