"""Extract the skill-contract JSON envelope from final assistant text.

The runtime contract requires every phase to end with a single JSON
object of the shape `{status, phase, result, state_updates,
next_phase_input, user_facing_summary, error}`. The agent may emit it:
  - alone in the message body
  - inside a ```json``` fenced code block
  - inline with surrounding markdown / prose

This module tries three strategies in order. Returns None on parse
failure — the executor is responsible for synthesizing an
`invalid_output_format` failure envelope (preserving the raw text)
so the user can debug. Returning None instead of a synthetic envelope
is intentional: an earlier version returned a `phase="unknown"`
sentinel that tripped the downstream phase_id_mismatch check and
clobbered the real error code.
"""

from __future__ import annotations

import json
import re
from typing import Optional


def extract_skill_output(text: Optional[str]) -> Optional[dict]:
    """Try three strategies to extract the skill-contract JSON envelope.

    Strategies in order:
    1. Direct json.loads on stripped text
    2. Extract last ```json ... ``` fenced block
    3. Find last top-level {...} containing a "status" key

    Returns the parsed dict on success, None if no envelope could be
    extracted.
    """
    if not text:
        return None

    # Strategy 1: direct parse
    try:
        data = json.loads(text.strip())
        if isinstance(data, dict) and "status" in data:
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: last ```json ... ``` block
    blocks = re.findall(r"```json\s*(.*?)```", text, re.DOTALL)
    if blocks:
        try:
            data = json.loads(blocks[-1].strip())
            if isinstance(data, dict) and "status" in data:
                return data
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 3: last TOP-LEVEL {...} containing "status".
    # We scan the text once tracking depth so nested objects (e.g.
    # a contract JSON whose `result` field happens to itself contain
    # "status") are NOT considered as standalone candidates. Only
    # outermost balanced {...} blocks are candidates; we return the
    # last one with a top-level "status" key.
    top_level_spans = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    top_level_spans.append((start, i + 1))
                    start = None
    for s, e in reversed(top_level_spans):
        try:
            data = json.loads(text[s:e])
            if isinstance(data, dict) and "status" in data:
                return data
        except (json.JSONDecodeError, ValueError):
            pass

    return None
