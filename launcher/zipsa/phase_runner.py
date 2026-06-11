"""Container-side entrypoint that runs one Python phase.

The launcher spawns a container per Python phase and invokes:

    python -m zipsa.phase_runner <phase-file>

This module imports the phase file, builds `ctx` (from env vars) and
`prev` (from the previous phase's `state.json`), calls `run(ctx, prev)`,
and writes the returned dict to `state.json` for the next phase.

Phase authors interact with the host (HitlServer, MCP tools) through
the higher-level `zipsa.hitl` / `zipsa.llm` modules, not through this
runner directly.

Exit codes:
    0  — phase ran and state.json written
    1  — phase raised, returned non-dict, or had no `run` function
    2  — invocation problem (bad args, missing env)

See `docs/zipsa-runtime-spec-2026-06-11.md` §2.1, §2.4.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path

# Env vars the launcher fills in when spawning the container. Their
# absence is OK on most fields (defaults to "" / 0); only ENV_STATE_PATH
# is required.
ENV_SKILL_NAME = "ZIPSA_SKILL_NAME"
ENV_SKILL_VERSION = "ZIPSA_SKILL_VERSION"
ENV_USER_QUERY = "ZIPSA_USER_QUERY"
ENV_RUN_ID = "ZIPSA_RUN_ID"
ENV_RUN_DIR = "ZIPSA_RUN_DIR"
ENV_RUN_DEPTH = "ZIPSA_RUN_DEPTH"
ENV_PREV_STATE_PATH = "ZIPSA_PREV_STATE_PATH"
ENV_STATE_PATH = "ZIPSA_STATE_PATH"


def _build_ctx() -> dict:
    return {
        "skill_name": os.environ.get(ENV_SKILL_NAME, ""),
        "version": os.environ.get(ENV_SKILL_VERSION, ""),
        "user_query": os.environ.get(ENV_USER_QUERY, ""),
        "run_id": os.environ.get(ENV_RUN_ID, ""),
        "run_dir": os.environ.get(ENV_RUN_DIR, ""),
        "depth": int(os.environ.get(ENV_RUN_DEPTH, "0") or "0"),
        "env": {k: v for k, v in os.environ.items() if k.startswith("ZIPSA_")},
    }


def _load_prev() -> dict:
    path = os.environ.get(ENV_PREV_STATE_PATH)
    if not path:
        return {}
    p = Path(path)
    if not p.is_file():
        return {}
    return json.loads(p.read_text())


def _load_phase(path: Path):
    spec = importlib.util.spec_from_file_location("phase_under_run", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load phase: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_state(state: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, default=str))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:]) if argv is None else argv

    if len(argv) != 1:
        print(
            "usage: python -m zipsa.phase_runner <phase-file>",
            file=sys.stderr,
        )
        return 2

    phase_path = Path(argv[0])
    if not phase_path.is_file():
        print(f"phase file not found: {phase_path}", file=sys.stderr)
        return 2

    state_path_str = os.environ.get(ENV_STATE_PATH)
    if not state_path_str:
        print(f"missing {ENV_STATE_PATH} env var", file=sys.stderr)
        return 2
    state_path = Path(state_path_str)

    try:
        module = _load_phase(phase_path)
    except Exception:
        traceback.print_exc()
        return 1

    if not hasattr(module, "run") or not callable(module.run):
        print(
            f"{phase_path}: phase module must define `run(ctx, prev)`",
            file=sys.stderr,
        )
        return 1

    ctx = _build_ctx()
    prev = _load_prev()

    try:
        result = module.run(ctx, prev)
    except Exception:
        traceback.print_exc()
        return 1

    if not isinstance(result, dict):
        print(
            f"{phase_path}: run() must return a dict, got "
            f"{type(result).__name__}",
            file=sys.stderr,
        )
        return 1

    _write_state(result, state_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
