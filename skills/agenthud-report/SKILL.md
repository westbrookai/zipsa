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

2. Invoke the skill-vendored agenthud wrapper, redirecting stdout to
   `/tmp/agenthud-report.json`. Do NOT pipe stdout through the Bash
   tool result — busy days produce 50-200KB+ of JSON.

   ```bash
   /skill/scripts/agenthud report \
     --date <target_date> \
     --format json \
     --include response,bash,edit \
     --detail-limit 200 \
     --with-git \
     > /tmp/agenthud-report.json
   ```

   The wrapper pins `agenthud@0.9.2` and warms the npx cache so the
   real call's stdout is guaranteed clean JSON. `~/.claude/projects`
   is bind-mounted at agenthud's default path; each `project_roots`
   entry is mounted at its own absolute host path so `--with-git`
   can resolve session `cwd → .git`.

   **`thinking` deliberately excluded** — verbose internal reasoning
   isn't share-worthy.

3. Run ONE jq query to produce a slim per-project summary, writing
   it as the artifact:

   ```bash
   jq '{
     target_date: "<target_date>",
     projects: ([.sessions[] | {
       project: .project,
       activity_count: (.activities | length),
       commits: ([.activities[] | select(.type == "commit") | .label] | unique),
       sample_responses: ([.activities[] | select(.type == "Response") | .text] | .[:3]),
       sample_edits: ([.activities[] | select(.type == "Edit") | .label] | .[:3]),
       sample_bash: ([.activities[] | select(.type == "Bash") | .label] | .[:3])
     }] | group_by(.project) | map({
       name: .[0].project,
       activity_count: (map(.activity_count) | add),
       commits: (map(.commits[]) | unique),
       sample_responses: (map(.sample_responses[]) | .[:4]),
       sample_edits: (map(.sample_edits[]) | unique | .[:5]),
       sample_bash: (map(.sample_bash[]) | unique | .[:5])
     }))
   }' /tmp/agenthud-report.json > /home/agent/runs/current/artifacts/agenthud-report.json
   ```

   **Shell-quoting notes:**
   - Wrap the whole jq filter in single quotes; nothing inside needs
     escaping.
   - Substitute `<target_date>` into the jq string literal before
     invoking (use bash variable expansion outside the single-quoted
     filter, or use jq's `--arg` form). Do NOT leave the literal
     placeholder text.
   - Use **explicit `{key: .key}` form** throughout — the
     `{key1, key2}` shorthand can be mangled by some wrappers.

4. The artifact's shape:

   ```json
   {
     "target_date": "2026-05-22",
     "projects": [
       {
         "name": "launcher",
         "activity_count": 47,
         "commits": ["feat: ...", "fix: ..."],
         "sample_responses": ["..."],
         "sample_edits": ["..."],
         "sample_bash": ["..."]
       }
     ]
   }
   ```

   If agenthud found 0 sessions, the artifact is still written with
   `projects: []`. This is a normal "no activity today" signal, not
   an error.

5. Emit the skill-envelope:

   ```json
   {
     "status": "ok",
     "phase": "report",
     "result": {
       "target_date": "<resolved>",
       "project_count": <N>,
       "activity_count": <total across projects>,
       "artifact": "agenthud-report.json"
     },
     "state_updates": {},
     "user_facing_summary": "agenthud report — {target_date} ({project_count} projects, {activity_count} activities)"
   }
   ```

   Single-phase skill, so no `next_phase_input` is needed.

## Error handling

- Invalid `target_date` → `error.code="invalid_target_date"`,
  user-facing summary `"invalid target_date: <input>"`.
- agenthud wrapper non-zero exit → `error.code="agenthud_failed"`,
  user-facing summary `"agenthud failed: <stderr first 200 chars>"`.
- jq non-zero exit → `error.code="jq_failed"`, user-facing summary
  similar.
- 0 sessions is NOT an error — write the empty-projects artifact and
  return `status="ok"`.

## Constraints

- This skill does NOT call `mcp__zipsa__run_skill` (atomic = leaf in
  the call graph).
- Output is exclusively via the artifact + the `result` field — agent
  text/markdown chatter is not the contract.
- Output language is English (this is a machine-to-machine skill).
