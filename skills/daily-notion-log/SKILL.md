# daily-notion-log Skill

Orchestrator: fetch a day's Claude Code activity via the atomic
`agenthud-report` skill, summarize it per project, and write one
Notion database row per project via the atomic `notion-page-write`
skill.

## Orchestrator skill contract

This is an **orchestrator** skill: a composer that calls
`mcp__zipsa__run_skill` to delegate work to atomic children. It owns
its own UX for cross-cutting decisions (Notion workspace/db naming,
target-date parsing) and surfaces the run as a single coherent flow
to the user. Atomic children it composes:

- `agenthud-report` — fetches per-project Claude Code activity
- `notion-page-write` — persists pre-formed Notion pages

Composition uses `mcp__zipsa__run_skill` (declared in `spec.children`)
and `mcp__zipsa__get_artifact` (always available).

Note on UX boundaries: atomic children may have their own UX too
(per-caller routing namespaces their memory and prompts safely). The
split is by responsibility, not by "atomic == silent". `agenthud-report`
owns its own `project_roots` prompt because that value is needed by
agenthud itself — see Per-user setup below.

## Per-user setup

**Launcher-resolved (before container starts):**

No `spec.requires` on this skill. `project_roots` belongs to the
atomic `agenthud-report` skill (where agenthud actually runs and
needs the mounted paths). On first invocation, the launcher prompts
for `project_roots` against `agenthud-report` via the parent
HitlServer's stdin — the user sees the prompt at the top-level
terminal. Subsequent invocations read the saved value.

**Agent-time prompts (remembered in skill memory):**

- `notion_workspace` — top-level Notion workspace name (or parent
  page name) where the daily-log database should live. Asked once.
- `notion_db_name` — title of the database to write rows into.
  Will be created under `notion_workspace` if it doesn't exist.
  Asked once. Suggest `zipsa-daily-log` in the prompt but accept
  whatever the user types.

Phrase the Notion prompts in the user's language.

## Period semantics

- Default target date: **yesterday**, in the user's local timezone
  (see runtime contract on `tz_iana`).
- The user may override via `user_query`: `today`, `yesterday`, or
  an ISO date `YYYY-MM-DD`.
- **Multi-day ranges are NOT supported.** If the user asks for more
  than one day, stop precheck with `status="out_of_scope"`.

## State carryover

Each phase reads `previous_phase_input` and emits `next_phase_input`.
Unless a phase explicitly says otherwise, `next_phase_input` MUST
include every field from `previous_phase_input` plus any new fields
this phase produces.

## Phases

### precheck

Verify Notion is reachable, resolve target date, ensure per-user
workspace/db are remembered, locate or create the Notion database.

1. **MCP availability**: call `mcp__notion__notion-search` with any
   non-empty query (the goal is verifying the server responds). If
   it errors or returns "No such tool available", stop with
   `status="failed"`, `error.code="mcp_unavailable"`,
   `user_facing_summary` (user's language):
   `"Notion is not connected. Run: zipsa connect notion"`.

2. **Resolve target_date** from `user_query`:
   - empty / whitespace → use yesterday in user's local timezone
   - `today` / `yesterday` → resolve to ISO date
   - `YYYY-MM-DD` (matches `^\d{4}-\d{2}-\d{2}$`) → use verbatim
   - more than one day requested → stop with `status="out_of_scope"`,
     `user_facing_summary`: `"Only single-day summaries are supported. Specify one date."`

3. **ask_once notion_workspace + notion_db_name** (in the user's
   language). Suggest `zipsa-daily-log` as the db_name example.
   Cache both values via the standard `mcp__zipsa__ask_once` flow.

4. **Resolve database**:
   - If `skill_state.db_id` is set, call `mcp__notion__notion-fetch`
     on it. On success, capture `db_id` and `data_sources[0].id` as
     `data_source_id`. On 404/permission error, fall through.
   - Otherwise, search with `mcp__notion__notion-search` for a
     database titled `notion_db_name`. From a hit, fetch and capture
     both ids.
   - Otherwise, search for a page titled `notion_workspace`. If
     found, create the database under that page. If not, create the
     page first, then the database. The `notion-create-database`
     response includes the data source — capture both ids.
   - Database schema:
     - `Date` (date)
     - `Project` (title)
     - `Summary` (rich_text)
     - `Sessions` (number)
     - `Tools used` (multi_select)
     - `Status` (select: in-progress, blocked, done, paused)

   **Why two ids?** `notion-create-pages` takes a
   `parent.data_source_id`, NOT a database id. Different values for
   different purposes; both must be captured here.

5. Output `next_phase_input`:
   ```json
   {
     "target_date": "YYYY-MM-DD",
     "db_id": "...",
     "data_source_id": "..."
   }
   ```

   `state_updates`: set `db_id` and `data_source_id` if newly
   resolved or created.

   `user_facing_summary` (user's language):
   `"Precheck complete — target {target_date}, DB resolved"`.

### fetch

Invoke the atomic `agenthud-report` child skill for `target_date`
and read its artifact.

1. Call:
   ```python
   result = mcp__zipsa__run_skill(
     name="agenthud-report",
     args=previous_phase_input.target_date
   )
   ```

2. If `result.status != "ok"`, bubble up: stop with
   `status="failed"`, `error.code="agenthud_report_failed"`,
   `user_facing_summary`: `"agenthud-report failed: <result.summary.error.message truncated to 200 chars>"`.

3. Read the artifact:
   ```python
   art = mcp__zipsa__get_artifact(
     skill=result.skill,
     version=result.version,
     run_id=result.run_id,
     name="agenthud-report.json"
   )
   ```

   `art.content` is the raw agenthud JSON (no projection — atomic
   skill emits everything):

   ```json
   {
     "date": "YYYY-MM-DD",
     "sessions": [
       {
         "project": "launcher",
         "start": "00:00", "end": "06:23",
         "activities": [
           {"time": "...", "icon": "$", "label": "Bash", "detail": "..."},
           {"time": "...", "icon": "~", "label": "Edit", "detail": "..."},
           {"time": "...", "icon": "◆", "label": "<sha>", "detail": "commit message"},
           ...
         ],
         "subAgents": []
       }
     ]
   }
   ```

   See `agenthud-report` SKILL.md for the full icon table. Two
   important facts for this orchestrator:
   - **Multiple sessions can share the same `project`** if the user
     opened/closed Claude Code more than once that day. Group by
     `session.project` before summarizing per project.
   - **Commits are `icon == "◆"`**, with `label` as the short SHA
     and `detail` as the commit subject.

4. If `art.content.sessions` is empty (no activity), set
   `next_phase_input.report_by_project = {}` and let downstream
   phases short-circuit. Do NOT stop the skill — empty days are
   valid (the write phase will simply skip writes).

5. Output `next_phase_input`:
   ```json
   {
     ...previous fields...,
     "report_by_project": {
       "launcher": {
         "session_count": <int>,
         "activity_count": <int>,
         "commits": ["<sha>: <subject>", ...],
         "labels": ["Bash", "Edit", ...],
         "sample_responses": ["<first 2-3 Response details>"],
         "sample_edits": ["<first 2-3 Edit details>"],
         "sample_bash": ["<first 2-3 Bash details>"]
       },
       "skills": {...}
     }
   }
   ```

   Build this dict by grouping `art.content.sessions[]` by
   `.project`, then for each project's combined `activities`:
   - `session_count` = number of sessions for this project
   - `activity_count` = total activities across those sessions
   - `commits` = entries with `icon == "◆"`, formatted `"{label}: {detail}"`
   - `labels` = `unique` over `activity.label` for non-commit
     entries (so "Bash", "Edit", etc. — drop the SHAs)
   - `sample_*` = first 2-3 `detail` strings per label (truncate
     each to 200 chars)

   `user_facing_summary` (user's language):
   - non-empty: `"Activity fetched — {N} projects, {C} commits"`
   - empty: `"No Claude Code activity on {target_date}"`

### prepare

Summarize each project's activity into the Notion row shape.
LLM-summarization phase — consumes `report_by_project` and produces
structured Notion page payloads.

1. Read `previous_phase_input.report_by_project`. If empty, output
   `next_phase_input.pages = []` and move on (write phase will
   skip the run_skill call entirely).

2. For each project entry, build a `page` object using the
   `notion-page-write` payload shape and the database schema:

   ```json
   {
     "properties": {
       "Project": "<project name>",
       "date:Date:start": "<previous_phase_input.target_date>",
       "date:Date:is_datetime": 0,
       "Summary": "<2-4 sentence narrative in user's language>",
       "Sessions": <session_count>,
       "Tools used": "<JSON-encoded string of unique labels>",
       "Status": "<best-guess: in-progress | blocked | done | paused>"
     },
     "content": "<optional markdown body with commit list etc.>"
   }
   ```

   **Field rules:**
   - `parent.type` MUST be `"data_source_id"` (set in next phase,
     not here — pages[] only needs `properties` and `content`).
   - `date:Date:start` and `date:Date:is_datetime` are literal
     property keys — that's how the Notion MCP encodes a date column.
   - `Tools used` is a JSON-encoded **string**, not a native array.
     Use the project's `labels` list — typically `["Bash", "Edit",
     "Read"]`. Example: `"[\"Bash\", \"Edit\", \"Read\"]"`.
   - `Status` must match one of the schema options exactly.

   **Summary narrative rules:**
   - 2-4 sentences in the user's language.
   - Lead with what was SHIPPED (`commits` list) if non-empty —
     reference the commit subjects, not the SHAs.
   - Otherwise lead with `sample_edits` (what files changed) and
     `sample_responses` (what the agent reported doing).
   - Cap at ~400 chars to keep the row readable in the Notion
     table view.

   **`content` body (optional):** if there are commits, include
   them as a markdown bullet list under "Commits" — this makes the
   Notion page itself useful for skim-reading what shipped.

   **Status inference:**
   - Has commits → `done`
   - Has edits but no commits → `in-progress`
   - Only bash/responses → `paused`
   - Default → `in-progress`

3. Output `next_phase_input`:
   ```json
   {
     ...previous fields...,
     "pages": [{...}, {...}]
   }
   ```

   `user_facing_summary` (user's language):
   `"Prepared {N} page(s)"`.

### write

Invoke the atomic `notion-page-write` child skill with the prepared
payload.

1. If `previous_phase_input.pages` is empty, skip the run_skill
   call. Set `result = {entries_created: 0, entries_updated: 0}` and
   end the phase with `status="ok"`.

2. Build the payload string:
   ```python
   payload = json.dumps({
     "data_source_id": previous_phase_input.data_source_id,
     "pages": previous_phase_input.pages,
     "upsert_by": None
   })
   ```

3. Call:
   ```python
   result = mcp__zipsa__run_skill(
     name="notion-page-write",
     args=payload
   )
   ```

4. If `result.status != "ok"`, bubble up: stop with
   `status="failed"`, `error.code="notion_page_write_failed"`,
   `user_facing_summary`: `"notion write failed: <error truncated>"`.

5. Read the child's artifact for the URLs:
   ```python
   art = mcp__zipsa__get_artifact(
     skill=result.skill, version=result.version,
     run_id=result.run_id, name="notion-pages.json"
   )
   ```

6. Set the phase `result` (this is the final phase — `result`
   surfaces to the user):
   ```json
   {
     "target_date": "...",
     "entries_created": <len(art.content.created)>,
     "entries_updated": 0,
     "pages": [<art.content.created>]
   }
   ```

   v0.1 always creates (the atomic `notion-page-write` rejects
   `upsert_by`); `entries_updated` is reserved for when upsert
   lands.

   `state_updates`: set `last_run_date` to `target_date`.

   `user_facing_summary` (user's language):
   `"Wrote {entries_created} page(s) for {target_date}"`.

## Constraints

- This orchestrator does NOT invoke any user-facing MCP tool other
  than `ask_once` in precheck and (implicit) `ask` calls from
  inside `ask_once` if needed.
- Phase isolation convention: a phase that uses HITL (precheck) does
  not also call `mcp__zipsa__run_skill`, and vice versa. The launcher
  does not enforce this — both tool families are always available —
  but mixing them in one phase tangles user-interaction policy with
  child-skill orchestration. Keep them in separate phases for
  reviewability.
- This orchestrator does NOT modify Claude session files (it never
  invokes agenthud directly — `agenthud-report` does).
- This orchestrator only touches the Notion database named by
  `notion_db_name` and (if needed) the parent page named by
  `notion_workspace`. Never touch other databases or pages.

## Standard messages

For `user_facing_summary`, use these phrasings (in the user's
language) for the common cases:

- Notion not connected: `"Notion is not connected. Run: zipsa connect notion"`
- Sessions empty: `"No Claude Code activity on <date>."`
- Multi-day request: `"Only single-day summaries are supported. Specify one date."`
- Success: `"Wrote N page(s) for <date>"`
