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
import shlex
import uuid
from datetime import datetime
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse


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
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
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


async def _pump(stream: asyncio.StreamReader, kind: str, queue: asyncio.Queue) -> None:
    """Read lines from a subprocess pipe into a merge queue."""
    while True:
        raw = await stream.readline()
        if not raw:
            await queue.put(("eof", kind))
            return
        text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
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
    out_task = asyncio.create_task(_pump(proc.stdout, "stdout", queue))
    err_task = asyncio.create_task(_pump(proc.stderr, "stderr", queue))

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
