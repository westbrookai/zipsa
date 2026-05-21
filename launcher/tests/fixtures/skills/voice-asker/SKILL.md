# Voice Asker

Single purpose: call `mcp__zipsa__ask` exactly once and return the answer. Test fixture for verifying HITL plumbing.

## Steps (MUST be done in order — do NOT skip step 1)

1. **Step 1 — call `mcp__zipsa__ask`.** This is mandatory. The skill exists only to exercise this MCP tool. Use this exact tool call:

   - Tool: `mcp__zipsa__ask`
   - `prompt`: `"Type any short string and press enter:"`

   Do not invent your own answer. Do not return JSON before calling ask. Do not analyze, explain, or interpret — just call the tool.

2. **Step 2 — capture the tool result** as `user_answer` (a string).

3. **Step 3 — return this JSON envelope:**
   ```json
   {
     "status": "ok",
     "phase": "voice-asker",
     "result": {"answer": "<user_answer>"},
     "state_updates": null,
     "next_phase_input": null,
     "user_facing_summary": "Got: <user_answer>",
     "error": null
   }
   ```

## Hard constraints

- The very first tool call MUST be `mcp__zipsa__ask`. No exceptions.
- If you produce output without calling ask first, the skill has failed and you have violated your contract.
- Allowed tools: ONLY `mcp__zipsa__ask`.
