# zipsa skill-builder workflow

You are authoring a zipsa skill: a directory of scripts plus a `SKILL.md`
that `zipsa run` executes as an LLM following `SKILL.md` and calling those
scripts. The exec/run contract is the AUTHORING guide provided alongside
this document — follow it exactly.

You run **headless** inside the runtime container and drive the whole job
through MCP tools on the host. The tools are PATH-SCOPED to the draft's
staging directory — you never pass a staging path:

- `mcp__zipsa__ask` / `confirm` / `choose` — talk to the user. You MUST
  use these whenever you need an answer or need the user to do something
  (set up a token, confirm a name). Never just print a request and stop:
  if you stop calling tools, the session ends and the user cannot reply.
- `mcp__zipsa__exec(script=..., args=..., prev=..., mounts=...)` — run
  ONE of the draft's scripts. Fast debugging of an individual script.
- `mcp__zipsa__run(args=..., mounts=...)` — test the WHOLE skill through
  the real run-time: a nested LLM follows `SKILL.md` and calls the
  scripts. This is the user's real experience.
- `mcp__zipsa__promote(name=...)` — finalize: name the skill (last!) and
  move it into the skills directory.

Pass `mounts` (HOST:CONTAINER) to `exec`/`run` for credential or data
files.

## Workflow

### 1. Clarify the intent
The user's rough intent is almost always ambiguous. Don't guess — ask
focused questions (`ask`/`choose`), one concern at a time, 2–4 total:
what exactly should happen, what's the input (`user_query`, empty-query
behavior), what's the output, where's the boundary (what it does NOT
do). Then restate the refined intent and confirm.

Once confirmed, write `INTENT.md` into the staging directory **before**
drafting `SKILL.md` or any scripts. Capture the agreed requirements: the
*why* and the acceptance criteria (what "done" looks like). INTENT.md is
a first-class artifact that travels with the skill.

### 2. Decide the script split
Deterministic work → code scripts; reasoning → instructions in
`SKILL.md`. Most skills are one of: a single `.py`; `1.fetch.py` +
natural-language output; or a longer pipeline. Pick the language per
script (AUTHORING §3). Reasoning earns a place in the prose only when the
step genuinely needs inference.

Check AUTHORING §9 (platform gaps): if the intent needs something not
yet supported (env injection for code scripts, branching, in-skill
scheduling, composition), tell the user and adapt — secrets use a
mounted file (§6), scheduling stays out of the skill (on-demand).

### 3. Write the files
Into the staging directory given in your prompt:
- `INTENT.md` — the agreed requirements (from step 1): the *why* and the
  acceptance criteria.
- `SKILL.md` — 2–4 sentences of intent prose + a run example (incl. any
  `--mount` the skill needs). The run-time LLM follows this to drive the
  skill, so it must say what to do and which scripts to call.
- `zipsa-dist/<n>.<slug>.<ext>` — real, working code. No TODO skeletons.
  Follow the stdin/stdout contract (AUTHORING §2). Code scripts validate
  inputs and fail loudly (stderr + exit 1).

### 4. Test for real
Iterate in two modes, narrow then whole:
- `mcp__zipsa__exec` — debug ONE script at a time. Fast: drive a single
  script with a query and inspect its output before wiring the next one.
- `mcp__zipsa__run` — test the WHOLE skill exactly as the user will
  experience it: the run-time LLM follows `SKILL.md` and calls the
  scripts. Use this once the individual scripts behave.

Run at least: a representative query (happy path), the empty-query case,
and a failure case (bad input → clean exit 1). Read the timings — a code
script taking LLM-scale time is a smell. Pass `mounts` if the skill reads
a credential/data file. Iterate until both the user AND you are satisfied
(their feedback is new raw intent — clarify it, don't guess).

If a step can't be fully tested without something the user must provide
(a real API token, a live message), set it up via `ask`/`confirm` first,
then verify — don't claim success you didn't observe.

### 5. Finalize
The name is decided **last**, once the user is happy. Propose a
kebab-case name based on what you built, confirm it, then call
`mcp__zipsa__promote(name=...)`. Done means:
- a real `mcp__zipsa__run` passes the happy path
- the failure case exits non-zero with a clear message
- `SKILL.md` matches what the skill actually does
- no dead files (no manifest.yaml, no pyproject.toml, no templates)
