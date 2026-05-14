# zipsa install Command Design

## Goal

Add `zipsa install` to fetch skills from GitHub or register local skills, enabling name-based execution. Replace path-based skill resolution with an installed-skills model.

## Architecture

Skills are installed into `~/.zipsa/skills/<name>/` and referenced by name in all commands. GitHub sources are downloaded via tarball API; local sources are copied or symlinked. An `_install.json` file tracks provenance and version in each skill directory. Run metadata is enriched with `user_input` to power the new `zipsa list` statistics display.

## Tech Stack

- Python `urllib` (stdlib) for GitHub tarball download (no `git`, no new dependency)
- Python `tarfile` for extraction
- Existing `Skill.load()` + `ValidationError` for install-time validation
- Existing `zipsa.paths` for directory resolution

---

## Section 1: CLI Changes

### New commands

```
zipsa install <source>           # GitHub (default scheme)
zipsa install --path <dir>       # local copy
zipsa install --link <dir>       # local symlink
zipsa uninstall <name>
zipsa list                       # installed skills + run stats  (new behavior)
zipsa discover [dir]             # renamed from current `zipsa list`
```

### Source format (GitHub only)

| Input | Interpretation |
|-------|---------------|
| `user/repo` | GitHub repo root, default branch |
| `user/repo/sub/path` | GitHub monorepo subpath |
| `user/repo@ref` | GitHub with branch/tag/commit |
| `user/repo/sub@ref` | GitHub subpath + ref |
| `github:user/repo/sub@ref` | Explicit GitHub scheme |
| `https://github.com/user/repo/...` | URL, parsed to above form |

Default scheme is GitHub. GitLab is not supported.

**Auth:** `GITHUB_TOKEN` env var is included as Bearer token when present (enables private repos). Anonymous otherwise.

---

## Section 2: Install Paths & Metadata

### Directory layout

```
~/.zipsa/skills/
  daily-progress/
    manifest.yaml
    SKILL.md
    _install.json        ← managed by zipsa
  hello-world -> /Users/.../skills/hello-world   ← symlink for --link
```

### `_install.json` structure

```json
{
  "source": "github:westbrookai/zipsa/skills/daily-progress",
  "ref": "main",
  "commit_sha": "a1b2c3d4e5f6...",
  "version": "0.1.0",
  "type": "github",
  "installed_at": "2026-05-11T12:00:00Z"
}
```

`type` values: `"github"` | `"copy"` | `"link"`.  
For `"copy"` and `"link"` types, `source` is the resolved absolute local path and `commit_sha` is absent.

---

## Section 3: GitHub Download Mechanism

Download uses the tarball API — no `git` required.

```
GET https://api.github.com/repos/{user}/{repo}/tarball/{ref}
→ .tar.gz stream → in-memory extraction → filter to subpath → write to ~/.zipsa/skills/<name>/
```

commit SHA is read from the `X-GitHub-Resolved-Sha` response header (or from the redirect URL).

### Install flow

1. Parse source string → `(user, repo, subpath, ref)`
2. Download tarball from GitHub API
3. Extract files matching `subpath/` prefix into a temp directory
4. Parse and validate `manifest.yaml` using existing `Skill.load()` + `ValidationError` → abort + cleanup on failure
5. Move from temp to `~/.zipsa/skills/<manifest-name>/`
6. Write `_install.json`
7. Print `✓ Installed <name> @ <version> (commit: a1b2c3d)`

**Conflict handling:** if `~/.zipsa/skills/<name>/` already exists, exit with error. `--force` flag overwrites.

---

## Section 4: `zipsa run` Name Resolution

All commands (`run`, `validate`, `view`, `connect`) accept a skill **name only** — not a path. This is a breaking change from the current path-based `skill_dir` argument.

Resolution:
```
zipsa run daily-progress "query"
→ ~/.zipsa/skills/daily-progress/ 조회
→ not found → "Skill 'daily-progress' not found. Try: zipsa install <source>"
```

A shared helper `resolve_skill(name: str) -> Path` in `zipsa/paths.py` handles the lookup and raises `SkillNotInstalledError` on miss. All CLI commands call this helper.

Local development workflow:
```bash
zipsa install --link ./my-skill   # register local skill
zipsa run my-skill "test query"
```

---

## Section 5: `zipsa list` (new) & `zipsa discover` (renamed)

### `zipsa list`

Scans `~/.zipsa/skills/` and computes run stats from `~/.zipsa/<name>@<version>/runs/*/metadata.json`.

```
$ zipsa list

Installed skills (3):

  daily-progress @ 0.1.0
    Last run: 2 hours ago · 12 runs · 83% success
    Source: github:westbrookai/zipsa-skills/skills/daily-progress@v0.1.0

  x-publisher @ 0.2.1
    Last run: yesterday · 4 runs · 100% success
    Source: github:westbrookai/zipsa-skills/skills/x-publisher@v0.2.1

  hello-world @ 0.1.0 (linked)
    Linked from: /Users/neochoon/work/zipsa/skills/hello-world
    Last run: never
```

Stats computation:
- **Last run**: latest `run_id` timestamp across all `<name>@*` directories
- **Run count**: total run directories across all versions
- **Success rate**: `(runs where is_error=false) / total_runs * 100`

### `zipsa discover`

Renamed from current `zipsa list`. Scans an arbitrary directory for skills. CLI argument remains a positional `skills_dir` (default: `.`).

---

## Section 6: `metadata.json` — Add `user_input`

`executor._save_metadata()` gains a `user_input: str` parameter and writes it to `metadata.json`:

```json
{
  "run_id": "2026-05-11_120000_00000",
  "skill_name": "daily-progress",
  "skill_version": "0.1.0",
  "user_input": "log today's progress",
  "is_error": false,
  ...
}
```

The `run()` method passes `user_input` through to `_save_metadata`.

---

## Section 7: `zipsa uninstall`

```
zipsa uninstall daily-progress
→ removes ~/.zipsa/skills/daily-progress/ (or symlink for linked skills)
→ preserves ~/.zipsa/daily-progress@0.1.0/ (run history intact)
→ prints: "✓ Uninstalled daily-progress"
```

For `--link` installs: removes the symlink only; the original directory is untouched.

---

## Error Handling

| Situation | Behavior |
|-----------|----------|
| GitHub 404 | "Repository or path not found: user/repo/sub" |
| Manifest validation failure | "Install failed: invalid manifest — {errors}" + cleanup temp files |
| Name already installed | "Skill 'name' is already installed. Use --force to overwrite." |
| Network error | "Failed to download: {error}" |
| `uninstall` name not found | "Skill 'name' is not installed." |
| `run` name not found | "Skill 'name' not found. Try: zipsa install <source>" |

---

## Breaking Changes

- `zipsa run <path>` no longer works — must use skill name
- `zipsa validate <path>` no longer works — must use skill name  
- `zipsa view <path>` no longer works — must use skill name
- `zipsa connect <path>` no longer works — must use skill name
- `zipsa list` behavior changes — use `zipsa discover` for old behavior
