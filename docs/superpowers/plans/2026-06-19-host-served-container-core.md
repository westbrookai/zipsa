# Host-Served Container Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the duplicated "host MCP server + headless `claude -p` container" skeleton shared by `run_forge` and `run_skill_llm` into one core, and give `forge` a `--dry-run` with the same no-leak contract as `run` (#173).

**Architecture:** A new module `zipsa/host_served_container.py` owns one argv builder (`build_host_served_argv`) and one orchestration core (`run_host_served_container`) that holds the dry-run / server-lifecycle / mcp-config / orphan-avoidance logic. `run_skill_llm` and `run_forge` become thin callers passing five factory seams (`work_dir_factory`, `server_factory`, `prompt_factory`, `execute`, `mcp_subdir`). The two old per-path argv builders are removed.

**Tech Stack:** Python 3.12, pytest, Typer (CLI), Docker (`docker run … claude -p`), FastMCP host servers.

**Spec:** `docs/superpowers/specs/2026-06-19-host-served-container-core-design.md` (#175).

**Branch:** `refactor/host-served-container-core`, stacked on `feat/dry-run-exec-run-parity` (#173/#174). Rebase onto `main` after #174 merges.

**Out of scope:** forge run-record (scope C), `CreateServer`/`run_create` cleanup, `exec` `.md` phase (no host server), `run`/`forge` real-path mcp-config accumulation (pre-existing).

---

## File structure

- Create `zipsa/host_served_container.py` — unified argv builder + core + config/print helpers + `build_mcp_config` (moved here).
- Create `tests/test_host_served_container.py` — builder + core unit tests.
- Modify `zipsa/create.py` — `run_forge` routed through the core, gains `dry_run`; `build_docker_argv` removed; re-export `build_mcp_config`.
- Modify `zipsa/run_llm.py` — `run_skill_llm` routed through the core; `build_run_argv` + `_CONTAINER_MCP_CONFIG` removed; import `build_mcp_config` from the new module.
- Modify `zipsa/cli.py` — `forge` command gains `--dry-run`.
- Modify `tests/test_run_llm.py` — delete `TestBuildRunArgv` (migrated to new suite); `run`/dry-run tests stay.
- Modify `tests/test_create.py` — delete `TestBuildDockerArgv` (migrated); `TestBuildMcpConfig` import path updated.
- Modify `tests/test_create_command.py` — add forge `--dry-run` tests.

**Deviation from spec (justified):** the spec sketched `build_host_served_argv(mount=(host, container, mode))`. In this codebase the container path always equals the host path (skill/staging mounted at their own absolute host path), so the builder takes `work_dir: Path` + `mode: str` instead of a 3-tuple (YAGNI — no separate container path is ever used).

---

## Task 1: New module — `build_host_served_argv` + `build_mcp_config`

**Files:**
- Create: `zipsa/host_served_container.py`
- Create: `tests/test_host_served_container.py`

- [ ] **Step 1: Write the failing builder tests**

Create `tests/test_host_served_container.py`:

```python
"""Tests for the shared host-served container core (#175)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from zipsa.host_served_container import (
    build_host_served_argv,
    build_mcp_config,
    _CONTAINER_MCP_CONFIG,
)


class TestBuildHostServedArgv:
    def test_ro_mount_and_mcp_wiring(self, tmp_path):
        wd = tmp_path / "skill"
        wd.mkdir()
        argv = build_host_served_argv(
            image="img", work_dir=wd, mode="ro",
            mcp_config_host=tmp_path / "m.json", prompt="P", env_file=None,
        )
        assert argv[:3] == ["docker", "run", "--rm"]
        assert f"{wd}:{wd}:ro" in argv
        assert f"{tmp_path / 'm.json'}:{_CONTAINER_MCP_CONFIG}:ro" in argv
        assert ["claude", "-p", "P"] == argv[argv.index("claude"):argv.index("claude") + 3]
        assert "--mcp-config" in argv and _CONTAINER_MCP_CONFIG in argv
        assert "--strict-mcp-config" in argv
        assert argv[-2:] == ["--permission-mode", "bypassPermissions"]
        assert argv[argv.index("-w") + 1] == str(wd)

    def test_rw_mount(self, tmp_path):
        wd = tmp_path / "staging"
        wd.mkdir()
        argv = build_host_served_argv(
            image="img", work_dir=wd, mode="rw",
            mcp_config_host=tmp_path / "m.json", prompt="P", env_file=None,
        )
        assert f"{wd}:{wd}:rw" in argv

    def test_env_file_added_when_given(self, tmp_path):
        wd = tmp_path / "skill"; wd.mkdir()
        ef = tmp_path / ".env"; ef.write_text("X=1\n")
        argv = build_host_served_argv(
            image="img", work_dir=wd, mode="ro",
            mcp_config_host=tmp_path / "m.json", prompt="P", env_file=ef,
        )
        assert "--env-file" in argv
        assert str(ef) in argv

    def test_extra_mounts_added_ro(self, tmp_path):
        wd = tmp_path / "skill"; wd.mkdir()
        creds = tmp_path / "creds"
        argv = build_host_served_argv(
            image="img", work_dir=wd, mode="ro",
            mcp_config_host=tmp_path / "m.json", prompt="P", env_file=None,
            extra_mounts=[(creds, "/run/creds")],
        )
        assert f"{creds}:/run/creds:ro" in argv

    @patch("zipsa.host_served_container.platform.system", return_value="Linux")
    def test_linux_adds_host_gateway(self, _mock_sys, tmp_path):
        wd = tmp_path / "skill"; wd.mkdir()
        argv = build_host_served_argv(
            image="img", work_dir=wd, mode="ro",
            mcp_config_host=tmp_path / "m.json", prompt="P", env_file=None,
        )
        assert "--add-host" in argv
        assert "host.docker.internal:host-gateway" in argv


class TestBuildMcpConfig:
    def test_embeds_port_and_token(self):
        cfg = build_mcp_config(51111, "tok")
        srv = cfg["mcpServers"]["zipsa"]
        assert "51111" in srv["url"]
        assert "tok" in srv["headersHelper"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd launcher && uv run pytest tests/test_host_served_container.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'zipsa.host_served_container'`.

- [ ] **Step 3: Create the module**

Create `zipsa/host_served_container.py`:

```python
"""Run a host-served claude container: a containerized `claude -p` whose
conversation (ask/confirm/choose) and tools (exec/run/promote) are served
by a host MCP server over host.docker.internal.

Design decisions:
- One argv builder + one orchestration core for BOTH forge and run; they
  differ only in mount mode (ro skill / rw staging), the host server, and
  how the container's output is handled (injected via factory seams).
- Cross-cutting concerns — --dry-run, server lifecycle, mcp-config write,
  and orphan-file avoidance — live here ONCE, not per caller.

Gotchas:
- The container path equals the host path (skill/staging mounted at their
  own absolute host path), so the builder needs only work_dir + mode.
- Under dry_run nothing is spawned and no port is bound: server_factory is
  never called, the work dir is never created (callers pass a placeholder),
  and the mcp-config is a single FIXED file (no per-run accumulation).
"""

from __future__ import annotations

import json
import os
import platform
import tempfile
from pathlib import Path
from typing import Callable

from . import paths as zipsa_paths

_CONTAINER_MCP_CONFIG = "/tmp/zipsa-mcp.json"
_MCP_TOOL_TIMEOUT_MS = 600_000


def build_mcp_config(port: int, token: str) -> dict:
    """The --mcp-config the container claude uses to reach the host MCP
    server. Container → host via host.docker.internal; token embedded
    directly (the file is host-private and mounted ro)."""
    return {
        "mcpServers": {
            "zipsa": {
                "type": "http",
                "url": f"http://host.docker.internal:{port}/mcp",
                "headersHelper": (
                    f'echo \'{{"Authorization": "Bearer {token}"}}\''
                ),
                "timeout": _MCP_TOOL_TIMEOUT_MS,
            }
        }
    }


def build_host_served_argv(
    *,
    image: str,
    work_dir: Path,
    mode: str,
    mcp_config_host: Path,
    prompt: str,
    env_file: Path | None,
    extra_mounts: "list[tuple[Path, str]] | None" = None,
) -> list[str]:
    """Build the headless `docker run … claude -p …` for a host-served
    session. Pure — unit-testable without docker. `mode` is "ro" (run:
    installed skill) or "rw" (forge: staging draft). `extra_mounts` are
    host paths mounted ro (run never uses them on the claude container)."""
    work_dir = work_dir.resolve()
    argv = ["docker", "run", "--rm"]
    if env_file is not None:
        argv += ["--env-file", str(env_file)]
    if platform.system() == "Linux":
        argv += ["--add-host", "host.docker.internal:host-gateway"]
    argv += [
        "-v", f"{work_dir}:{work_dir}:{mode}",
        "-v", f"{mcp_config_host}:{_CONTAINER_MCP_CONFIG}:ro",
    ]
    for host, container in extra_mounts or []:
        argv += ["-v", f"{host}:{container}:ro"]
    argv += [
        "-w", str(work_dir),
        image,
        "claude", "-p", prompt,
        "--mcp-config", _CONTAINER_MCP_CONFIG,
        "--strict-mcp-config",
        "--permission-mode", "bypassPermissions",
    ]
    return argv
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd launcher && uv run pytest tests/test_host_served_container.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/neochoon/WestbrookAI/zipsa
git add launcher/zipsa/host_served_container.py launcher/tests/test_host_served_container.py
git commit -m "feat(core): host_served_container — unified argv builder + mcp-config (#175)"
```

---

## Task 2: The orchestration core — `run_host_served_container`

**Files:**
- Modify: `zipsa/host_served_container.py`
- Modify: `tests/test_host_served_container.py`

- [ ] **Step 1: Write the failing core tests**

Append to `tests/test_host_served_container.py`:

```python
from unittest.mock import MagicMock


class TestRunHostServedContainer:
    def _common(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        ef = tmp_path / ".env"  # absent → env_file None branch
        return ef

    def test_dry_run_spawns_nothing_and_no_server(self, tmp_path, monkeypatch, capsys):
        from zipsa.host_served_container import run_host_served_container
        ef = self._common(tmp_path, monkeypatch)
        server_factory = MagicMock()
        execute = MagicMock()

        rc = run_host_served_container(
            image="img", env_file=ef,
            work_dir_factory=lambda dry: tmp_path / "wd",
            mode="rw", extra_mounts=None,
            server_factory=server_factory,
            prompt_factory=lambda wd: "PROMPT",
            execute=execute,
            mcp_subdir="staging",
            dry_run=True,
        )

        assert rc == 0
        server_factory.assert_not_called()
        execute.assert_not_called()
        out = capsys.readouterr().out
        assert "docker run" in out
        assert "PROMPT" in out
        assert ".mcp.json" in out
        # un-created work dir; single fixed config
        assert not (tmp_path / "wd").exists()
        cfgs = list((tmp_path / "home" / "staging").glob("*.mcp.json"))
        assert [c.name for c in cfgs] == ["dry-run.mcp.json"]

    def test_dry_run_config_is_single_fixed_file(self, tmp_path, monkeypatch):
        from zipsa.host_served_container import run_host_served_container
        ef = self._common(tmp_path, monkeypatch)
        for _ in range(2):
            run_host_served_container(
                image="img", env_file=ef,
                work_dir_factory=lambda dry: tmp_path / "wd",
                mode="ro", extra_mounts=None,
                server_factory=MagicMock(), prompt_factory=lambda wd: "P",
                execute=MagicMock(), mcp_subdir="run", dry_run=True,
            )
        cfgs = list((tmp_path / "home" / "run").glob("*.mcp.json"))
        assert len(cfgs) == 1

    def test_real_path_starts_stops_server_and_runs_execute(self, tmp_path, monkeypatch):
        from zipsa.host_served_container import run_host_served_container
        ef = self._common(tmp_path, monkeypatch)
        srv = MagicMock(); srv.port = 51120; srv.token = "tok"
        captured = {}
        def execute(argv):
            captured["argv"] = argv
            return 7

        rc = run_host_served_container(
            image="img", env_file=ef,
            work_dir_factory=lambda dry: tmp_path / "wd",
            mode="ro", extra_mounts=None,
            server_factory=lambda wd: srv,
            prompt_factory=lambda wd: "P",
            execute=execute, mcp_subdir="run", dry_run=False,
        )

        assert rc == 7
        srv.start.assert_called_once()
        srv.stop.assert_called_once()
        assert captured["argv"][:2] == ["docker", "run"]

    def test_real_path_stops_server_even_when_execute_raises(self, tmp_path, monkeypatch):
        from zipsa.host_served_container import run_host_served_container
        ef = self._common(tmp_path, monkeypatch)
        srv = MagicMock(); srv.port = 1; srv.token = "t"
        def boom(argv):
            raise RuntimeError("x")

        import pytest
        with pytest.raises(RuntimeError):
            run_host_served_container(
                image="img", env_file=ef,
                work_dir_factory=lambda dry: tmp_path / "wd",
                mode="ro", extra_mounts=None,
                server_factory=lambda wd: srv,
                prompt_factory=lambda wd: "P",
                execute=boom, mcp_subdir="run", dry_run=False,
            )
        srv.stop.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd launcher && uv run pytest tests/test_host_served_container.py::TestRunHostServedContainer -q`
Expected: FAIL — `ImportError: cannot import name 'run_host_served_container'`.

- [ ] **Step 3: Implement the core + helpers**

Append to `zipsa/host_served_container.py`:

```python
def _config_dir(subdir: str) -> Path:
    d = zipsa_paths.zipsa_home() / subdir
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_dry_run_config(subdir: str) -> Path:
    """A single FIXED dry-run.mcp.json (overwritten each run) with
    placeholder port/token — dry runs never accumulate config files."""
    cfg = _config_dir(subdir) / "dry-run.mcp.json"
    cfg.write_text(json.dumps(build_mcp_config(0, "<token>")))
    return cfg


def _write_run_config(subdir: str, port: int, token: str) -> Path:
    """Real-path config: a unique temp file (preserves run's current
    behavior; real-path accumulation is pre-existing and out of scope)."""
    fd, path = tempfile.mkstemp(prefix="run-", suffix=".mcp.json", dir=_config_dir(subdir))
    os.close(fd)
    cfg = Path(path)
    cfg.write_text(json.dumps(build_mcp_config(port, token)))
    return cfg


def _print_dry_run(argv: list[str], mcp_config_host: Path) -> None:
    """Mirror #173's dry-run shape: the full command on one line, then one
    arg per line (scannable), plus the mcp-config path."""
    print("=== DRY RUN (host-served container) ===")
    print(f"MCP config: {mcp_config_host}")
    print()
    print(" ".join(str(a) for a in argv))
    for i, arg in enumerate(argv):
        print(f"  [{i:2d}] {arg}")


def run_host_served_container(
    *,
    image: str,
    env_file: Path | None,
    work_dir_factory: "Callable[[bool], Path]",
    mode: str,
    extra_mounts: "list[tuple[Path, str]] | None",
    server_factory: "Callable[[Path], object]",
    prompt_factory: "Callable[[Path], str]",
    execute: "Callable[[list[str]], int]",
    mcp_subdir: str,
    dry_run: bool = False,
) -> int:
    """Run (or, under dry_run, describe) a host-served claude container.

    Seams: `work_dir_factory(dry_run)` resolves the mount dir (creating it
    only on the real path); `server_factory(work_dir)` builds the host MCP
    server (called ONLY on the real path); `prompt_factory(work_dir)` the
    prompt; `execute(argv)` runs the container and returns its exit code.
    `mcp_subdir` is the ~/.zipsa subdir for the config file.
    """
    work_dir = work_dir_factory(dry_run)
    prompt = prompt_factory(work_dir)
    ef = env_file if (env_file is not None and env_file.exists()) else None

    if dry_run:
        cfg = _write_dry_run_config(mcp_subdir)
        argv = build_host_served_argv(
            image=image, work_dir=work_dir, mode=mode,
            mcp_config_host=cfg, prompt=prompt, env_file=ef,
            extra_mounts=extra_mounts,
        )
        _print_dry_run(argv, cfg)
        return 0

    server = server_factory(work_dir)
    server.start()
    try:
        cfg = _write_run_config(mcp_subdir, server.port, server.token)
        argv = build_host_served_argv(
            image=image, work_dir=work_dir, mode=mode,
            mcp_config_host=cfg, prompt=prompt, env_file=ef,
            extra_mounts=extra_mounts,
        )
        return execute(argv)
    finally:
        server.stop()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd launcher && uv run pytest tests/test_host_served_container.py -q`
Expected: PASS (10 tests total).

- [ ] **Step 5: Commit**

```bash
cd /Users/neochoon/WestbrookAI/zipsa
git add launcher/zipsa/host_served_container.py launcher/tests/test_host_served_container.py
git commit -m "feat(core): run_host_served_container — dry-run/lifecycle/mcp-config in one place (#175)"
```

---

## Task 3: Route `run_skill_llm` through the core (parity refactor)

**Files:**
- Modify: `zipsa/run_llm.py:179-298` (the `run_skill_llm` body), imports near `:38-43`
- Test: `tests/test_run_llm.py` (existing `TestRunSkillLlm*` + dry-run suites must stay green)

- [ ] **Step 1: Confirm the current run tests are green (baseline)**

Run: `cd launcher && uv run pytest tests/test_run_llm.py -q`
Expected: PASS. This is the regression baseline — behavior must not change.

- [ ] **Step 2: Update imports**

In `zipsa/run_llm.py`, change the create import (around line 38) from:

```python
from .create import _is_interactive, build_mcp_config
```

to:

```python
from .create import _is_interactive
from .host_served_container import build_mcp_config, run_host_served_container
```

(Leave `build_mcp_config` referenced where used; it now resolves from the new module.)

- [ ] **Step 3: Replace the `run_skill_llm` body with the core call**

Replace the entire body of `run_skill_llm` (everything after the docstring, lines ~203-298) with:

```python
    if stdout is None:
        stdout = sys.stdout
    if stderr is None:
        stderr = sys.stderr
    if env_file is None:
        env_file = zipsa_paths.global_env_file()
    skill_root = skill_root.resolve()

    def _server(work_dir: Path):
        hitl_io = HitlIO(
            stdin=sys.stdin, stdout=sys.stdout,
            stdout_lock=threading.Lock(), is_interactive=_is_interactive(sys.stdin),
        )
        # extra_mounts (skill creds) go to the SCRIPT's exec sub-container via
        # RunScriptHandler.default_mounts — NOT the claude container.
        handler = RunScriptHandler(
            docker_image=image, skill_root=work_dir, default_mounts=extra_mounts,
        )
        return RunServer(hitl_io, handler)

    def _execute(argv: list[str]) -> int:
        run_dir = exec_runner.new_run_dir(skill_root.name)
        try:
            (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        started = time.monotonic()
        # stdin DEVNULL — HITL goes over MCP, not claude's stdin. stdout/stderr
        # PIPEd so we can tee them to the terminal AND the run record.
        proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        out_chunks: list[bytes] = []
        err_chunks: list[bytes] = []
        t_out = threading.Thread(
            target=_tee_stream, args=(proc.stdout, stdout, out_chunks), daemon=True,
        )
        t_err = threading.Thread(
            target=_tee_stream, args=(proc.stderr, stderr, err_chunks), daemon=True,
        )
        t_out.start()
        t_err.start()
        exit_code = proc.wait()
        t_out.join()
        t_err.join()
        duration_ms = int((time.monotonic() - started) * 1000)
        _write_run_record(
            run_dir,
            skill_name=skill_root.name,
            exit_code=exit_code,
            duration_ms=duration_ms,
            user_input=user_input,
            stdout_bytes=b"".join(out_chunks),
            stderr_bytes=b"".join(err_chunks),
        )
        return exit_code

    return run_host_served_container(
        image=image,
        env_file=env_file,
        work_dir_factory=lambda _dry: skill_root,
        mode="ro",
        extra_mounts=None,  # claude container; creds reach the script handler
        server_factory=_server,
        prompt_factory=lambda wd: build_run_prompt(wd, user_input),
        execute=_execute,
        mcp_subdir="run",
        dry_run=dry_run,
    )
```

Update the `run_skill_llm` docstring's last paragraph to drop the placeholder-config wording (the dry-run is now handled by the core). Keep the rest.

- [ ] **Step 4: Run the full run_llm suite to verify parity**

Run: `cd launcher && uv run pytest tests/test_run_llm.py -q`
Expected: PASS. The dry-run tests (`TestRunSkillLlmDryRun`) still pass because the core reproduces the same contract; `subprocess.Popen` / `RunServer` patch targets in `zipsa.run_llm` remain valid (both are still referenced inside the closures).

If `TestBuildRunArgv` fails to import `build_run_argv` — it does not yet; that class is deleted in Task 6. Leave it for now (it still passes against the not-yet-removed function).

- [ ] **Step 5: Commit**

```bash
cd /Users/neochoon/WestbrookAI/zipsa
git add launcher/zipsa/run_llm.py
git commit -m "refactor(run): route run_skill_llm through host_served_container (#175)"
```

---

## Task 4: Route `run_forge` through the core + add `dry_run`

**Files:**
- Modify: `zipsa/create.py:201-273` (`run_forge`), imports
- Test: `tests/test_create.py` (`TestRunCreate` stays green; add dry-run tests)

- [ ] **Step 1: Write the failing forge dry-run tests**

Add to `tests/test_create.py` (a new class):

```python
class TestRunForgeDryRun:
    """`run_forge(dry_run=True)` prints the would-run command + mcp-config
    path and returns 0 WITHOUT starting a ForgeServer (no bound port),
    spawning the container, or leaving an orphan staging dir / config (#175)."""

    @patch("zipsa.create.subprocess.run")
    @patch("zipsa.create.ForgeServer")
    def test_dry_run_spawns_nothing(self, mock_forge_cls, mock_run, tmp_path, monkeypatch):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        srv = MagicMock(); srv.port = 5; srv.token = "t"
        mock_forge_cls.return_value = srv

        from zipsa.create import run_forge
        rc = run_forge("make a thing", skills_dir=tmp_path / "skills", image="img", dry_run=True)

        assert rc == 0
        mock_run.assert_not_called()
        srv.start.assert_not_called()

    @patch("zipsa.create.subprocess.run")
    @patch("zipsa.create.ForgeServer")
    def test_dry_run_leaves_no_orphan_staging_or_config(
        self, mock_forge_cls, mock_run, tmp_path, monkeypatch
    ):
        home = tmp_path / "home"
        monkeypatch.setenv("ZIPSA_HOME", str(home))
        srv = MagicMock(); srv.port = 5; srv.token = "t"
        mock_forge_cls.return_value = srv

        from zipsa.create import run_forge
        run_forge("x", skills_dir=tmp_path / "skills", image="img", dry_run=True)
        run_forge("x", skills_dir=tmp_path / "skills", image="img", dry_run=True)

        staging = home / "staging"
        drafts = list(staging.glob("draft-*")) if staging.exists() else []
        # the placeholder dir is never created on disk
        assert all(not d.is_dir() for d in drafts)
        cfgs = list(staging.glob("*.mcp.json")) if staging.exists() else []
        assert len(cfgs) <= 1
```

Ensure `MagicMock` and `patch` are imported at the top of `tests/test_create.py` (add `from unittest.mock import MagicMock, patch` if not already present).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd launcher && uv run pytest tests/test_create.py::TestRunForgeDryRun -q`
Expected: FAIL — `TypeError: run_forge() got an unexpected keyword argument 'dry_run'`.

- [ ] **Step 3: Update imports + replace `run_forge` body**

In `zipsa/create.py`, add near the other imports (top-level is fine; no cycle — `host_served_container` imports nothing from `create`):

```python
from .host_served_container import build_mcp_config, run_host_served_container
```

Remove the local `def build_mcp_config(...)` (now imported/re-exported) — keep the name importable from `create` for back-compat via the import above.

Replace `run_forge` (signature + body) with:

```python
def run_forge(
    intent: str,
    *,
    skills_dir: Path,
    image: str,
    env_file: Path | None = None,
    dry_run: bool = False,
) -> int:
    """Forge a skill via a host-served headless container session. Returns
    the container claude's exit code.

    With `dry_run=True` the would-run command + mcp-config path are printed
    and 0 is returned WITHOUT starting the ForgeServer (no bound port),
    spawning the container, or creating a staging dir.
    """
    import sys
    import threading

    from . import paths as zipsa_paths
    from .core.hitl_mcp import HitlIO
    from .core.run_draft_handler import RunDraftHandler

    if env_file is None:
        env_file = zipsa_paths.global_env_file()

    staging_root = zipsa_paths.zipsa_home() / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)

    def _work_dir(dry: bool) -> Path:
        if dry:
            # Placeholder — NOT created on disk; the printed mount is plausible
            # but a dry run leaves no orphan staging dir.
            return staging_root / "draft-DRYRUN"
        return Path(tempfile.mkdtemp(prefix="draft-", dir=staging_root))

    def _server(work_dir: Path):
        hitl_io = HitlIO(
            stdin=sys.stdin, stdout=sys.stdout,
            stdout_lock=threading.Lock(), is_interactive=_is_interactive(sys.stdin),
        )
        return ForgeServer(
            hitl_io,
            exec_handler=RunScriptHandler(docker_image=image, skill_root=work_dir),
            run_handler=RunDraftHandler(image=image, skill_root=work_dir),
            promote_handler=PromoteSkillHandler(dest_root=skills_dir),
            staging_path=str(work_dir),
        )

    def _execute(argv: list[str]) -> int:
        # stdin DEVNULL: the host terminal's stdin belongs to the HITL reader,
        # not the container. stdout/stderr inherit so progress shows.
        return subprocess.run(argv, stdin=subprocess.DEVNULL).returncode

    return run_host_served_container(
        image=image,
        env_file=env_file,
        work_dir_factory=_work_dir,
        mode="rw",
        extra_mounts=None,
        server_factory=_server,
        prompt_factory=lambda wd: build_forge_prompt(intent, wd),
        execute=_execute,
        mcp_subdir="staging",
        dry_run=dry_run,
    )
```

- [ ] **Step 4: Run forge tests to verify pass + no regression**

Run: `cd launcher && uv run pytest tests/test_create.py -q`
Expected: PASS — new `TestRunForgeDryRun` passes; existing `TestRunCreate` (patches `zipsa.create.subprocess.run` + `ForgeServer`) still passes (both names still referenced in `create`). `TestBuildDockerArgv` still passes against the not-yet-removed `build_docker_argv` (removed in Task 6).

- [ ] **Step 5: Commit**

```bash
cd /Users/neochoon/WestbrookAI/zipsa
git add launcher/zipsa/create.py launcher/tests/test_create.py
git commit -m "feat(forge): route run_forge through core + add dry_run (#175)"
```

---

## Task 5: CLI — `forge --dry-run`

**Files:**
- Modify: `zipsa/cli.py:742-794` (the `forge` command)
- Test: `tests/test_create_command.py` (`TestForgeCommand`)

- [ ] **Step 1: Write the failing CLI test**

Add to `tests/test_create_command.py` inside `TestForgeCommand` (or a new method):

```python
    @patch("zipsa.create.subprocess.run")
    @patch("zipsa.create.ForgeServer")
    def test_forge_dry_run_prints_and_runs_nothing(
        self, mock_forge_cls, mock_run, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("ZIPSA_HOME", str(tmp_path / "home"))
        srv = MagicMock(); srv.port = 5; srv.token = "t"
        mock_forge_cls.return_value = srv

        result = runner.invoke(app, ["forge", "make a thing", "--dry-run"])

        assert result.exit_code == 0, result.output
        mock_run.assert_not_called()
        srv.start.assert_not_called()
        assert "docker run" in result.output
```

Ensure `MagicMock`, `patch`, and `runner = CliRunner()` are available in the test module (match the existing imports in `tests/test_create_command.py`).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd launcher && uv run pytest tests/test_create_command.py::TestForgeCommand::test_forge_dry_run_prints_and_runs_nothing -q`
Expected: FAIL — `No such option: --dry-run`.

- [ ] **Step 3: Add the `--dry-run` option and thread it**

In `zipsa/cli.py` `forge_skill`, add a parameter after `skills_dir` (before the closing `):`):

```python
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the would-run command and exit without forging"),
    ] = False,
```

And in the `run_forge(...)` call (around line 784) pass it through:

```python
        rc = run_forge(
            intent, skills_dir=dest, image=image, dry_run=dry_run,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd launcher && uv run pytest tests/test_create_command.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/neochoon/WestbrookAI/zipsa
git add launcher/zipsa/cli.py launcher/tests/test_create_command.py
git commit -m "feat(cli): forge --dry-run (#175)"
```

---

## Task 6: Remove the old argv builders + migrate/delete their tests

**Files:**
- Modify: `zipsa/run_llm.py` (delete `build_run_argv`, `_CONTAINER_MCP_CONFIG`)
- Modify: `zipsa/create.py` (delete `build_docker_argv`, `_CONTAINER_MCP_CONFIG`)
- Modify: `tests/test_run_llm.py` (delete `TestBuildRunArgv`)
- Modify: `tests/test_create.py` (delete `TestBuildDockerArgv`; fix `build_mcp_config` import)

- [ ] **Step 1: Delete `build_run_argv` and run_llm's container constant**

In `zipsa/run_llm.py`, delete the entire `def build_run_argv(...)` function (lines ~71-98) and the `_CONTAINER_MCP_CONFIG = "/tmp/zipsa-run-mcp.json"` line (~43). Then confirm nothing else in `run_llm.py` references either (Task 3 removed the call site):

Run: `cd launcher && grep -n "build_run_argv\|_CONTAINER_MCP_CONFIG" zipsa/run_llm.py`
Expected: no output.

- [ ] **Step 2: Delete `build_docker_argv` and create's container constant**

In `zipsa/create.py`, delete `def build_docker_argv(...)` (lines ~161-198) and the `_CONTAINER_MCP_CONFIG = "/tmp/zipsa-create-mcp.json"` line (~70). Confirm:

Run: `cd launcher && grep -n "build_docker_argv\|_CONTAINER_MCP_CONFIG" zipsa/create.py`
Expected: no output.

- [ ] **Step 3: Delete the migrated test classes**

- In `tests/test_run_llm.py`: delete the whole `class TestBuildRunArgv:` (lines ~61-95) — its coverage now lives in `TestBuildHostServedArgv`. Remove the now-unused `build_run_argv` import if present.
- In `tests/test_create.py`: delete the whole `class TestBuildDockerArgv:` (lines ~76-121). In `TestBuildMcpConfig` and the import block (line ~19), import `build_mcp_config` from `zipsa.create` still works (re-exported) — leave it, or repoint to `zipsa.host_served_container`. Remove the `build_docker_argv` name from the import list at line ~19.

- [ ] **Step 4: Run the full suite**

Run: `cd launcher && uv run pytest -q`
Expected: PASS — full suite green. Confirm no import errors from the deletions.

- [ ] **Step 5: Commit**

```bash
cd /Users/neochoon/WestbrookAI/zipsa
git add launcher/zipsa/run_llm.py launcher/zipsa/create.py launcher/tests/test_run_llm.py launcher/tests/test_create.py
git commit -m "refactor: remove build_run_argv/build_docker_argv, migrate tests (#175)"
```

---

## Final verification

- [ ] **Full suite green**

Run: `cd launcher && uv run pytest -q`
Expected: all pass (the #173 baseline count + the new builder/core/forge-dry-run tests; minus the two deleted builder suites).

- [ ] **Manual smoke (optional, needs Docker)**

```bash
cd launcher && uvr --project=launcher zipsa forge "a tiny hello skill" --dry-run
```
Expected: prints `docker run … claude -p …` + an mcp-config path, exits 0, and `~/.zipsa/staging/` has no new `draft-*` dir and at most one `*.mcp.json`.

- [ ] **No stray references**

Run: `cd launcher && grep -rn "build_run_argv\|build_docker_argv" zipsa/ tests/ | grep -v "_build_docker_argv"`
Expected: no output (the `_build_docker_argv` in `exec_runner.py` is a different, in-scope-excluded function).
