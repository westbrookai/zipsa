# Zipsa Launcher

Multi-runtime skill launcher for Claude Code, Codex, and Gemini CLI.

## Installation

```bash
pip install -e ".[dev]"
```
## Usage

```bash
# Run a skill
zipsa run weather "Seoul weather"

# With specific runtime
zipsa run weather "Seoul" --runtime claude

# Validate manifest
zipsa validate ../zipsa-skills/weather

# List skills
zipsa list 

# List runtimes
zipsa runtimes
```

### Resuming a failed run

After a multi-phase skill fails (e.g. `bip-daily-x` at the `post` phase),
re-running the same command auto-detects the prior failure and prompts:

```
$ zipsa run bip-daily-x today
Previous run: 2026-05-21_231116 (1 hour ago)
  args: "today"
  status: limits_exceeded — phase 'post': cost $0.13 > limit $0.10
...
Resume from 'post'? [Y/n]:
```

Accept (`Y` or empty enter) to skip the already-completed phases and
retry the failed one. The prior run's `next_phase_input` is loaded
automatically, so HITL-iterated state (like an approved draft) is
preserved across the retry.

In non-interactive contexts (cron, piped stdin), zipsa refuses to
silently auto-resume or auto-start-fresh — it exits 2 with a message
asking you to pass `--no-resume` explicitly:

```
$ zipsa run bip-daily-x today < /dev/null
Error: previous failed run found (2026-05-21_231116, phase 'post'); pass --no-resume to start fresh, or run interactively to resume
```

## Development

See [Design Document](../docs/zipsa-python-design.md) for architecture details.
