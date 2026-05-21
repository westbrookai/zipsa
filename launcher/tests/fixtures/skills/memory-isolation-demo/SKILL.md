# Memory Isolation Demo

Prove parent's memory writes are NOT visible to child skills via run_skill.

## Steps

1. Call `mcp__zipsa__remember` with `key="color"`, `value="red"`. This writes to memory-isolation-demo's OWN memory file.

2. Call `mcp__zipsa__recall` with `key="color"` to verify YOUR own write — should return "red".

3. Call `mcp__zipsa__run_skill` with `name="memory-peek"`, `args=""`. The child runs and reports what IT sees for key="color".

4. The child's response is `{status, exit_code, skill, version, run_id, summary}`. The peek result lives in `summary.result.color_value` — extract it.

5. Return:
   ```json
   {
     "status": "ok",
     "phase": "memory-isolation-demo",
     "result": {
       "parent_recalled": "red",
       "child_recalled": "<value from child's summary.result.color_value, expected: null or empty>",
       "isolation_verified": true_if_child_recalled_is_null_else_false
     },
     "state_updates": null,
     "next_phase_input": null,
     "user_facing_summary": "Parent saw 'red'; child saw <child_value> — isolation <verified|broken>",
     "error": null
   }
   ```

## Constraints

- Use ONLY `mcp__zipsa__remember`, `mcp__zipsa__recall`, and `mcp__zipsa__run_skill`. Nothing else.
