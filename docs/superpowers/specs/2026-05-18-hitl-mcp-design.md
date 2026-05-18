# Human-in-the-Loop via Launcher MCP Server — Design

**Date:** 2026-05-18
**Status:** Draft — pending user approval
**Scope:** Add interactive user prompts (ask / confirm / choose) to zipsa skills via an MCP server that the launcher hosts on the host side.

## Context

Skills currently have no way to pause mid-execution and request information
from the user. The existing `needs_input` status code in the multi-phase
contract is the closest mechanism:

- Works **only** in multi-phase skills (single-shot skills can't trigger it).
- Requires the skill author to emit structured JSON with the right shape.
- Forces the phase to end and re-execute — multi-turn dialog is awkward.

This proposal replaces and supersedes `needs_input` with a uniform mechanism
exposed as MCP tools the agent calls like any other tool. Works for both
single-shot and multi-phase skills, mid-execution dialog is natural, and
the agent's mental model is "use this tool" rather than "emit this status".

## Goals

- Any skill (single-shot or multi-phase) can request a free-text answer,
  yes/no confirmation, or selection from a list of options.
- No new manifest fields. Capability available by default to every skill.
- Unattended runs (cron, non-TTY) degrade gracefully — the tool returns a
  well-defined error so the agent can fall back.
- Minimal disruption to existing executor: ~one new thread, one lock,
  small runtime contract update.
- Implementation budget: 3-5 days focused work.

## Non-goals

- **Threshold-based intervention** ("ask user when 80% of budget used").
  Out of scope for v1. Will be addressed through runtime-contract guidance
  that nudges the agent to self-check ("if running long, call confirm").
- **Multi-skill or session-shared state.** Each `zipsa run` invocation has
  its own MCP server instance and socket; no cross-run state.
- **Rich UI primitives** (forms, file pickers, etc.). Text-only v1.
- **`progress(message)` and `pause_for_review(state)` tools.** Reasonable
  next additions but YAGNI for v1.
- **asyncio refactor of the executor.** Threading is sufficient because
  agent tool calls naturally serialize container output (container is
  idle while waiting for tool result).

## Architecture

```
┌─────────────────────────────── HOST ──────────────────────────────┐
│                                                                   │
│  Launcher process (Python, single-threaded today;                 │
│                    adds one daemon thread for MCP)                │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │ Main thread: existing executor loop                         │  │
│  │   - Popen(docker run …)                                     │  │
│  │   - for line in process.stdout: render                      │  │
│  └─────────────────────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │ MCP thread (NEW): HTTP MCP server on 127.0.0.1:<port>       │  │
│  │   - Listens on dynamic port                                 │  │
│  │   - Handles ask/confirm/choose by                           │  │
│  │     printing prompt to stdout + reading stdin               │  │
│  │   - Validates Bearer <token> on every request               │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  stdout_lock — held briefly during prompt display                 │
│                                                                   │
│  Port + token: regenerated per run, kept in launcher memory only  │
└───────────────────────────────┬───────────────────────────────────┘
                                │
                       host.docker.internal:<port>
                                │
┌──────────────────────────── CONTAINER ────────────────────────────┐
│                                                                   │
│  claude (MCP client)                                              │
│  └─ Reads .claude.json: mcpServers.zipsa = {                      │
│        type: "http",                                              │
│        url: "http://host.docker.internal:<port>/mcp",             │
│        headersHelper: '... Authorization: Bearer $ZIPSA_HITL_TOKEN' │
│      }                                                            │
│                                                                   │
│  Agent calls mcp__zipsa__ask({prompt: "…"})                       │
│   → HTTP POST to host.docker.internal:<port>/mcp                  │
│   → with Authorization: Bearer <token>                            │
│   → launcher prints prompt, reads input, returns answer           │
│   → HTTP response back → agent continues                          │
└───────────────────────────────────────────────────────────────────┘
```

### Why TCP / host.docker.internal / HTTP MCP (revised from socat)

An earlier draft of this spec proposed a unix socket bridged by socat
inside the container. Research showed Docker for Mac does not support
unix socket transmission across the hypervisor (issue #483: "Socket
files and named pipes only transmit between containers and between OS X
processes — no transmission across the hypervisor is supported").
WSL2 + Docker Desktop has the same hypervisor boundary. The socat
approach would not have worked on the primary target (macOS).

Switched to TCP / HTTP for portability:

- **Works on every platform**: macOS, Windows, Linux, WSL2 — all
  Docker Desktop variants and native Docker Engine.
- **`host.docker.internal`** is native on Docker Desktop (Mac, Win).
  Linux Docker Engine needs `--add-host=host.docker.internal:host-gateway`,
  added automatically by the executor when running on Linux.
- **HTTP MCP transport** is supported by Claude Code's MCP client
  out of the box (`mcpServers.zipsa = { type: "http", url: ... }`).
  No transport relay binary in the container — runtime image unchanged.
- **`mcp` Python SDK** has a streamable-HTTP server implementation
  that the MCP thread wires into a small HTTP framework (stdlib
  `http.server` or `uvicorn` if already a dep).
- **Auth via per-run Bearer token**: random 32-byte token generated
  at launch, kept in launcher memory and injected as the env var
  `ZIPSA_HITL_TOKEN` in the container. The MCP server's `headersHelper`
  emits `{"Authorization": "Bearer $ZIPSA_HITL_TOKEN"}` so the token
  travels with every request. Server rejects any request without the
  matching token. Prevents another local process on the same host
  from connecting and impersonating the agent.

## Components

### 1. Launcher MCP server (`launcher/zipsa/core/hitl_mcp.py`, new)

A small module implementing three tools:

```python
# Pseudocode interface, exact API matches mcp SDK shape
server.tool("ask", schema={"prompt": str, "default": str | None})
server.tool("confirm", schema={"message": str, "default": bool | None})
server.tool("choose", schema={"prompt": str, "options": list[str]})
```

Each tool, when called:
1. Acquire `stdout_lock`.
2. Print a clearly delimited prompt block to stdout:
   ```
   ──── User input needed ────
   [ask] <prompt>
   >
   ```
3. Read one line from stdin.
4. (For `confirm`) parse to bool; for `choose`, validate index/match.
5. Print closing marker:
   ```
   ──── Resuming ────
   ```
6. Release `stdout_lock`.
7. Return result as MCP TextContent.

On stdin EOF / `--auto` mode / non-TTY, each tool returns an MCP
error result like `{ "isError": true, "content": [...] }` carrying a
machine-readable code (`HITL_UNATTENDED`) so the agent can handle it.

### 2. Server runner (`launcher/zipsa/core/hitl_runner.py`, new)

Manages the lifecycle of the MCP server thread:

- `start(stdout_lock: threading.Lock) → HitlServer`
  - Picks an available TCP port on `127.0.0.1` (bind to port 0,
    read assigned port).
  - Generates a random Bearer token (`secrets.token_urlsafe(32)`).
  - Spawns daemon thread that runs the HTTP MCP server on that port.
  - Returns `HitlServer` exposing `.port`, `.token`.
- `HitlServer.stop()`
  - Shuts down HTTP server, joins thread.
- Used by executor before/after `docker run`.

### 3. Executor integration (`launcher/zipsa/core/executor.py`, modified)

In `_execute_skill` and `_execute_phases`:

- Before container starts: `HitlServer.start(...)`, capture `port` and
  `token`.
- Add the token to the env dict as `ZIPSA_HITL_TOKEN=<token>`.
- Pass `port` to `build_claude_json` so the URL can be baked into
  `.claude.json`.
- On Linux only (not macOS / Windows Docker Desktop), add
  `--add-host=host.docker.internal:host-gateway` to the docker command.
  Detected via `platform.system() == "Linux"`.
- After container exits: `HitlServer.stop()`.
- All inside a `try / finally` so the server is always cleaned up.

The existing `subprocess.Popen` + `readline` loop stays unchanged.
A single new `threading.Lock` is shared with the MCP runner.

### 4. .claude.json change (`launcher/zipsa/core/skill.py`, modified)

`build_claude_json` accepts a new `hitl_port: int` parameter and
always registers the `zipsa` MCP server:

```json
{
  "projects": {
    "/home/agent/workspace": {
      "mcpServers": {
        "zipsa": {
          "type": "http",
          "url": "http://host.docker.internal:<port>/mcp",
          "headersHelper": "echo '{\"Authorization\": \"Bearer '\"$ZIPSA_HITL_TOKEN\"'\"}'"
        },
        "notion": { ... existing ... }
      }
    }
  }
}
```

Universal — every skill gets the `zipsa` MCP server entry. The token
travels via env-var-driven `headersHelper`; the container can rotate
tokens per run without rewriting `.claude.json` for every invocation
(env var alone changes).

### 5. PreToolUse hook allow list (`launcher/zipsa/core/executor.py`)

`_write_phase_allow_file` (and `_write_default_phase_allow_file`)
implicitly include `mcp__zipsa__ask`, `mcp__zipsa__confirm`,
`mcp__zipsa__choose` so the hook permits these for every skill /
phase without manifest changes. Skill authors do not see this.

### 6. Renderer change (`launcher/zipsa/core/renderer.py`, modified)

For `tool_use` events where the tool name starts with `mcp__zipsa__`,
print only a short marker (e.g. `[asking user]`) instead of the full
tool block. The detailed prompt is printed by the MCP server itself
in a clearly delimited block. Avoids redundant display.

### 7. runtime image

**No changes required.** HTTP MCP is supported natively by Claude
Code's MCP client — no transport relay binary to install in the
container. `RUNTIME_VERSION` does not bump for this feature.

### 8. runtime-contract.md (modified)

Add a section after the existing "Tool usage" section:

```
## Asking the user

You may pause and request information from the user using these MCP
tools (always available, no need to declare them):

- mcp__zipsa__ask({prompt}) → user's free-text reply
- mcp__zipsa__confirm({message}) → bool
- mcp__zipsa__choose({prompt, options}) → one of options

Guidelines:
- Ask only when essential information is missing or you are about to
  take an irreversible / destructive action and the intent is unclear.
- Do not ask things you can reasonably infer or default.
- Maximum 3 user prompts per phase — excessive asking is friction.
- Phrase questions in the user's language.
- If the tool returns isError with code "HITL_UNATTENDED", the run is
  non-interactive. Fall back to status=needs_input (multi-phase) or
  status=failed with error.code="hitl_unattended" (single-shot).
```

## Data flow — concrete example

Weather skill, user runs `zipsa run weather "오늘 날씨"`.

1. Launcher: `HitlServer.start()` → port=54123, token=abc...xyz.
   Env dict gets `ZIPSA_HITL_TOKEN=abc...xyz` (per-run, ephemeral).
2. Launcher builds `.claude.json` with
   `mcpServers.zipsa.url = "http://host.docker.internal:54123/mcp"`.
3. Launcher runs docker with the env file, on Linux also with
   `--add-host=host.docker.internal:host-gateway`.
4. Container claude reads `.claude.json`, instantiates the `zipsa`
   HTTP MCP client. `headersHelper` runs and emits the Bearer header
   with the env-var-supplied token.
5. Initial MCP handshake: client POSTs to `…/mcp` with `Authorization:
   Bearer abc...xyz` → launcher's HTTP server validates and responds.
6. Agent reads system prompt + user input "오늘 날씨". Sees the
   question lacks a location.
7. Agent calls `mcp__zipsa__ask({prompt: "어느 도시의 날씨를 알려드릴까요?"})`.
8. Tool call event emitted to container stdout. Launcher renderer
   suppresses the full tool_use display (`mcp__zipsa__*` rule) and
   prints `[asking user]` on its own line.
9. HTTP request hits launcher MCP server. Server acquires
   `stdout_lock`, prints:
   ```
   ──── User input needed ────
   [ask] 어느 도시의 날씨를 알려드릴까요?
   >
   ```
10. User types `서울` + Enter. MCP server reads, releases lock, returns
    `"서울"` as MCP TextContent in the HTTP response.
11. Agent receives tool_result, continues normally. Calls WebFetch
    for Seoul weather, summarizes, returns answer.
12. Container exits → `HitlServer.stop()` shuts down HTTP server,
    joins thread. Port and token are released; nothing persisted.

## Unattended mode

Detect non-interactive runs via `sys.stdin.isatty()` at executor
start. A `--auto` CLI flag for explicit unattended override is **out
of scope for v1** — `isatty()` alone covers cron/CI/redirected stdin.
When unattended, the MCP server still starts and accepts connections,
but every tool call returns:

```json
{
  "isError": true,
  "content": [
    {"type": "text",
     "text": "{\"code\":\"HITL_UNATTENDED\",\"message\":\"This run is not interactive; cannot ask user.\"}"}
  ]
}
```

The agent receives the error, follows runtime-contract guidance, and
either emits `needs_input` (multi-phase) or fails cleanly (single-shot).

## Concurrency model

- **Main thread** runs the existing executor loop. Blocking
  `subprocess.Popen` + `readline()` over docker stdout.
- **MCP thread** (daemon) runs the HTTP MCP server bound to
  `127.0.0.1:<port>`. Handles requests in its own event loop or
  thread pool, depending on the HTTP framework used.
- **stdout coordination**: a single `threading.Lock()` shared between
  the renderer (when printing event output) and the MCP server (when
  printing user prompts). The lock is held only for the short window
  of printing a prompt or printing one rendered event. Contention is
  near-zero in practice because the agent waiting for a tool result
  means container stdout is idle.
- **stdin** is read only by the MCP thread when handling ask/confirm/choose.
  The main thread never reads stdin.
- **Shutdown order**: container exit → main loop returns → executor
  calls `HitlServer.stop()` → MCP thread join.

## File layout

```
launcher/zipsa/core/
├── hitl_mcp.py          NEW — MCP tools (ask/confirm/choose) + auth check
├── hitl_runner.py       NEW — HTTP server lifecycle, port + token mgmt
├── executor.py          MODIFIED — start/stop runner, inject token env,
│                                   Linux add-host flag, default allow list
├── skill.py             MODIFIED — include zipsa HTTP MCP entry in .claude.json
└── renderer.py          MODIFIED — suppress mcp__zipsa__* tool_use detail

launcher/zipsa/system-prompts/
└── runtime-contract.md  MODIFIED — add "Asking the user" section

runtime/                  UNCHANGED (HTTP MCP is native; no socat install)

launcher/tests/
├── test_hitl_mcp.py     NEW — unit tests for tool handlers (mock stdin/stdout)
├── test_hitl_runner.py  NEW — HTTP server lifecycle, auth, port assignment
├── test_executor.py     MODIFIED — token env inject, add-host on Linux
└── test_skill.py        MODIFIED — zipsa HTTP MCP entry in .claude.json
```

## Tests

### Unit (no docker, no real socket)

- `ask` returns user input when stdin pre-seeded
- `confirm` parses "y"/"n"/"yes"/"no"/`default`
- `choose` accepts index or exact match, rejects out-of-range with retry
- Unattended mode returns the `HITL_UNATTENDED` error structure
- stdout_lock acquired and released correctly

### Integration (docker, real container)

- End-to-end: minimal skill that calls `mcp__zipsa__ask`, verify user
  prompt appears and the agent receives the typed answer.
- Hook integration: ensure `mcp__zipsa__ask` is in `phase-allow.json`
  by default; remove it manually and verify the call is denied.
- Auth rejection: a curl with no/incorrect Bearer to the launcher's
  port returns 401 — agent's request with correct token succeeds.
- Unattended: run with stdin redirected from /dev/null, verify agent
  gets the error and the run terminates with the expected status.
- Concurrent stdout: rapid event stream + an ask call — verify no
  garbling of the prompt within rendered events (visual check or
  marker-based assertion).
- Linux add-host: on a Linux runner, verify
  `--add-host=host.docker.internal:host-gateway` is in the docker
  command and the container resolves the host.

### Manual

- `zipsa run weather "오늘 날씨"` and answer "서울" interactively.
- `cron` simulation: `zipsa run weather "오늘 날씨" --auto` and verify
  graceful failure path.

## Error handling

- Port binding fails (no free port, permission): launcher aborts the
  run with a clear "could not start HITL server" error.
- `host.docker.internal` not resolvable in container (Linux without
  add-host flag): claude reports MCP connect failure; agent cannot
  use HITL tools. Add-host is added automatically on Linux to prevent
  this; if user removes it via `--docker-opt`, that's on them.
- MCP thread crashes mid-run: launcher logs the crash but allows the
  container to finish. Subsequent HITL calls from the agent will fail
  with HTTP error; the agent handles as unattended.
- Auth token mismatch: server returns 401, agent sees tool error.
  Should not happen in normal operation (token is injected via env).
- User Ctrl-C during prompt: SIGINT propagates; launcher tears down
  container and HTTP server. (No special partial-state recovery in v1.)
- User sends bad input for `confirm`/`choose`: tool re-prompts up to
  3 times, then returns error.

## Backwards compatibility

- Existing skills continue to work unchanged. The new `zipsa` MCP
  entry is added universally but is only used if the agent chooses
  to call it.
- The legacy `needs_input` status code remains supported in the
  multi-phase loop, but the runtime-contract guidance now recommends
  using the MCP tools instead. A follow-up may deprecate `needs_input`
  once skills migrate.
- Existing PreToolUse hook still enforces tool allow lists; the
  `mcp__zipsa__*` names are added to the default allow list, so no
  manifest update is required.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Port allocation race (two zipsa runs grab same port) | Low | Bind to port 0 (kernel picks free port); per-process |
| MCP thread leak after error | Medium | `try/finally` in executor; daemon thread dies on launcher exit |
| stdout garbling during simultaneous output | Low (see concurrency analysis) | stdout_lock + delimiter markers |
| Agent over-asks, frustrating user | Medium | runtime-contract guidance + max 3 / phase soft rule |
| Linux user removes `--docker-opt --add-host` accidentally | Low | Add automatically by default; document that overriding it breaks HITL |
| Other local user steals token (read process env or memory) | Low | Token is short-lived (per run), 256 bits, only in launcher memory + container env |
| Future move to B' (Python skill) makes this throwaway | Medium-High | The *concept* (ask/confirm/choose) maps directly to SDK calls; the *MCP server implementation* is throwaway. Acceptable. |

## Implementation budget

| Chunk | Days |
|---|---|
| `hitl_mcp.py` (tool handlers + auth + tests) | 1 |
| `hitl_runner.py` (HTTP server + port + token lifecycle) | 0.5 |
| `executor.py` integration (env inject + Linux add-host) + tests | 0.5 |
| `skill.py` .claude.json + tests | 0.25 |
| `renderer.py` mcp__zipsa__* suppression + tests | 0.25 |
| `runtime-contract.md` update | 0.25 |
| End-to-end manual verification | 0.5 |
| Buffer (corner cases, dev_overlay interaction) | 1 |

**Total: 4.25 days focused work** — within the 3-5 day estimate.

## Open questions

None blocking. Reasonable defaults:
- HTTP server framework: stdlib `http.server` for v1 (no new deps); if
  the `mcp` SDK requires a specific framework, defer to its choice.
- URL path: `/mcp` (standard MCP HTTP path).
- Prompt markers: `──── User input needed ────` / `──── Resuming ────`
  (configurable later if needed)
- Default for `confirm` if user just hits Enter: bool from `default`
  parameter or `false` if absent
- Maximum re-prompts on bad input: 3

## Success criteria

- A weather skill given "오늘 날씨" prompts the user for a location and
  produces the correct weather for that location.
- A multi-phase skill (e.g. daily-progress) can call
  `mcp__zipsa__confirm({message: "Notion에 4개 entry 작성할까요?"})`
  before its persist phase and proceed/abort based on the answer.
- `zipsa run <skill> "..." --auto` runs without prompts and produces
  a deterministic "needs more info" failure when the agent would have
  asked.
- No regressions in existing skill runs (weather, hello-world,
  daily-progress reference runs).

## Migration plan

1. Ship this feature as `feat/hitl-mcp` PR (the implementation).
2. No runtime image change required — feature ships entirely in the
   launcher.
3. (Optional follow-up) Deprecate `needs_input` status in
   runtime-contract, point users to MCP tools.
4. (Optional follow-up) Add `progress` / `pause_for_review` tools as
   demand surfaces.
