# Memory Peek

Single purpose: call `mcp__zipsa__recall` with key="color" and report the result.

## Steps

1. Call `mcp__zipsa__recall` with `key="color"`.
2. The tool returns either the stored value (string) or null/None.
3. Return:
   ```json
   {
     "status": "ok",
     "phase": "memory-peek",
     "result": {"color_value": "<value or null>"},
     "state_updates": null,
     "next_phase_input": null,
     "user_facing_summary": "memory-peek saw color=<value>",
     "error": null
   }
   ```
