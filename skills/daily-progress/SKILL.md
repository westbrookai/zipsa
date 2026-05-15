# Daily Progress Skill

Summarize Claude Code work for a single day across all projects and log
it to a Notion database in the Westbrook AI HQ workspace.

## Purpose

For a target date (default: yesterday), find all Claude Code sessions
that had activity on that date, group them by project, summarize the
work, and write one entry per project to the `zipsa-daily-log` Notion
database.

The heavy lifting (scanning session JSONL files, grouping by project,
extracting activities) is delegated to **agenthud**, a deterministic CLI
tool that produces a structured per-project report. The agent then
summarizes and writes to Notion.

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
- agenthud version: `0.8.4` (pinned for reproducibility)

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

### report

Fetch a structured per-project activity report from agenthud, then
group/summarize for Notion.

Steps:

1. Invoke agenthud via Bash:

   ```bash
   npx agenthud@0.8.4 report \
     --date <target_date> \
     --format json \
     --include response,bash,edit,thinking \
     --detail-limit 0
   ```

   Notes:
   - `~/.claude/projects` is bind-mounted into the container at the
     default path agenthud expects, so no env var setup is needed.
   - The command output is a JSON document with shape
     `{date, sessions: [{project, start, end, activities, subAgents}]}`.
     Capture stdout in full.
   - If no sessions match the date, agenthud emits a document with
     `sessions: []`. Treat that as "no activity on that date" and pass
     `projects: []` to the next phase.

2. For each project in the report, build a summary tuple:
   - `name`: the `project` field
   - `sessions`: 1 if the project appears once; if the same project
     name appears as multiple distinct sessions (rare — agenthud groups
     per session id then re-groups by project), sum them
   - `summary`: 2–4 sentence narrative in the user's language,
     synthesized from the `activities` (Response messages convey
     intent; Edit/Bash convey what changed)
   - `tools_used`: deduplicated set of `activity.label` values from
     Bash/Edit/etc. (drop Thinking and Response; keep the
     tool-execution labels)
   - `status`: best guess from the activity tail —
     `done` (clear completion), `blocked` (explicit blocker mentioned),
     `paused` (explicit pause), `in-progress` (default)

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

If `sessions: []`, return `projects: []`. The persist phase will
short-circuit.

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
