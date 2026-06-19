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
