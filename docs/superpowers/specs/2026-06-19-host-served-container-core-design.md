# Design — shared host-served container core for forge/run (#175)

**Status:** Approved (brainstorming). Scope **B**: structural extraction +
propagate `--dry-run` / leak-avoidance to `forge`. Scope **C** (forge
run-record) is a follow-up Issue.

**Depends on #173/#174** (the `run` dry-run + leak fixes). This branch is
stacked on `feat/dry-run-exec-run-parity`; rebase onto `main` once #174 merges.

## Problem

`run_forge` (`create.py`) and `run_skill_llm` (`run_llm.py`) copy-paste the same
skeleton:

> start a host MCP server → `build_mcp_config(port, token)` → write the
> mcp-config to a host file → build a `docker run … claude -p …` argv → run the
> container → `finally: server.stop()`.

Both run claude `-p` (no TTY into the container) **conversing with the host user
over MCP** (`ask`/`confirm`/`choose`) and calling host-served tools — so the
container is not "headless" in spirit; it is *host-served*. The two argv builders
(`build_run_argv` / `build_docker_argv`) are ~90 % identical, differing only in
the mount policy:

| | `run` | `forge` |
|---|---|---|
| work dir mount | `skill_root` `:ro` (+ `extra_mounts`) | `staging` `:rw` |
| host server | `RunServer` (exec tool) | `ForgeServer` (exec + run-draft + promote) |
| output handling | `Popen` → tee to terminal + run-record | `subprocess.run`, stdio inherited (no record) |
| work dir lifecycle | `skill_root` already exists | `mkdtemp` a fresh staging dir |

Because the skeleton is duplicated, cross-cutting concerns live per-path.
`--dry-run` and the two leak fixes (orphan `~/.zipsa/exec-out/` dir, unbounded
`dry-run-*.mcp.json`) landed in `exec`/`run` (#173) but **`forge` has no
`--dry-run` at all**, and would reproduce the same leaks (its `mkdtemp` staging
dir + per-run mcp-config) if naively given one.

Both paths are docker-only — there is **no** `--local` for `run`/`forge`
(`--local` exists solely on `zipsa exec`, the deterministic phase runner, which
is a different path and out of scope here).

## Goal (scope B)

1. Extract a shared core that owns the duplicated, cross-cutting sequence —
   including `--dry-run`, port-leak avoidance, and orphan-file avoidance — so it
   lives in exactly one place.
2. Route both `run` and `forge` through it as thin callers.
3. `forge` gains `--dry-run` with the same contract as `run`: print the argv +
   mcp-config path, **start no server (no bound port), spawn no container, leave
   no orphan staging dir / config file**, exit 0.
4. `run` behavior is unchanged (pure refactor parity).

## Design

### New module `zipsa/host_served_container.py`

Purpose: build and run a host-served claude container (a containerized claude
whose conversation + tools are served by a host MCP server), with one
implementation of the dry-run / lifecycle / mcp-config / orphan-avoidance logic.

**(a) Unified argv builder** — replaces both `build_run_argv` and
`build_docker_argv`:

```python
def build_host_served_argv(
    *, image, mount, mcp_config_host, prompt, env_file,
    extra_mounts=None,
) -> list[str]:
    """One `docker run … claude -p …` builder for forge + run.
    `mount = (host, container, mode)` — run: (skill_root, skill_root, "ro");
    forge: (staging, staging, "rw"). Pure; unit-testable without docker."""
```

The body is the shared argv assembly already common to both builders:
`docker run --rm`, optional `--env-file`, Linux `--add-host
host.docker.internal:host-gateway`, the `mount` and the `:ro` mcp-config mount,
`extra_mounts` (ro), `-w`, image, then `claude -p <prompt> --mcp-config
<container-path> --strict-mcp-config --permission-mode bypassPermissions`.

**(b) The core** — owns dry-run, server lifecycle, mcp-config write/cleanup,
work-dir lifecycle, argv assembly. Five factory seams passed as keyword args
(no policy object — keep it a plain function):

```python
def run_host_served_container(
    *, image, env_file,
    work_dir_factory,   # (dry_run: bool) -> Path
                        #   run:   lambda _dry: skill_root            (exists; never created)
                        #   forge: real -> mkdtemp staging / dry -> un-created placeholder
    mount_mode,         # "ro" | "rw"
    extra_mounts,       # list[(host, container)] | None  (ro; claude container)
    server_factory,     # (work_dir: Path) -> Server      built ONLY on the real path
    prompt_factory,     # (work_dir: Path) -> str
    execute,            # (argv: list[str]) -> int        output handling only
    mcp_subdir,         # "run" | "staging"  (subdir under ~/.zipsa for the config)
    dry_run=False,
) -> int:
    work_dir = work_dir_factory(dry_run)
    prompt   = prompt_factory(work_dir)

    if dry_run:
        cfg = _write_dry_run_config(mcp_subdir)          # FIXED path dry-run.mcp.json, placeholder port/token
        argv = build_host_served_argv(
            image=image, mount=(work_dir, work_dir, mount_mode),
            mcp_config_host=cfg, prompt=prompt,
            env_file=env_file if env_file.exists() else None,
            extra_mounts=extra_mounts,
        )
        _print_host_served_dry_run(argv, cfg)
        return 0                                          # no server, no container, no mkdtemp

    server = server_factory(work_dir)
    server.start()
    try:
        cfg  = _write_run_config(mcp_subdir, server.port, server.token)
        argv = build_host_served_argv(
            image=image, mount=(work_dir, work_dir, mount_mode),
            mcp_config_host=cfg, prompt=prompt,
            env_file=env_file if env_file.exists() else None,
            extra_mounts=extra_mounts,
        )
        return execute(argv)
    finally:
        server.stop()
```

Leak avoidance is structural and central:
- dry-run never calls `server_factory`/`start` → no bound port.
- `work_dir_factory(dry_run=True)` returns an **un-created** placeholder for
  forge → no orphan staging dir (mirrors the `run` fix that made `exec-out` a
  placeholder under dry-run).
- `_write_dry_run_config` writes a single **fixed** `dry-run.mcp.json`
  (overwritten each run) → no unbounded accumulation. Only the *dry-run* config
  is a fixed file; `_write_run_config` (real path) keeps a **unique** temp name,
  preserving `run`'s current real-path behavior (the pre-existing real-path
  config accumulation is out of scope — do not change it here).

`build_mcp_config` moves into this module; `create.py` re-imports it for
backward compatibility within the package.

### `run` and `forge` become thin callers

**`run_skill_llm`** (`run_llm.py`):
- `work_dir_factory = lambda _dry: skill_root.resolve()` (the installed skill;
  never created, so no dry-run leak there).
- `mount_mode = "ro"`. The core's `extra_mounts` kwarg is **`None`** — the
  caller's own `extra_mounts` param (skill creds) is a *different* scope: it
  reaches the *script* sub-container via `RunScriptHandler.default_mounts`, never
  the claude container. (Two distinct things sharing a name; the core sees `None`.)
- `server_factory = lambda wd: RunServer(hitl_io, RunScriptHandler(docker_image=image, skill_root=wd, default_mounts=extra_mounts))` — here `extra_mounts` is the caller's param, not the core kwarg.
- `prompt_factory = lambda wd: build_run_prompt(wd, user_input)`.
- `execute(argv)`: the existing `Popen` + `_tee_stream` + `_write_run_record`
  logic, **unchanged**, returning the container exit code. The `run_dir` is
  created inside `execute` (real path only) → no dry-run record leak.
- `mcp_subdir = "run"`.

**`run_forge`** (`create.py`):
- `work_dir_factory`: real → `mkdtemp(prefix="draft-", dir=staging_root)`; dry →
  `staging_root / "draft-DRYRUN"` (un-created placeholder).
- `mount_mode = "rw"`, `extra_mounts = None`.
- `server_factory = lambda wd: ForgeServer(hitl_io, exec_handler=RunScriptHandler(...wd...), run_handler=RunDraftHandler(...wd...), promote_handler=PromoteSkillHandler(dest_root=skills_dir), staging_path=str(wd))`.
- `prompt_factory = lambda wd: build_forge_prompt(intent, wd)`.
- `execute(argv) = subprocess.run(argv, stdin=subprocess.DEVNULL).returncode`
  (stdio inherited — unchanged forge behavior).
- `mcp_subdir = "staging"`.

### CLI

`forge` command (`cli.py`) gains a `--dry-run` option threaded to
`run_forge(dry_run=…)`. No other `forge` flags change. `run`/`exec` CLI
unchanged.

### Removed / migrated

- `build_run_argv` and `build_docker_argv` are **removed** (replaced by
  `build_host_served_argv`). Their unit tests migrate to a single
  `build_host_served_argv` suite covering the ro / rw / extra_mounts variants.
- `build_mcp_config` moves to the new module; `create.py` re-exports.
- `CreateServer` / `run_create` (legacy) are **not** touched.
- `exec`'s `.md` phase is **not** a member of this skeleton (it runs `claude -p`
  with no host MCP server) and is left as-is.

## Testing

- **Unit** `build_host_served_argv`: ro (run-shaped) mount, rw (forge-shaped)
  mount, `extra_mounts` appended ro, `--env-file` present/absent, Linux
  `--add-host`. Absorbs the two old builders' suites.
- **Core** `run_host_served_container`:
  - dry-run: `server_factory` not invoked / `server.start` not called,
    `execute` not called, returns 0, prints argv + a config path, work dir is an
    un-created placeholder.
  - real: `server.start`/`server.stop` called (stop even when `execute` raises),
    `execute` called with the built argv, config written under `mcp_subdir`.
- **forge `--dry-run`** (CLI + `run_forge(dry_run=True)`): `subprocess`/`Popen`
  not called, server not started, exit 0, **no orphan `staging/draft-*` dir and
  no orphan `run`/`staging` `*.mcp.json`** after two runs (the leak assertions,
  mirroring #173's).
- **`run` regression**: existing `run_skill_llm` tests stay green — output,
  teeing, and run-record behavior unchanged by the refactor.
- Full suite green (`cd launcher && uv run pytest`).

## Risks / notes

- The refactor must preserve `run`'s exact output/record behavior — the
  `Popen`+tee+record block moves verbatim into the `execute` closure; do not
  restructure it in the same change.
- `server.stop()` must still run on every real-path exit (including `execute`
  raising) — the `finally` guarantees it.
- Keep the dry-run print format consistent with #173's
  (`_print_run_dry_run` / `_print_phase_dry_run`): full command line + scannable
  indexed args.
- Scope discipline: only the extraction + forge `--dry-run`. No forge
  run-record (scope C), no `CreateServer` cleanup, no `exec` `.md` changes.
