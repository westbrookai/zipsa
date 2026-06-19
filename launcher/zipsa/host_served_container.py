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
from collections.abc import Callable
from pathlib import Path

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
    extra_mounts: list[tuple[Path, str]] | None = None,
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
    work_dir_factory: Callable[[bool], Path],
    mode: str,
    extra_mounts: list[tuple[Path, str]] | None,
    server_factory: Callable[[Path], object],
    prompt_factory: Callable[[Path], str],
    execute: Callable[[list[str]], int],
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
