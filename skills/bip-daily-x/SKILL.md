# bip-daily-x Skill

Generate one tweet about the user's daily Claude Code work, refine
via user feedback, and post to X after explicit approval.

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
2. Call `mcp__zipsa__ask_once` with key=`voice` (EXACTLY that — not
   `x_voice`, `tweet_voice`, or any other variant). Prompt: "1–2
   sentences describing how you want your tweets to sound."
   The cached answer is reused on subsequent runs.
3. Resolve target date from the user query. Default: today in the
   user's local timezone (see runtime contract on `tz_iana`).

### report

Fetch a structured per-project activity report from agenthud, then
extract per-project slices for the draft phase.

Steps:

1. Warm the npm/npx cache, THEN capture agenthud output. First-time
   npx invocations print "Need to install..." / progress messages to
   stdout which would corrupt our JSON capture (jq then fails with
   "Invalid literal at line 1, column 4"). A throwaway warmup call
   guarantees the real call's stdout is clean JSON.

   ```bash
   # Warmup — discard output, just populate npx cache.
   npx -y agenthud@0.9.2 --version > /dev/null 2>&1

   # Real call. Stdout is now guaranteed to be clean JSON; we redirect
   # to a file because Claude Code's Bash tool truncates stdout at
   # ~30k chars and high-activity days produce 50-200KB+ of JSON.
   npx -y agenthud@0.9.2 report \
     --date <target_date> \
     --format json \
     --include response,bash,edit \
     --detail-limit 200 \
     --with-git \
     > /tmp/agenthud-report.json
   ```

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
