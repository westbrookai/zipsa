# bip-daily-x Skill

Generate one tweet about the user's daily Claude Code work, refine
via user feedback, and post to X after explicit approval.

## Per-user setup

This skill posts to the user's own X account using credentials they
provide in `~/.zipsa/.env`:

- `X_API_KEY`
- `X_API_SECRET`
- `X_ACCESS_TOKEN`
- `X_ACCESS_SECRET`

These are OAuth 1.0a credentials (4 strings, no expiry). The user
generates them once at https://console.x.com under their own X
Developer App. The launcher passes them into this skill's container
automatically; this skill never touches them directly — it just
expects them in env.

The user's preferred tweet voice is asked once on first run and
remembered as `voice` in skill memory.

## Phases

### precheck

1. Verify all 4 X env vars are present by running
   `python3 /skill/scripts/post.py --check-env`. The script reads the
   env vars and emits a single JSON line: `{"status":"ok",...}` or
   `{"status":"failed","error":"missing env var(s): [...]"}`. On
   `failed`, stop the phase with `status=failed`,
   `error.code="x_credentials_missing"`, and put the script's error
   message into `user_facing_summary` so the user sees exactly which
   var(s) are missing.
2. Ask the user once for their X voice (1–2 sentences describing how
   they want their tweets to sound). Remember the answer. On
   subsequent runs the cached answer is used.
3. Resolve target date from the user query. Default: today in the
   user's local timezone (see runtime contract on `tz_iana`).

### report

Invoke agenthud for the target date:

```bash
npx agenthud@0.8.4 report \
  --date <target_date> \
  --format json \
  --include response,bash,edit,thinking \
  --detail-limit 0
```

If the result has `sessions: []`, stop the skill with `status=ok` and
`user_facing_summary` "No Claude Code activity today — skipping post."
No draft, no prompts, no post.

Otherwise pass the per-project structured report to the next phase.

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
