# bip-daily-x Skill

Generate one tweet about the user's daily Claude Code work, refine
via user feedback, and post to X after explicit approval.

## Language Policy

- Agent reasoning, phase goals, field names: **English**.
- All user-facing strings (prompts, error messages,
  `user_facing_summary`): **`execution_context.user_language`** —
  see the runtime contract's `user_language` description. Localize
  every prompt and summary into that language. The English phrasings
  in this SKILL.md are *intent descriptions* — phrase the actual
  prompt naturally in the user's language, do NOT translate these
  English strings word-for-word.
- The final tweet text: **English** (it is the deliverable, regardless
  of UI language).
- When the intent description contains a brace-delimited token like
  `{N}` or `{example}`, substitute the actual runtime value when
  emitting the string. Field names referenced in prose (`voice`,
  `interests`, `next_phase_input`, etc.) stay in English.

## State Carryover

Each phase reads `previous_phase_input` and emits `next_phase_input`.
Unless a phase explicitly states otherwise, `next_phase_input` MUST
include every field present in `previous_phase_input` plus any new
fields this phase produces. The `...previous fields...` shorthand in
the JSON examples below means "carry forward every field from
previous_phase_input verbatim" — it is not a license to drop fields.

## Per-user setup

Three groups of user-specific values:

**Launcher-resolved (before container starts):**

- **`project_roots`** — directories containing the user's git projects,
  declared in `spec.requires`. The launcher prompts on first run (or
  via `zipsa configure bip-daily-x`), saves at
  `~/.zipsa/bip-daily-x@<version>/requires.yaml`, and mounts each path
  at its own absolute host path so `agenthud --with-git` can resolve
  session-cwd → .git lookups. Tweet drafting uses the resulting `◆`
  commit entries — knowing what shipped is part of what's tweet-worthy.

**Environment (via `~/.zipsa/.env`, auto-injected into container):**

- `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` —
  OAuth 1.0a credentials (4 strings, no expiry) generated once at
  https://console.x.com under the user's X Developer App. The
  launcher passes them automatically; this skill never touches them
  directly. Precheck verifies presence via the bundled script.

**Agent-time (remembered in skill memory):**

- `voice` — 1–2 sentences describing the user's preferred tweet tone.
  Asked once on first run, then reused.
- `interests` — comma-separated list of 3-5 topics the user wants
  the `interests` phase to web-search every day. Asked once on first
  run. `config.default_interests` is shown as an example in the prompt
  but the stored value is whatever the user types.

## Phases

### precheck

1. Verify all 4 X env vars are present by running
   `python3 /skill/scripts/post.py --check-env`. The script reads the
   env vars and emits a single JSON line: `{"status":"ok",...}` or
   `{"status":"failed","error":"missing env var(s): [...]"}`. On
   `failed`, stop the phase with `status=failed`,
   `error.code="x_credentials_missing"`, and put the script's error
   message into `user_facing_summary` (in `user_language`, intent:
   "X env vars missing: ...") so the user sees exactly which var(s)
   are missing.

2. Call `mcp__zipsa__ask_once` with key=`voice` (EXACTLY that — not
   `x_voice`, `tweet_voice`, or any other variant).
   Prompt in `user_language`, intent:
   "Tell me your tweet tone in 1-2 sentences."
   The cached answer is reused on subsequent runs.

3. Call `mcp__zipsa__ask_once` with key=`interests`.
   Build the prompt in `user_language` at runtime — intent:
   "Enter 3-5 interest topics separated by commas. e.g. {example}"
   — where `{example}` is `config.default_interests` joined with
   `, ` (so when the manifest's `default_interests` changes, the
   prompt's example stays in sync — do not hardcode the items).
   Parse the user's response into a list of trimmed, non-empty strings.
   Store the raw response into ask_once memory under key=`interests`;
   pass the parsed list downstream as `next_phase_input.interests`.
   The cached answer is reused on subsequent runs.

4. Resolve target_date_default from the user query. Default: today in
   the user's local timezone (see runtime contract on `tz_iana`). This
   is only an initial value — `ask_agenthud` may override it later.

5. Set `next_phase_input`:
   ```json
   {
     "voice": "<from ask_once>",
     "interests": ["...", "...", "..."],
     "target_date_default": "YYYY-MM-DD"
   }
   ```
   `user_facing_summary` in `user_language`, intent:
   "Precheck complete — voice/interests loaded"

### discover

Search public build-in-public tweets via the WebSearch built-in tool
and extract tone/structure insights for the draft phase.

1. Read `config.discover_query` from the manifest at runtime (do not
   hardcode the literal — the manifest is the source of truth). Use
   it as the WebSearch query.

2. Call `WebSearch` with that query. WebSearch typically returns
   5-10 results, which is sufficient. Examples of useful results:
   short personal updates with metrics, before/after framing,
   question-style hooks.

3. From the result snippets, extract **3-5 short insights** about what
   format/tone is working today. Phrase them as actionable rules a
   tweet writer could apply. The bullets below are illustrative shapes
   only — do not reuse verbatim; your insights should reflect today's
   actual search results:

   - "Lead with a concrete number in the first 50 chars"
   - "Before/after framing outperforms generic 'shipped X' posts"
   - "End with a question to drive replies"

   Do NOT copy other people's tweet text verbatim (avoidance of
   inadvertent plagiarism). Distill, don't paste.

4. If the first `WebSearch` call errors, retry **exactly once**. If
   that retry also errors, OR if `WebSearch` returns 0 results on
   either call, set `insights=[]` and continue — the draft phase can
   still operate.

5. Output `next_phase_input`:
   ```json
   {
     ...previous fields...,
     "insights": ["...", "...", "..."]
   }
   ```
   `user_facing_summary` in `user_language`, intent:
   - If `insights` is non-empty: "BIP trend analysis complete —
     {N} insights" (substitute `{N}` with the count, e.g., `3`).
   - If `insights` is empty: "No BIP search results — continuing
     without web data".

### interests

Confirm or override the user's interest topics for this run, then
WebSearch and summarize current chatter on those topics.

1. Read `interests` from `previous_phase_input` (originally set by
   precheck from ask_once memory).

2. Ask the user in `user_language`, inlining the current list as a
   brace substitution. Substitute `{interests_joined}` with the list
   joined by `, ` (plain text, no markdown emphasis — the runtime
   surfaces this as terminal output, where literal asterisks read
   worse than plain text).

   Prompt intent:
   "Currently stored interests: {interests_joined}.
   Search these as-is today, or different topics?
   (For different topics, list separated by commas; for same, press Enter.)"

3. Parse the user reply:
   - Empty / whitespace only → `interests_used = interests` (the stored list).
   - Non-empty → parse as comma-separated, trim each item, drop empties.
     `interests_used = parsed list`. Do NOT overwrite the stored
     `interests` ask_once value — this override is for this run only.
   - On post-trim empty list (e.g., user typed only commas/whitespace
     that yielded no items) → reprompt once. If the second attempt also
     yields an empty list, fall back to the stored list and mention in
     `user_facing_summary`. Note: a single item without commas (`"foo"`)
     is a valid one-item list — do NOT trigger reprompt for that.

4. Call `WebSearch`. If `interests_used` has 1-2 items, do a single
   query joining them with `OR`. If 3+ items, do **one query per item
   in `interests_used` list order, capped at the first 3 items**, to
   keep tool budget reasonable and behaviour deterministic.

5. Synthesize the result snippets into **3-5 sentences of English
   prose** summarizing what's notable across these topics today.
   This text feeds the English draft phase directly.

   Example shape only — your prose must reflect today's actual search
   results, not these phrasings:
   > "Topic A is seeing a wave of activity around <specific angle>
   > this week. Topic B's conversation is dominated by <specific
   > question>. Topic C is comparatively quiet, with the main thread
   > being <specific concern>."

6. On `WebSearch` 0-results or repeated failure, set
   `interests_summary = "(no search results)"` and continue.
   (Plain English — this field is consumed by the English `draft`
   phase; localized status belongs in `user_facing_summary` only.)

7. Output `next_phase_input`:
   ```json
   {
     ...previous fields...,
     "interests_summary": "<English prose>",
     "interests_used": ["...", "...", "..."]
   }
   ```
   `user_facing_summary` in `user_language`, intent:
   "Interest search summary complete — {N} topics" (substitute `{N}`
   with `len(interests_used)`).

### ask_agenthud

Decide whether to enrich the draft with today's Claude Code activity.

1. Ask the user in `user_language`, intent:
   "Reflect today's Claude Code work in the tweet too? (y/N)"

2. Parse the reply. Treat as **yes** any affirmative form in the
   user's language (`y` / `Y` / `yes` / language-specific equivalents).
   Treat everything else (including empty input) as **no**. Defaulting
   unknown input to **no** is intentional — agenthud is the expensive
   branch.

3. If **no**, emit:
   ```json
   next_phase_input = {
     ...previous fields...,
     "use_agenthud": false
   }
   ```
   `user_facing_summary` in `user_language`, intent:
   "agenthud unused — writing from web data only".
   Then end the phase. Do NOT proceed to step 4.

4. If **yes**, ask the user in `user_language`, intent:
   "Which period?
   1) today
   2) yesterday
   3) custom (YYYY-MM-DD)"

5. Parse the period reply:
   - `1` / `today` / language-equivalent → `target_date = "today"`
   - `2` / `yesterday` / language-equivalent → `target_date = "yesterday"`
   - `3` → reprompt once in `user_language`, intent:
     "Enter in YYYY-MM-DD format." Parse the second reply as ISO
     date (must match regex `^\d{4}-\d{2}-\d{2}$`).
   - Any direct `YYYY-MM-DD` input on the first prompt is also accepted.

   If the first reply matches none of the above forms (e.g.,
   `tomorrow`, `next week`, garbage text): reprompt once in
   `user_language`, intent: "Choose 1, 2, 3 or enter YYYY-MM-DD
   format." If the second reply also fails to parse, default to
   `target_date = "today"` and mention the fallback in
   `user_facing_summary`. Semantic validation of the ISO date (e.g.,
   "not in the future") is deferred — agenthud will simply return
   zero sessions on absurd dates and `report` will short-circuit
   gracefully.

6. Emit:
   ```json
   next_phase_input = {
     ...previous fields...,
     "use_agenthud": true,
     "target_date": "<resolved>"
   }
   ```
   `user_facing_summary` in `user_language`, intent:
   "agenthud using: {target_date}" (substitute `{target_date}` with
   the resolved value).

**Precedence note.** Both `target_date_default` (set by precheck) and
`target_date` (set here on the `use_agenthud=true` branch) may exist
in `next_phase_input`. Downstream phases MUST read `target_date` if
present and fall back to `target_date_default` only if `target_date`
is absent. On the `use_agenthud=false` branch this phase does not
emit `target_date` at all, but `report` short-circuits in that case
so the field is unused.

### report

Conditional phase. If `use_agenthud=false` from the previous phase,
return immediately with no activity data. Otherwise, fetch a
structured per-project activity report from agenthud and slice it
for the draft phase.

Steps:

0. Read `use_agenthud` from `previous_phase_input`. If `false`:
   - Set `next_phase_input = {...previous fields..., "report": null}`.
   - `user_facing_summary` in `user_language`, intent: "agenthud skipped"
   - End phase with `status=ok`. Do not invoke any Bash tool.

   This short-circuit uses 1 turn and ~$0.001. If `true`, proceed
   to step 1 below using `target_date` from `previous_phase_input`
   (or `target_date_default` if `target_date` is absent — see
   ask_agenthud's Precedence note).

1. Invoke the skill-vendored agenthud wrapper, redirecting stdout to
   a file. Do NOT capture stdout into the Bash tool result —
   high-activity days produce 50-200KB+ of JSON, past Claude Code's
   ~30k-char Bash output truncation.

   ```bash
   /skill/scripts/agenthud report \
     --date <target_date> \
     --format json \
     --include response,bash,edit \
     --detail-limit 200 \
     --with-git \
     > /tmp/agenthud-report.json
   ```

   The wrapper at `/skill/scripts/agenthud` is a shell script bundled
   with the skill. It pins `agenthud@0.9.2`, warms the npx cache
   before the real invocation (so first-run npm noise doesn't corrupt
   our JSON capture), and forwards stdout/stderr/exit code unchanged.

   The phase's `allowed_tools` whitelists exactly this path —
   `Bash(/skill/scripts/agenthud:*)` — instead of the broader
   `Bash(npx:*)`. That keeps the agent from invoking arbitrary
   npm packages.

   Notes:
   - `~/.claude/projects` is bind-mounted at agenthud's default path.
   - **`thinking` deliberately excluded** — Claude's internal reasoning
     is verbose and tweets are about what was SHIPPED, not what was
     thought.
   - `--with-git` requires the `project_roots` mounts to resolve each
     session's `cwd → .git` (see Per-user setup). Tweet-worthy
     commits appear as `◆` entries in activities.
   - `--detail-limit 200` caps activity body length so file size stays
     manageable on busy days.

2. Run ONE jq query that produces the slim per-project summary you
   need for the draft phase, written to a file. Do NOT iterate
   exploratorily — a single query is enough.

   ```bash
   jq '[.sessions[] | {
     project: .project,
     activity_count: (.activities | length),
     commits: ([.activities[] | select(.type == "commit") | .label] | unique),
     sample_responses: ([.activities[] | select(.type == "Response") | .text] | .[:3]),
     sample_edits: ([.activities[] | select(.type == "Edit") | .label] | .[:3]),
     sample_bash: ([.activities[] | select(.type == "Bash") | .label] | .[:3])
   }] | group_by(.project) | map({
     project: .[0].project,
     activity_count: (map(.activity_count) | add),
     commits: (map(.commits[]) | unique),
     sample_responses: (map(.sample_responses[]) | .[:4]),
     sample_edits: (map(.sample_edits[]) | unique | .[:5]),
     sample_bash: (map(.sample_bash[]) | unique | .[:5])
   })' /tmp/agenthud-report.json > /tmp/projects.json
   ```

   **Important shell-quoting notes:**
   - The whole jq filter is wrapped in single quotes; nothing inside
     needs escaping.
   - Use **explicit `{key: .key}` form** throughout — do NOT use jq's
     `{key1, key2}` shorthand. Some agent/shell wrappers strip braces
     or comma in the shorthand and jq then complains
     `"expecting ':'"`.

   The query groups by project (merging multiple sessions for the
   same project), takes ≤3-5 samples per category, and dedupes
   commits/edits/bash labels.

3. Read `/tmp/projects.json` (small, ~5-10KB). If the parsed array
   is empty (`[]`) — meaning agenthud found 0 sessions — do NOT
   stop the skill. Continue with
   `report = {"target_date": "<resolved>", "projects": []}`. The
   draft phase will fall back to the web-sourced inputs.
   `user_facing_summary` in `user_language`, intent:
   "No Claude Code activity today — continuing with web data".

4. Build `next_phase_input` for the draft phase, picking the 1-3
   most share-worthy items across all projects:

   ```json
   {
     ...previous fields including insights, interests_summary,
        interests_used, voice...,
     "max_tweet_chars": 280,
     "report": {
       "target_date": "2026-05-20",
       "projects": [
         {
           "name": "launcher",
           "highlights": ["...the 1-3 things worth tweeting..."],
           "commits": ["fix: ...", "feat: ..."]
         }
       ]
     }
   }
   ```

   "Share-worthy" means: a finished feature, a clear bug fix with a
   measurable result, a refactor that ships, a notable insight. Not:
   exploration without conclusion, tooling minor edits, work in
   progress without a milestone.

   `user_facing_summary` in `user_language`, intent:
   "Today's activity summary complete — {N} projects" (substitute
   `{N}` with the number of projects).

### draft

Write ONE English tweet ≤ `config.max_tweet_chars` (280) characters
in the user's `voice`, drawing on whichever inputs are available.

Inputs (read from `previous_phase_input`):

- `voice` (string) — user's tweet tone, from precheck/ask_once.
- `insights` (list of strings) — tone/structure rules from discover.
- `interests_summary` (string) — English prose summary from interests.
- `interests_used` (list of strings) — topic labels.
- `report` (object|null) — agenthud slice if user opted in; null otherwise.

Rules:

- The tweet **must be English**. Even when the user types Korean in
  the review phase, the tweet text stays English.
- Stay in `voice`. Apply 1-2 of the `insights` if natural — do not
  force them.
- Prioritize content sources in this order:
  1. If `report` is non-null and `report.projects` has a clear
     share-worthy highlight, lead with that (specific shipped thing).
  2. Otherwise lead with the most interesting thread from
     `interests_summary`.
- Use `insights` to shape the format (lead with a number, end with
  a question, etc.).
- Do NOT include hashtag chains. One hashtag at most, only if natural.
- Do NOT include URLs unless they are essential.

Output:

```json
next_phase_input = {
  ...previous fields...,
  "draft": "<English tweet, ≤ 280 chars>"
}
```

`user_facing_summary` in `user_language`, intent: "Draft complete".

### review

Run a localized review loop on the English draft.

**Language constraint:**
- All conversation with the user in this phase is in
  `execution_context.user_language`.
- The draft text itself is **English** and must NOT be translated
  when shown back to the user.
- The user's feedback may arrive in any language; understand it and
  apply to the English draft regardless.

Steps:

1. Show the draft (English) with its character count. Then ask in
   `user_language`, intent: "Go with this? Or edit? (empty = confirm)".

2. If the user gives empty input → treat as approval, jump to step 4.

3. If the user gives feedback (any language) → apply the feedback to
   the draft while keeping it in **English** and in `voice`. Verify
   ≤ `config.max_tweet_chars`. Go back to step 1.

   Cap at `config.max_review_iterations` (5) iterations. After the
   cap, force a binary decision in `user_language`, intent:
   "Review limit (5) reached. Post as-is? (y/N)".

4. Final confirmation in `user_language`, intent: "Post to X? (y/N)".

   - `y` / `yes` (any affirmative in user_language) → set
     `next_phase_input.approved_for_post = true` and proceed.
   - Anything else (including empty) → stop with `status=failed`,
     `error.code="user_declined"`, `user_facing_summary` in
     `user_language`, intent: "User canceled the post".

Output (on approval):
```json
next_phase_input = {
  ...previous fields...,
  "draft": "<final English tweet>",
  "approved_for_post": true
}
```
`user_facing_summary` in `user_language`, intent:
"Final confirmation — posting".

### post

Run the bundled helper:

```bash
python3 /skill/scripts/post.py "<approved draft>"
```

Parse the single JSON line from stdout.

- On `status="ok"`: set the phase `result` to the parsed JSON. Set
  `user_facing_summary` in `user_language`, intent: "Posted: <url>"
  (substitute `<url>` with the actual URL from the script output).
- On `status="failed"`: bubble up as `status=failed`,
  `error.code="x_post_failed"`. Set `user_facing_summary` in
  `user_language`, intent: "Post failed: <error>" (substitute
  `<error>` with the script's error message; truncate to 200 chars).

The `tweet_id` in `result` is the durable key for "what posted
when" — future retrieval depends on it.

## Constraints

- Do NOT call the X API yourself with curl or any HTTP tool. Use the
  bundled `post.py` — it handles OAuth 1.0a signing correctly.
- Single tweet only. No threads, no replies, no attachments in v0.1.
- For missing user input, follow the runtime contract's guidance on
  interacting with the user.
