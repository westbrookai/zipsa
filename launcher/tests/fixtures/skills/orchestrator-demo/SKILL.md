# Orchestrator Demo

Single purpose: prove the run_skill -> get_artifact chain works.

## Steps

1. Take the user_query as the city name (e.g. "Sydney", "Tokyo").

2. Call `mcp__zipsa__run_skill` with:
   - `name`: `"weather"`
   - `args`: the city name from user_query

3. The response is `{status, exit_code, skill, version, run_id, summary}`. If status is not "ok", return your own status="failed" with the child's error.

4. If status is "ok", call `mcp__zipsa__get_artifact` with:
   - `skill`: the `skill` field from step 3's response
   - `version`: the `version` field from step 3's response
   - `run_id`: the `run_id` field from step 3's response
   - `name`: `"weather.json"`

5. The artifact response is `{name, size, content}` where `content` is the parsed JSON written by weather. Return:

   ```json
   {
     "status": "ok",
     "phase": "orchestrator-demo",
     "result": {
       "child_skill": "<skill from step 3>",
       "child_version": "<version>",
       "child_run_id": "<run_id>",
       "weather_content": <content from step 4, the parsed JSON>
     },
     "state_updates": null,
     "next_phase_input": null,
     "user_facing_summary": "Read <location>: <condition>, <temp_c>°C via child weather skill.",
     "error": null
   }
   ```

## Constraints

- Use ONLY `mcp__zipsa__run_skill` and `mcp__zipsa__get_artifact`. No other tools.
- No preamble. Just the JSON envelope.
- Do not pass anything other than the city name as args. Weather skill doesn't need date or other input.
