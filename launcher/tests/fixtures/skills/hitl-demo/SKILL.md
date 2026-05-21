# HITL Demo

Single purpose: invoke `voice-asker` as a child and report the answer it collected.

## Steps

1. Call `mcp__zipsa__run_skill` with:
   - `name`: `"voice-asker"`
   - `args`: `"what is your favorite color?"`

2. The child will prompt the user — the prompt should appear in your terminal (the parent's terminal). When the user answers, the child completes and returns.

3. The response is `{status, exit_code, skill, version, run_id, summary}`. The user's answer lives in `summary.result.answer` (per voice-asker's SKILL.md).

4. Return:
   ```json
   {
     "status": "ok",
     "phase": "hitl-demo",
     "result": {
       "child_skill": "voice-asker",
       "child_run_id": "<run_id>",
       "user_answer": "<answer from summary.result.answer>"
     },
     "state_updates": null,
     "next_phase_input": null,
     "user_facing_summary": "voice-asker collected: '<answer>'",
     "error": null
   }
   ```

## Constraints

- Use ONLY `mcp__zipsa__run_skill`. Do NOT call `ask`/`confirm`/`choose` yourself — the child does that.
- No preamble.
