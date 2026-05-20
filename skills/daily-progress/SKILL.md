# Daily Progress Skill

Summarize Claude Code work for a single day across all projects and log
it to a Notion database in the user's workspace.

## Purpose

For a target date (default: yesterday), find all Claude Code sessions
that had activity on that date, group them by project, summarize the
work, and write one entry per project to the user's configured Notion
daily-log database.

The heavy lifting (scanning session JSONL files, grouping by project,
extracting activities) is delegated to **agenthud**, a deterministic CLI
tool that produces a structured per-project report. The agent then
summarizes and writes to Notion.

## Per-user setup

Three values are user-specific.

**Launcher-resolved (before container starts):**

- **`project_roots`** — directories containing the user's git projects.
  Declared as `spec.requires.project_roots` in the manifest. The
  launcher prompts for these on first `zipsa run` (or via
  `zipsa configure daily-progress`) and saves them at
  `~/.zipsa/daily-progress@<version>/requires.yaml`. Each path is
  mounted at its own absolute host path inside the container so
  `agenthud --with-git` can resolve each session's `cwd → .git` lookup.
  This skill never prompts for project_roots itself — the values are
  present at startup or the run aborts (exit 4).

**Agent-time prompts (remembered in skill memory):**

- **Notion workspace name** — the top-level Notion workspace where the
  daily-log database should live (e.g. a parent page that holds the DB).
  Stable key: `notion_workspace`.
- **Notion database name** — the title of the database to write rows
  into. Will be created if it doesn't exist. Stable key:
  `notion_db_name` (suggest something like `zipsa-daily-log` as a
  default in the prompt, but accept whatever the user types).

Phrase the Notion prompts in the user's language.

## Period semantics

- Default target date: **yesterday**, in the user's local timezone.
- The user may specify another single date in their query, including
  relative terms ("yesterday", "today", or a specific ISO date like
  `2026-05-10`).
- **Multi-day ranges are not supported.** If the user requests more
  than one day, stop the precheck phase with status=out_of_scope and
  briefly explain only single-day summaries are supported.

## Skill-author defaults

These come from the manifest (`spec.config`):

- `default_target_date`: `yesterday`
- `agenthud_version`: `0.9.2` (pinned for reproducibility)

## Phases

### precheck

Verify everything needed to run is in place, parse the user query for a
target date, and prepare the Notion database reference.

Steps:

1. **MCP availability**: call `mcp__notion__notion-search` directly
   with a minimal query (anything non-empty works — the goal is to
   verify the server responds). If the call returns an error or
   "No such tool available", stop with status=failed and
   `error.code="mcp_unavailable"`.
2. **Resolve target date**:
   - Parse the user query for an explicit date or relative term.
   - If the user asks for more than one day, stop with
     status=out_of_scope.
   - If no date is mentioned, use yesterday in the user's local
     timezone.
3. **Resolve workspace + database name** (per-user setup):
   - Ask once for `notion_workspace` and `notion_db_name` if not
     already remembered. See the "Per-user setup" section above.
4. **Resolve database**:
   - If `skill_state.db_id` is set, call `mcp__notion__notion-fetch` on
     it. The response is the database object; capture both its id and
     `data_sources[0].id`. If the fetch fails (404 / permission), drop
     to the search step.
   - Otherwise (or after a failed fetch), search with
     `mcp__notion__notion-search` for a database titled with the
     remembered `notion_db_name`. From a hit, fetch the database object
     and capture both ids as above.
   - If still not found, search for a page titled with the remembered
     `notion_workspace`. If found, create the database under that page.
     If not, create the page first, then the database under it. The
     `notion-create-database` response includes the data source —
     capture both ids.
   - Database schema:
     - `Date` (date)
     - `Project` (title)
     - `Summary` (rich_text)
     - `Sessions` (number)
     - `Tools used` (multi_select)
     - `Status` (select: in-progress, blocked, done, paused)

**Why two ids?** Notion's MCP tool `notion-create-pages` takes a
`parent.data_source_id`, NOT a database id. They're distinct values
(one for the database container page, one for its row store). Without
the data_source id, persist cannot place rows in the database.

`next_phase_input` schema:

    {
      "target_date": "2026-05-10",
      "db_id": "<notion database id>",
      "data_source_id": "<notion data source id>"
    }

Target date must be computed in the user's local timezone (see the
runtime contract on `tz_iana`). Do not put a timezone field on
`next_phase_input` — downstream phases either don't need it or read it
themselves from `execution_context`.

`state_updates`: set `db_id` and `data_source_id` if newly resolved or
created.

### report

Fetch a structured per-project activity report from agenthud, then
group/summarize for Notion.

Steps:

1. Invoke agenthud via Bash:

   ```bash
   npx agenthud@0.9.2 report \
     --date <target_date> \
     --format json \
     --include response,bash,edit,thinking \
     --detail-limit 0 \
     --with-git
   ```

   Notes:
   - `~/.claude/projects` is bind-mounted into the container at the
     default path agenthud expects, so no env var setup is needed.
   - `--with-git` makes agenthud emit `◆` commit entries for each
     session, resolved via `git --git-dir=<session.cwd>/.git log`.
     This only works because the launcher mounts `requires.project_roots`
     at their absolute host paths (see Per-user setup). If git isn't
     available for a particular session (project not in
     `project_roots`), agenthud silently skips git for that session
     without failing — but the commit entries are part of why we run
     `--with-git` in the first place, so callers should ensure
     `project_roots` covers the projects they care about.
   - The command output is a JSON document with shape
     `{date, sessions: [{project, start, end, activities, subAgents}]}`.
     Activities for each session now include `◆` commit entries when
     git resolution succeeded. Capture stdout in full.
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

`next_phase_input` schema (pass `db_id` and `data_source_id` through
from precheck so persist doesn't have to re-discover them):

    {
      "target_date": "2026-05-10",
      "db_id": "...",
      "data_source_id": "...",
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

1. Check for an existing entry: search the data source for a row where
   `Date == target_date` and `Project == project_name`. Use
   `mcp__notion__notion-search` with a query that mentions both, or
   `mcp__notion__notion-fetch` on the data source and scan results.
2. If an entry exists, update it with `mcp__notion__notion-update-page`
   using `page_id=<row id>`, `command="update_properties"`,
   `properties={...same shape as create...}` (see below).
3. If not, create a new row using the **exact** call format below.

#### `mcp__notion__notion-create-pages` payload

The parent must reference the **data_source_id**, not the database id.
Date is split into two property keys. Multi-select values come in as a
stringified JSON array. Example:

```json
{
  "parent": {
    "type": "data_source_id",
    "data_source_id": "<previous_phase_output.data_source_id>"
  },
  "pages": [
    {
      "properties": {
        "Project": "skill-runtime-poc",
        "date:Date:start": "2026-05-14",
        "date:Date:is_datetime": 0,
        "Summary": "Two-sentence narrative...",
        "Sessions": 1,
        "Tools used": "[\"Bash\", \"Edit\", \"Read\"]",
        "Status": "in-progress"
      },
      "content": "## Markdown body — optional richer page content"
    }
  ]
}
```

Notes:
- `parent.type` MUST be `"data_source_id"`. Other forms
  (`database_id`, `database_url`, `data_source_url` as a top-level key)
  silently create orphan pages instead of database rows.
- `date:Date:start` and `date:Date:is_datetime` are literal property
  keys — that's how the MCP encodes a date column.
- `Tools used` is a JSON-encoded **string**, not a native array.
- `Status` must match one of the schema options: `in-progress`,
  `blocked`, `done`, `paused`.

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
- **Notion scope**: only touch the remembered `notion_db_name` database
  and (if needed) the `notion_workspace` parent page. Never touch other
  databases or pages.
- **Concise reporting**: `user_facing_summary` should be 3 sentences
  or fewer.

## Standard messages

Use these phrasings in `user_facing_summary` (in the user's language)
for the common cases:

- Notion not connected: "Notion is not connected. Run: zipsa connect notion"
- Sessions directory empty: "No Claude Code sessions found in the projects directory."
- No sessions on target date: "No Claude Code activity on <date>."
- Multi-day request: "Only single-day summaries are supported. Specify one date."
