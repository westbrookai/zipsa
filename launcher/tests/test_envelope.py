"""Tests for the strict skill-envelope parser.

The runtime contract is treated as law: every phase MUST end with a
single JSON envelope and NOTHING else. These tests pin that down.

Subcodes the parser raises:
  - text_outside_envelope: any non-whitespace before/after the JSON
  - malformed_json: the JSON body itself failed to parse
  - invalid_schema: parsed JSON didn't match the Envelope model
    (missing required field, extra field, bad status enum, etc.)
"""

import pytest

from zipsa.core.envelope import (
    Envelope,
    EnvelopeError,
    parse_envelope_strict,
)


# ---------------------------------------------------------------------------
# Accept: contract-compliant envelopes
# ---------------------------------------------------------------------------


class TestStrictParserAccepts:
    def _payload(self, **overrides) -> str:
        import json
        base = {
            "status": "ok",
            "phase": "main",
            "result": {"k": "v"},
            "state_updates": None,
            "next_phase_input": None,
            "user_facing_summary": "done",
            "error": None,
        }
        base.update(overrides)
        return json.dumps(base)

    def test_raw_json_object_accepted(self):
        env = parse_envelope_strict(self._payload())
        assert isinstance(env, Envelope)
        assert env.status == "ok"
        assert env.phase == "main"

    def test_raw_json_with_surrounding_whitespace_accepted(self):
        """Whitespace before/after is normal — it's prose that's banned."""
        text = "\n\n  " + self._payload() + "\n  "
        env = parse_envelope_strict(text)
        assert env.status == "ok"

    def test_single_fenced_json_block_accepted(self):
        text = "```json\n" + self._payload() + "\n```"
        env = parse_envelope_strict(text)
        assert env.status == "ok"

    def test_fenced_block_with_surrounding_whitespace_accepted(self):
        text = "\n```json\n" + self._payload() + "\n```\n"
        env = parse_envelope_strict(text)
        assert env.status == "ok"

    def test_status_failed_with_error_accepted(self):
        text = self._payload(
            status="failed",
            error={"code": "mcp_unavailable", "message": "boom"},
        )
        env = parse_envelope_strict(text)
        assert env.status == "failed"
        assert env.error is not None
        assert env.error.code == "mcp_unavailable"

    def test_status_out_of_scope_accepted(self):
        env = parse_envelope_strict(self._payload(status="out_of_scope"))
        assert env.status == "out_of_scope"


# ---------------------------------------------------------------------------
# Reject: text outside the envelope
# ---------------------------------------------------------------------------


class TestRejectsTextOutsideEnvelope:
    """The contract violation that broke hello-world. Any prose, header,
    or extra fence around the envelope is rejected."""

    def _payload(self) -> str:
        import json
        return json.dumps({
            "status": "ok", "phase": "main", "result": None,
            "state_updates": None, "next_phase_input": None,
            "user_facing_summary": "ok", "error": None,
        })

    def test_prose_before_raw_json_rejected(self):
        text = "I completed the task. " + self._payload()
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict(text)
        assert e.value.subcode == "text_outside_envelope"

    def test_prose_after_raw_json_rejected(self):
        text = self._payload() + "\n\nThanks!"
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict(text)
        assert e.value.subcode == "text_outside_envelope"

    def test_prose_before_fence_rejected(self):
        text = "Some explanation.\n```json\n" + self._payload() + "\n```"
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict(text)
        assert e.value.subcode == "text_outside_envelope"

    def test_prose_after_fence_rejected(self):
        text = "```json\n" + self._payload() + "\n```\n\nLet me know!"
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict(text)
        assert e.value.subcode == "text_outside_envelope"

    def test_multiple_fences_rejected(self):
        """hello-world's exact failure mode: an Output Format block
        before the envelope fence."""
        text = (
            "```\nHello from zipsa!\nStatus : OK\n```\n\n"
            "```json\n" + self._payload() + "\n```"
        )
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict(text)
        assert e.value.subcode == "text_outside_envelope"

    def test_excerpt_preserved_for_debugging(self):
        text = "Hello from zipsa!\n\n" + self._payload()
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict(text)
        # excerpt should include the offending prefix so the user
        # can see what the agent emitted
        assert "Hello from zipsa!" in e.value.excerpt


# ---------------------------------------------------------------------------
# Reject: malformed JSON
# ---------------------------------------------------------------------------


class TestRejectsMalformedJSON:
    def test_empty_text_rejected(self):
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict("")
        # Empty isn't text-outside, it's missing entirely
        assert e.value.subcode in ("malformed_json", "text_outside_envelope")

    def test_garbage_text_rejected(self):
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict("I could not complete the task.")
        assert e.value.subcode in ("malformed_json", "text_outside_envelope")

    def test_truncated_json_rejected(self):
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict('{"status": "ok", "phase":')
        assert e.value.subcode == "malformed_json"

    def test_fence_with_truncated_json_rejected(self):
        text = '```json\n{"status": "ok", "phase":\n```'
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict(text)
        assert e.value.subcode == "malformed_json"

    def test_json_array_at_top_rejected(self):
        """Envelope must be an object, not a list."""
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict('[1, 2, 3]')
        # Either parses to non-dict (schema fail) or rejected outright
        assert e.value.subcode in ("malformed_json", "invalid_schema")


# ---------------------------------------------------------------------------
# Reject: schema violations (Pydantic)
# ---------------------------------------------------------------------------


class TestRejectsInvalidSchema:
    def _payload(self, **overrides) -> str:
        import json
        base = {
            "status": "ok",
            "phase": "main",
            "result": None,
            "state_updates": None,
            "next_phase_input": None,
            "user_facing_summary": "done",
            "error": None,
        }
        base.update(overrides)
        # remove keys set to a sentinel
        return json.dumps({k: v for k, v in base.items() if v != "<<DROP>>"})

    def test_missing_status_rejected(self):
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict(self._payload(status="<<DROP>>"))
        assert e.value.subcode == "invalid_schema"

    def test_missing_phase_rejected(self):
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict(self._payload(phase="<<DROP>>"))
        assert e.value.subcode == "invalid_schema"

    def test_missing_user_facing_summary_rejected(self):
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict(self._payload(user_facing_summary="<<DROP>>"))
        assert e.value.subcode == "invalid_schema"

    def test_bad_status_enum_rejected(self):
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict(self._payload(status="success"))
        assert e.value.subcode == "invalid_schema"

    def test_unknown_top_level_field_rejected(self):
        """Catches typos like user_face_summary instead of user_facing_summary."""
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict(self._payload(user_face_summary="oops"))
        assert e.value.subcode == "invalid_schema"

    def test_empty_user_facing_summary_rejected(self):
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict(self._payload(user_facing_summary=""))
        assert e.value.subcode == "invalid_schema"

    def test_failed_without_error_rejected(self):
        """status=failed requires a populated error object — otherwise
        the launcher has no diagnostic to surface."""
        with pytest.raises(EnvelopeError) as e:
            parse_envelope_strict(self._payload(status="failed", error=None))
        assert e.value.subcode == "invalid_schema"
