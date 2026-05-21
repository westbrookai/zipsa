# bip-daily-x Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `agenthud` opt-in per run; always do web-sourced BIP inspiration + user-interest summary; English tweet, Korean conversation; explicit confirm before post.

**Architecture:** Insert three new phases (`discover`, `interests`, `ask_agenthud`) between `precheck` and `report`; turn `report` into a short-circuiting phase that no-ops when `use_agenthud=false`; bump version `0.2.4 → 0.3.0`.

**Tech Stack:** zipsa manifest YAML (`apiVersion: zipsa.dev/v1alpha1`), SKILL.md (markdown agent instructions), Claude Haiku 4.5 + Sonnet 4.6, WebSearch built-in tool. No new scripts — existing `scripts/agenthud` wrapper and `scripts/post.py` are unchanged.

**Spec:** `docs/superpowers/specs/2026-05-21-bip-daily-x-redesign.md`

**Working directory:** all paths in this plan are relative to the worktree root `/Users/neochoon/WestbrookAI/zipsa/.worktrees/bip-daily-x-rework`.

---

## File Structure

Files this plan touches (all inside the worktree):

- **Modify** `skills/bip-daily-x/manifest.yaml` — version bump, new config keys, new state_schema entries, three new phase definitions, updated aggregate limits.
- **Modify** `skills/bip-daily-x/SKILL.md` — new Language Policy header, extended precheck (asks `interests` ask_once), three new phase sections (`discover`, `interests`, `ask_agenthud`), short-circuit clause in `report`, updated `draft` / `review` / `post` sections.

No new files. No script changes. No other files touched.

---

## Verification Cycle (skill-specific)

Skills don't have unit tests. Each task ends with a verification step using:

```bash
uvx zipsa validate ./skills/bip-daily-x
```

The final task runs a dry-run + a live smoke test.

---

## Task 1: Rewrite manifest.yaml (version, config, state_schema, phases, limits)

**Files:**
- Modify: `skills/bip-daily-x/manifest.yaml` (entire file)

- [ ] **Step 1: Replace the manifest with the new version**

Overwrite `skills/bip-daily-x/manifest.yaml` with the content below (preserves all comments from the current file; adds new phases and config). This is one atomic rewrite — copy verbatim:

```yaml
apiVersion: zipsa.dev/v1alpha1
kind: Skill
metadata:
  name: bip-daily-x
  version: 0.3.0
  author: westbrookai
  description: |
    Generate, refine via user feedback, and post a single daily tweet
    about your build-in-public progress. Pulls inspiration from public
    BIP chatter and your interest topics; optionally enriches with
    today's Claude Code activity via agenthud.
  tags: [productivity, build-in-public, x, claude-code]

spec:
  purpose: |
    Produce one tweet per day. Always pull web context (BIP trends +
    user interest summary). Optionally enrich with agenthud activity
    if the user opts in. Iterate via Korean-language review, then
    post to X after explicit approval. Refuse anything not in scope.

  instructions: ./SKILL.md

  # bip-daily-x is meant to be run daily with no args (`zipsa run bip-daily-x`).
  # Without a default_query the launcher leaves user_query empty, which
  # triggers the runtime contract's empty-query 집사 intro path — the
  # agent then burns budget on contract-reading instead of doing precheck
  # work. "today" is the sensible default; users can override by passing
  # "yesterday" or an ISO date.
  default_query: today

  # Sonnet 4.6 handles tweet drafting + Korean feedback refinement well;
  # mechanical and web-search phases override to Haiku below.
  model:
    name: claude-sonnet-4-6

  # Per-user host-side values the launcher resolves before container start.
  requires:
    project_roots:
      type: list[directory]
      prompt: |
        Which directories contain your git projects?
        These get mounted read-only so agenthud --with-git can resolve
        the .git directory referenced by each Claude session's `cwd` —
        the tweet draft benefits from knowing what was committed.
        One path per line, ~ is expanded; empty line to finish.

  mounts:
    # Bind-mount Claude session logs so agenthud picks them up.
    - host: ~/.claude/projects
      container: /home/agent/.claude/projects
      mode: ro

    # Mount each project root at its own host path inside the container
    # so agenthud --with-git resolves the session's cwd to a real .git.
    - source: requires.project_roots
      preserve_host_path: true
      mode: ro

  # X creds (X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET)
  # come through ~/.zipsa/.env automatically. The precheck phase
  # verifies their presence by inspecting execution_context.

  tools:
    builtin: []

  config:
    default_target_date: today
    max_review_iterations: 5
    max_tweet_chars: 280
    agenthud_version: "0.9.2"        # pinned for reproducibility
    discover_query: "#buildinpublic OR #buildinpublic AI"
    # Example topics shown to the user on first run as the ask_once
    # prompt for `interests`. The persisted value is the user's input.
    default_interests:
      - AI agents
      - MCP
      - build-in-public

  state_schema:
    insights:
      type: list[string]
    interests_summary:
      type: string
    use_agenthud:
      type: bool
    target_date:
      type: string
    report:
      type: object
    draft:
      type: string

  phases:
    - id: precheck
      goal: |
        Verify the 4 X credentials are present, ensure voice and
        interests are remembered, resolve target date.
      # Mechanical: env-var check via script + two ask_once calls.
      # Haiku is ~10x cheaper than Sonnet on this kind of work, but
      # Haiku is also verbose in its thinking — give it a bit more
      # room than the absolute minimum.
      model:
        name: claude-haiku-4-5-20251001
      allowed_tools:
        - Bash(python3:*)
      limits:
        max_turns: 8
        max_cost_usd: 0.10
        timeout_seconds: 120

    - id: discover
      goal: |
        WebSearch public build-in-public chatter and extract 3-5
        tone/structure insights for the draft phase.
      model:
        name: claude-haiku-4-5-20251001
      allowed_tools:
        - WebSearch
      limits:
        max_turns: 8
        max_cost_usd: 0.10
        timeout_seconds: 120

    - id: interests
      goal: |
        Confirm or override stored interest topics, then WebSearch
        and summarize current chatter on those topics.
      model:
        name: claude-haiku-4-5-20251001
      allowed_tools:
        - WebSearch
      limits:
        max_turns: 8
        max_cost_usd: 0.10
        timeout_seconds: 180

    - id: ask_agenthud
      goal: |
        Ask whether to fetch today's Claude Code activity; if yes,
        ask which period (today/yesterday/YYYY-MM-DD).
      model:
        name: claude-haiku-4-5-20251001
      allowed_tools: []
      limits:
        max_turns: 4
        max_cost_usd: 0.05
        timeout_seconds: 600

    - id: report
      goal: |
        Conditional. If use_agenthud=false, immediately return
        result.report=null. If true, run agenthud for the target
        date and produce a structured per-project activity summary.
      # Mechanical: bash npx + jq slicing + a small JSON dump.
      model:
        name: claude-haiku-4-5-20251001
      allowed_tools:
        - Bash(/skill/scripts/agenthud:*)   # tight: only this wrapper, no arbitrary npx
        - Bash(echo:*)        # harmless; lets agent log status
        - Bash(jq:*)          # primary slicing tool for large reports
        - Bash(wc:*)          # let agent check file size
        - Read                # fallback for small files / inspecting jq output
      limits:
        max_turns: 14
        max_cost_usd: 0.20
        timeout_seconds: 240

    - id: draft
      goal: |
        Write a single English tweet (<= max_tweet_chars) in the
        user's voice, combining insights + interests_summary +
        optional report.
      # Sonnet 4.6 (inherited from spec.model) — creative tweet writing
      # is where the better model actually shows up, but Opus is overkill
      # for 280 chars.
      allowed_tools: []
      limits:
        max_turns: 3
        max_cost_usd: 0.05
        timeout_seconds: 60

    - id: review
      goal: |
        Show the English draft to the user and run a Korean review
        loop. Accept feedback, regenerate up to 5 times, then
        confirm before posting.
      # Sonnet (inherited) — iterating on user feedback while keeping
      # voice consistent and the tweet in English.
      allowed_tools: []
      limits:
        max_turns: 10
        max_cost_usd: 0.20
        timeout_seconds: 1800

    - id: post
      goal: |
        Run /skill/scripts/post.py with the approved draft; surface
        the tweet URL and id in Korean user_facing_summary.
      # Mechanical: invoke a script with prepared input.
      model:
        name: claude-haiku-4-5-20251001
      allowed_tools:
        - Bash(python3:*)
      limits:
        max_turns: 4
        max_cost_usd: 0.05
        timeout_seconds: 60

  # Aggregate run limits (sum across phases should not exceed)
  limits:
    max_turns: 59         # 8+8+8+4+14+3+10+4 = 59
    max_cost_usd: 0.85
    timeout_seconds: 3180 # 120+120+180+600+240+60+1800+60 = 3180
```

- [ ] **Step 2: Validate**

Run from the worktree root:

```bash
uvx zipsa validate ./skills/bip-daily-x
```

Expected: passes with no errors.

If validation complains about `state_schema` types or `config` shape, capture the error and adjust the format to match what the launcher accepts (`launcher/zipsa/core/models.py` is the source of truth).

- [ ] **Step 3: Commit**

```bash
git add skills/bip-daily-x/manifest.yaml
git commit -m "feat(bip-daily-x): manifest 0.3.0 — discover/interests/ask_agenthud phases + new config

- Bump version 0.2.4 -> 0.3.0
- Add three new phases between precheck and report
- Make report phase conditional (use_agenthud flag from ask_agenthud)
- New config: discover_query, default_interests
- New state_schema entries for inter-phase data flow
- Adjusted aggregate limits to cover new phases

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: SKILL.md — Language Policy header + precheck `interests` ask_once

**Files:**
- Modify: `skills/bip-daily-x/SKILL.md` (top of file + Per-user setup + precheck section)

- [ ] **Step 1: Add Language Policy section near the top**

Insert this section in `SKILL.md` directly after the first paragraph (the one ending "post to X after explicit approval."), before "## Per-user setup":

```markdown
## Language Policy

- Agent reasoning, phase goals, field names: **English**.
- All user-facing strings (prompts shown to the user, error messages
  surfaced to the user, `user_facing_summary` per phase): **Korean**.
- The final tweet text: **English** (it is the deliverable).

This document uses English for clarity. The example user-facing
strings quoted in each phase below are the exact Korean strings the
agent must produce.
```

- [ ] **Step 2: Update "Agent-time" block in Per-user setup**

Find this existing block:

```markdown
**Agent-time (remembered in skill memory):**

- `voice` — 1–2 sentences describing the user's preferred tweet tone.
  Asked once on first run, then reused.
```

Replace with:

```markdown
**Agent-time (remembered in skill memory):**

- `voice` — 1–2 sentences describing the user's preferred tweet tone.
  Asked once on first run, then reused.
- `interests` — comma-separated list of 3-5 topics the user wants
  the `interests` phase to web-search every day. Asked once on first
  run. `config.default_interests` is shown as an example in the prompt
  but the stored value is whatever the user types.
```

- [ ] **Step 3: Update precheck section**

Find the existing precheck section (the part that lists items 1, 2, 3 — env check, voice ask_once, target date) and replace it with:

```markdown
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
   Prompt (Korean) — use `config.default_interests` as the example list:
   "관심 주제 3-5개를 쉼표로 입력해주세요. 예: AI agents, MCP, build-in-public"
   Parse the response into a list of trimmed strings.
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
```

- [ ] **Step 4: Validate**

```bash
uvx zipsa validate ./skills/bip-daily-x
```

Expected: passes.

- [ ] **Step 5: Commit**

```bash
git add skills/bip-daily-x/SKILL.md
git commit -m "feat(bip-daily-x): language policy header + precheck asks interests

- Add Language Policy section: English reasoning, Korean user-facing, English tweet
- precheck now calls ask_once for new 'interests' key alongside 'voice'
- target_date_default now passed downstream so ask_agenthud can override

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: SKILL.md — add `discover` section

**Files:**
- Modify: `skills/bip-daily-x/SKILL.md` (insert new section after precheck, before existing report section)

- [ ] **Step 1: Insert `discover` section between precheck and report**

Insert this section in SKILL.md immediately after the precheck section, before the `### report` heading:

````markdown
### discover

Search public build-in-public tweets via the WebSearch built-in tool
and extract tone/structure insights for the draft phase.

1. Build the WebSearch query from `config.discover_query` (default:
   `"#buildinpublic OR #buildinpublic AI"`).

2. Call `WebSearch` with that query. Aim for 5-10 results. Examples
   of useful results: short personal updates with metrics, before/after
   framing, question-style hooks.

3. From the result snippets, extract **3-5 short insights** about what
   format/tone is working today. Phrase them as actionable rules a
   tweet writer could apply. Examples:

   - "Lead with a concrete number in the first 50 chars"
   - "Before/after framing outperforms generic 'shipped X' posts"
   - "End with a question to drive replies"

   Do NOT copy other people's tweet text verbatim (avoidance of
   inadvertent plagiarism). Distill, don't paste.

4. If WebSearch returns 0 results OR fails twice in a row, set
   `insights=[]` and continue — the draft phase can still operate.

5. Output `next_phase_input`:
   ```json
   {
     ...previous fields...,
     "insights": ["...", "...", "..."]
   }
   ```
   `user_facing_summary` (Korean):
   "BIP 트렌드 분석 완료 — 인사이트 N개"
   (where N is the count of insights; if 0, say "인사이트 추출 실패, 계속 진행").
````

- [ ] **Step 2: Validate**

```bash
uvx zipsa validate ./skills/bip-daily-x
```

Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add skills/bip-daily-x/SKILL.md
git commit -m "feat(bip-daily-x): add discover phase — WebSearch BIP insights

WebSearch on config.discover_query, extracts 3-5 tone/structure insights
as short actionable rules. Tolerates empty results (continues with insights=[]).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: SKILL.md — add `interests` section

**Files:**
- Modify: `skills/bip-daily-x/SKILL.md` (insert new section after discover, before report)

- [ ] **Step 1: Insert `interests` section immediately after discover**

Insert this section in SKILL.md directly after the new `discover` section:

````markdown
### interests

Confirm or override the user's interest topics for this run, then
WebSearch and summarize current chatter on those topics.

1. Read `interests` from `previous_phase_input` (originally set by
   precheck from ask_once memory).

2. Ask the user (Korean), inlining the current list:

   ```
   현재 저장된 관심사: {interests_joined}
   오늘은 이대로 검색할까요, 아니면 다른 주제로 갈까요?
   (다른 주제면 쉼표로 나열, 그대로면 엔터)
   ```

   where `{interests_joined}` is the list joined with `, ` and wrapped
   in `**bold**` for the user prompt (e.g., `**AI agents, MCP, build-in-public**`).

3. Parse the user reply:
   - Empty / whitespace only → `interests_used = interests` (the stored list).
   - Non-empty → parse as comma-separated, trim each item.
     `interests_used = parsed list`. Do NOT overwrite the stored
     `interests` ask_once value — this override is for this run only.
   - On parse failure (no comma-splittable items) → reprompt once.
     If second attempt also fails, fall back to the stored list and
     mention in `user_facing_summary`.

4. Call `WebSearch`. If `interests_used` has 1-2 items, do a single
   query joining them with `OR`. If 3+ items, do up to 3 separate
   queries (one per top-3 items) to keep tool budget reasonable.

5. Synthesize the result snippets into **3-5 sentences of English
   prose** summarizing what's notable across these topics today.
   This text feeds the English draft phase directly.

   Examples:
   > "Agent frameworks are seeing a wave of multi-agent orchestration
   > posts this week, especially around handoff protocols. MCP
   > adoption questions are dominant — people are asking what to
   > standardize next. ..."

6. On WebSearch 0-results or repeated failure, set
   `interests_summary = "(검색 결과 없음)"` and continue.

7. Output `next_phase_input`:
   ```json
   {
     ...previous fields...,
     "interests_summary": "<English prose>",
     "interests_used": ["...", "...", "..."]
   }
   ```
   `user_facing_summary` (Korean):
   "관심사 검색 요약 완료 — {N} 주제"
````

- [ ] **Step 2: Validate**

```bash
uvx zipsa validate ./skills/bip-daily-x
```

Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add skills/bip-daily-x/SKILL.md
git commit -m "feat(bip-daily-x): add interests phase — per-run override + WebSearch summary

Shows stored interests, accepts override (this-run-only), runs WebSearch,
produces 3-5 sentence English summary for draft phase.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: SKILL.md — add `ask_agenthud` section

**Files:**
- Modify: `skills/bip-daily-x/SKILL.md` (insert section after interests, before report)

- [ ] **Step 1: Insert `ask_agenthud` section immediately after interests**

Insert this section in SKILL.md directly after the `interests` section:

````markdown
### ask_agenthud

Decide whether to enrich the draft with today's Claude Code activity.

1. Ask the user (Korean):

   ```
   오늘 작업한 Claude Code 내용도 트윗에 반영할까요? (y/N)
   ```

2. Parse: any of `y`, `Y`, `yes`, `네`, `예` → yes. Anything else
   (including empty input) → no.

3. If **no**:
   ```json
   next_phase_input = {
     ...previous fields...,
     "use_agenthud": false
   }
   ```
   `user_facing_summary` (Korean): "agenthud 미사용 — 웹 데이터로만 작성"

4. If **yes**, ask for the period (Korean):

   ```
   어떤 기간을 볼까요?
   1) today (오늘)
   2) yesterday (어제)
   3) 직접 입력 (YYYY-MM-DD)
   ```

5. Parse the period reply:
   - `1` or `today` → `target_date = "today"`
   - `2` or `yesterday` → `target_date = "yesterday"`
   - `3` → prompt once more: "YYYY-MM-DD 형식으로 입력해주세요." Parse
     as ISO date (regex `^\d{4}-\d{2}-\d{2}$`).
   - Any direct YYYY-MM-DD input on the first prompt also accepted.
   - On any parse failure → reprompt once. On second failure, default
     to `today` and mention in `user_facing_summary`.

6. Output:
   ```json
   next_phase_input = {
     ...previous fields...,
     "use_agenthud": true,
     "target_date": "<resolved>"
   }
   ```
   `user_facing_summary` (Korean): "agenthud 사용: {target_date}"
````

- [ ] **Step 2: Validate**

```bash
uvx zipsa validate ./skills/bip-daily-x
```

Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add skills/bip-daily-x/SKILL.md
git commit -m "feat(bip-daily-x): add ask_agenthud phase — opt-in gate + period menu

Asks user whether to use agenthud, then on yes asks for period
(today/yesterday/YYYY-MM-DD). Sets use_agenthud + target_date for
the conditional report phase.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: SKILL.md — make `report` short-circuit on `use_agenthud=false`

**Files:**
- Modify: `skills/bip-daily-x/SKILL.md` (existing `### report` section, replace first paragraph)

- [ ] **Step 1: Replace the report section opening**

Find the existing `### report` section header and its first paragraph:

```markdown
### report

Fetch a structured per-project activity report from agenthud, then
extract per-project slices for the draft phase.

Steps:
```

Replace with:

```markdown
### report

Conditional phase. If `use_agenthud=false` from the previous phase,
return immediately with no activity data. Otherwise, fetch a
structured per-project activity report from agenthud and slice it
for the draft phase.

Steps:

0. Read `use_agenthud` from `previous_phase_input`. If `false`:
   - Set `next_phase_input = {...previous fields..., "report": null}`.
   - `user_facing_summary` (Korean): "agenthud 건너뜀"
   - End phase with `status=ok`. Do not invoke any Bash tool.

   This short-circuit uses 1 turn and ~$0.001. If `true`, proceed
   to step 1 below using `target_date` from `previous_phase_input`.
```

(The numbered steps 1-4 that follow stay as they are — they describe
the actual agenthud invocation and jq slicing. Don't touch them.)

- [ ] **Step 2: Inside the existing step 4 ("Build `next_phase_input`"), update the JSON example**

Find the existing step-4 JSON example in the report section (the one
showing `target_date`, `voice`, `max_tweet_chars`, `projects`) and
replace it with this updated JSON that carries forward the new fields:

```json
{
  ...previous fields including insights, interests_summary, interests_used, voice...,
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

`user_facing_summary` (Korean): "오늘 활동 요약 완료 — {N} 프로젝트"

Also: the existing instruction that says "If the parsed array is
empty (`[]`) — meaning agenthud found 0 sessions — stop the skill
with `status=ok` and `user_facing_summary` "No Claude Code activity
today — skipping post." No draft, no prompts, no post." — replace
that with:

```markdown
3. Read `/tmp/projects.json` (small, ~5-10KB). If the parsed array
   is empty (`[]`) — meaning agenthud found 0 sessions — do NOT
   stop the skill. Continue with `report={"target_date": ..., "projects": []}`.
   The draft phase will fall back to the web-sourced inputs.
   `user_facing_summary` (Korean): "오늘 Claude Code 활동 없음 — 웹 데이터로 진행"
```

- [ ] **Step 3: Validate**

```bash
uvx zipsa validate ./skills/bip-daily-x
```

Expected: passes.

- [ ] **Step 4: Commit**

```bash
git add skills/bip-daily-x/SKILL.md
git commit -m "feat(bip-daily-x): report phase short-circuits on use_agenthud=false

When ask_agenthud sets use_agenthud=false, report immediately ends with
report=null using one turn. When true, runs agenthud as before. Also
removes the empty-projects skip-the-skill behaviour — the draft phase
can now produce a tweet from web data alone.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: SKILL.md — update `draft`, `review`, `post` sections

**Files:**
- Modify: `skills/bip-daily-x/SKILL.md` (replace `### draft`, `### review`, `### post` sections)

- [ ] **Step 1: Replace the `### draft` section**

Replace the existing `### draft` section with:

```markdown
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

`user_facing_summary` (Korean): "초안 작성 완료"
```

- [ ] **Step 2: Replace the `### review` section**

Replace the existing `### review` section with:

```markdown
### review

Run a Korean-language review loop on the English draft.

**Language constraint:**
- All conversation with the user in this phase is **Korean**.
- The draft text itself is **English** and must NOT be translated
  when shown back to the user.

Steps:

1. Show the draft (English) with its character count. Then ask
   (Korean):
   ```
   이대로 갈까요? 수정 요청? (엔터=확정)
   ```

2. If the user gives empty input → treat as approval, jump to step 4.

3. If the user gives feedback (in Korean or English) → apply the
   feedback to the draft while keeping it in **English** and in
   `voice`. Verify ≤ `config.max_tweet_chars`. Go back to step 1.

   Cap at `config.max_review_iterations` (5) iterations. After the
   cap, force a binary decision with:
   ```
   추가 수정 한도(5회) 도달. 이대로 게시할까요? (y/N)
   ```

4. Final confirmation (Korean):
   ```
   X에 게시할까요? (y/N)
   ```

   - `y` / `yes` / `네` / `예` → set
     `next_phase_input.approved_for_post = true` and proceed.
   - Anything else (including empty) → stop with `status=failed`,
     `error.code="user_declined"`,
     `user_facing_summary` (Korean): "사용자가 게시를 취소했습니다".

Output (on approval):
```json
next_phase_input = {
  ...previous fields...,
  "draft": "<final English tweet>",
  "approved_for_post": true
}
```
`user_facing_summary` (Korean): "최종 컨펌 완료 — 게시 진행"
```

- [ ] **Step 3: Replace the `### post` section**

Replace the existing `### post` section with:

````markdown
### post

Run the bundled helper:

```bash
python3 /skill/scripts/post.py "<approved draft>"
```

Parse the single JSON line from stdout.

- On `status="ok"`: set the phase `result` to the parsed JSON. Set
  `user_facing_summary` (Korean): `"게시 완료: <url>"`.
- On `status="failed"`: bubble up as `status=failed`,
  `error.code="x_post_failed"`. Set `user_facing_summary` (Korean):
  `"게시 실패: <error>"` (truncated to 200 chars).

The `tweet_id` in `result` is the durable key for "what posted
when" — future retrieval depends on it.
````

- [ ] **Step 4: Validate**

```bash
uvx zipsa validate ./skills/bip-daily-x
```

Expected: passes.

- [ ] **Step 5: Commit**

```bash
git add skills/bip-daily-x/SKILL.md
git commit -m "feat(bip-daily-x): draft/review/post consume new inputs + Korean UX

- draft: consumes insights + interests_summary + optional report;
  English tweet only, prioritizes report highlights when available
- review: Korean conversation loop, English tweet preserved verbatim,
  final 'X에 게시할까요? (y/N)' confirmation gate
- post: Korean user_facing_summary on success/failure

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: Final validate + dry-run

**Files:** none modified — verification only.

- [ ] **Step 1: Final validate**

```bash
uvx zipsa validate ./skills/bip-daily-x
```

Expected: passes with no errors or warnings.

- [ ] **Step 2: Dry-run**

```bash
uvx zipsa run ./skills/bip-daily-x "today" --dry-run
```

Expected output should show:
- 8 phases listed in order: precheck, discover, interests, ask_agenthud, report, draft, review, post.
- Model per phase: Haiku for precheck/discover/interests/ask_agenthud/report/post; Sonnet for draft/review.
- `allowed_tools` correctly set per phase — only `WebSearch` for discover/interests; only `Bash(python3:*)` for precheck/post; only the agenthud + jq + read tools for report; empty for ask_agenthud/draft/review.
- Mounts: `~/.claude/projects` ro + each `project_roots` entry.
- Env vars resolved: `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`.

If any of those is wrong, fix the manifest and re-run.

- [ ] **Step 3: If anything was fixed, commit**

If Steps 1 or 2 found issues that required edits, commit:

```bash
git add skills/bip-daily-x/manifest.yaml skills/bip-daily-x/SKILL.md
git commit -m "fix(bip-daily-x): manifest/SKILL.md adjustments after dry-run

<one-line description of what was off>

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

If nothing needed fixing, skip the commit.

---

## Task 9: Live smoke test (user-driven)

**Files:** none — runtime verification only.

- [ ] **Step 1: Hand off to the user**

Tell the user: "Manifest and SKILL.md changes complete. Ready for a smoke test. Three scenarios to try (one per zipsa run invocation, in any order):

1. `uvx zipsa run ./skills/bip-daily-x` — answer **N** when asked about agenthud. Verify: tweet drafted from web data only, Korean review loop, English tweet posted (or declined).
2. `uvx zipsa run ./skills/bip-daily-x` — answer **y** + `today` for agenthud. Verify: agenthud runs, projects appear in report, draft incorporates a shipped highlight.
3. `uvx zipsa run ./skills/bip-daily-x` — answer **y** + a `YYYY-MM-DD` date. Verify: that specific date's activity is fetched.

For each run, check `~/.zipsa/bip-daily-x@0.3.0/runs/<latest>/summary.json` after completion:
- `status` == `ok` (or `failed` with `error.code=user_declined` if you cancelled the post)
- `cost_usd` under 0.85
- `turns` under 59"

- [ ] **Step 2: Triage any smoke-test failures**

If a scenario fails, capture:
- Which phase failed (from `summary.json` → `phase`)
- The `error.code` and `user_facing_summary`
- The corresponding output.jsonl entries (`grep`-able)

Open a follow-up fix task per failure. Do NOT mark this task complete until all three scenarios pass end-to-end.

---

## Self-Review Notes (filled in by plan author)

**Spec coverage check:**
- Phase flow (spec §"Phase Flow") → Tasks 1, 3, 4, 5
- Language Policy (spec §"Language Policy") → Task 2 (header) + Tasks 3-7 (per-phase prompts)
- Memory keys (spec §"Memory Keys") → Task 2 (precheck extension)
- Conditional report execution (spec §"Conditional `report` Execution") → Task 6
- manifest.yaml changes (spec §"manifest.yaml Changes") → Task 1
- SKILL.md changes (spec §"SKILL.md Changes") → Tasks 2-7
- Error handling (spec §"Error Handling") → embedded per-phase in Tasks 3-7
- Migration / version bump (spec §"Migration") → Task 1 (version field)
- Testing plan (spec §"Testing Plan") → Tasks 8 and 9
- Cost estimates / aggregate cap (spec §"Cost Estimates") → Task 1 (`max_cost_usd: 0.85`)

All spec sections covered.

**Placeholder scan:** No TBD/TODO/"implement later" entries. All code blocks contain final content. Numbers in aggregate limits computed (59 turns, $0.85, 3180s) and match phase sums.

**Type/name consistency:**
- `use_agenthud` (bool) — set in ask_agenthud (Task 5), read in report (Task 6). Consistent.
- `target_date` (string) — set in ask_agenthud (Task 5), used by report (Task 6 / existing logic in §"### report" step 1). Consistent.
- `insights` (list[string]) — set in discover (Task 3), read in draft (Task 7). Consistent.
- `interests_summary` (string) and `interests_used` (list[string]) — set in interests (Task 4), read in draft (Task 7). Consistent.
- `report` (object|null) — set in report (Task 6), read in draft (Task 7). Consistent.
- `draft` (string) — set in draft (Task 7), refined in review (Task 7), consumed by post (Task 7). Consistent.
- `approved_for_post` (bool) — set in review (Task 7); post just runs after seeing the field. Consistent.
- `voice` (string) — ask_once key in precheck (Task 2), read in draft (Task 7). Consistent.
- `interests` (list[string]) — ask_once key in precheck (Task 2), read+overridden in interests (Task 4). Consistent.
