# Skill Authoring Guide

> The contract for writing zipsa skills that run under `zipsa exec`.
> This is the single source of truth for authors (human or LLM). It
> ships with the launcher (`zipsa/authoring/`), not with any skill —
> the launcher owns the contract.

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
- Claude auth is injected automatically for `.md` phases (host
  `~/.zipsa/.env` → `CLAUDE_CODE_OAUTH_TOKEN`).

A good `.md` phase says: what the input means, what to produce, what
keys go in the result. See `weather/zipsa-dist/2.report.md`.

## 6. Credentials & secrets

Code phases get **no environment-variable injection** (by design —
they stay env-clean). The supported way to give a code phase a secret
(API token, bot credentials) is a **mounted file**:

1. Keep the secret in a host file, e.g.
   `~/.zipsa/credentials/<service>.json`.
2. The caller mounts it at run time:
   ```bash
   zipsa exec ./my-skill \
     --mount ~/.zipsa/credentials/telegram.json:/mnt/creds/telegram.json
   ```
3. The code phase reads it from the container path:
   ```python
   creds = json.loads(Path("/mnt/creds/telegram.json").read_text())
   ```
   If the file/key is missing, `exit(1)` with a clear message.

Document the mount your skill needs in its `SKILL.md` run example —
the caller (or `zipsa create`'s test step) supplies it. Never bake a
secret into the skill files.

## 7. Running

```bash
zipsa exec ./my-skill "user query"          # docker (default)
zipsa exec ./my-skill "user query" --local  # host, fast authoring loop
zipsa exec ./my-skill --out ./artifacts     # choose the /out host dir
zipsa exec ./my-skill --image custom:tag    # override runtime image
zipsa exec ./my-skill --mount ~/data        # host path ro at the SAME
                                            # container path (repeatable)
zipsa exec ./my-skill --mount ~/x.json:/mnt/x.json   # HOST:CONTAINER form
```

`--mount` serves two needs: secrets (§6) and tools that embed host
paths in their data (e.g. agenthud resolving a session's `cwd` to its
`.git`). No-op under `--local` (the host is already visible).

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
- **Docker file sharing (macOS):** skill paths and mount sources
  outside Docker Desktop's shared list (e.g. `/tmp`) mount empty.
  Keep skills + mounted files under `/Users`.
- First run pulls the runtime image (~2GB) — progress shows on stderr.

## 8. Patterns

**Fetch → report** (the canonical hybrid — see `weather/`):
deterministic fetch/parse in `.py`, natural-language output in `.md`.

**Artifact handoff**: phase 1 writes `/out/big.json` + returns
`{"data_file": "big.json"}`; phase 2 reads it from `ctx.out_dir`.

**Validate-first**: phase 1 checks preconditions and exits 1 with a
clear stderr message before any expensive work happens.

**Credential-gated send** (see `wahroonga-umbrella-alert/`): a code
phase reads a mounted token file and calls an external API; exits 1
cleanly if the credential is absent.

**Empty-query default**: decide explicitly what no-input means
(weather treats it as "IP-based location"; erroring out is also fine
— just be deliberate).

**Scheduled skill**: keep the skill schedule-agnostic (it just runs
once). The user wires the cadence separately:
`zipsa schedule add <label> --cron "0 8 * * *" <path> [--mount ...]`.

## 9. Not yet (don't design against these)

- HITL (a *skill* asking the user mid-run — distinct from `zipsa
  create`'s authoring HITL)
- Environment-variable injection for code phases (use a mounted file
  for secrets — §6)
- Branching (sub-phase XOR)
- install-by-name, composition (one skill calling another)
- Tools in LLM phases

When a skill genuinely needs one of these, that's a platform feature
request — raise it, don't work around it with fragile hacks.
