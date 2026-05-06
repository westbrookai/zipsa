# SKILL Run Logging

## Overview

Every SKILL execution automatically saves its output to a timestamped directory for future reference, analytics, and skill memory.

## Directory Structure

```
<skill_dir>/.zipsa/runs/<YYYY-MM-DD_HHMMSS_microseconds>/
  ├── output.jsonl      # Complete stdout from container (real-time)
  ├── summary.jsonl     # Filtered important events (post-execution)
  └── metadata.json     # Extracted metrics (post-execution)
```

**Example:**
```
zipsa-skills/weather/.zipsa/runs/2026-05-04_090443_123456/
  ├── output.jsonl      # All Claude Code stream-json events
  ├── summary.jsonl     # system init, assistant, user, result events only
  └── metadata.json     # Tokens, cost, duration, turns
```

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

### summary.jsonl

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

### metadata.json

Extracted metrics from the `result` event:

```json
{
  "run_id": "2026-05-04_090443_123456",
  "skill_name": "weather",
  "skill_version": "0.1.0",
  "timestamp": "2026-05-04T09:04:43Z",
  "duration_ms": 15562,
  "duration_api_ms": 14586,
  "num_turns": 3,
  "total_cost_usd": 0.099,
  "is_error": false,
  "stop_reason": "end_turn",
  "terminal_reason": "completed",
  "usage": {
    "input_tokens": 7,
    "output_tokens": 302,
    "cache_creation_input_tokens": 18324,
    "cache_read_input_tokens": 35477
  },
  "model_usage": {
    "claude-sonnet-4-6": {
      "input_tokens": 7,
      "output_tokens": 302,
      "cache_read_input_tokens": 35477,
      "cost_usd": 0.083909
    }
  }
}
```

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

**View latest run metadata:**
```bash
cat zipsa-skills/weather/.zipsa/runs/$(ls -t zipsa-skills/weather/.zipsa/runs/ | head -1)/metadata.json | jq .
```

**View latest run summary:**
```bash
cat zipsa-skills/weather/.zipsa/runs/$(ls -t zipsa-skills/weather/.zipsa/runs/ | head -1)/summary.jsonl
```

**Calculate total cost for all runs:**
```bash
find zipsa-skills/weather/.zipsa/runs -name "metadata.json" -exec jq -r '.total_cost_usd // 0' {} \; | awk '{sum+=$1} END {print sum}'
```

## Error Handling

### Execution Failures

If Docker execution fails:
- `output.jsonl` contains partial output (already saved in real-time)
- `summary.jsonl` and `metadata.json` are still generated from available data
- `metadata.json` sets `is_error: true`
- Warning printed to stderr (execution still fails as expected)

**Example error metadata:**
```json
{
  "run_id": "2026-05-04_101530_456789",
  "skill_name": "weather",
  "skill_version": "0.1.0",
  "timestamp": "2026-05-04T10:15:30Z",
  "is_error": true,
  "error": "No result event found - execution may have failed"
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

`summary.jsonl` and `metadata.json` are generated after execution completes:
- Read from `output.jsonl`
- Filter/extract relevant data
- Written in finally block (always runs)

## Troubleshooting

**Q: No logs directory created**
- Check if running in dry-run mode (`--dry-run`)
- Verify skill directory has write permissions

**Q: Empty output.jsonl**
- Check if Docker execution started successfully
- Verify runtime (Claude Code) is producing output

**Q: metadata.json shows is_error: true**
- Check `error` field for details
- Review `output.jsonl` for execution traces
- Common cause: Docker execution failed before `result` event

**Q: How to prevent logs from being committed to git?**
- Add `.zipsa/runs/` to skill's `.gitignore`
- Already included in weather skill template

## References

- Design spec: `docs/specs/2026-05-04-run-logging-design.md`
- Research notes: `docs/research-notes-run-logging.md`
- Test suite: `tests/test_run_logger.py`
