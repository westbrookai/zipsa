# bip-daily-x Skill — Design

**Date:** 2026-05-18 (rev 2026-05-19: BYO OAuth 1.0a path)
**Status:** Draft — pending user approval

**Auth approach:** Bring-your-own X credentials. The user registers
their own X Developer App once (paying X's per-tweet fee from their
own billing account) and puts the 4 OAuth 1.0a credentials in
`~/.zipsa/.env`:

- `X_API_KEY` (consumer key)
- `X_API_SECRET` (consumer secret)
- `X_ACCESS_TOKEN`
- `X_ACCESS_SECRET`

OAuth 1.0a tokens don't expire — paste once, forget. Posting requires
HMAC-SHA1 request signing, handled by a small stdlib-only Python
helper bundled with the skill (no `tweepy` / npm dependency to
install at runtime).

The previously-designed shared-app OAuth 2.0 PKCE flow
(`2026-05-18-sign-in-with-x-design.md`) is deferred — it's the
right answer if and when zipsa needs a multi-user hosted onboarding
flow, but for personal CLI use the BYO path is simpler and cheaper.

**Required launcher change (included in this PR):** auto-mount the
skill's source directory at `/skill:ro` inside the container, so the
skill can ship helper scripts (`scripts/post.py`) and reach them at a
stable path.

---

## Goal

One sentence: **Generate a single tweet about the user's daily
claudecode work, refine it through user feedback, and post to X after
explicit approval.**

The skill is the user's daily "build in public" lever. The point is
that posting daily is hard enough that any friction (going to a
website, opening a tweet composer, deciding what to write) becomes a
reason not to post. The skill removes the friction: it reads the day's
work from Claude Code session logs, drafts something in the user's
voice, iterates until the user says OK, posts.

## Phase shape

Five phases, each with its own tool allowlist and limits.

### 1. `precheck`

- Verify all 4 X env vars are present (`X_API_KEY`, `X_API_SECRET`,
  `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`). On any missing → `status=failed`,
  `error.code="x_credentials_missing"`, `user_facing_summary` naming the
  missing var(s) and pointing to the setup doc.
- Resolve target date: parse user query for date/relative term;
  default to `config.default_target_date` (today) in the host's local
  timezone (read from `execution_context.tz_iana`).
- Ensure `voice` is set in skill memory. If not, ask once for a 1–2
  sentence description of the user's X voice. Store under stable key
  `voice`.
- Tools: `mcp__zipsa__ask_once`. Env-var presence check is done via
  the skill prompt — the contract tells the agent the 4 var names are
  passed through; the agent reads them or fails fast. We don't need a
  shell call to check existence.
- Limits: 4 turns / $0.05 / 60s.

### 2. `report`

- Run agenthud for the target date (same invocation as
  daily-progress).
- If `sessions: []` → return `status=ok` with a
  `user_facing_summary` "claudecode 작업 없음 — 게시 생략" and
  `next_phase_input = null`. The skill ends here — no draft, no post.
- Otherwise pass the structured per-project report to the next phase.
- Tools: `Bash(npx:*)`.
- Limits: 5 turns / $0.10 / 120s.

### 3. `draft`

- LLM-only. Given `previous_phase_output.report` and `voice` from
  memory, write a single tweet (≤ `config.max_tweet_chars`).
- No clever formatting (no JSON wrapping, no thread structure). Just
  the tweet text in the next_phase_input as `draft: "<text>"`.
- Tools: none.
- Limits: 3 turns / $0.05 / 60s.

### 4. `review`

The HITL loop. Within this single phase:

1. Show the current draft to the user via
   `mcp__zipsa__ask({prompt: "이대로 갈까요? 피드백 주세요 (빈 응답=OK)"})`.
   The launcher's HITL renderer prints the draft text right before
   the prompt (we'll structure the ask prompt so the draft is part of
   the rendered block — exact format TBD during implementation).
2. If user submits empty → break (approved). Record `final_draft`.
3. If non-empty feedback → regenerate the draft applying the feedback
   while staying in `voice`. Re-show. Increment counter.
4. Hard cap: `config.max_review_iterations` rounds (default 5). After
   the cap, the next ask must be a `confirm("계속 다듬을까요? 아니면 이대로 게시?")`
   with `yes` ending review with the current draft and `no` aborting
   the skill with `status=failed`, `error.code="review_no_consensus"`.
5. Final step before exiting the phase: `mcp__zipsa__confirm({message: "이 내용으로 X에 게시할까요?"})`.
   `no` → abort with `status=failed`, `error.code="user_declined"`.
   `yes` → pass `final_draft` to the next phase.
- Tools: `mcp__zipsa__ask`, `mcp__zipsa__confirm`.
- Limits: 10 turns / $0.20 / 1800s (long timeout to absorb user
  thinking time — see BACKLOG note on excluding HITL wait from
  timeouts).

### 5. `post`

- Invoke the bundled helper: `python3 /skill/scripts/post.py "<final_draft>"`.
  The script reads the 4 X env vars, signs the request per RFC 5849
  (OAuth 1.0a HMAC-SHA1), POSTs to `https://api.x.com/2/tweets`, and
  emits a single JSON line to stdout.
- Expected stdout on success:
  `{"status": "ok", "tweet_id": "<id>", "url": "https://x.com/i/web/status/<id>", "text": "<text>"}`.
- Expected stdout on failure:
  `{"status": "failed", "error": "<body>", "http_code": <int>}`.
- The agent parses the JSON, sets the phase `result` to it, and writes
  `user_facing_summary` like "게시 완료: <url>" (or English equivalent
  based on voice).
- On `status="failed"` from the script: bubble up as the phase
  `status=failed`, `error.code="x_post_failed"`.
- **Why `tweet_id` matters:** it's the durable key for "what was
  posted when." A future skill (or query) can look up past posts by
  this id without re-scraping. Always include it in `result`.
- Tools: `Bash(python3:*)`. (`scripts/post.py` is bundled with the
  skill and reachable at `/skill/scripts/post.py` thanks to the
  launcher mount.)
- Limits: 4 turns / $0.05 / 60s.

#### `scripts/post.py` contract

- **Imports stdlib only** (`base64`, `hashlib`, `hmac`, `json`, `os`,
  `secrets`, `sys`, `time`, `urllib.parse`, `urllib.request`). No
  `tweepy`, no `requests`, no `pip install` at runtime.
- ~70 lines including signing, request, error handling.
- Single argv: the tweet text. Reads creds from env.
- Always exits 0 if the script ran end-to-end (HTTP failure included);
  the JSON body distinguishes ok / failed. Exits non-zero only on
  argv/env validation failure.
- Has its own pytest tests against RFC 5849 §1.2 example vectors for
  the HMAC-SHA1 signature math (no network).

## SKILL.md style (per the established principle)

Natural language only. No mention of `mcp__zipsa__*` tools (the
runtime contract maps intent → tool). Examples:

- "Ask the user for their X voice — 1 to 2 sentences describing how
  they want to sound. Remember the answer."
- "Show the draft to the user and ask whether to revise. If they give
  feedback, regenerate and re-show."
- "Confirm before posting."

## Manifest

```yaml
apiVersion: zipsa.dev/v1alpha1
kind: Skill
metadata:
  name: bip-daily-x
  version: 0.1.0
  author: westbrookai
  description: Generate, refine via user feedback, and post a single
    daily tweet about your Claude Code work.

spec:
  purpose: |
    Produce one tweet per day about the user's Claude Code work,
    iterate until the user is satisfied, then post to X after
    explicit approval. Refuse anything not in this scope.

  instructions: ./SKILL.md

  model:
    name: claude-opus-4-7

  # Same bind-mount as daily-progress so agenthud sees session logs.
  mounts:
    - host: ~/.claude/projects
      container: /home/agent/.claude/projects
      mode: ro

  # X credentials live in ~/.zipsa/.env (the launcher already loads
  # that as a second --env-file). The 4 vars (X_API_KEY, X_API_SECRET,
  # X_ACCESS_TOKEN, X_ACCESS_SECRET) get pulled into the container env
  # without any per-skill declaration. precheck phase fails fast if
  # any are missing.

  tools:
    builtin: []

  config:
    default_target_date: today        # today | yesterday
    max_review_iterations: 5
    x_post_endpoint: https://api.x.com/2/tweets
    max_tweet_chars: 280

  state_schema: {}                    # no cross-run state

  phases:
    - id: precheck
      goal: |
        Verify the 4 X credentials are present, ensure voice is
        remembered, resolve target date.
      allowed_tools: []
      limits:
        max_turns: 4
        max_cost_usd: 0.05
        timeout_seconds: 60

    - id: report
      goal: |
        Run agenthud for the target date and produce a structured
        per-project activity summary.
      allowed_tools:
        - Bash(npx:*)
      limits:
        max_turns: 5
        max_cost_usd: 0.10
        timeout_seconds: 120

    - id: draft
      goal: |
        Write a single tweet in the user's voice from the report.
      allowed_tools: []
      limits:
        max_turns: 3
        max_cost_usd: 0.05
        timeout_seconds: 60

    - id: review
      goal: |
        Show the draft, accept feedback, regenerate up to 5 times,
        then confirm before posting.
      allowed_tools: []                # ask/confirm always-on, no others
      limits:
        max_turns: 10
        max_cost_usd: 0.20
        timeout_seconds: 1800

    - id: post
      goal: |
        Run /skill/scripts/post.py with the approved draft; surface
        the tweet URL and id.
      allowed_tools:
        - Bash(python3:*)
      limits:
        max_turns: 4
        max_cost_usd: 0.05
        timeout_seconds: 60

  limits:
    max_turns: 26
    max_cost_usd: 0.45
    timeout_seconds: 2100
```

## Memory layout (per-skill)

```
~/.zipsa/bip-daily-x@0.1.0/memory/skill-mem.json:
{
  "voice": "<1-2 sentence description in user's words>"
}
```

No global-scope memory used.

## Empty-day behavior

If agenthud reports zero sessions for the target date, the skill
short-circuits at the report phase. No draft, no prompts, no post.
This is the right default — pretending there's something to share
when there isn't is worse than not posting.

A future v0.2 could add "stuck day" mode (`zipsa run bip-daily-x
"실패한 거 공유" "오늘 막힌 거"` etc.) but that's a different skill or
a `--mode` flag, not core to v0.1.

## Failure surfaces

- `x_credentials_missing` (precheck): one or more of the 4 X env vars
  is unset. Message names which one(s) and points to the setup doc.
- `review_no_consensus` (review): user hit iteration cap and declined
  to stop. Re-run when ready.
- `user_declined` (review): user confirmed "no" at the final gate. No
  post.
- `x_post_failed` (post): X API returned non-2xx. As of 2026 X has no
  free tier — likely causes are billing/account suspension, token
  revoked, or content policy. Surface the X error body; user decides.

## YAGNI

- Thread format, attachments, polls, scheduled posts.
- Cross-day batching (one post summarizing multiple days).
- Edit-after-post or delete.
- Per-language A/B (voice prompt covers language).
- "Recent posts" memory for dedup — easy to add later if real,
  premature now.

## Test plan

Manifest validation:
- `zipsa validate skills/bip-daily-x` passes.
- `auth_providers: [x]` resolves; unknown provider name fails.

Phase contracts:
- Empty-day path: fixture report with `sessions: []` → skill ends at
  report with `status=ok`, no further phases invoked.
- Review loop cap: simulate 5 feedback rounds then a 6th non-empty;
  skill must force the confirm gate, not silently keep regenerating.

End-to-end (manual, real X account):
- Connect X (PR #1 path), run skill with a normal claudecode day,
  give one round of feedback, confirm, verify the tweet shows up at
  the returned URL and matches `final_draft`.
- Run again same day to confirm idempotency expectations: today
  there's NO state preventing a second post. The user is expected to
  judge — we don't dedupe. Decide whether to add a "you posted today
  already, post again?" confirm; current spec says NO (KISS), call
  that out for review.
