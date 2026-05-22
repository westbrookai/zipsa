# agenthud-report Skill

Fetch a structured per-project activity report from agenthud for a
given date and emit it as a JSON artifact for downstream skills.

## Atomic skill contract

This is an **atomic** skill: a leaf in the call graph — it does NOT
call `mcp__zipsa__run_skill`, so it can be composed by orchestrators
without depth/cycle concerns. Single-responsibility per Unix
philosophy: agenthud invocation only, no downstream-domain knowledge
(Notion, X, etc.).

For this particular skill:

- One input: a target date string.
- One output: a JSON artifact at `artifacts/agenthud-report.json`.
- `project_roots` is launcher-resolved via `spec.requires`; the
  agent never prompts the user inside the container.
- No agent-time HITL (`ask`, `confirm`, `choose`, `ask_once`) and no
  skill-memory reads/writes — this skill genuinely has nothing it
  needs to ask or remember beyond what the launcher provides.

(Atomic skills MAY use HITL or memory in general — per-caller routing
gives them their own namespace whether invoked directly or as a child.
This one just doesn't need to.)

Orchestrator skills compose this via `mcp__zipsa__run_skill` and read
the artifact via `mcp__zipsa__get_artifact`.

## Input

`user_query` is the target date. Accepted forms:

- `today` (default if `user_query` is empty or whitespace)
- `yesterday`
- `YYYY-MM-DD` (must match `^\d{4}-\d{2}-\d{2}$`)

Any other input → fail with `error.code="invalid_target_date"` and a
short message naming the bad input.

## Phase: report

1. Resolve `target_date` from `user_query` per the Input rules above.

2. Invoke the skill-vendored agenthud wrapper, writing its full
   stdout directly to the artifact path. The artifact IS the raw
   agenthud report — this skill does no projection, no slicing, no
   include-filtering. Orchestrators that need a slimmer shape do
   their own jq pass on the artifact.

   ```bash
   /skill/scripts/agenthud report \
     --date <target_date> \
     --format json \
     --include all \
     --with-git \
     > /home/agent/runs/current/artifacts/agenthud-report.json
   ```

   The wrapper pins `agenthud@0.9.2` and warms the npx cache so the
   real call's stdout is guaranteed clean JSON. `~/.claude/projects`
   is bind-mounted at agenthud's default path; each `project_roots`
   entry is mounted at its own absolute host path so `--with-git`
   can resolve session `cwd → .git` and emit commit entries.

   **Why `--include all` and no `--detail-limit`:** this is an atomic
   skill. Trimming activity types or truncating bodies up-front
   throws away information the orchestrator might need. Some
   orchestrators want only commits; others want responses for tweet
   drafting; a future one might want thinking. Atomic emits the full
   record; orchestrators decide what to drop. Busy days can produce
   500KB+ of JSON — within the 10 MiB artifact cap.

3. Compute metadata for the `result` field using `jq` on the
   artifact (no projection, just counting):

   ```bash
   jq '{
     session_count: (.sessions | length),
     activity_count: ([.sessions[].activities | length] | add // 0),
     project_count: ([.sessions[].project] | unique | length)
   }' /home/agent/runs/current/artifacts/agenthud-report.json
   ```

   These three numbers go into the skill envelope's `result` field.
   The orchestrator can compute anything else from the artifact.

4. The artifact is the raw agenthud `--format json` output, schema
   defined by agenthud itself. Verified shape (agenthud 0.9.2):

   ```json
   {
     "date": "2026-05-22",
     "sessions": [
       {
         "project": "launcher",
         "start": "00:00",
         "end": "06:23",
         "activities": [
           {"time": "00:01", "icon": "$",   "label": "Bash",     "detail": "grep -n ..."},
           {"time": "00:01", "icon": "<",   "label": "Response", "detail": "Now CLI side..."},
           {"time": "00:01", "icon": ">",   "label": "User",     "detail": "..."},
           {"time": "00:00", "icon": "~",   "label": "Edit",     "detail": "executor.py"},
           {"time": "00:02", "icon": "…",   "label": "Thinking", "detail": "..."},
           {"time": "00:03", "icon": "○",   "label": "Read",     "detail": "..."},
           {"time": "01:49", "icon": "◆",   "label": "9bca6ab",  "detail": "perf: gate ..."}
         ],
         "subAgents": []
       }
     ]
   }
   ```

   **Activity icon → semantic table** (downstream skills filter by
   this, since `label` is overloaded — type-name for most activities,
   git SHA for commits):

   | icon | meaning                          |
   |------|----------------------------------|
   | `$`  | Bash command (detail = command)  |
   | `<`  | Agent Response (detail = text)   |
   | `>`  | User input (detail = message)    |
   | `~`  | Edit (detail = file path)        |
   | `○`  | Read/Write (detail = file path)  |
   | `…`  | Thinking (detail = reasoning)    |
   | `◆`  | Commit (label = SHA, detail = commit message) |

   For commit entries, `label` is the short SHA and `detail` is the
   commit subject. The `--with-git` flag is what enables emission of
   these — without per-session mounts (`spec.requires.project_roots`)
   they won't appear.

   If agenthud found 0 sessions on the date, the artifact has
   `sessions: []`. That is a normal "no activity today" signal, not
   an error.

5. Emit the skill-envelope:

   ```json
   {
     "status": "ok",
     "phase": "report",
     "result": {
       "target_date": "<resolved>",
       "session_count": <N>,
       "activity_count": <total across sessions>,
       "project_count": <unique projects>,
       "artifact": "agenthud-report.json"
     },
     "state_updates": {},
     "user_facing_summary": "agenthud report — {target_date} ({session_count} sessions, {activity_count} activities, {project_count} projects)"
   }
   ```

   Single-phase skill, so no `next_phase_input` is needed.

## Error handling

- Invalid `target_date` → `error.code="invalid_target_date"`,
  user-facing summary `"invalid target_date: <input>"`.
- agenthud wrapper non-zero exit → `error.code="agenthud_failed"`,
  user-facing summary `"agenthud failed: <stderr first 200 chars>"`.
- jq non-zero exit on the metadata pass → `error.code="jq_failed"`,
  user-facing summary similar. The artifact has still been written
  in this case (step 2 already succeeded); the failure is purely on
  the metadata count.
- 0 sessions is NOT an error — the artifact still has `sessions: []`
  and the run returns `status="ok"`.

## Constraints

- This skill does NOT call `mcp__zipsa__run_skill` (atomic = leaf in
  the call graph).
- This skill does NOT project, slice, or filter the agenthud output.
  The artifact is the raw `--include all --with-git` JSON. Down­-
  stream skills own their own slicing decisions.
- Output is exclusively via the artifact + the `result` field — agent
  text/markdown chatter is not the contract.
- Output language is English (this is a machine-to-machine skill).
