# Artifact Roundtrip Skill

Two phases that prove intra-skill artifact sharing via MCP.

## Phase: write

1. Build this exact content (deterministic, no randomness):
   ```json
   {
     "answer": 42,
     "marker": "roundtrip-ok",
     "list": [1, 2, 3]
   }
   ```

2. Write it to `/home/agent/runs/current/artifacts/roundtrip.json` using the Write tool.

3. Return:
   ```json
   {
     "status": "ok",
     "phase": "write",
     "result": {"written": true, "name": "roundtrip.json"},
     "state_updates": null,
     "next_phase_input": {
       "expected_content": {"answer": 42, "marker": "roundtrip-ok", "list": [1, 2, 3]},
       "artifact_name": "roundtrip.json"
     },
     "user_facing_summary": "Wrote roundtrip.json artifact.",
     "error": null
   }
   ```

## Phase: read

1. From `execution_context`, read `run_id`. From `previous_phase_output`, read `expected_content` and `artifact_name`.

2. Call `mcp__zipsa__get_artifact` with arguments:
   - `skill`: `"artifact-roundtrip"`
   - `version`: `"0.1.0"`
   - `run_id`: `<execution_context.run_id>` (the literal value from above, e.g. `"2026-05-21_140000_000"`)
   - `name`: `<artifact_name>` (the value from previous_phase_output)

3. Compare the returned `content` field deeply with `expected_content`.

4. Return:
   - If they match:
     ```json
     {
       "status": "ok",
       "phase": "read",
       "result": {"roundtrip": "verified", "size": <size from get_artifact>, "content": <content from get_artifact>},
       "state_updates": null,
       "next_phase_input": null,
       "user_facing_summary": "Roundtrip OK — content read back matches what was written.",
       "error": null
     }
     ```
   - If they don't match:
     ```json
     {
       "status": "failed",
       "phase": "read",
       "result": null,
       "state_updates": null,
       "next_phase_input": null,
       "user_facing_summary": "Roundtrip MISMATCH — see error.",
       "error": {
         "code": "roundtrip_mismatch",
         "message": "expected vs actual diff details here"
       }
     }
     ```

## Constraints

- Phase write: Use ONLY the Write tool plus zipsa runtime tools.
- Phase read: Use ONLY `mcp__zipsa__get_artifact`. Do not use Read or Bash to peek at the file directly — the whole point is to test the MCP path.
- Both phases: no preamble, no narration. Just produce the JSON envelope.
