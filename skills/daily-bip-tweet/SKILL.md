# daily-bip-tweet Skill

Orchestrator: fetch a day's Claude Code activity via the atomic
`agenthud-report` skill, draft a build-in-public tweet in the user's
voice, run a Korean-language review loop, and publish via the atomic
`x-post` skill.

## Orchestrator skill contract

This is an **orchestrator** skill: a composer that calls
`mcp__zipsa__run_skill` to delegate the heavy lifting to atomic
children. It owns the cross-cutting UX — voice management, BIP
research, draft+review loop — and emits a single coherent flow to
the user. Atomic children:

- `agenthud-report` — fetches per-project Claude Code activity
- `x-post` — publishes the approved tweet text to X

Composition uses `mcp__zipsa__run_skill` (declared in
`spec.children`) and `mcp__zipsa__get_artifact` (always available).

Note on UX boundaries: atomic children may have their own UX too
(per-caller routing namespaces their memory and prompts safely). The
split is by responsibility, not "atomic == silent". `agenthud-report`
owns its `project_roots` prompt because agenthud itself needs the
mounted paths. `x-post` is genuinely stateless — it gets the
finished tweet from the caller. `voice` lives in THIS orchestrator's
memory because tweet voice is a daily-bip-tweet decision; if a
future second tweet orchestrator wants the same voice, the right
fix is to extract a `tweet-voice` atomic and have both orchestrators
call it (BACKLOG until that second orchestrator exists).

## Language Policy

- Agent reasoning, phase goals, field names: **English**.
- All user-facing strings (prompts, error messages,
  `user_facing_summary`): **Korean**.
- The final tweet text: **English** (it is the deliverable).

Example user-facing Korean strings quoted below are **verbatim** —
do not paraphrase, do not translate. Brace-delimited tokens like
`{N}` or `{target_date}` are placeholders — substitute with the
actual runtime value.

## State carryover

Each phase reads `previous_phase_input` and emits `next_phase_input`.
Unless a phase explicitly says otherwise, `next_phase_input` MUST
include every field from `previous_phase_input` plus any new fields
this phase produces.

## Per-user setup

**Launcher-resolved (before container starts):**

No `spec.requires` on this skill. `project_roots` belongs to the
atomic `agenthud-report` skill, where agenthud actually runs. On
first invocation, the launcher prompts for `project_roots` against
`agenthud-report` via the parent HitlServer's stdin — the user sees
the prompt at the top-level terminal. Subsequent invocations read
the saved value.

**Environment (via `~/.zipsa/.env`):**

X credentials (`X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`,
`X_ACCESS_SECRET`) come through automatically. The orchestrator
verifies their presence in precheck before doing expensive work.

**Agent-time prompts (remembered in skill memory):**

- `voice` — 1–2 sentences describing the user's tweet tone. Asked
  once on first run, reused thereafter.
- `interests` — comma-separated list of 3-5 topics for the
  `interests` phase to search every day. Asked once. The manifest's
  `config.default_interests` is shown as an example in the prompt;
  the stored value is whatever the user types.

## Period semantics

- Default: **today** (manifest `default_query`).
- Override via `user_query`: `today`, `yesterday`, or
  `YYYY-MM-DD`.

## Phases

### precheck

Verify X credentials, ensure `voice` and `interests` are remembered,
resolve target date.

1. Verify all 4 X env vars are present. The launcher injects them
   via `--env-file ~/.zipsa/.env` into the container's environment;
   they are NOT exposed inside the `execution_context` JSON, so you
   must read them via a shell call. Run exactly:

   ```bash
   python3 -c 'import os; missing = [k for k in ("X_API_KEY","X_API_SECRET","X_ACCESS_TOKEN","X_ACCESS_SECRET") if not os.environ.get(k)]; print(",".join(missing) if missing else "OK")'
   ```

   - Output `OK` → continue to step 2.
   - Output is a comma-separated list of variable names → stop with
     `status="failed"`, `error.code="x_credentials_missing"`,
     `user_facing_summary` (Korean):
     `"X 환경변수 누락: <the comma-separated list from stdout>"`.

   Do NOT decide the env vars are missing based on the JSON
   `execution_context` alone — that block doesn't carry process
   environment.

2. Call `mcp__zipsa__ask_once` with key=`voice` (EXACTLY that — not
   `x_voice`, `tweet_voice`, etc.). Prompt (Korean):
   `"1–2 문장으로 트윗 톤을 알려주세요."`
   Cached answer is reused on subsequent runs.

3. Call `mcp__zipsa__ask_once` with key=`interests`. Build the
   Korean prompt at runtime by joining `config.default_interests`
   with `, ` and substituting into:
   `"관심 주제 3-5개를 쉼표로 입력해주세요. 예: {example}"`
   (so when the manifest's `default_interests` changes, the prompt's
   example stays in sync — do not hardcode the items).
   Parse the user's response into a list of trimmed non-empty
   strings. Store the raw response into ask_once memory under
   key=`interests`; pass the parsed list downstream as
   `next_phase_input.interests`.

4. Resolve `target_date` from `user_query`:
   - empty / whitespace / `today` → today in user's local timezone
   - `yesterday` → yesterday in user's local timezone
   - `YYYY-MM-DD` (matches `^\d{4}-\d{2}-\d{2}$`) → use verbatim
   - anything else → default to today and mention the fallback in
     `user_facing_summary`.

5. Output `next_phase_input`:
   ```json
   {
     "voice": "<from ask_once>",
     "interests": ["...", "...", "..."],
     "target_date": "YYYY-MM-DD"
   }
   ```

   `user_facing_summary` (Korean):
   `"프리체크 완료 — voice/interests 로드, target {target_date}"`.

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

2. If `result.status != "ok"`, do NOT stop the skill — agenthud is
   nice-to-have for this skill, not required. Set
   `next_phase_input.report = null` and log
   `user_facing_summary` (Korean):
   `"agenthud 실패 — 웹 데이터로만 진행"`. Continue to discover.

3. Read the artifact **directly off the child-runs mount** — NOT
   via `mcp__zipsa__get_artifact` (that would pull the full JSON
   through MCP and bust Claude's per-tool-result token cap on big
   days). The launcher pre-mounts the child's runs dir at
   `/home/agent/children/agenthud-report/runs/<run_id>/artifacts/`.

   Use `Bash(jq:*)` to project — your goal is the `activity_slim`
   shape below, not the full artifact in your context. Example:

   ```bash
   ART=/home/agent/children/agenthud-report/runs/<run_id>/artifacts/agenthud-report.json
   wc -c "$ART"   # sanity check: file exists and is non-trivial
   jq '.sessions | length' "$ART"
   ```

   The raw agenthud schema (see `agenthud-report` SKILL.md):
   `{date, sessions[{project, start, end,
   activities[{time, icon, label, detail}], subAgents}]}`. Activity
   icons that matter for tweet drafting:
   - `◆` = commit (label=SHA, detail=commit subject) — the gold
     standard for "share-worthy" content
   - `~` = file edits
   - `<` = agent responses (often the most quote-worthy prose)

4. If `jq '.sessions | length' "$ART"` returns 0 (no activity on the
   date), set `next_phase_input.activity_slim = null` and use Korean
   `user_facing_summary`: `"오늘 Claude Code 활동 없음 — 웹 데이터로 진행"`.

5. Reduce the artifact to a draft-friendly slim shape (the
   draft phase reads this, NOT the full artifact — keeps that
   phase's token budget reasonable):

   ```json
   "activity_slim": {
     "commits": [
       {"project": "launcher", "sha": "9bca6ab", "subject": "perf: gate ..."}
     ],
     "by_project": {
       "launcher": {
         "edit_count": 60, "bash_count": 193, "response_count": 115,
         "sample_responses": ["<first 2-3 Response details, trimmed>"],
         "sample_edits": ["<first 2-3 Edit details>"]
       }
     }
   }
   ```

   A single jq one-liner produces this shape; run it and capture
   stdout, then put the parsed result into `next_phase_input`:

   ```bash
   jq '{
     commits: [.sessions[] as $s | $s.activities[]
                | select(.icon == "◆")
                | {project: $s.project, sha: .label, subject: .detail}],
     by_project: (
       [.sessions[] | {project, activities}]
       | group_by(.project) | map({
           project: .[0].project,
           activities: [.[].activities[]]
         }) | map({
           key: .project,
           value: {
             edit_count:     ([.activities[] | select(.icon == "~")] | length),
             bash_count:     ([.activities[] | select(.icon == "$")] | length),
             response_count: ([.activities[] | select(.icon == "<")] | length),
             sample_responses: ([.activities[] | select(.icon == "<") | .detail[:200]] | .[:3]),
             sample_edits:     ([.activities[] | select(.icon == "~") | .detail] | unique | .[:3])
           }
         }) | from_entries
     )
   }' "$ART"
   ```

   Shell-quoting: the whole filter is single-quoted; the `$s`
   inside is jq's variable syntax, not a shell variable — it stays
   intact. Use **explicit `{key: .key}` form** as in agenthud-report —
   the `{key1, key2}` shorthand can be mangled by some wrappers.

6. Output `next_phase_input`:
   ```json
   {
     ...previous fields...,
     "activity_slim": <the slim shape above, or null>
   }
   ```

   `user_facing_summary` (Korean):
   - success with commits: `"agenthud 로드 — {N} 프로젝트, {C} 커밋"`
   - success no commits: `"agenthud 로드 — {N} 프로젝트 (커밋 없음)"`
   - success empty: `"오늘 Claude Code 활동 없음 — 웹 데이터로 진행"`
   - failure: `"agenthud 실패 — 웹 데이터로만 진행"`

### discover

Search public build-in-public chatter via `WebSearch` and extract
3-5 short tone/structure insights for the draft phase.

1. Read `config.discover_query` from the manifest at runtime (do not
   hardcode the literal — the manifest is the source of truth).

2. Call `WebSearch` with that query. Examples of useful results:
   short personal updates with metrics, before/after framing,
   question-style hooks.

3. Extract **3-5 short insights** about what format/tone is working
   today. Phrase as actionable rules a tweet writer could apply.
   The bullets below are illustrative shapes only — do not reuse
   verbatim:

   - "Lead with a concrete number in the first 50 chars"
   - "Before/after framing outperforms generic 'shipped X' posts"
   - "End with a question to drive replies"

   Do NOT copy other people's tweet text verbatim. Distill, don't
   paste.

4. On `WebSearch` error, retry **exactly once**. If the retry also
   fails OR returns 0 results, set `insights = []` and continue.

5. Output `next_phase_input`:
   ```json
   {
     ...previous fields...,
     "insights": ["...", "...", "..."]
   }
   ```

   `user_facing_summary` (Korean):
   - non-empty: `"BIP 트렌드 분석 완료 — 인사이트 {N}개"`
   - empty: `"BIP 검색 결과 없음 — 웹 데이터 없이 계속"`

### interests_search

WebSearch the user's interest topics and summarize current chatter
in English prose (the draft phase is English).

1. Read `previous_phase_input.interests` (set by precheck from
   ask_once).

2. Call `WebSearch`. If `interests` has 1-2 items, do a single
   query joining them with `OR`. If 3+ items, do **one query per
   item in list order, capped at the first 3 items**, to keep tool
   budget reasonable and behavior deterministic.

3. Synthesize the result snippets into **3-5 sentences of English
   prose** summarizing what's notable across these topics today.
   This text feeds the English `draft` phase.

   Example shape only — your prose must reflect today's actual
   results, not these phrasings:
   > "Topic A is seeing a wave of activity around <specific angle>
   > this week. Topic B's conversation is dominated by <specific
   > question>. Topic C is comparatively quiet, with the main thread
   > being <specific concern>."

4. On `WebSearch` 0-results or repeated failure, set
   `interests_summary = "(no search results)"` and continue. Plain
   English — this field is consumed by the English draft phase;
   Korean status belongs in `user_facing_summary`.

5. Output `next_phase_input`:
   ```json
   {
     ...previous fields...,
     "interests_summary": "<English prose>"
   }
   ```

   `user_facing_summary` (Korean):
   `"관심사 검색 요약 완료 — {N} 주제"` (substitute `{N}` with
   the number of items actually queried, max 3).

### draft

Write ONE English tweet ≤ `config.max_tweet_chars` (280) in the
user's `voice`.

Inputs (from `previous_phase_input`):

- `voice` (string)
- `insights` (list of strings)
- `interests_summary` (string)
- `interests` (list of strings) — topic labels
- `activity_slim` (object|null) — fetch phase's reduced agenthud
  view: `{commits: [{project, sha, subject}], by_project: {...}}`

Rules:

- The tweet **must be English**. Even if the user reviews in Korean,
  the tweet text stays English.
- Stay in `voice`. Apply 1-2 of the `insights` if natural — do not
  force them.
- Prioritize content sources in this order:
  1. If `activity_slim.commits` is non-empty, pick the 1-2 most
     share-worthy commit subjects and lead with what shipped.
  2. Else if `activity_slim.by_project.*.sample_responses` has a
     finished-sounding line (e.g. "shipped", "fixed", "merged"),
     use that.
  3. Otherwise lead with the most interesting thread from
     `interests_summary`.
- Shape with `insights` (lead with a number, end with a question,
  etc.).
- NO hashtag chains. One hashtag at most, only if natural.
- NO URLs unless essential.

Output:

```json
next_phase_input = {
  ...previous fields...,
  "draft": "<English tweet, ≤ 280 chars>"
}
```

`user_facing_summary` (Korean): `"초안 작성 완료"`.

### review

Run a Korean-language review loop on the English draft.

**Language constraint:**
- All conversation with the user in this phase is **Korean**.
- The draft text itself is **English** and must NOT be translated
  when shown back to the user.

Steps:

1. Show the draft (English) with its character count. Ask (Korean):
   ```
   이대로 갈까요? 수정 요청? (엔터=확정)
   ```

2. If the user gives empty input → treat as approval, jump to step 4.

3. If the user gives feedback (Korean or English) → apply the
   feedback to the draft while keeping it in **English** and in
   `voice`. Verify ≤ `config.max_tweet_chars`. Go back to step 1.

   Cap at `config.max_review_iterations` (5). After the cap, force
   a binary decision with:
   ```
   추가 수정 한도(5회) 도달. 이대로 게시할까요? (y/N)
   ```

4. Final confirmation (Korean):
   ```
   X에 게시할까요? (y/N)
   ```

   - `y` / `yes` / `네` / `예` / `응` / `ㅇ` / `ㅇㅇ` →
     `approved_for_post = true`, proceed.
   - Anything else (including empty) → stop with `status="failed"`,
     `error.code="user_declined"`,
     `user_facing_summary` (Korean): `"사용자가 게시를 취소했습니다"`.

Output (on approval):

```json
next_phase_input = {
  ...previous fields...,
  "draft": "<final English tweet>",
  "approved_for_post": true
}
```

`user_facing_summary` (Korean): `"최종 컨펌 완료 — 게시 진행"`.

### post

Invoke the atomic `x-post` child skill with the approved draft.

1. Call:
   ```python
   result = mcp__zipsa__run_skill(
     name="x-post",
     args=previous_phase_input.draft
   )
   ```

2. If `result.status != "ok"`, stop with `status="failed"`,
   `error.code="x_post_failed"`,
   `user_facing_summary` (Korean):
   `"게시 실패: <result.summary.error.message truncated to 200 chars>"`.

3. On success, read the artifact off the child-runs mount:

   ```bash
   ART=/home/agent/children/x-post/runs/<result.run_id>/artifacts/tweet-result.json
   cat "$ART"   # tiny file (~200 bytes)
   ```

   Contents: `{"status": "ok", "tweet_id", "url", "text"}`.

4. Set the phase `result` (final phase — surfaces to the user):
   ```json
   {
     "tweet_id": "<from artifact>",
     "url": "<from artifact>",
     "text": "<from artifact>"
   }
   ```

   `user_facing_summary` (Korean):
   `"게시 완료: <url>"`.

The `tweet_id` is the durable key for "what posted when".

## Constraints

- Do NOT call the X API directly — only via `mcp__zipsa__run_skill(name="x-post", ...)`.
- Single tweet only — no threads, no replies, no attachments in v0.1.
- Phase isolation convention: phases that use HITL (precheck, review)
  do not also call `mcp__zipsa__run_skill`, and vice versa. The
  launcher does not enforce this — both tool families are always
  available — but mixing them in one phase tangles user-interaction
  policy with child-skill orchestration. Keep them in separate
  phases for reviewability.
- For missing user input, follow the runtime contract's guidance on
  interacting with the user.
