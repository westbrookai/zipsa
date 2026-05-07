# Centralized Skill Run Storage Design

**Date:** 2026-05-07
**Status:** Approved

## Problem

Skill runtime data (logs, MCP config, env file) is currently stored inside the skill directory at `skill_dir/.zipsa/`. This causes two problems:

1. Logs are scattered across skill directories, making cross-skill log lookup difficult and preventing skills from referencing each other's run history.
2. Skill directories are meant for definition (manifest, instructions) not runtime state — mixing both is conceptually messy.

## Design

Move all runtime data out of `skill_dir/.zipsa/` into `~/.zipsa/<skill-name>@<version>/`.

### New Directory Structure

```
~/.zipsa/
├── .env                           # Global env file (existing)
├── .credentials.json              # Claude credentials (existing)
├── runtime-config.yaml            # Runtime config (existing)
│
├── hello-world@0.1.0/
│   ├── .claude.json               # MCP config — persists after execution
│   ├── .claude.json.org           # Reference copy for post-run comparison
│   ├── .env                       # Per-execution env — deleted after run
│   └── runs/
│       └── 2026-05-07_120000_000/
│           ├── output.jsonl       # Raw stream-json output
│           ├── summary.jsonl      # Filtered important events
│           └── metadata.json      # Cost, duration, tokens
│
└── weather@1.2.0/
    ├── .claude.json
    └── runs/
        └── ...
```

`skill_dir/.zipsa/` is no longer created.

### Naming Convention

Skill directory name: `<skill-name>@<version>` (e.g., `hello-world@0.1.0`).

Using name + version (not just name) so different versions of the same skill have isolated configs and log histories. This also makes it easy to compare run behavior across versions.

### File Lifecycle

| File | Created | Deleted |
|------|---------|---------|
| `.claude.json` | Before each run (regenerated) | Never |
| `.claude.json.org` | Before each run (regenerated) | Never |
| `.env` | Before each run | After run (contains secrets) |
| `runs/<timestamp>/` | During run | Never (retained for history) |

`.claude.json` is regenerated each run because MCP server config may change between versions. It is not deleted after execution because Claude Code may modify it during the run, and `.claude.json.org` is kept for post-run diffing.

## Code Changes

### `Skill.build_claude_json(output_dir=None)`

Add an `output_dir: Path | None` parameter. When provided, write `.claude.json` and `.claude.json.org` to that directory. Default: `~/.zipsa/<name>@<version>/`.

Remove the existing `skill_dir/.zipsa/` path logic.

### `DockerExecutor.run()`

Compute the skill data directory:
```python
skill_data_dir = Path.home() / ".zipsa" / f"{skill.name}@{skill.manifest.metadata.version}"
skill_data_dir.mkdir(parents=True, exist_ok=True)
```

Pass `skill_data_dir` to `build_claude_json()` and use it for `run_dir` and `_write_env_file()`.

### `DockerExecutor._write_env_file(skill, env)`

Change write path from `skill.skill_dir / ".zipsa" / ".env"` to `skill_data_dir / ".env"`.

### `DockerExecutor._build_docker_command()`

Update `.claude.json` mount path to use the new `skill_data_dir` location. No other mount changes needed.

## Testing

- Update `test_run_creates_claude_config` — assert `.claude.json` exists in `~/.zipsa/<name>@<version>/`
- Update `test_run_cleans_up_env_file` — assert `.env` deleted from `~/.zipsa/<name>@<version>/`
- Update `test_build_docker_command_uses_env_file` — assert env file path is under `~/.zipsa/`
- Update `TestRuntimeConfig` — check env file at new path
- Add `test_run_creates_run_dir_in_home` — assert `runs/<timestamp>/` created under `~/.zipsa/<name>@<version>/`
- Ensure no `skill_dir/.zipsa/` is created in any test

## Migration

No migration of existing `skill_dir/.zipsa/` data. Old directories can be deleted manually or left in place — they will not be read by the new code.
