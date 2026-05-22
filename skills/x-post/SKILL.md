# x-post Skill

Post a single tweet to X (Twitter) using OAuth 1.0a credentials.
Atomic publish skill — no drafting, no review, no voice.

## Atomic skill contract

This is an **atomic** skill: a leaf in the call graph — it does NOT
call `mcp__zipsa__run_skill`. Single-responsibility: publish one
approved tweet, nothing else (no drafting, no review loop, no voice).

For this particular skill:

- One input: the exact tweet text to publish.
- One output: a JSON artifact at `artifacts/tweet-result.json` with
  the resulting tweet's id and URL.
- All content decisions (voice, drafting, review/approval) live in
  the caller. This skill receives a finished string and publishes it.
- No agent-time HITL or skill memory needed: the caller passes the
  text each call. X credentials come from `~/.zipsa/.env` (launcher
  injects automatically).

(Atomic skills MAY use HITL or memory in general — per-caller
routing namespaces them safely. This one just doesn't need to.)

Orchestrator skills compose this for "I have an approved tweet text;
publish it."

## Input

`user_query` is the tweet text to post, verbatim. It MUST be:

- non-empty after stripping leading/trailing whitespace
- ≤ 280 characters (post-strip length)

The skill does NOT alter, trim, or sanitize the text beyond
whitespace stripping at the boundaries.

If the input fails these checks, fail with
`error.code="invalid_tweet_text"` and a short message naming the
problem.

## Phase: post

1. Read `user_query`. Strip leading/trailing whitespace. Verify
   non-empty and length ≤ 280. On failure, emit the
   `invalid_tweet_text` error and stop.

2. Invoke the bundled helper exactly once:

   ```bash
   python3 /skill/scripts/post.py "<stripped tweet text>"
   ```

   The script reads `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`,
   `X_ACCESS_SECRET` from env and emits a single JSON line to stdout.
   Do NOT call the X API directly with curl or any other HTTP tool.

3. Parse the single JSON line from stdout. Two shapes:

   - On API success:
     ```json
     {"status": "ok", "tweet_id": "...", "url": "https://x.com/...", "text": "..."}
     ```
   - On API failure:
     ```json
     {"status": "failed", "error": "...", "http_code": <int>}
     ```

   The script exits 0 in both cases; exit 1 only on argv/env
   validation failure (rare, since we validated above and env vars
   come from `~/.zipsa/.env`).

4. On `status="ok"`:

   - Write the JSON to `/home/agent/runs/current/artifacts/tweet-result.json`.
   - Emit the skill-envelope:
     ```json
     {
       "status": "ok",
       "phase": "post",
       "result": {
         "tweet_id": "...",
         "url": "https://x.com/...",
         "artifact": "tweet-result.json"
       },
       "state_updates": {},
       "user_facing_summary": "posted: <url>"
     }
     ```

5. On `status="failed"`:

   - Do NOT write a partial artifact.
   - Emit:
     ```json
     {
       "status": "failed",
       "phase": "post",
       "error": {
         "code": "x_post_failed",
         "message": "<error truncated to 200 chars>",
         "http_code": <int>
       },
       "user_facing_summary": "x post failed (<http_code>): <error>"
     }
     ```

## Error handling

- Empty / overlong tweet text → `error.code="invalid_tweet_text"`.
- Missing X env vars (script exits 1) → `error.code="x_credentials_missing"`,
  surface the script's stderr in `user_facing_summary`.
- X API failure → `error.code="x_post_failed"` per step 5.

## Constraints

- This skill does NOT call `mcp__zipsa__run_skill` (atomic = leaf).
- This skill does NOT modify the tweet text (no trimming beyond
  whitespace boundaries, no character substitution). The caller's
  approved text is published verbatim.
- Single tweet only — no threads, no replies, no media.
- Output language is English.
