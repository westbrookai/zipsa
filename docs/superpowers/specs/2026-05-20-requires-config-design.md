# Requires Config — Design

**Date:** 2026-05-20
**Status:** Draft — pending user approval
**Scope:** Add a launcher-readable, per-user, per-skill persistent
configuration mechanism. A skill manifest declares `spec.requires:`
listing the host-side values it needs (e.g. `project_roots: list[directory]`).
On first run, the launcher prompts the user for these values, validates,
and stores them at `~/.zipsa/<skill>@<version>/requires.yaml`. The
manifest then references these values in `mounts:` so they become
docker `-v` flags at run time.

This is the **third kind** of zipsa configuration storage, distinct
from skill memory (agent-readable) and runtime-config (launcher-wide):

| Kind | Reader | When | Storage |
|---|---|---|---|
| Skill memory (`ask_once`) | Agent | After container starts | `~/.zipsa/<skill>@<ver>/memory/*.json` |
| Runtime config | Launcher | All runs | `~/.zipsa/runtime-config.yaml` |
| **Requires config (new)** | Launcher | Before container starts (per-skill) | `~/.zipsa/<skill>@<ver>/requires.yaml` |

---

## Goal

A skill author can declare *"this skill needs these host-side values
to operate"* in the manifest. The launcher handles asking the user,
saving the answers, validating on every run, and wiring the values
into mounts. The first concrete consumer is `daily-progress` needing
git project root paths so agenthud's `--with-git` works without
hand-rolled mount edits.

## Why this is needed

`daily-progress` wants to call `agenthud report --with-git`, which
requires read access to each project's `.git` directory. The project
list isn't knowable until agenthud has scanned the day's Claude
sessions, but mount decisions must be made *before* the container
starts. The natural solution is to ask the user once for the parent
directories that contain their git projects, mount those, and let
agenthud discover repos within them.

This is a host-side configuration question. It can't be answered by:

- **Skill memory** — agent reads memory inside the container; too late
  to influence the docker command.
- **Runtime config** — global across all skills; wrong namespace for
  per-skill values.
- **Manifest defaults** — skill author can't know the user's directory
  layout.
- **`ask_once`** — agent-time HITL prompt; same too-late problem as
  skill memory.

A new "launcher prompts user once, stores answer, uses on every future
run" concept is required.

## Security note: why this is the *safest* form of dynamic input

An earlier sketch considered letting the agent emit mount paths via
its contract JSON (e.g. `result.next_phase_mounts: [...]`). Rejected
because prompt injection through any input the agent reads (Claude
session JSONLs, MCP tool outputs, etc.) could trick it into requesting
hostile paths (e.g. `~/.ssh`), and the launcher would have no way to
distinguish legitimate from injected requests.

`requires` avoids this because the trust boundary is **"a value the
user typed directly into a TTY prompt before any LLM ran."** No LLM
output is ever consulted to construct a mount source. Subsequent runs
read the saved YAML — also never agent-influenced.

This also closes the per-run consent habituation gap: there is no
per-run prompt to skim past. The one-time prompt establishes a
durable, user-blessed pattern.

---

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **Manifest field name: `spec.requires:`** (not `user_config`, not `inputs`). Each entry is a key with `type:` and `prompt:` subfields. | Short, declarative, npm-style. All entries implicitly required (block name self-documents). |
| 2 | **v1 types: `string`, `directory`, `list[directory]` only.** | Covers daily-progress's actual need. `file`, `path`, `secret`, `int` deferred until a second use case justifies them (YAGNI). |
| 3 | **Storage: `~/.zipsa/<skill>@<version>/requires.yaml`** (per-version, matching existing `<skill>@<version>/` convention for `runs/` and `.env`). | Consistent with existing layout. Upgrade re-prompts (safe). Carry-over from previous version (decision 4) softens upgrade friction. |
| 4 | **Carry-over from latest previous version** on first run of a new version. Launcher looks for `<skill>@*/requires.yaml`, takes the most recent, copies matching keys (same name + type). User confirms with one prompt. | Solves upgrade UX without losing per-version isolation. |
| 5 | **Storage format: YAML** (`yaml.safe_dump`). Atomic write via tmp + rename. | Consistent with manifest format. Atomic protects against partial writes on Ctrl+C. |
| 6 | **Lifecycle: hybrid (option 3 of earlier discussion).** First run with missing values: launcher prompts inline. Future re-config: explicit `zipsa configure <skill>` command. | First-run friction = zero. Re-config path is explicit and discoverable. |
| 7 | **CLI surface: `zipsa configure <skill>`** — single argument, full walk-through. No `<key>` or `--reset` in v1. | YAGNI. Add when a second use case forces them. |
| 8 | **Non-interactive (no TTY) + missing values → exit 4 (`user_declined`).** Same exit code as HITL_UNATTENDED, with summary `error.code = requires_unset` (or `requires_stale`). | Reuses existing semantics. cron/automation users run `zipsa configure` once, then cron runs `zipsa run`. |
| 9 | **Stale path detection at run start.** If a saved path no longer exists, prompt for `edit / keep / skip-missing` (TTY) or exit 4 (no TTY). | Path may disappear (worktree removed, dir renamed). Detect early instead of failing in docker. |
| 10 | **Mount wiring: directive-based, NO template strings.** New mount fields `source:` (referencing `requires.X`), `container_prefix:` (for list expansion). `host:` (static) preserved. | No template engine dependency. Pydantic validates at manifest load. Self-documenting. |
| 11 | **basename collision → run-time error.** No automatic suffix (`zipsa-2`). User edits `requires.yaml` or renames a directory. | YAGNI. First user who hits it informs the v2 design. |
| 12 | **No env-var injection from `requires` in v1.** Mounts only. | Daily-progress doesn't need env injection. bip-daily-x's `x_env_file` is a future consumer. Defer until then. |

---

## Manifest schema

```yaml
spec:
  requires:
    project_roots:
      type: list[directory]
      prompt: |
        Which directories contain your git projects?
        (one path per line, ~ is expanded, empty line to finish)

    obsidian_vault:                     # example future use; not in v1 consumers
      type: directory
      prompt: "Path to your Obsidian vault"

  mounts:
    # Static (existing form preserved, no behavior change)
    - host: ~/.claude/projects
      container: /home/agent/.claude/projects
      mode: ro

    # Dynamic single (requires.X where X.type = directory)
    - source: requires.obsidian_vault
      container: /vault
      mode: ro

    # Dynamic list (requires.X where X.type = list[directory])
    - source: requires.project_roots
      container_prefix: /projects/
      mode: ro
```

**Naming rules:**
- Keys: lowercase + underscore (`project_roots`)
- Enforced by Pydantic validator on `RequiresSpec`

**Type system (v1):**

| Type | Validation | Saved as |
|---|---|---|
| `string` | non-empty | string |
| `directory` | `Path(value).expanduser().exists() and is_dir()` | absolute path string (expanded) |
| `list[directory]` | each element validated as `directory` | list of absolute path strings |

---

## Mount validation (manifest load time)

Pydantic validators on `MountSpec` and `RequiresSpec`:

| Condition | Error |
|---|---|
| `source: requires.X` and `X` not in `spec.requires` | `unknown requires key: X` |
| Both `source:` and `host:` set | `mutually exclusive` |
| `requires.X.type = directory` + `container_prefix:` used | `use 'container' for single directory` |
| `requires.X.type = list[directory]` + `container:` used | `use 'container_prefix' for list` |
| `container_prefix:` does not end with `/` | `must end with '/'` |

Invalid manifests fail at `zipsa run` startup. No container spawned.

---

## Mount expansion (run time)

Pseudocode in `_build_docker_command`:

```python
for mount_spec in manifest.spec.mounts:
    if mount_spec.host:                            # static
        host_path = expanduser(mount_spec.host)
        docker_cmd += ["-v", f"{host_path}:{mount_spec.container}:{mount_spec.mode}"]

    elif mount_spec.source:                        # dynamic
        key = mount_spec.source.removeprefix("requires.")
        value = requires_values[key]

        if isinstance(value, str):                 # directory
            docker_cmd += ["-v", f"{value}:{mount_spec.container}:{mount_spec.mode}"]

        elif isinstance(value, list):              # list[directory]
            container_paths = []
            for path in value:
                cp = mount_spec.container_prefix + Path(path).name
                if cp in container_paths:
                    raise MountCollisionError(...)
                container_paths.append(cp)
                docker_cmd += ["-v", f"{path}:{cp}:{mount_spec.mode}"]
```

---

## Lifecycle — `zipsa run` flow

```
zipsa run daily-progress yesterday
  │
  ├─ load manifest (Pydantic validation — mount rules above)
  │
  ├─ spec.requires empty → skip (existing skills unaffected)
  │
  └─ spec.requires non-empty:
     ├─ read ~/.zipsa/<skill>@<ver>/requires.yaml (may be empty)
     ├─ for each key in spec.requires, classify:
     │   ✓ present + type ok + paths valid       → ok
     │   ✗ missing OR type mismatch              → needs_prompt
     │   ⚠ present but path no longer exists     → needs_revalidation
     │
     ├─ if needs_prompt and no previous version: TTY prompt inline (or exit 4)
     │  if needs_prompt and previous version has compatible values:
     │     prompt "Carry over from <prev>? [Y/n]"   (Y == accept, n == fresh prompt)
     │
     ├─ if needs_revalidation: TTY re-prompt with the stale value shown as
     │    current; enter must re-validate (not silently re-accept) — empty
     │    input on a stale current behaves the same as fresh prompting.
     │    (Or exit 4 in non-interactive mode.)
     │
     ├─ save updated requires.yaml (atomic: tmp → rename)
     │
     └─ proceed to docker run with mounts expanded
```

### Inline prompt UX (first run)

```
$ zipsa run daily-progress yesterday
[zipsa] daily-progress requires configuration:

  project_roots — Which directories contain your git projects?
  (one path per line, ~ is expanded, empty line to finish)
  > ~/Code
  > ~/WestbrookAI
  > 

[zipsa] Validated:
  /Users/neochoon/Code         ✓
  /Users/neochoon/WestbrookAI  ✓

[zipsa] Saved to ~/.zipsa/daily-progress@0.4.0/requires.yaml
[zipsa] (To change later: zipsa configure daily-progress)
[zipsa] Starting run...
```

### Stale path UX

v1 implementation: the standard inline prompt is re-shown with the saved
value as `current:`. Pressing enter re-runs validation against the
current value — if the path still doesn't exist, the user sees the
validation error and is prompted to type a fresh value. No
3-option `[edit/keep/skip-missing]` UI in v1 (deferred — most users
will either re-type or run `zipsa configure`).

```
$ zipsa run daily-progress yesterday
[zipsa] daily-progress has stale paths in: project_roots.
project_roots — Which directories contain your git projects?
Current:
  /Users/neochoon/Code
  /Users/neochoon/OldProject       ← no longer exists
  /Users/neochoon/WestbrookAI
Press enter to keep, or type new value(s):
  > ~/Code
  > ~/WestbrookAI
  > 

[zipsa] Updated. Continuing run...
```

### Carry-over UX (after skill version bump)

```
$ zipsa run daily-progress yesterday          # first 0.5.0 run
[zipsa] Found previous install: daily-progress@0.4.0
  project_roots: 2 item(s)
Carry over? [Y/n]: 
[zipsa] Starting run...
```

`[Y/n]` only: Y accepts the prior values, n falls through to the normal
inline prompt for fresh values. v1 deliberately drops the `edit` /
`start-fresh` distinction the design originally sketched — `n` already
covers "start fresh" and `edit` adds little value at this scale.

### Non-interactive matrix

| TTY | Saved values | Action |
|---|---|---|
| yes | none | inline prompt |
| yes | stale | inline re-validation prompt |
| no | none | exit 4, `summary.error.code = requires_unset` |
| no | stale | exit 4, `summary.error.code = requires_stale` |
| any | all valid | proceed |

---

## CLI: `zipsa configure <skill>`

Single command, single argument. Walks through all `spec.requires`
keys, showing current values and allowing per-key replacement.

### First-time configure

```
$ zipsa configure daily-progress
[zipsa] daily-progress@0.4.0

project_roots — Which directories contain your git projects?
(one path per line, ~ is expanded, empty line to finish)
> ~/Code
> ~/WestbrookAI
> 

[zipsa] Validated:
  /Users/neochoon/Code         ✓
  /Users/neochoon/WestbrookAI  ✓

[zipsa] Saved to ~/.zipsa/daily-progress@0.4.0/requires.yaml
```

### Updating existing values

```
$ zipsa configure daily-progress
[zipsa] daily-progress@0.4.0

project_roots — Which directories contain your git projects?
Current:
  /Users/neochoon/Code
  /Users/neochoon/WestbrookAI

Update? Press enter to keep, or type new values (empty line to finish):
> ~/NewCode
> 

[zipsa] Validated:
  /Users/neochoon/NewCode      ✓

[zipsa] Updated.
```

### Edge cases

| Situation | Behavior |
|---|---|
| `zipsa configure unknown-skill` | exit 1, `Error: skill 'unknown-skill' not installed` |
| Skill installed but `spec.requires` empty | exit 0, `daily-progress has no required configuration.` |
| `zipsa configure` invoked without TTY | exit 4, `Error: configure requires an interactive terminal.` |
| User Ctrl+C mid-prompt | exit 130, no partial write (atomic rename) |
| Validation fails (path missing) | re-prompt up to 3 times → exit 1 if user can't supply a valid value |

### List-type editing semantics

For `list[directory]`: first input line is enter → keep current. First
input line is a path → start collecting a new replacement list (empty
line ends). Partial add/remove operations on existing lists are NOT in
v1.

---

## `zipsa list` integration

Existing output augmented with a configure-status indicator:

```
$ zipsa list
Installed skills (3):
  daily-progress@0.4.0  ⚠ needs configure (1 required, 0 set)
  hello-world@0.1.2
  weather@0.3.1
```

Indicator appears only when `spec.requires` is non-empty AND not all
required keys are set. Healthy/uninvolved skills look unchanged.

`install_health.check_install()` returns an additional flag
(`requires_missing: int` or similar) so the renderer can emit the
warning without re-loading the manifest twice.

---

## Components

**New files:**

| File | Responsibility |
|---|---|
| `launcher/zipsa/core/requires.py` | `load_requires()`, `save_requires()`, `validate_value()`, `prompt_for_value()`, `carry_over_from_previous()`, `classify_state()` (ok / needs_prompt / needs_revalidation) |
| `launcher/tests/test_requires.py` | Unit tests for the above |
| `launcher/tests/test_configure_command.py` | Integration tests for `zipsa configure` |
| `launcher/tests/fixtures/skills/requires-demo/` | Fixture skill with `spec.requires` for integration tests |

**Modified files:**

| File | Change |
|---|---|
| `launcher/zipsa/core/models.py` | `RequiresSpec`, `MountSpec` extended (`source`, `container_prefix`), validators |
| `launcher/zipsa/cli.py` | `configure` command; `run` checks requires + triggers prompt flow; `list` indicator |
| `launcher/zipsa/core/executor.py` | Mount expansion (see pseudocode above) |
| `launcher/zipsa/paths.py` | `skill_requires_file(name, version)` helper |
| `launcher/zipsa/core/install_health.py` | `check_install()` includes requires status |
| `launcher/tests/test_cli.py` | run-time requires check integration |
| `launcher/tests/test_models.py` | Manifest schema validation cases |
| `launcher/tests/test_executor.py` | Mount expand integration |
| `launcher/CLAUDE.md` | Document the `requires` pattern |
| `skills/README.md` | Manifest writer guide section |

---

## Test plan

### Unit (`test_requires.py`)

- `load_requires(skill, version)` — missing file → empty dict; present → parsed
- `save_requires(...)` — atomic write (writes tmp, renames)
- `validate_value("directory", "~/Code")` — expanduser + exists + is_dir → returns absolute
- `validate_value("directory", "/no/such/path")` — `ValueError`
- `validate_value("list[directory]", ["~/Code", "~/X"])` — per-element validation
- `validate_value("string", "")` → `ValueError`
- `validate_value("string", "hi")` → returns `"hi"`
- `validate_value("string", 123)` → `ValueError` (type mismatch)
- `prompt_for_value(...)` — patch `sys.stdin` to simulate input
- `carry_over_from_previous(skill, "0.5.0")` — when `@0.4.0` has values + `@0.5.0` empty → returns matching keys
- Carry-over excludes keys with type mismatch between versions
- `classify_state(spec_requires, saved_values)` returns `(ok, needs_prompt, needs_revalidation)` partitioning

### Integration (`test_configure_command.py`)

- `zipsa configure daily-progress` with new requires-demo fixture → file created
- Second invocation — current values shown, enter keeps them
- Second invocation — new input replaces
- Unknown skill → exit 1
- Skill with empty requires → exit 0 + no-op message
- No TTY → exit 4
- Validation fails 3 times → exit 1
- Ctrl+C mid-prompt → no file change

### Integration (`test_cli.py` additions)

- `zipsa run` on existing skill without requires → unchanged (regression)
- `zipsa run` with requires set → proceeds, mounts visible in dry-run docker cmd
- `zipsa run` with requires missing + TTY → inline prompt → run proceeds
- `zipsa run` with requires missing + no TTY → exit 4, summary `error.code=requires_unset`
- `zipsa run` with stale path + TTY → re-validation prompt
- `zipsa run` with stale path + no TTY → exit 4, summary `error.code=requires_stale`
- Carry-over flow: pre-populate `@0.4.0/requires.yaml`, run `@0.5.0` → carry-over prompt appears
- `zipsa list` shows `⚠ needs configure` indicator only when applicable

### Integration (`test_executor.py` additions)

- Mount expand with `requires.project_roots: ["/a", "/b"]` → `-v /a:/projects/a:ro -v /b:/projects/b:ro` both present
- basename collision (`/x/zipsa` and `/y/zipsa`) → clear error, no container spawned

### Schema validation (`test_models.py` additions)

- `source: requires.unknown_key` → `ValidationError` at manifest load
- `source:` + `host:` together → `ValidationError`
- `requires.X.type=directory` + `container_prefix:` → `ValidationError`
- `requires.X.type=list[directory]` + `container:` → `ValidationError`
- `container_prefix: /projects` (no trailing slash) → `ValidationError`

### Backward compatibility regression

- All existing manifests (hello-world, weather, daily-progress, bip-daily-x, fixtures/test-skill) have no `spec.requires` → all tests pass unchanged
- `zipsa list` output for skills without `spec.requires` is byte-for-byte identical to before

### Manual smoke (post-merge)

```bash
# 1. Existing skill, no requires → unchanged
zipsa run hello-world

# 2. New fixture
zipsa install --link launcher/tests/fixtures/skills/requires-demo
zipsa configure requires-demo
cat ~/.zipsa/requires-demo@0.1.0/requires.yaml
zipsa run requires-demo "hi"
zipsa list

# 3. Non-interactive
mv ~/.zipsa/requires-demo@0.1.0/requires.yaml /tmp/
zipsa run requires-demo "hi" </dev/null   # expect exit 4
```

---

## Out of scope (BACKLOG candidates after first consumer ships)

- **Env var injection** — `requires.X` exposed as `$VAR` to the agent.
  Defer until a second consumer (bip-daily-x's `x_env_file`?) forces it.
- **Fine-grained configure subcommands** — `zipsa configure <skill> <key>`,
  `--reset`, `--show`, `--all`. v1 has only the full walk-through.
- **List partial edits** — add/remove individual items in a long list
  without re-typing the rest.
- **Additional types** — `file`, `path`, `secret` (echo off), `int`,
  `bool`. Add as second-consumer needs surface.
- **Per-path mapping** — `project_roots: {Code: zipsa, Personal: zipsa-personal}`
  to resolve basename collisions automatically.
- **Sharing requires across skills** — e.g. a global `project_roots` that
  multiple skills inherit. Likely better solved by `runtime-config.yaml`
  with skill-scoped sections, not by extending `requires`.

---

## First consumer: daily-progress (separate PR)

After this design ships, the daily-progress manifest gets:

```yaml
spec:
  requires:
    project_roots:
      type: list[directory]
      prompt: |
        Which directories contain your git projects?
        (one path per line, ~ is expanded, empty line to finish)

  mounts:
    - host: ~/.claude/projects
      container: /home/agent/.claude/projects
      mode: ro
    - source: requires.project_roots
      container_prefix: /projects/
      mode: ro
```

Plus a bump to `npx agenthud@0.9.2` and adding `--with-git` to the
report invocation. That work is a follow-up PR, not part of this one.

---

## Open questions

None at design time. Implementation may surface edge cases (e.g.,
exact wording for prompts, handling of relative paths in user input,
behavior when the requires file is hand-edited to an invalid YAML).
Those are implementation-detail decisions, not design-shape decisions.
