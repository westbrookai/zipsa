# Daily Progress Skill

Summarize Claude Code work for a single day across all projects and log
it to a Notion database in the Westbrook AI HQ workspace.

## Purpose

For a target date (default: yesterday), find all Claude Code sessions
that had activity on that date, group them by project, summarize the
work, and write one entry per project to the `zipsa-daily-log` Notion
database.

## Period semantics

- Default target date: **yesterday**, in the configured timezone
  (`Australia/Sydney`).
- The user may specify another single date in their query, including
  relative terms ("yesterday", "today", or a specific ISO date like
  `2026-05-10`).
- **Multi-day ranges are not supported.** If the user requests more
  than one day, stop the precheck phase with status=out_of_scope and
  briefly explain only single-day summaries are supported.

## Configuration values

These are provided in the skill manifest:

- Workspace name: `Westbrook AI HQ`
- Database name: `zipsa-daily-log`
- Timezone: `Australia/Sydney`

## Phases

### precheck

Verify everything needed to run is in place, parse the user query for a
target date, and prepare the Notion database reference.

Steps:

1. **MCP availability**: call `mcp__notion__notion-search` directly
   with a minimal query (e.g. `query="zipsa-daily-log"`). If the call
   returns an error or "No such tool available", stop with
   status=failed and `error.code="mcp_unavailable"`.
2. **Resolve target date**:
   - Parse the user query for an explicit date or relative term.
   - If the user asks for more than one day, stop with
     status=out_of_scope.
   - If no date is mentioned, use yesterday in the configured timezone.
3. **Resolve database**:
   - If `skill_state.db_id` is set, validate it with
     `mcp__notion__notion-fetch`. If valid, reuse.
   - Otherwise, search with `mcp__notion__notion-search` for a database
     titled `zipsa-daily-log`.
   - If still not found, search for a page titled `Westbrook AI HQ`. If
     found, create the database under that page. If not, create the
     page first, then the database under it.
   - Database schema:
     - `Date` (date)
     - `Project` (title)
     - `Summary` (rich_text)
     - `Sessions` (number)
     - `Tools used` (multi_select)
     - `Status` (select: in-progress, blocked, done, paused)

`next_phase_input` schema:

    {
      "target_date": "2026-05-10",
      "db_id": "<notion database id>",
      "timezone": "Australia/Sydney"
    }

`state_updates`: set `db_id` if newly resolved or created.

### discover

Find all Claude Code session files that had activity on the target
date.

The sessions directory is mounted at `/home/agent/workspace/sessions`.
Each subdirectory corresponds to one project; the directory name is the
project's working directory with non-alphanumeric characters replaced
by `-`. Example: `-Users-neochoon-WestbrookAI-skill-runtime-poc`.

Each project subdirectory contains `*.jsonl` session files (one per
session). Only files **directly inside** the project subdirectory are
session files — ignore any files nested deeper.

Steps:

1. Convert the target date to a UTC time window using Bash:

   ```bash
   python3 -c "
   from datetime import datetime
   import zoneinfo
   tz = zoneinfo.ZoneInfo('Australia/Sydney')
   d = datetime.strptime('TARGET_DATE', '%Y-%m-%d')
   start = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz)
   end   = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz)
   print(start.timestamp(), end.timestamp())
   "
   ```

2. Use `find` to list all `*.jsonl` files modified within that window:

   ```bash
   find /home/agent/workspace/sessions \
     -maxdepth 2 -name '*.jsonl' \
     -newer <start_sentinel> ! -newer <end_sentinel>
   ```

   To use timestamps with `find -newer`, create temporary sentinel
   files with `touch -d`:

   ```bash
   touch -d @<start_ts> /tmp/zipsa_start
   touch -d @<end_ts>   /tmp/zipsa_end
   find /home/agent/workspace/sessions \
     -maxdepth 2 -name '*.jsonl' \
     -newer /tmp/zipsa_start ! -newer /tmp/zipsa_end
   rm /tmp/zipsa_start /tmp/zipsa_end
   ```

3. From each matching path, extract the project directory name (the
   component at depth 1 under sessions root) and group files by it.

`next_phase_input` schema:

    {
      "target_date": "2026-05-10",
      "db_id": "...",
      "session_files": [
        {
          "project_dir": "-Users-neochoon-WestbrookAI-skill-runtime-poc",
          "project_name": "skill-runtime-poc",
          "files": ["/path/to/session1.jsonl", "/path/to/session2.jsonl"]
        }
      ]
    }

Project name decoding: take the last meaningful path component (after
splitting on `-`). For the example above:
`-Users-neochoon-WestbrookAI-skill-runtime-poc` → `skill-runtime-poc`.

If no sessions were found, return `session_files: []`. Later phases
will short-circuit.

### analyze

Read each discovered session and extract per-project information.

For each session file:

1. Read with `mcp__sessions__read_file`. Session files can be large; if
   so, read the head (first ~80 lines) and the tail (last ~80 lines).
2. From the **head**, extract the first message where `type=user` and
   capture its `content` as the session goal.
3. From the **full content read so far**, collect distinct `tool_name`
   values from `tool_use` events.
4. From the **tail**, identify the last assistant message and any
   nearby decision-like statements as the session outcome.

Group by project. Per project, produce:

- `sessions`: count
- `summary`: 2–4 sentence narrative in the user's language combining
  goals and outcomes across all sessions
- `tools_used`: deduplicated list of distinct tool names
- `status`: best guess — `in-progress` (default), `done` (clear
  completion), `blocked` (explicit blocker), `paused` (explicit pause)

`next_phase_input` schema:

    {
      "target_date": "2026-05-10",
      "db_id": "...",
      "projects": [
        {
          "name": "skill-runtime-poc",
          "sessions": 4,
          "summary": "...",
          "tools_used": ["Bash", "Edit", "Read"],
          "status": "in-progress"
        }
      ]
    }

If `session_files` was empty, return `projects: []`.

### persist

Write one Notion entry per project for the target date.

For each project in `previous_phase_output.projects`:

1. Check for an existing entry: `mcp__notion__notion-fetch` the
   database and look for a page where `Date == target_date` and
   `Project == project_name`. (Or use `mcp__notion__notion-search` with
   appropriate filters.)
2. If an entry exists, update it with `mcp__notion__notion-update-page`:
   `Summary`, `Sessions`, `Tools used`, `Status`.
3. If not, create a new page with `mcp__notion__notion-create-pages`,
   populating all fields including `Date` and `Project`.

If `projects` is empty, skip all writes. The phase still returns
status=ok.

`result` schema (final phase, surfaces to the user):

    {
      "target_date": "2026-05-10",
      "entries_created": <count>,
      "entries_updated": <count>,
      "db_url": "<notion database URL>"
    }

`state_updates`: set `last_run_date` to the target date.

## Behavior rules

- **Read-only sessions mount**: never attempt to modify session files.
- **Notion scope**: only touch the configured database and (if needed)
  the `Westbrook AI HQ` parent page. Never touch other databases or
  pages.
- **Concise reporting**: `user_facing_summary` should be 3 sentences
  or fewer.

## Standard messages

Use these phrasings in `user_facing_summary` (in the user's language)
for the common cases:

- Notion not connected: "Notion is not connected. Run: zipsa connect notion"
- Sessions directory empty: "No Claude Code sessions found in the projects directory."
- No sessions on target date: "No Claude Code activity on <date>."
- Multi-day request: "Only single-day summaries are supported. Specify one date."
