"""Run subprocess + SSE stream.

POST /api/runs spawns `zipsa run <skill> [args]` as a subprocess and
returns a run_id. GET /api/runs/{id}/stream is an SSE channel that
relays stdout + stderr lines as they arrive, plus a final `exit` event
with the subprocess return code.

Process registry is in-memory (per server process). For spike: an
orphaned subprocess (e.g. user closes tab mid-run) keeps running to
completion — we don't kill on disconnect. Multi-run is supported via
unique run_ids.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse


# The renderer prints `Run dir: <path>` near the end of every run.
# We snag the path off stdout so /summary can locate summary.json
# without re-scanning the disk.
_RUN_DIR_RE = re.compile(r"Run dir:\s*(\S+)")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


router = APIRouter(prefix="/api/runs", tags=["runs"])


# run_id -> {process, skill, args, started_at, exit_code}
_runs: dict[str, dict[str, Any]] = {}


# Override hook for tests — lets us swap `zipsa run X` for a trivial
# command (e.g. `python -c 'print("hi")'`) without monkey-patching
# asyncio internals.
def _build_run_command(skill: str, args: Optional[str]) -> list[str]:
    cmd = ["zipsa", "run", skill]
    if args:
        cmd.extend(shlex.split(args))
    return cmd


class RunStartRequest(BaseModel):
    skill: str
    args: Optional[str] = None


class RunStartResponse(BaseModel):
    run_id: str


@router.post("", response_model=RunStartResponse)
async def start_run(req: RunStartRequest) -> RunStartResponse:
    cmd = _build_run_command(req.skill, req.args)
    # ZIPSA_FORCE_INTERACTIVE tells the launcher to honor HITL prompts
    # even though stdin is a pipe (not a TTY) — otherwise ask/confirm/
    # choose immediately raise HitlUnattended and the run fails.
    env = {**os.environ, "ZIPSA_FORCE_INTERACTIVE": "1"}
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    run_id = uuid.uuid4().hex[:12]
    _runs[run_id] = {
        "process": proc,
        "skill": req.skill,
        "args": req.args,
        "started_at": datetime.now().isoformat(),
        "exit_code": None,
    }
    return RunStartResponse(run_id=run_id)


class StdinRequest(BaseModel):
    text: str


@router.post("/{run_id}/stop")
async def stop_run(run_id: str) -> dict:
    """Terminate a running subprocess.

    Sends SIGTERM, waits up to 2 seconds for graceful shutdown,
    then escalates to SIGKILL. Already-finished runs return 200
    with running=False instead of an error so the UI can be lazy.
    """
    entry = _runs.get(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    proc: asyncio.subprocess.Process = entry["process"]
    if proc.returncode is not None:
        return {"ok": True, "running": False}
    try:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    except ProcessLookupError:
        # Subprocess already gone — race with natural exit.
        pass
    return {"ok": True, "running": False}


@router.post("/{run_id}/stdin")
async def write_stdin(run_id: str, req: StdinRequest) -> dict:
    """Feed a line to the subprocess's stdin.

    The web UI calls this when the user answers a HITL prompt.
    Appends a newline so the launcher's `stdin.readline()` returns.
    """
    entry = _runs.get(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    proc: asyncio.subprocess.Process = entry["process"]
    if proc.stdin is None or proc.stdin.is_closing():
        raise HTTPException(status_code=410, detail="stdin_closed")
    proc.stdin.write((req.text + "\n").encode("utf-8"))
    await proc.stdin.drain()
    return {"ok": True}


async def _pump(stream: asyncio.StreamReader, kind: str, queue: asyncio.Queue,
                entry: dict) -> None:
    """Read lines from a subprocess pipe into a merge queue.

    Also peeks at stdout for the `Run dir: <path>` line so /summary
    can locate this run's summary.json without scanning the disk.
    """
    while True:
        raw = await stream.readline()
        if not raw:
            await queue.put(("eof", kind))
            return
        text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if kind == "stdout" and entry.get("run_dir") is None:
            m = _RUN_DIR_RE.search(_ANSI_RE.sub("", text))
            if m:
                entry["run_dir"] = m.group(1)
        await queue.put(("line", {"stream": kind, "text": text}))


async def _event_stream(run_id: str) -> AsyncIterator[dict]:
    """Async generator yielding SSE events for the run."""
    entry = _runs.get(run_id)
    if entry is None:
        yield {
            "event": "error",
            "data": json.dumps({"error": "run_not_found"}),
        }
        return
    proc: asyncio.subprocess.Process = entry["process"]

    queue: asyncio.Queue = asyncio.Queue()
    out_task = asyncio.create_task(_pump(proc.stdout, "stdout", queue, entry))
    err_task = asyncio.create_task(_pump(proc.stderr, "stderr", queue, entry))

    open_streams = 2
    while open_streams > 0:
        kind, payload = await queue.get()
        if kind == "eof":
            open_streams -= 1
        else:
            yield {"event": "line", "data": json.dumps(payload)}

    await proc.wait()
    entry["exit_code"] = proc.returncode
    # Reap the reader tasks (already done at this point).
    for t in (out_task, err_task):
        if not t.done():
            t.cancel()
    yield {
        "event": "exit",
        "data": json.dumps({"exit_code": proc.returncode}),
    }


@router.get("/{run_id}/stream")
async def stream_run(run_id: str):
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail="run_not_found")
    return EventSourceResponse(_event_stream(run_id))


@router.get("/{run_id}/summary")
def get_run_summary(run_id: str) -> dict:
    """Read the canonical summary.json the launcher wrote for this run.

    The renderer prints these fields as text, but the disk file is the
    source of truth — it has the structured `result` dict and the full
    `error` object the UI can render as a card. 410 once the subprocess
    has finished but no summary.json was produced (e.g. user stopped
    early — the launcher only writes at successful exit).
    """
    entry = _runs.get(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    run_dir = entry.get("run_dir")
    if not run_dir:
        raise HTTPException(status_code=425, detail="run_dir_not_yet_known")
    summary_path = Path(run_dir) / "summary.json"
    if not summary_path.exists():
        raise HTTPException(status_code=410, detail="summary_not_written")
    try:
        data = json.loads(summary_path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"summary_unreadable: {e}")
    return {"run_id": run_id, "run_dir": run_dir, "summary": data}


@router.get("/{run_id}")
def get_run(run_id: str) -> dict:
    """Metadata for a single run (status check without subscribing)."""
    entry = _runs.get(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    proc = entry["process"]
    return {
        "run_id": run_id,
        "skill": entry["skill"],
        "args": entry["args"],
        "started_at": entry["started_at"],
        "exit_code": entry["exit_code"] if proc.returncode is None else proc.returncode,
        "running": proc.returncode is None,
    }
