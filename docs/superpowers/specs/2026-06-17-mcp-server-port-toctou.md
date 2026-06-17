# MCP host servers: fix free-port TOCTOU (flaky tests) (#152)

## Problem

The host MCP servers (`CreateServer`, `RunServer`, `ForgeServer`, and the
legacy `hitl_runner` server) all start the same way:

```python
self.port = _pick_free_port()          # bind(127.0.0.1,0) → port → close()
...
config = uvicorn.Config(app, host="0.0.0.0", port=self.port, ...)
self._uvicorn_server = uvicorn.Server(config)
threading.Thread(target=self._uvicorn_server.run, ...).start()
# then a readiness loop: connect(127.0.0.1, self.port) until it answers
```

`_pick_free_port()` (`core/hitl_runner.py`) binds an ephemeral socket,
reads the assigned port, **closes** it, and returns the integer. uvicorn
then **re-binds** that port later, from another thread. Between the close
and the re-bind there is a **TOCTOU window**: a concurrent server start
(many server tests run in one suite) can grab the same just-freed port,
so one server fails to bind. The readiness loop then times out and
`start()` raises `RuntimeError`, surfacing as intermittent test failures:

- `test_create_server.py::TestTools::test_exec_tool_delegates_to_handler`
- `test_run_server.py::TestRunServer::test_no_promote_tool_registered`
- (same family as the older
  `tests/auth/test_browser.py::...::test_state_mismatch_raises_oauth_callback_error`,
  hardcoded port 54394 — see launcher/BACKLOG.md)

These pass in isolation, fail under full-suite contention, and re-run
green — eroding trust in the main-branch CI signal.

The readiness loop is NOT the bug (it correctly waits for listen); the
bug is the gap between picking and binding the port.

## Decision — hand uvicorn a pre-bound socket (close the window)

Stop closing the socket. Bind it once, keep it open, and pass it to
uvicorn via `Server.run(sockets=[sock])`, which uses the provided socket
and does not re-bind. There is then no interval in which the port is
unowned.

**Bind host must stay `0.0.0.0`.** The container reaches these servers
via `host.docker.internal` (the host's real IP), not loopback. Binding
the pre-created socket to `127.0.0.1` would make the servers unreachable
from the container and break real forge/run MCP calls. So the pre-bound
socket binds `("0.0.0.0", 0)` — same exposure as today (DNS-rebind
protection + allowed-hosts middleware already guard it). The readiness
probe to `127.0.0.1:port` still works (loopback is part of `0.0.0.0`).

### Rejected alternatives

- **Retry start() on bind failure** (pick a new port, try again N times):
  masks the race, adds latency, still races. Worse.
- **Switch tests to mock the transport**: hides a real production race
  (forge/run servers could hit it in the wild); doesn't fix the cause.
- **`SO_REUSEADDR` alone**: doesn't prevent two servers from both
  succeeding to bind the same port then fighting — wrong tool here.

## Implementation

### `core/hitl_runner.py`

Add a helper that returns a **bound, listening** socket (and keep its
port via `getsockname`):

```python
def _bind_free_socket(host: str = "0.0.0.0") -> socket.socket:
    """Bind an ephemeral port and return the still-open listening socket.

    Hand this socket to uvicorn (`Server.run(sockets=[sock])`) so the
    port is never released between pick and bind — closes the TOCTOU
    window that made concurrent server starts flaky. Binds 0.0.0.0 on
    purpose: the container reaches the host via host.docker.internal,
    not loopback.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, 0))
    s.listen()
    return s
```

Remove `_pick_free_port` once no caller remains (or keep as a thin
wrapper only if something outside these servers still needs an int — grep
first; if unused, delete it).

### Each server (`create_server`, `run_server`, `forge_server`,
`hitl_runner`'s server class)

In `start()`:

```python
self._socket = _bind_free_socket()
self.port = self._socket.getsockname()[1]
config = uvicorn.Config(app, host="0.0.0.0", port=self.port, ...)  # port kept for logs/consistency
self._uvicorn_server = uvicorn.Server(config)
self._thread = threading.Thread(
    target=lambda: self._uvicorn_server.run(sockets=[self._socket]),
    daemon=True, name=f"<srv>-mcp-{self.port}",
)
self._thread.start()
# readiness loop unchanged
```

In `stop()`: after `should_exit = True`, also close `self._socket` if
still open (best-effort; uvicorn typically closes sockets it was given).
Store `self._socket` alongside the existing thread/server fields.

Keep the four implementations consistent. (A shared base to dedupe the
~30-line boilerplate is tempting but is separate tech debt — out of scope
here; do NOT refactor the duplication in this change, just fix the race
identically in each.)

## Tests

- `_bind_free_socket()` returns an open socket bound to a nonzero port,
  listening; two consecutive calls return different ports; the returned
  socket's port matches `getsockname`.
- `start()` of each server still sets `port > 0` and the readiness loop
  succeeds (existing tests already cover this — keep them green).
- A focused concurrency/regression test: start several servers
  (e.g. 10–20 `RunServer`/`CreateServer`) in quick succession / threads
  and assert all bind distinct ports and all become reachable (this is
  the scenario the TOCTOU broke). Keep it bounded so it doesn't slow the
  suite much.
- The two previously-flaky tests must pass; run the relevant files in a
  loop (e.g. 20×) locally to confirm no intermittent bind failure.

## Out of scope

- Deduping the 4 near-identical `start()`/server bodies into a shared
  base (separate refactor).
- The `test_browser.py` OAuth-callback flake uses a different server
  (`LocalCallbackServer`, hardcoded port 54394) — same family but its own
  fix (free-port + ready-wait there too). Note it; address only if cheap
  in the same pass, else leave its BACKLOG entry.
