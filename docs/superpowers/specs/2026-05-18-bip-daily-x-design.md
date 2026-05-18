# bip-daily-x Skill — Design

**Date:** 2026-05-18
**Status:** Draft — pending user approval
**Depends on:** `2026-05-18-sign-in-with-x-design.md` (Sign in with X
must ship and merge first; this skill needs `ZIPSA_TOKEN_X` injected).

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

- Verify `ZIPSA_TOKEN_X` is in env (Sign-in-with-X already gated this
  at executor startup; this is a belt-and-suspenders check). On missing
  → `status=failed`, `error.code="x_token_missing"`.
- Resolve target date: parse user query for date/relative term;
  default to `config.default_target_date` (today) in the host's local
  timezone (read from `execution_context.tz_iana`).
- Ensure `voice` is set in skill memory. If not, ask once for a 1–2
  sentence description of the user's X voice. Store under stable key
  `voice`.
- Tools: `mcp__zipsa__ask_once`.
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

- One `curl -X POST https://api.x.com/2/tweets` with
  `Authorization: Bearer $ZIPSA_TOKEN_X` and body
  `{"text": "<final_draft>"}`.
- Parse the response. Extract `data.id`; build URL
  `https://x.com/i/web/status/<id>`.
- `result`: `{posted: true, tweet_id, tweet_url, text}`.
- `user_facing_summary`: "게시 완료: <tweet_url>" (or English
  equivalent based on voice).
- On non-2xx: `status=failed`, `error.code="x_post_failed"`,
  surface the response body.
- Tools: `Bash(curl:*)`.
- Limits: 4 turns / $0.05 / 60s.

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

  # New top-level field from the Sign-in-with-X PR.
  auth_providers:
    - x

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
        Verify X token, ensure voice is remembered, resolve target date.
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
        Post the approved draft to X and return the tweet URL.
      allowed_tools:
        - Bash(curl:*)
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

- `x_token_missing` (precheck): Sign-in-with-X gate failed earlier;
  should be rare in practice. User runs `zipsa connect x`.
- `review_no_consensus` (review): user hit iteration cap and declined
  to stop. Re-run when ready.
- `user_declined` (review): user confirmed "no" at the final gate. No
  post.
- `x_post_failed` (post): X API returned non-2xx. Likely rate limit
  (500/month on free), token revoked, or content policy. Surface the
  error body; user decides.

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
