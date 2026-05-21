# bip-daily-x Skill

Generate one tweet about the user's daily Claude Code work, refine
via user feedback, and post to X after explicit approval.

## Language Policy

- Agent reasoning, phase goals, field names: **English**.
- All user-facing strings (prompts shown to the user, error messages
  surfaced to the user, `user_facing_summary` per phase): **Korean**.
- The final tweet text: **English** (it is the deliverable).

This document uses English for clarity. The example user-facing
strings quoted in each phase below are the **verbatim** Korean
strings the agent must produce — do not paraphrase, do not translate.
When referring to internal field names (`voice`, `interests`,
`next_phase_input`, etc.) inside Korean prose, keep the field name
in English. Where a Korean example contains a brace-delimited token
like `{N}` or `{example}`, that token is a substitution placeholder
— replace it with the actual runtime value when emitting the string.

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
   message into `user_facing_summary` (Korean: "X 환경변수 누락: ...")
   so the user sees exactly which var(s) are missing.

2. Call `mcp__zipsa__ask_once` with key=`voice` (EXACTLY that — not
   `x_voice`, `tweet_voice`, or any other variant).
   Prompt (Korean):
   "1–2 문장으로 트윗 톤을 알려주세요."
   The cached answer is reused on subsequent runs.

3. Call `mcp__zipsa__ask_once` with key=`interests`.
   Build the Korean prompt at runtime by joining
   `config.default_interests` with `, ` and substituting into:
   `"관심 주제 3-5개를 쉼표로 입력해주세요. 예: {example}"`
   (so if the manifest's `default_interests` ever changes, the
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
   `user_facing_summary` (Korean): "프리체크 완료 — voice/interests 로드"

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
   `user_facing_summary` (Korean):
   - If `insights` is non-empty: `"BIP 트렌드 분석 완료 — 인사이트 {N}개"`
     (substitute `{N}` with the count, e.g., `3`).
   - If `insights` is empty: `"BIP 검색 결과 없음 — 웹 데이터 없이 계속"`.

### interests

Confirm or override the user's interest topics for this run, then
WebSearch and summarize current chatter on those topics.

1. Read `interests` from `previous_phase_input` (originally set by
   precheck from ask_once memory).

2. Ask the user (Korean), inlining the current list as a brace
   substitution. The prompt is a template — substitute
   `{interests_joined}` with the list joined by `, ` (plain text,
   no markdown emphasis — the runtime surfaces this as terminal
   output, where literal asterisks read worse than plain text):

   ```
   현재 저장된 관심사: {interests_joined}
   오늘은 이대로 검색할까요, 아니면 다른 주제로 갈까요?
   (다른 주제면 쉼표로 나열, 그대로면 엔터)
   ```

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
   phase; Korean status belongs in `user_facing_summary` only.)

7. Output `next_phase_input`:
   ```json
   {
     ...previous fields...,
     "interests_summary": "<English prose>",
     "interests_used": ["...", "...", "..."]
   }
   ```
   `user_facing_summary` (Korean):
   `"관심사 검색 요약 완료 — {N} 주제"` (substitute `{N}` with
   `len(interests_used)`).

### ask_agenthud

Decide whether to enrich the draft with today's Claude Code activity.

1. Ask the user (Korean):

   ```
   오늘 작업한 Claude Code 내용도 트윗에 반영할까요? (y/N)
   ```

2. Parse the reply. Treat as **yes** any of: `y`, `Y`, `yes`, `Yes`,
   `YES`, `네`, `예`, `응`, `ㅇ`, `ㅇㅇ`. Treat everything else
   (including empty input) as **no**. Defaulting unknown input to
   **no** is intentional — agenthud is the expensive branch.

3. If **no**, emit:
   ```json
   next_phase_input = {
     ...previous fields...,
     "use_agenthud": false
   }
   ```
   `user_facing_summary` (Korean): `"agenthud 미사용 — 웹 데이터로만 작성"`
   Then end the phase. Do NOT proceed to step 4.

4. If **yes**, ask the user (Korean):

   ```
   어떤 기간을 볼까요?
   1) today (오늘)
   2) yesterday (어제)
   3) 직접 입력 (YYYY-MM-DD)
   ```

5. Parse the period reply:
   - `1`, `today`, or `오늘` → `target_date = "today"`
   - `2`, `yesterday`, or `어제` → `target_date = "yesterday"`
   - `3` → reprompt once with: `"YYYY-MM-DD 형식으로 입력해주세요."`
     Parse the second reply as ISO date (must match regex
     `^\d{4}-\d{2}-\d{2}$`).
   - Any direct `YYYY-MM-DD` input on the first prompt is also accepted.

   If the first reply matches none of the above forms (e.g.,
   `tomorrow`, `next week`, garbage text): reprompt once with
   `"1, 2, 3 중 선택하거나 YYYY-MM-DD 형식으로 입력해주세요."`. If
   the second reply also fails to parse, default to
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
   `user_facing_summary` (Korean):
   `"agenthud 사용: {target_date}"` (substitute `{target_date}` with
   the resolved value).

**Precedence note.** Both `target_date_default` (set by precheck) and
`target_date` (set here on the `use_agenthud=true` branch) may exist
in `next_phase_input`. Downstream phases MUST read `target_date` if
present and fall back to `target_date_default` only if `target_date`
is absent. On the `use_agenthud=false` branch this phase does not
emit `target_date` at all, but `report` short-circuits in that case
so the field is unused.

### report

Fetch a structured per-project activity report from agenthud, then
extract per-project slices for the draft phase.

Steps:

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
   is empty (`[]`) — meaning agenthud found 0 sessions — stop the
   skill with `status=ok` and `user_facing_summary` "No Claude Code
   activity today — skipping post." No draft, no prompts, no post.

4. Build `next_phase_input` for the draft phase, picking the 1-3
   most share-worthy items across all projects:

   ```json
   {
     "target_date": "2026-05-20",
     "voice": "<from memory>",
     "max_tweet_chars": 280,
     "projects": [
       {
         "name": "launcher",
         "highlights": ["...the 1-3 things worth tweeting..."],
         "commits": ["fix: ...", "feat: ..."]
       }
     ]
   }
   ```

   "Share-worthy" means: a finished feature, a clear bug fix with a
   measurable result, a refactor that ships, a notable insight. Not:
   exploration without conclusion, tooling minor edits, work in
   progress without a milestone.

### draft

Write ONE tweet, ≤ `config.max_tweet_chars` (280) characters, in the
user's `voice`. The tweet should communicate the day's most
share-worthy progress — pick one concrete thing rather than a list.
Pass the text as `draft` to the next phase.

### review

Show the draft to the user and ask whether to revise. If they give
empty input, treat it as approval. If they give feedback, apply the
feedback while staying in voice, then re-show. Cap at
`config.max_review_iterations` rounds; after the cap, force a
yes/no decision.

Before posting, confirm one final time ("Post this to X?").
If the user says no, stop with `status=failed`,
`error.code="user_declined"`.

### post

Run the bundled helper:

```bash
python3 /skill/scripts/post.py "<approved draft>"
```

Parse the single JSON line from stdout.

- On `status="ok"`: set the phase `result` to the parsed JSON. Write
  `user_facing_summary` like "게시 완료: <url>" (or English).
- On `status="failed"`: bubble up as `status=failed`,
  `error.code="x_post_failed"`, with the script's `error` in
  `user_facing_summary` (truncated to 200 chars).

The `tweet_id` in `result` is the durable key for "what posted
when" — future retrieval depends on it.

## Constraints

- Do NOT call the X API yourself with curl or any HTTP tool. Use the
  bundled `post.py` — it handles OAuth 1.0a signing correctly.
- Single tweet only. No threads, no replies, no attachments in v0.1.
- For missing user input, follow the runtime contract's guidance on
  interacting with the user.
