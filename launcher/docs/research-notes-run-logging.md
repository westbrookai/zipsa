# Research Notes: SKILL Run Logging & Analytics

**Date:** 2026-05-04
**Purpose:** Document findings for implementing run logging and metrics extraction

## User Requirements

1. Save execution results to timestamped folders: `<skill_dir>/.zipsa/runs/YYYY-MM-DD_HHMMSS/`
2. Store JSONL outputs in two files:
   - `output.jsonl` - complete stream-json output
   - `summary.jsonl` - filtered important events only
3. Extract meaningful metrics from execution data
4. Format and display turn-by-turn information

## Use Cases

- **Periodic Skills**: Skills like `bookkeeper-xero` that run regularly and need to reference past executions
- **Memory/Context**: Each skill maintains its own execution history as memory
- **Future**: MCP server for querying past runs (not in initial implementation)

## Claude Code Stream-JSON Output Structure

### Event Types Observed

From actual weather skill execution (`./zipsa.sh weather "What's the weather in Tokyo?"`):

#### 1. `system` (init)
```json
{
  "type": "system",
  "subtype": "init",
  "session_id": "d2b8ba8c-a5ab-45be-93b8-1605e6fdb5d3",
  "tools": ["Task", "WebFetch", "..."],
  "mcp_servers": [],
  "model": "claude-sonnet-4-6",
  "permissionMode": "bypassPermissions",
  "claude_code_version": "2.1.114",
  "agents": ["Explore", "general-purpose", "Plan", "statusline-setup"],
  "skills": ["update-config", "debug", "..."]
}
```

#### 2. `rate_limit_event`
```json
{
  "type": "rate_limit_event",
  "rate_limit_info": {
    "status": "allowed",
    "resetsAt": 1777886400,
    "rateLimitType": "five_hour",
    "overageStatus": "rejected"
  }
}
```

#### 3. `assistant` (with thinking)
```json
{
  "type": "assistant",
  "message": {
    "model": "claude-sonnet-4-6",
    "id": "msg_013eZux26jbrKLMZEy2AXEYp",
    "role": "assistant",
    "content": [
      {
        "type": "thinking",
        "thinking": "The user wants to know the current weather in Tokyo...",
        "signature": "..."
      }
    ],
    "usage": {
      "input_tokens": 3,
      "cache_creation_input_tokens": 17421,
      "cache_read_input_tokens": 0,
      "output_tokens": 0
    }
  }
}
```

#### 4. `assistant` (with tool_use)
```json
{
  "type": "assistant",
  "message": {
    "content": [
      {
        "type": "tool_use",
        "id": "toolu_01Xzv4mYt3t72CxE3sRxPpbE",
        "name": "ToolSearch",
        "input": {"query": "select:WebFetch", "max_results": 1}
      }
    ]
  }
}
```

#### 5. `user` (tool_result)
```json
{
  "type": "user",
  "message": {
    "role": "user",
    "content": [
      {
        "type": "tool_result",
        "tool_use_id": "toolu_01Xzv4mYt3t72CxE3sRxPpbE",
        "content": [{"type": "tool_reference", "tool_name": "WebFetch"}]
      }
    ]
  },
  "timestamp": "2026-05-04T09:04:43.557Z",
  "tool_use_result": {
    "matches": ["WebFetch"],
    "query": "select:WebFetch"
  }
}
```

#### 6. `assistant` (final text)
```json
{
  "type": "assistant",
  "message": {
    "content": [
      {
        "type": "text",
        "text": "Tokyo is currently 20°C and partly cloudy..."
      }
    ]
  }
}
```

#### 7. `result` (final summary - MOST IMPORTANT)
```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "duration_ms": 15562,
  "duration_api_ms": 14586,
  "num_turns": 3,
  "result": "Tokyo is currently 20°C and partly cloudy...",
  "stop_reason": "end_turn",
  "session_id": "d2b8ba8c-a5ab-45be-93b8-1605e6fdb5d3",
  "total_cost_usd": 0.09904609999999998,
  "usage": {
    "input_tokens": 7,
    "cache_creation_input_tokens": 18324,
    "cache_read_input_tokens": 35477,
    "output_tokens": 302,
    "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
    "cache_creation": {
      "ephemeral_1h_input_tokens": 18324,
      "ephemeral_5m_input_tokens": 0
    },
    "iterations": [...]
  },
  "modelUsage": {
    "claude-haiku-4-5-20251001": {
      "inputTokens": 14522,
      "outputTokens": 123,
      "cacheReadInputTokens": 0,
      "cacheCreationInputTokens": 0,
      "costUSD": 0.015137000000000001
    },
    "claude-sonnet-4-6": {
      "inputTokens": 7,
      "outputTokens": 302,
      "cacheReadInputTokens": 35477,
      "cacheCreationInputTokens": 18324,
      "costUSD": 0.08390909999999999
    }
  },
  "permission_denials": [],
  "terminal_reason": "completed"
}
```

## Extractable Metrics

### High-Level Metrics (from `result` event)
- `num_turns`: Number of conversation turns (3 in example)
- `duration_ms`: Total execution time in milliseconds (15562)
- `duration_api_ms`: API call time (14586)
- `total_cost_usd`: Total cost in USD (0.099)
- `stop_reason`: Why execution stopped (end_turn, error, etc.)
- `is_error`: Whether execution failed
- `terminal_reason`: completed, error, timeout, etc.

### Token Usage (from `usage` object)
- `input_tokens`: 7
- `output_tokens`: 302
- `cache_creation_input_tokens`: 18324
- `cache_read_input_tokens`: 35477
- `server_tool_use.web_search_requests`: 0
- `server_tool_use.web_fetch_requests`: 0

### Model-Specific Usage (from `modelUsage`)
- Per-model breakdown (haiku vs sonnet)
- Individual costs per model
- Cache hit rates

### Turn-by-Turn Information
From `usage.iterations[]` array - detailed per-turn metrics

### Tool Usage
- Tool names from `tool_use` events
- Tool results and timing from `tool_use_result` objects
- Success/failure status

## Important Events for `summary.jsonl`

Recommended filter:
- `system` (init only)
- `assistant` (text and tool_use, optionally thinking)
- `user` (tool_result)
- `result` (final summary)
- Any `error` events

Skip:
- `rate_limit_event` (unless needed for debugging)
- Duplicate/partial messages

## Comparison: Claude Code Session Logs vs SKILL Run Logs

### Claude Code Logs (`~/.claude/projects/<project-path>/<uuid>.jsonl`)
- **Purpose**: Session resumption, internal state
- **Content**: File history snapshots, tracked file backups, conversation state
- **Location**: `~/.claude/projects/` (not configurable)
- **Structure**:
  ```json
  {
    "type": "file-history-snapshot",
    "messageId": "...",
    "snapshot": {
      "trackedFileBackups": {},
      "timestamp": "..."
    }
  }
  ```

### SKILL Run Logs (our implementation)
- **Purpose**: Skill execution memory, analytics, monitoring
- **Content**: Stream-json output, metrics, turn data
- **Location**: `<skill_dir>/.zipsa/runs/<timestamp>/` (skill-specific)
- **Structure**: Raw stream-json + extracted metrics

**Key Difference**: Claude Code logs are for session state, our logs are for skill memory and analysis.

## Storage Structure

```
zipsa-skills/
  weather/
    manifest.yaml
    SKILL.md
    .zipsa/
      runs/
        2026-05-04_090443/
          output.jsonl        # Full stream-json
          summary.jsonl       # Important events only
          metadata.json       # Extracted metrics
```

## Future Features (Not in Initial Implementation)

- MCP server for querying past runs
- Automatic context injection from previous runs
- Run comparison and analytics dashboard

## Questions Remaining

- Output display format (CLI, web UI, etc.)
- Retention policy (how long to keep runs)
- Run naming/tagging beyond timestamps
- Error handling and partial run storage
