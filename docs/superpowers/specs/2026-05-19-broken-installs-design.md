# Broken linked installs visible to list + recoverable via install — Design

**Date:** 2026-05-19
**Status:** Draft — pending user approval
**Scope:** When a `--link` install's source disappears (worktree removed,
directory deleted), `zipsa list` shows the entry with a `[broken]` marker
+ recovery hint instead of silently filtering it out, and `zipsa install`
replaces it instead of erroring with "already installed."

---

## Motivation

We've hit this twice in two days:

1. After PR #16 (memory feature) shipped, the `weather` skill's source
   `--link` pointed into `.worktrees/feat-memory/`, which got removed
   on cleanup. The skill became invisible to `zipsa list`. Running
   `zipsa install --link` for it returned `Error: Skill 'weather' is
   already installed. Use --force to overwrite.` — but `zipsa list`
   said it wasn't installed. Two commands disagreed.

2. Same pattern today after PR #27 with `hello-world`. The
   user-facing symptom: "hello-world 지워졌는데" (it's gone). The
   actual symptom: dangling symlink hidden by `zipsa list`.

The BACKLOG entry from 2026-05-18 (#19) already documented this; we
deferred and it bit twice more. Time to fix.

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | `zipsa list` SHOWS broken entries with a `✗ broken` marker + reason + recovery hint, instead of silently filtering them. | Disagreement between list and install is the actual bug. Hidden broken entries are the worst possible UX. |
| 2 | `zipsa install` replaces a broken entry transparently and prints `Replaced broken link: <name> (linked)`. No prompt, no `--force` needed. | `--force` was needed because install assumed the existing entry was valid. Broken entries have no value to preserve. |
| 3 | "Broken" = any condition that prevents the launcher from loading the entry as a Skill. Includes: dangling symlink, missing manifest.yaml, unparseable manifest. The reason string distinguishes which. | One bucket from the user's perspective ("can't use this entry"). |
| 4 | Out of scope: `zipsa doctor` (auto-prune), worktree-cleanup integration. Both remain BACKLOG items. | Keeps the PR focused. The two bugs from decisions 1 + 2 are what's actually biting. |

## User-facing behavior

### `zipsa list` — before vs after

**Before** (broken entry hidden, no signal):

```
Installed skills (2):

  daily-progress@0.4.0 (linked)
    17 runs · 94% success
    Linked from: /Users/neochoon/WestbrookAI/zipsa/skills/daily-progress

  weather@0.3.1 (linked)
    22 runs · 54% success
    Linked from: /Users/neochoon/WestbrookAI/zipsa/skills/weather
```

**After** (broken entry visible, reason + hint):

```
Installed skills (3):

  daily-progress@0.4.0 (linked)
    17 runs · 94% success
    Linked from: /Users/neochoon/WestbrookAI/zipsa/skills/daily-progress

  hello-world  ✗ broken
    Linked source missing: /Users/neochoon/WestbrookAI/zipsa/.worktrees/feat-empty-query-intro/skills/hello-world
    Fix: zipsa install --link <new-path>  (or: zipsa uninstall hello-world)

  weather@0.3.1 (linked)
    22 runs · 54% success
    Linked from: /Users/neochoon/WestbrookAI/zipsa/skills/weather
```

Notes:
- The count includes broken entries (`Installed skills (3)`).
- Name is taken from the directory entry (always available — that's
  what survives a broken symlink).
- Version is not shown for broken entries (we couldn't load it).
- The reason line names the exact failure mode (`Linked source
  missing`, `manifest.yaml not found`, `Invalid manifest: <pydantic
  error head>`).
- The Fix line gives both repair paths.

### `zipsa install --link <path>` against a broken existing entry — before vs after

**Before:**

```
$ zipsa install --link ./skills/hello-world
Error: Skill 'hello-world' is already installed. Use --force to overwrite.
```

**After:**

```
$ zipsa install --link ./skills/hello-world
Replaced broken link: hello-world (linked)
```

Notes:
- No `--force` needed. The existing entry is broken; we have nothing
  to preserve.
- An EXPLICIT `--force` still works the same as today for the
  not-broken case (overwrites a healthy install). Behavior for broken
  ↔ not-broken is the only difference.
- Source-side install (`zipsa install <github-source>`) goes through
  the same path and gets the same treatment.

## Detection — what counts as "broken"

When walking `~/.zipsa/skills/*`:

| Condition | Result |
|---|---|
| Symlink, target doesn't exist | broken, reason `Linked source missing: <target>` |
| Directory or valid symlink, but no `manifest.yaml` | broken, reason `manifest.yaml not found` |
| `manifest.yaml` exists but `Skill.load()` raises (yaml parse / pydantic validation) | broken, reason `Invalid manifest: <first line of error>` |
| All other load errors | broken, reason `Load failed: <exception class>` |

Detection helper lives in `zipsa/core/install_health.py` (new) so both
CLI commands can use it.

## What changes in the codebase

| File | Change |
|---|---|
| `launcher/zipsa/core/install_health.py` (new) | `check_install(path) -> InstallHealth` returning `{ok: bool, reason: Optional[str]}` |
| `launcher/zipsa/cli.py` — `list` command | Instead of `try: Skill.load(...); except: continue` (the silent skip), wrap with `check_install` and render a broken row when not ok. |
| `launcher/zipsa/cli.py` — `install` command | Before raising the "already installed" error, check if the existing entry is broken via `check_install`. If broken → log `Replaced broken link: …`, remove the old entry, proceed with normal install. |
| `launcher/tests/test_install_health.py` (new) | Unit tests on `check_install` for each detection case. |
| `launcher/tests/test_cli.py` | Add `TestListBrokenEntries` + `TestInstallReplacesBroken` integration tests. |

No change to: `Skill.load`, executor, manifest model, MCP, HITL, limits.

## Test plan

Unit (`test_install_health.py`):
- Healthy linked install (target exists, manifest valid) → `ok=True`
- Dangling symlink → `ok=False, reason='Linked source missing: …'`
- Symlink to a dir without manifest.yaml → `ok=False, reason='manifest.yaml not found'`
- Symlink to a dir with corrupt manifest.yaml → `ok=False, reason='Invalid manifest: …'`
- Real directory install (no symlink) with valid manifest → `ok=True`
- Real directory install missing manifest → `ok=False, reason='manifest.yaml not found'`

Integration (`test_cli.py`):
- `zipsa list` with one healthy + one broken (dangling symlink) entry
  → both appear; broken row contains `✗ broken` and `Linked source
  missing`.
- `zipsa install --link <new-path>` when existing entry is broken
  → succeeds, prints `Replaced broken link:`, post-condition: link
  points to new path.
- `zipsa install --link <new-path>` when existing entry is healthy
  AND no `--force` → still errors with "already installed" (existing
  behavior preserved for healthy entries).

Manual smoke:
- Make a tmp dir with a valid manifest, `zipsa install --link <tmp>`,
  delete the tmp, `zipsa list` shows broken with the correct reason,
  `zipsa install --link <new-tmp-with-same-name>` replaces cleanly.

## YAGNI / out of scope

- **`zipsa doctor`** — auto-prune broken entries, batch repair. Useful
  but not needed for the immediate pain. Stays in BACKLOG.
- **Worktree-cleanup hook** — automatic launcher symlink cleanup when
  `git worktree remove` runs. Cross-tool concern (git, superpowers
  workflow); separate PR scope.
- **`--all` / `--healthy` filters on `zipsa list`** — premature. Most
  users want to see broken entries by default.
- **Pruning broken entries during `zipsa list`** — list is a read
  command; mutations belong to `install` / `uninstall` / `doctor`.

## Open questions

- For broken entries, do we show their source path (linked source
  target, even though it's missing) in the "Fix:" hint? Decision:
  yes — it's the most likely thing the user wants to grep for when
  searching their filesystem. The hint says "or: zipsa install
  --link <new-path>" with `<new-path>` as a literal placeholder
  because we don't know where they re-checked out the source.
- Should we attempt to AUTO-RECOVER if the user runs the original
  `zipsa run <broken-skill> ...` command (not install)? Probably
  not — `run` should refuse with the same broken-row message,
  pointing them to `install --link`. Keeps the recovery path
  explicit. Add as a small bonus to scope if cheap.
