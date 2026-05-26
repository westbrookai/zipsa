# hello-world

Identify the runtime you're on (Claude Code / Codex / Gemini) and
your model name. Confirm zipsa is up.

## What to put in result

- `runtime`: one of "Claude Code" / "Codex" / "Gemini"
- `model`: your actual model name (e.g. claude-sonnet-4-6,
  gpt-4o, gemini-2.0-flash)
- `status`: "OK"

## What to put in user_facing_summary

A friendly one-line greeting in the user's language, mentioning
the runtime and confirming zipsa is up. Mention today's date if
it feels natural; skip if it doesn't.

Examples (English; localize):
- "Hello from zipsa! Claude Code (claude-sonnet-4-6) is running."
- "Hi! All good — running on Claude Code, model claude-sonnet-4-6."

That's it. No additional output, no narration, no markdown blocks
outside the envelope.
