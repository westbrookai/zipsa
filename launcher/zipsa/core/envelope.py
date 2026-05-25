"""Strict skill-envelope parser — the runtime contract's enforcement.

The contract says every phase MUST end with a single JSON envelope and
NOTHING else. This module is the law that enforces it: any text outside
the envelope, malformed JSON, or schema mismatch raises EnvelopeError
with a specific subcode the executor surfaces as `error.code=contract_violation`.

Two accepted shapes (both are stripped of surrounding whitespace):
  1. A raw JSON object as the entire message body.
  2. A single ```json fenced block as the entire message body.

Anything else — prose, multiple fences, an untagged ``` block, JSON
followed by "Let me know!" — is rejected.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, model_validator


_FENCE_RE = re.compile(r"```json\s*\n(.+?)\n```", re.DOTALL)


class ErrorDetail(BaseModel):
    """The `error` object that accompanies status=failed."""

    model_config = {"extra": "allow"}  # skill-author free-form context

    code: str = Field(min_length=1)
    message: Optional[str] = None


class Envelope(BaseModel):
    """The mandatory final message of every phase.

    `extra='forbid'` is load-bearing: catches typos like `user_face_summary`
    and any rogue field the skill might add. Skills express extra context
    via `result` / `state_updates` / `error`, not by inventing top-level keys.
    """

    model_config = {"extra": "forbid"}

    status: Literal["ok", "failed", "out_of_scope"]
    phase: str = Field(min_length=1)
    result: Any = None
    state_updates: Optional[dict] = None
    next_phase_input: Any = None
    user_facing_summary: str = Field(min_length=1)
    error: Optional[ErrorDetail] = None

    @model_validator(mode="after")
    def _failed_requires_error(self) -> "Envelope":
        if self.status == "failed" and self.error is None:
            raise ValueError("status=failed requires a populated `error` object")
        return self


class EnvelopeError(Exception):
    """Raised when an agent message violates the runtime contract.

    Carries a specific subcode the executor maps to user-visible
    `error.code=contract_violation` + `error.subcode=<subcode>`, plus a
    short excerpt of the offending input for debugging.
    """

    def __init__(self, subcode: str, message: str, excerpt: str = "") -> None:
        super().__init__(message)
        self.subcode = subcode
        self.excerpt = excerpt


def _excerpt(text: str, limit: int = 200) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "..."


def parse_envelope_strict(text: Optional[str]) -> Envelope:
    """Parse and validate the agent's final message as a skill envelope.

    Raises EnvelopeError on any violation. Never returns None.

    Subcode dispatch:
      - text_outside_envelope: the text isn't shaped like an envelope
        attempt at all (no leading `{`/`[`, no ```json fence), OR there
        is content trailing a successfully-parsed envelope.
      - malformed_json: looks like JSON but fails to parse.
      - invalid_schema: parses to JSON but doesn't match Envelope.
    """
    if not text or not text.strip():
        raise EnvelopeError(
            "malformed_json",
            "Agent emitted no text — cannot parse envelope.",
            excerpt="",
        )

    stripped = text.strip()
    excerpt = _excerpt(stripped)

    # Path A: a single ```json fenced block as the entire body.
    # re.fullmatch ensures nothing precedes or trails the fence — that's
    # what makes hello-world's "Output Format block + envelope" pattern
    # fail here.
    fence = re.fullmatch(_FENCE_RE, stripped)
    if fence:
        return _validate_json_body(fence.group(1).strip(), excerpt)

    # Path B: raw JSON consuming the entire body.
    # raw_decode lets us distinguish "JSON ended early then prose" (=
    # text_outside_envelope) from "JSON itself is broken" (= malformed).
    if stripped.startswith("{") or stripped.startswith("["):
        decoder = json.JSONDecoder()
        try:
            obj, end = decoder.raw_decode(stripped)
        except json.JSONDecodeError as e:
            raise EnvelopeError(
                "malformed_json",
                f"Envelope JSON failed to parse: {e.msg}",
                excerpt=excerpt,
            ) from e
        trailing = stripped[end:].strip()
        if trailing:
            raise EnvelopeError(
                "text_outside_envelope",
                "Agent emitted text after the envelope JSON.",
                excerpt=excerpt,
            )
        return _validate_parsed_obj(obj, excerpt)

    # Neither raw JSON nor a fenced block — pure prose, multiple fences,
    # an untagged ``` block, etc.
    raise EnvelopeError(
        "text_outside_envelope",
        "Agent message contained text outside the envelope JSON. "
        "The contract requires the final message to be ONLY the envelope.",
        excerpt=excerpt,
    )


def _validate_json_body(body: str, excerpt: str) -> Envelope:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise EnvelopeError(
            "malformed_json",
            f"Envelope JSON failed to parse: {e.msg}",
            excerpt=excerpt,
        ) from e
    return _validate_parsed_obj(data, excerpt)


def _validate_parsed_obj(data: Any, excerpt: str) -> Envelope:
    try:
        return Envelope.model_validate(data)
    except ValidationError as e:
        raise EnvelopeError(
            "invalid_schema",
            f"Envelope failed schema validation: {e.errors(include_url=False)}",
            excerpt=excerpt,
        ) from e
