# SKILL Run Logging and Analytics Design

**Date:** 2026-05-04
**Status:** Approved for implementation

## Overview

Add execution logging to zipsa launcher that saves run outputs to skill-specific directories. Each execution creates a timestamped folder containing raw output, filtered summary, and extracted metrics.

## Goals

1. Save all execution outputs for future reference
2. Enable skill-specific memory and context (skills can reference past executions)
3. Extract meaningful metrics (tokens, cost, duration, turns)
4. Support future MCP-based run querying

## Non-Goals (Future Work)

- MCP server for querying past runs
- Automatic context injection from previous runs
- CLI commands for run analysis
- Retention policies

## Architecture

### High-Level Flow

```
Docker container stdout
    ↓
DockerExecutor._execute_skill()
    ↓ (simultaneously)
    ├─→ output.jsonl (real-time file write)
    └─→ yield event (stream to CLI)
    ↓
After completion:
    ├─→ summary.jsonl (filter important events)
    └─→ metadata.json (extract metrics)
```

### Directory Structure

```
<skill_dir>/.zipsa/runs/<YYYY-MM-DD_HHMMSS_microseconds>/
  ├── output.jsonl      # Complete stdout from container (real-time)
  ├── summary.jsonl     # Filtered important events (post-execution)
  └── metadata.json     # Extracted metrics (post-execution)
```

**Example:**
```
zipsa-skills/weather/.zipsa/runs/2026-05-04_090443_123456/
  ├── output.jsonl
  ├── summary.jsonl
  └── metadata.json
```

### Timestamp Format

`YYYY-MM-DD_HHMMSS_microseconds`

- Sortable chronologically
- Microseconds prevent collision during concurrent executions
- Format: `datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")[:23]`

## Component Design

### 1. Run Directory Creation

**Location:** `DockerExecutor.run()` - beginning of method

**Implementation:**
```python
timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")[:23]
run_dir = skill.skill_dir / ".zipsa" / "runs" / timestamp
run_dir.mkdir(parents=True, exist_ok=True)
```

**Pass to:** `_execute_skill(docker_cmd, mcp_config_path, run_dir)`

### 2. output.jsonl - Real-Time Saving

**Location:** `DockerExecutor._execute_skill()`

**Implementation:**
```python
output_file = run_dir / "output.jsonl"

with open(output_file, 'w', buffering=1) as f:  # Line buffering
    for line in process.stdout:
        # Write to file immediately
        f.write(line)

        # Parse and yield (existing logic)
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            yield event
        except json.JSONDecodeError:
            yield {"type": "text", "content": line}
```

**Benefits:**
- No memory overhead
- Partial logs preserved on failure
- Docker stdout directly saved

### 3. summary.jsonl - Important Events Only

**Location:** New method `DockerExecutor._save_summary(run_dir)`

**Filter Criteria:**
- `type == "system"` AND `subtype == "init"`
- `type == "assistant"` (all assistant messages)
- `type == "user"` (includes tool_result)
- `type == "result"` (final summary)
- `"error"` in `type` (any error events)

**Implementation:**
```python
def _save_summary(self, run_dir: Path):
    """Generate summary.jsonl from output.jsonl."""
    output_file = run_dir / "output.jsonl"
    summary_file = run_dir / "summary.jsonl"

    important_types = {"system", "assistant", "user", "result"}

    with open(output_file, 'r') as inf, open(summary_file, 'w') as outf:
        for line in inf:
            try:
                event = json.loads(line)
                event_type = event.get("type", "")

                # Include important events
                if (event_type in important_types or
                    "error" in event_type):
                    # For system events, only keep init
                    if event_type == "system":
                        if event.get("subtype") == "init":
                            outf.write(line)
                    else:
                        outf.write(line)
            except json.JSONDecodeError:
                continue
```

**Called:** In `_execute_skill()` finally block (after stream completes)

### 4. metadata.json - Metrics Extraction

**Location:** New method `DockerExecutor._save_metadata(run_dir, skill)`

**Extracted Metrics:**

From `result` event:
```json
{
  "run_id": "2026-05-04_090443_123456",
  "skill_name": "weather",
  "skill_version": "1.0.0",
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
    "cache_read_input_tokens": 35477,
    "web_search_requests": 0,
    "web_fetch_requests": 0
  },
  "model_usage": {
    "claude-haiku-4-5-20251001": {
      "input_tokens": 14522,
      "output_tokens": 123,
      "cost_usd": 0.015137
    },
    "claude-sonnet-4-6": {
      "input_tokens": 7,
      "output_tokens": 302,
      "cache_read_input_tokens": 35477,
      "cost_usd": 0.083909
    }
  }
}
```

**Implementation:**
```python
def _save_metadata(self, run_dir: Path, skill: Skill):
    """Extract metrics from output.jsonl and save to metadata.json."""
    output_file = run_dir / "output.jsonl"
    metadata_file = run_dir / "metadata.json"

    # Find result event
    result_event = None
    with open(output_file, 'r') as f:
        for line in f:
            try:
                event = json.loads(line)
                if event.get("type") == "result":
                    result_event = event
                    break
            except json.JSONDecodeError:
                continue

    if not result_event:
        # Execution failed before result
        metadata = {
            "run_id": run_dir.name,
            "skill_name": skill.name,
            "skill_version": skill.manifest.metadata.version,
            "timestamp": datetime.now().isoformat(),
            "is_error": True,
            "error": "No result event found"
        }
    else:
        # Extract from result event
        metadata = {
            "run_id": run_dir.name,
            "skill_name": skill.name,
            "skill_version": skill.manifest.metadata.version,
            "timestamp": datetime.now().isoformat(),
            "duration_ms": result_event.get("duration_ms"),
            "duration_api_ms": result_event.get("duration_api_ms"),
            "num_turns": result_event.get("num_turns"),
            "total_cost_usd": result_event.get("total_cost_usd"),
            "is_error": result_event.get("is_error", False),
            "stop_reason": result_event.get("stop_reason"),
            "terminal_reason": result_event.get("terminal_reason"),
            "usage": result_event.get("usage", {}),
            "model_usage": result_event.get("modelUsage", {})
        }

    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2)
```

**Called:** In `_execute_skill()` finally block

## Error Handling

### 1. Execution Failures

**Scenario:** Docker container fails (auth error, timeout, crash)

**Behavior:**
- `output.jsonl` contains partial output (already saved)
- `summary.jsonl` and `metadata.json` generated from available data
- `metadata.json` sets `is_error: true`
- No exception thrown from logging (catch and log internally)

### 2. Dry-Run Mode

**Behavior:** No logging when `dry_run=True`

**Reason:** Dry-run doesn't execute, so no real run data to log

**Implementation:** Skip run_dir creation and all logging

### 3. File Write Errors

**Scenario:** Disk full, permission denied

**Behavior:**
- Catch exception in logging code
- Log warning to stderr
- Continue execution (don't fail skill run due to logging)

### 4. Concurrent Executions

**Protection:** Microsecond-precision timestamps prevent collisions

**Edge Case:** If collision occurs (extremely rare):
- Second execution overwrites first
- Acceptable trade-off vs UUID complexity

## File Changes

### Modified Files

1. **zipsa/core/executor.py**
   - `run()`: Create run_dir, pass to _execute_skill
   - `_execute_skill()`: Save output.jsonl real-time, call summary/metadata methods
   - Add: `_save_summary()`, `_save_metadata()`

2. **zipsa/core/skill.py**
   - Verify `skill_dir` property exists (should already exist)

3. **.gitignore** (in each skill directory)
   - Add: `.zipsa/runs/`

### New Files

None

## Testing Strategy

### Unit Tests (zipsa-launcher/tests/test_run_logger.py)

1. **test_run_dir_creation**
   - Verify directory created with correct timestamp format
   - Check `.zipsa/runs/<timestamp>/` structure

2. **test_output_jsonl_saved**
   - Mock Docker output
   - Verify all lines written to output.jsonl

3. **test_summary_filtering**
   - Create test output.jsonl with various event types
   - Verify summary.jsonl contains only important events

4. **test_metadata_extraction**
   - Create test output.jsonl with result event
   - Verify metadata.json has correct metrics

5. **test_dry_run_no_logging**
   - Run with dry_run=True
   - Verify no run directory created

6. **test_error_partial_log**
   - Simulate Docker failure mid-execution
   - Verify output.jsonl has partial data
   - Verify metadata.json marks is_error=true

### Integration Test

1. **test_weather_skill_logging**
   - Actually run weather skill
   - Verify all three files created
   - Spot-check metadata accuracy

## Migration

No migration needed - new feature, no breaking changes.

## Future Enhancements

### Phase 2: Run Querying (not in this design)

1. MCP server for querying past runs
2. CLI commands:
   - `zipsa runs <skill_name>` - list runs
   - `zipsa analyze <run_id>` - show metrics
3. System prompt injection (past context)

### Phase 3: Run Management

1. Retention policies (auto-delete old runs)
2. Run tagging/naming
3. Export to other formats

## References

- Research notes: `docs/research-notes-run-logging.md`
- Claude Code stream-json format: See research notes for event structure
- Example output: `/tmp/weather-output-bash.txt`
