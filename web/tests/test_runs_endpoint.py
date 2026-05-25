"""Contract tests for /api/runs — spawn + stream.

Streaming end-to-end is verified live. Here we pin the endpoint shapes
+ subprocess wiring using a trivial command (python -c print()) instead
of `zipsa run`, swapped in via the `_build_run_command` hook.
"""

import asyncio
import json
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _swap_runner_command(monkeypatch):
    """Replace `zipsa run X` with a deterministic python one-liner so
    tests don't need Docker / network / a real skill. Keeps both stdout
    and stderr output paths exercised."""
    def fake_cmd(skill: str, args):
        # Echo skill name to stdout and a marker to stderr, then exit.
        script = (
            f'import sys; print("hello {skill}"); '
            f'print("info {skill}", file=sys.stderr); '
            f'sys.exit(0)'
        )
        return [sys.executable, "-c", script]
    monkeypatch.setattr("api.runs._build_run_command", fake_cmd)


@pytest.fixture
def client():
    from app import app
    # Clear the in-memory registry between tests so run_ids don't leak.
    from api import runs
    runs._runs.clear()
    return TestClient(app)


def test_start_run_returns_run_id(client):
    resp = client.post("/api/runs", json={"skill": "hello-world"})
    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
    assert len(data["run_id"]) > 0


def test_start_run_records_in_registry(client):
    resp = client.post("/api/runs", json={"skill": "weather"})
    run_id = resp.json()["run_id"]
    info = client.get(f"/api/runs/{run_id}").json()
    assert info["skill"] == "weather"
    assert info["run_id"] == run_id


def test_get_run_returns_404_for_unknown(client):
    resp = client.get("/api/runs/does-not-exist")
    assert resp.status_code == 404


def test_stream_run_returns_404_for_unknown(client):
    resp = client.get("/api/runs/does-not-exist/stream")
    assert resp.status_code == 404


# End-to-end SSE delivery is covered by live verification (`cd web &&
# uv run uvicorn app:app`, click Run on a skill in the browser).
# httpx TestClient + sse-starlette doesn't reliably deliver events
# synchronously, so the streaming test would hang.


def test_event_stream_generator_yields_line_and_exit():
    """Bypass HTTP and exercise the generator directly so we still
    have automated coverage of the event-shape contract without
    hitting the SSE/TestClient blocker.

    Both subprocess creation and stream consumption MUST run in the
    same asyncio loop — otherwise the pipe readers are bound to a
    dead loop and the test hangs forever."""
    import asyncio
    from api import runs as runs_mod

    async def run_full() -> list[tuple[str, dict]]:
        # Clean the per-process registry inside this loop so the
        # subprocess proc lives in the same loop as the consumer.
        runs_mod._runs.clear()
        resp = await runs_mod.start_run(runs_mod.RunStartRequest(skill="demo"))
        out: list[tuple[str, dict]] = []
        async for evt in runs_mod._event_stream(resp.run_id):
            payload = json.loads(evt["data"])
            out.append((evt["event"], payload))
        return out

    events = asyncio.run(run_full())
    line_events = [p for e, p in events if e == "line"]
    assert any(p["stream"] == "stdout" and "hello demo" in p["text"]
               for p in line_events)
    assert any(p["stream"] == "stderr" and "info demo" in p["text"]
               for p in line_events)
    exit_evt = next(p for e, p in events if e == "exit")
    assert exit_evt["exit_code"] == 0


def test_multiple_runs_get_unique_ids(client):
    a = client.post("/api/runs", json={"skill": "x"}).json()["run_id"]
    b = client.post("/api/runs", json={"skill": "x"}).json()["run_id"]
    assert a != b


def test_stdin_endpoint_returns_404_for_unknown(client):
    resp = client.post(
        "/api/runs/does-not-exist/stdin",
        json={"text": "hello"},
    )
    assert resp.status_code == 404


def test_stdin_endpoint_feeds_subprocess(monkeypatch):
    """Spawn a subprocess that reads ONE line from stdin and echoes
    it back, then verify POST /stdin actually delivers."""
    import asyncio
    import sys
    from api import runs as runs_mod

    def fake_cmd(skill, args):
        # Read one line, echo it, exit.
        script = 'import sys; line = sys.stdin.readline().strip(); print(f"got:{line}")'
        return [sys.executable, "-c", script]
    monkeypatch.setattr("api.runs._build_run_command", fake_cmd)

    async def run_full() -> list[tuple[str, dict]]:
        runs_mod._runs.clear()
        resp = await runs_mod.start_run(runs_mod.RunStartRequest(skill="x"))
        rid = resp.run_id
        # Feed stdin
        await runs_mod.write_stdin(rid, runs_mod.StdinRequest(text="Sydney"))
        out: list[tuple[str, dict]] = []
        async for evt in runs_mod._event_stream(rid):
            payload = json.loads(evt["data"])
            out.append((evt["event"], payload))
        return out

    events = asyncio.run(run_full())
    line_events = [p for e, p in events if e == "line"]
    assert any("got:Sydney" in p["text"] for p in line_events)
    exit_evt = next(p for e, p in events if e == "exit")
    assert exit_evt["exit_code"] == 0
