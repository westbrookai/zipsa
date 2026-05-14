# Multi-Phase Skill Execution Design

## Goal

Enable skills to declare multiple sequential phases, each with its own goal,
tool allowlist, and resource limits. The launcher runs phases one at a time,
passing the output of each phase as input to the next.

## Motivation

Single-phase skills hit token and cost limits quickly when the work spans
multiple distinct concerns (e.g., check connections → discover data → analyze
→ write results). Splitting into phases gives each step a tighter sandbox and
makes failures easier to diagnose.

---

## Core Architecture Principle

**System prompt = static (identical across all phases → prompt cache hit).**
**User message = dynamic (rebuilt per phase).**

```
System prompt:
  <runtime_contract>   ← fixed rules
  <skill_definition>   ← full SKILL.md (all phases visible to agent)

User message (per phase):
  <execution_context>  ← phase id, goal, previous output, state, user query
  <execution_trigger>  ← "Execute phase: <id>"
```

The runtime contract enforces phase isolation: the agent executes only the
current phase even though it can read all phase instructions in `skill_definition`.
The system prompt never changes within a skill run, so the prompt cache is warm
from phase 2 onwards.

---

## Section 1: Manifest Schema Changes

### New: `phases` array

```yaml
spec:
  phases:
    - id: precheck
      goal: |
        Verify MCP connections, resolve target date from user query,
        and locate or create the Notion database.
      allowed_tools:
        - mcp__notion__notion-search
        - mcp__notion__notion-fetch
        - mcp__notion__notion-create-database
        - mcp__notion__notion-create-pages
      limits:
        max_turns: 5
        max_cost_usd: 0.05
        timeout_seconds: 60

    - id: discover
      goal: |
        Find Claude Code session files modified on the target date.
      allowed_tools:
        - mcp__sessions__list_directory
        - mcp__sessions__get_file_info
        - mcp__sessions__search_files
      limits:
        max_turns: 6
        max_cost_usd: 0.06
        timeout_seconds: 60
```

Fields per phase:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique phase identifier. Must match a `###` section in SKILL.md. |
| `goal` | string | Brief statement of what the phase accomplishes. Injected into `execution_context`. |
| `allowed_tools` | string[] | Full `mcp__<server>__<tool>` names (or builtin names). Launcher enforces via `--allowedTools`. |
| `limits.max_turns` | int | Hard cap on Claude turns for this phase. |
| `limits.max_cost_usd` | float | Hard cap on API cost for this phase. |
| `limits.timeout_seconds` | int | Wall-clock timeout for this phase. |

### New: `state_schema`

```yaml
spec:
  state_schema:
    db_id: string        # Notion database ID once resolved
    last_run_date: string
```

Declares the keys the skill may read and write via `state_updates`. The
launcher validates `state_updates` against this schema before applying.

### New: `config`

```yaml
spec:
  config:
    workspace_name: "Westbrook AI HQ"
    db_name: "zipsa-daily-log"
    timezone: "Australia/Sydney"
    default_target_date: yesterday
```

Skill-author-defined constants. Injected into `execution_context` per phase.
Users may override values at install time (future: `zipsa configure`).

### Changed: `limits` (aggregate)

A top-level `limits` block caps the entire run across all phases.
Checked at phase boundaries only — no mid-phase abort.

```yaml
spec:
  limits:
    max_turns: 35
    max_cost_usd: 0.60
    timeout_seconds: 420
```

### Backwards compatibility

Skills without a `phases` key are treated as single-phase. The launcher
wraps them in an implicit phase using the top-level `allowed_tools` and
`limits`. Single-phase skills receive only `user_query`, `skill_state`, and
`config` in the user message (no `phase_id`, no `previous_phase_output`).

---

## Section 2: System Prompt (Static, Cached)

`system-prompt-template.md` is restored to contain both `<runtime_contract>`
and `<skill_definition>`. The full SKILL.md is injected into
`<skill_definition>` — all phases are visible to the agent at once.
The runtime contract enforces that the agent executes only the current phase.

Template structure:

```
<runtime_contract>
{contract}
</runtime_contract>

<skill_definition name="{skill_name}" version="{skill_version}">
{skill_body}
</skill_definition>
```

The system prompt is **identical for every phase** of a run. Phase 2+ benefit
from a warm prompt cache. No `<execution_context>` in the system prompt.

---

## Section 3: User Message (Dynamic, Per-Phase)

A new `user-message-template.md` in `system-prompts/` defines the user message
sent to Claude Code for each phase invocation.

```
<execution_context>
date: {date}
time: {time}
timezone: {timezone}

phase_id: {phase_id}
phase_goal: {phase_goal}
previous_phase_output: {previous_phase_output}
skill_state: {skill_state}
user_query: {user_query}
config: {config}
</execution_context>

Execute phase: {phase_id}
```

Fields:

| Field | Present when | Description |
|-------|-------------|-------------|
| `date`, `time`, `timezone` | always | Wall-clock at phase start |
| `phase_id` | multi-phase | ID of the current phase |
| `phase_goal` | multi-phase | `goal` from the manifest phase definition |
| `previous_phase_output` | phase index ≥ 1 | `next_phase_input` from previous phase (JSON) |
| `skill_state` | always | Current persisted state snapshot (JSON) |
| `user_query` | always | Original user query. On `needs_input` re-run, `user_answer` is appended. |
| `config` | always | Skill config values (JSON) |

### `needs_input` re-run

When a phase returns `needs_input`, the same phase restarts from scratch
(not resumed). The launcher rebuilds the user message with all the same
fields, plus:

```
user_answer: "<user's response to needs_input>"
```

The agent receives no memory of the previous attempt.

---

## Section 4: Output JSON

```json
{
  "status": "ok" | "needs_input" | "failed" | "out_of_scope",
  "phase": "<current phase id>",
  "result": <phase output — null for intermediate phases>,
  "state_updates": <state delta or null>,
  "next_phase_input": <data for the next phase, or null>,
  "user_facing_summary": "<3 sentences max, in user's language>",
  "needs_input": {...} | null,
  "error": {...} | null
}
```

- `next_phase_input`: the contract between phases. Passed verbatim as
  `previous_phase_output` to the next phase. Skill author defines its schema
  in SKILL.md. Only the final phase may set this to null.
- `result`: only meaningful in the final phase. Intermediate phases set to null.
- `state_updates`: JSON object of key → new value (or null to delete). Launcher
  applies after a successful phase.

### Status semantics

| Status | Launcher behavior |
|--------|-------------------|
| `ok` | Apply `state_updates`, pass `next_phase_input` to next phase. |
| `needs_input` | Surface `needs_input` to user, restart same phase with `user_answer`. |
| `failed` | Abort run, report error to user. |
| `out_of_scope` | Abort run, report out-of-scope to user. |

---

## Section 5: Launcher Phase Sequencing

```
cumulative_turns = 0
cumulative_cost = 0.0
previous_output = null
skill_state = load_state(skill)

for each phase in manifest.phases:
    # Aggregate limit check at phase boundary
    if cumulative_turns >= aggregate.max_turns: abort(failed)
    if cumulative_cost >= aggregate.max_cost_usd: abort(failed)

    user_message = build_user_message(phase, previous_output, skill_state, user_query, config)
    output = run_claude_code(
        system_prompt=system_prompt,   # same for all phases
        user_message=user_message,
        allowed_tools=phase.allowed_tools,
        limits=phase.limits,
    )
    cumulative_turns += output.num_turns
    cumulative_cost += output.cost_usd

    phase_json = extract_skill_status(output.last_assistant_text)

    if phase_json.status == "ok":
        apply_state_updates(phase_json.state_updates)
        previous_output = phase_json.next_phase_input
        continue

    elif phase_json.status == "needs_input":
        user_answer = prompt_user(phase_json.needs_input)
        # restart same phase — prepend to loop iteration
        continue same phase with user_answer injected

    else:  # failed | out_of_scope
        abort(phase_json)

surface(final_phase_json.result, final_phase_json.user_facing_summary)
```

The launcher enforces `allowed_tools` per phase via `--allowedTools`. The
runtime contract in the system prompt also declares the list, but the launcher
is the hard enforcement layer.

---

## Section 6: SKILL.md Structure

SKILL.md must contain one `###` section per phase id. All sections are
included in `<skill_definition>` and visible to the agent. The runtime
contract instructs the agent to execute only the phase named in `phase_id`.

```markdown
### precheck

Verify everything needed to run is in place...

Steps:
1. ...

`next_phase_input` schema:
    { "target_date": "...", "db_id": "..." }

`state_updates`: set `db_id` if newly resolved.

### discover

Find all Claude Code session files...
```

---

## Implementation Checklist

- [ ] Restore `<skill_definition>` to `system-prompt-template.md`
- [ ] Remove `<execution_context>` from `system-prompt-template.md`
- [ ] Create `system-prompts/user-message-template.md`
- [ ] Add `phases`, `state_schema`, `config` to `Skill` / `SkillManifest` Pydantic models
- [ ] Update `executor.py`: phase loop, user message builder, aggregate limit check
- [ ] Update `_extract_skill_status` to handle `next_phase_input`
- [ ] State persistence: load/save `skill_state` across phases
- [ ] Update `manifest.yaml` validation to accept new fields
- [ ] Update tests
