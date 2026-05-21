# Test Parent

Single purpose: invoke hello-world via mcp__zipsa__run_skill and report what came back.

## Steps

1. Call `mcp__zipsa__run_skill` with arguments:
   - `name`: `"hello-world"`
   - `args`: `""`

2. The response is a dict: `{status, exit_code, skill, version, run_id, summary}`.

3. Return:
   ```json
   {
     "status": "ok",
     "phase": "test-parent",
     "result": {
       "child_skill": "<skill from response>",
       "child_version": "<version from response>",
       "child_run_id": "<run_id from response>",
       "child_status": "<status from response>"
     },
     "state_updates": null,
     "next_phase_input": null,
     "user_facing_summary": "Invoked hello-world; got run_id=<run_id>",
     "error": null
   }
   ```

   If the response has `status: "failed"`, return your own status="failed" with the child's error verbatim.

## Constraints

- Use ONLY `mcp__zipsa__run_skill`. No other tools.
- No preamble. Just the JSON envelope.
