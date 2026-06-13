# Skill Authoring Guide

> The contract for writing zipsa skills that run under `zipsa exec`.
> This is the single source of truth for authors (human or LLM).
> Verified against the runtime as of Phase 2 (PRs #109, #110, #111).

## 1. Anatomy

```
my-skill/
├── SKILL.md            ← short intent prose, for humans (runtime never reads it)
└── zipsa-dist/
    ├── 1.fetch.py      ← phase 1 (Python)
    ├── 2.report.md     ← phase 2 (LLM)
    └── helper.py       ← non-phase files are ignored by discovery
```

- No metadata file. Skill name = directory basename.
- A phase is any file in `zipsa-dist/` matching
  `<int>.<kebab-slug>.<ext>`. Everything else is ignored — ship
  helpers/readmes freely.
- Phases run **sequentially by number**. Numbers sort numerically
  (`10` after `2`). Dotted sub-ids (`3.1`) are reserved for future
  branching — using them today is an error.

## 2. Phase contract (language-agnostic)

A phase is a process.

**stdin** — one JSON line:

```json
{"ctx": {"skill_name": "my-skill", "user_query": "서울", "out_dir": "/out"},
 "prev": {"records": 3}}
```

- `ctx.user_query` — the argument to `zipsa exec <path> [query]`,
  empty string if absent.
- `ctx.out_dir` — writable directory for artifacts (see §4).
- `prev` — the previous phase's result; `{}` for the first phase, or
  when the previous phase emitted no result.

**stdout** — the **last line that parses as a JSON object** is the
phase's result. Everything before it is treated as logs (print
progress freely). A JSON array or bare value doesn't count. No JSON
object at all → result is `null` (still success if exit code 0).

**exit code** — `0` = success, anything else stops the chain and
becomes `zipsa exec`'s exit code; stderr is shown to the user. Fail
loudly: validate inputs and `exit(1)` with a clear stderr message
rather than emitting a half-result.

## 3. Languages

The extension picks the runner (inside the runtime container):

| ext | runner | notes |
|---|---|---|
| `.py` | `python` (3.11) | stdlib only unless you know the image has it |
| `.sh` | `bash` | `jq`, `curl`, `rg` available in the image |
| `.js` | `node` (24) | |
| `.ts` | `npx tsx` | |
| `.go` | `go run` | ⚠ image lacks Go yet — works under `--local` only |
| `.md` | `claude -p` | LLM phase, see §5 |

No shebang, no chmod — the dispatch table is the contract.

## 4. /out — the artifact channel

All phases of a run share one writable directory, mounted at `/out`
(its host path is printed in the result JSON as `out_dir`).

Two channels, two jobs:
- **prev** (values): small structured metadata, flows phase → phase
  automatically.
- **/out** (files): big payloads. Write the file, put its *name* in
  your result so the next phase knows what to look for.

```python
pathlib.Path(ctx["out_dir"], "data.json").write_text(...)
print(json.dumps({"data_file": "data.json", "records": 3}))
```

## 5. LLM phases (`.md`)

The file's markdown is your instruction to the model. The runtime
appends the input payload (`ctx` + `prev`) and the output rule (last
line = JSON object) automatically — don't restate the envelope
mechanics, just say what to do and what keys to put in the result.

Constraints:
- **No tools.** Pure reasoning over the input. Anything that needs
  computation, network, or files belongs in a code phase before or
  after.
- Single turn. Keep the task focused.
- Auth is injected automatically (host `~/.zipsa/.env` →
  `CLAUDE_CODE_OAUTH_TOKEN`); code phases never see it.

A good `.md` phase says: what the input means, what to produce, what
keys go in the result. See `skills/weather/zipsa-dist/2.report.md`.

## 6. Running

```bash
zipsa exec ./my-skill "user query"          # docker (default)
zipsa exec ./my-skill "user query" --local  # host, fast authoring loop
zipsa exec ./my-skill --out ./artifacts     # choose the /out host dir
zipsa exec ./my-skill --image custom:tag    # override runtime image
zipsa exec ./my-skill --mount ~/.claude/projects --mount ~/code
                                            # host paths visible ro at the SAME
                                            # absolute path in the container
                                            # (repeatable; no-op with --local)
zipsa exec ./my-skill --mount ~/.claude/projects:/home/agent/.claude/projects
                                            # HOST:CONTAINER overrides the
                                            # container path
```

`--mount` is for skills whose tools embed host paths in their data
(e.g. agenthud resolving a session's `cwd` to its `.git`) or read
fixed locations under the container home. Document the mounts your
skill needs in its SKILL.md run example — the caller supplies them.

The host's timezone is injected as `TZ` automatically — date
arithmetic in a phase ("yesterday") means the user's yesterday, not
UTC's.

Output:

```json
{
  "skill_name": "my-skill",
  "mode": "docker",
  "result": { ... },              // last phase's result
  "exit_code": 0,
  "duration_ms": 7617,
  "out_dir": "/Users/.../exec-out/my-skill-xxxx",
  "phases": [
    {"id": "1", "slug": "fetch",  "exit_code": 0, "duration_ms": 2184},
    {"id": "2", "slug": "report", "exit_code": 0, "duration_ms": 5433}
  ]
}
```

Gotchas:
- **Docker file sharing (macOS):** skill paths outside Docker
  Desktop's shared list (e.g. `/tmp`) mount empty and fail with
  "can't open file /skill/...". Keep skills under `/Users`.
- First run pulls the runtime image (~2GB) — progress shows on
  stderr.
- Code phases run with **no secrets and no env injection**. If your
  phase needs an API token, that platform feature doesn't exist yet
  (see §8).

## 7. Patterns

**Fetch → report** (the canonical hybrid — see `skills/weather/`):
deterministic fetch/parse in `.py`, natural-language output in `.md`.

**Artifact handoff**: phase 1 writes `/out/big.json` + returns
`{"data_file": "big.json"}`; phase 2 reads it from `ctx.out_dir`.

**Validate-first**: phase 1 checks preconditions and exits 1 with a
clear stderr message before any expensive work happens.

**Empty-query default**: decide explicitly what no-input means
(weather treats it as "IP-based location"; erroring out is also fine
— just be deliberate).

## 8. Not yet (don't design against these)

- HITL (asking the user mid-run)
- Credentials / env injection for code phases
- Branching (sub-phase XOR)
- Scheduling, install-by-name, composition (calling other skills)
- Tools in LLM phases

When a skill genuinely needs one of these, that's a platform feature
request — raise it, don't work around it with fragile hacks.
