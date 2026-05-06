# Hello World

## Purpose

Smoke test for the zipsa runtime. Confirms that the agent is running and identifies the active runtime.

## Instructions

1. Identify which runtime you are running on:
   - **Claude Code** — if you are Claude (Anthropic)
   - **Codex** — if you are running via OpenAI Codex CLI
   - **Gemini** — if you are running via Google Gemini CLI
2. Report your model name (e.g. `claude-opus-4-7`, `gpt-4o`, `gemini-2.0-flash`)
3. Output the result in the exact format below.

## Output Format

Output exactly this, filled in:

```
Hello from zipsa!

Runtime : Claude Code         # or Codex / Gemini
Model   : claude-opus-4-7     # actual model name
Status  : OK
```

Nothing else. No explanation, no extra text.
