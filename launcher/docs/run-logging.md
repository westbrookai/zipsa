# SKILL Run Logging

## Overview

Every SKILL execution automatically saves its output to a timestamped directory for future reference, analytics, and skill memory.

## Directory Structure

```
~/.zipsa/<name>@<version>/runs/<YYYY-MM-DD_HHMMSS_microseconds>/
  ├── output.jsonl      # Complete stdout from container (real-time)
  ├── events.jsonl     # Filtered important events (post-execution)
  └── summary.json      # Single-object run outcome (real-time, schema v1)
```

**Example:**
```
~/.zipsa/weather@0.3.1/runs/2026-05-19_113200_45358/
  ├── output.jsonl      # All Claude Code stream-json events
  ├── events.jsonl     # system init, assistant, user, result events only
  └── summary.json      # status, cost, turns, error, usage, model_usage, etc.
```

> **Note (May 2026):** `summary.json` absorbed the old `metadata.json` —
> see PR `chore: merge metadata.json into summary.json`. Pre-May 2026
> run dirs may still have `metadata.json` instead; `zipsa list` reads
> either.

## File Descriptions

### output.jsonl

Complete Docker stdout saved in real-time (line-buffered). Contains all Claude Code stream-json events:

- `system` (init, rate_limit_event, etc.)
- `assistant` (thinking, tool_use, text)
- `user` (tool_result)
- `result` (final summary with metrics)
- Any error events

**Use cases:**
- Debugging execution flow
- Understanding tool usage patterns
- Analyzing thinking process

### events.jsonl

Filtered version of output.jsonl containing only important events:

- `system` (init only, not rate_limit_event)
- `assistant` (all messages)
- `user` (all messages, including tool_result)
- `result` (final summary)
- Any event with "error" in type

**Use cases:**
- Quick review of execution
- Conversation replay
- Context for future runs

### summary.json

Single-object structured run outcome. The launcher exit code matches
the `exit_code` field; a parent meta-skill can read this one file to
branch on what happened. Schema is versioned (`schema_version`).

```json
{
  "schema_version": 1,
  "status": "ok",
  "exit_code": 0,
  "skill": "weather",
  "version": "0.3.1",
  "started_at": "2026-05-19T11:32:00+10:00",
  "finished_at": "2026-05-19T11:32:18+10:00",
  "duration_seconds": 18.3,
  "cost_usd": 0.0707,
  "turns": 2,
  "phases": [
    {"id": "main", "status": "ok", "cost_usd": 0.07, "turns": 2}
  ],
  "result": {"temp_C": 19, "city": "Sydney"},
  "error": null,
  "user_input": "시드니 날씨",
  "stop_reason": "end_turn",
  "usage": {
    "input_tokens": 7, "output_tokens": 302,
    "cache_creation_input_tokens": 18324,
    "cache_read_input_tokens": 35477
  },
  "model_usage": {
    "claude-sonnet-4-6": {
      "inputTokens": 7, "outputTokens": 302,
      "cacheReadInputTokens": 35477, "costUSD": 0.083909
    }
  }
}
```

**Status / exit code mapping:**

| status | exit_code | meaning |
|---|---|---|
| `ok` | 0 | run succeeded; `result` populated, `error` null |
| `failed` | 1 | business failure (skill returned `status=failed`) |
| `out_of_scope` | 2 | skill refused the request |
| `limits_exceeded` | 3 | launcher killed the run on a cost/time/turn breach |
| `user_declined` | 4 | HITL `confirm` no, or `HITL_UNATTENDED` |
| `infra_failed` | 5 | Docker crashed or some other infra-level failure |
| (no summary, exit 130) | — | Ctrl+C |

For non-ok statuses, `error` is populated with `{code, message, details}`. `result` is null.

**Use cases:**
- Cost analysis
- Performance monitoring
- Token usage optimization

## Usage

### Automatic Logging

Logging happens automatically on every skill execution:

```bash
zipsa run weather "What's the weather in Tokyo?"
```

Logs are saved to `zipsa-skills/weather/.zipsa/runs/<timestamp>/`

### Dry-Run Mode

Dry-run mode does NOT create logs:

```bash
zipsa run weather "test" --dry-run
```

### Accessing Logs

**List all runs for a skill:**
```bash
ls -lt zipsa-skills/weather/.zipsa/runs/
```

**View latest run outcome:**
```bash
cat ~/.zipsa/weather@0.3.1/runs/$(ls -t ~/.zipsa/weather@0.3.1/runs/ | head -1)/summary.json | jq .
```

**View latest run event log (the JSONL stream):**
```bash
cat ~/.zipsa/weather@0.3.1/runs/$(ls -t ~/.zipsa/weather@0.3.1/runs/ | head -1)/events.jsonl
```

**Calculate total cost for all runs of a skill:**
```bash
find ~/.zipsa/weather@0.3.1/runs -name "summary.json" -exec jq -r '.cost_usd // 0' {} \; | awk '{sum+=$1} END {print sum}'
```

## Error Handling

### Execution Failures

If Docker execution fails:
- `output.jsonl` contains partial output (already saved in real-time)
- `events.jsonl` is generated from whatever was streamed
- `summary.json` is written from in-memory run state with
  `status="infra_failed"` (or another non-ok status depending on the
  failure mode) and `error` populated.
- Warning printed to stderr (execution still fails as expected).

**Example error summary:**
```json
{
  "schema_version": 1,
  "status": "infra_failed",
  "exit_code": 5,
  "skill": "weather",
  "version": "0.3.1",
  "error": {
    "code": "docker_failed",
    "message": "Docker exited with non-zero code"
  }
}
```

### Logging Failures

If logging itself fails (disk full, permission denied):
- Warning printed to stderr
- Skill execution continues (doesn't fail due to logging errors)

## Future Enhancements

### Phase 2: Run Querying (Planned)

- MCP server for querying past runs
- CLI commands:
  - `zipsa runs <skill_name>` - list runs
  - `zipsa analyze <run_id>` - show detailed metrics
- System prompt injection (past context)

### Phase 3: Run Management (Planned)

- Retention policies (auto-delete old runs)
- Run tagging/naming
- Export to CSV/JSON formats

## Implementation Details

### Timestamp Format

`YYYY-MM-DD_HHMMSS_microseconds` (23 characters total)

- Sortable chronologically
- Microseconds prevent collision during concurrent executions
- Format: `datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")[:23]`

### Real-Time Saving

`output.jsonl` is saved in real-time using line buffering (`buffering=1`):
- No memory overhead
- Partial logs preserved on failure
- Docker stdout directly written to file

### Post-Processing

`events.jsonl` is generated after execution from `output.jsonl` (filter
the useful events). `summary.json` is generated from in-memory state
tracked DURING the run (status, cost, turns, errors) plus the captured
Claude SDK `result` event (usage, model_usage, stop_reason). Both are
written in the executor's finally block.

## Troubleshooting

**Q: No logs directory created**
- Check if running in dry-run mode (`--dry-run`)
- Verify skill directory has write permissions

**Q: Empty output.jsonl**
- Check if Docker execution started successfully
- Verify runtime (Claude Code) is producing output

**Q: summary.json shows status != "ok"**
- Check `error.code` and `error.message` for details
- Status maps: `failed` (skill returned status=failed), `out_of_scope`
  (skill refused), `limits_exceeded` (cost/time/turn breach),
  `user_declined` (HITL no / unattended), `infra_failed` (Docker
  crash or no result event)
- For `infra_failed`: review `output.jsonl` for execution traces

**Q: How to prevent logs from being committed to git?**
- Add `.zipsa/runs/` to skill's `.gitignore`
- Already included in weather skill template

## References

- Design spec: `docs/specs/2026-05-04-run-logging-design.md`
- Research notes: `docs/research-notes-run-logging.md`
- Test suite: `tests/test_run_logger.py`
