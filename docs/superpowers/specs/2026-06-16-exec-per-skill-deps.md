# exec: per-skill Python dependencies via `uv run --script` (PEP 723)

> Design doc for GitHub issue #135. Lets a skill bring its own PyPI
> dependencies without changing the runtime image.

## Context / problem

`exec` runs Python phases as `python <script>` inside the runtime
container, so a phase can only use the standard library plus whatever is
baked into the image (currently: node/npx, go, uv, jq/yq, …). Any skill
needing a PyPI package — `gtfs-realtime-bindings`/`protobuf`, `requests`,
`beautifulsoup4`, … — has no way to get it.

Baking every library into one universal runtime image does **not** scale:
the image grows without bound, and **customers who author their own
skills cannot ask us to patch the runtime per skill.**

Surfaced by a live `zipsa forge` E2E (a Sydney bus GTFS-Realtime →
Telegram alert): GTFS-Realtime is protobuf; the runtime has no protobuf
library, so the forge agent hand-rolled a stdlib `struct` protobuf parser
that desynced and crashed every poll in an infinite loop, blocking the
forge test.

## Goal

A Python skill phase declares its PyPI dependencies **inline (PEP 723)**
and `exec` runs it via **`uv run --script`** (uv already ships in the
runtime image). uv resolves + caches the per-skill deps at run time. **No
per-skill image change; any PyPI library; works for customer skills.**

```python
# /// script
# dependencies = ["gtfs-realtime-bindings"]
# ///
import json, sys
from google.transit import gtfs_realtime_pb2
...
```

## Proven (stock `ghcr.io/westbrookai/zipsa-runtime:0.4.9`, no image change)

- `uv run --script` on a PEP 723 script installs `gtfs-realtime-bindings`
  + protobuf and imports them successfully.
- Confirmed with the skill **directory mounted read-only** — exactly how
  the exec runner mounts `/skill` (`-v <skill_root>:/skill:ro`):
  `Installed 2 packages` → script ran → emitted its JSON result line.
- (A single-FILE bind mount under `/tmp` failed with a Docker Desktop
  file-sharing quirk; irrelevant — the real runner mounts the whole skill
  directory from a shared path under `/Users`.)

## Design

### 1. Runner change (the core)
`launcher/zipsa/exec_runner.py` — `RUNNERS["py"]`:
```python
"py": ["uv", "run", "--script"],   # was ["python"]
```
The phase file path is appended as the last arg, as today, so:
- docker: `uv run --script /skill/zipsa-dist/<n>.<slug>.py`
- local (`--local`): `uv run --script <hostpath>` (host has uv)

`uv run --script` honors PEP 723 inline metadata; a script with **no**
metadata runs with no extra deps (stdlib only) — same effect as bare
`python` today, so existing skills keep working.

Other runners unchanged: `sh`/bash, `js`/node, `ts`/npx-tsx, `go`/go-run
(npx and go already fetch their own deps); `.md` LLM phases unchanged.

The stdin `{"ctx",...}` payload, last-JSON-line result contract, exit
code, and `/out` artifacts are all unchanged — `uv run --script` just
replaces the interpreter in front of the same script.

### 2. uv cache (perf — recommended in this change)
Each phase runs in a fresh `--rm` container, so without a shared cache uv
re-downloads deps on every run. Persist the cache:
- Add a host uv-cache dir (e.g. `~/.zipsa/uv-cache`) and mount it into the
  container at the uv cache path, or set `UV_CACHE_DIR` to a mounted dir.
- Implement in `_build_docker_argv` (mount + `-e UV_CACHE_DIR=...`), for
  `.py` phases at least. Local mode already uses the host's `~/.cache/uv`.
- First-ever fetch still downloads (needs network — exec containers have
  network; API skills need it anyway). Subsequent runs hit the cache.

### 3. Docs
- `AUTHORING.md`: document the convention — Python scripts declare PyPI
  deps via PEP 723 (`# /// script` … `dependencies = [...]` … `# ///`);
  no block = stdlib only. Note network is required on first dep fetch.
- `skill-builder.md` (feasibility step): **reframe** — "needs a PyPI
  library" is NOT a platform gap (declare it via PEP 723); only flag a
  gap for capabilities uv/pip cannot provide (e.g. a persistent daemon,
  hardware). This directly addresses why the forge agent wrongly
  hand-rolled protobuf instead of declaring a dep.

## Files
- `launcher/zipsa/exec_runner.py` — `RUNNERS["py"]`; `_build_docker_argv`
  (uv cache mount + `UV_CACHE_DIR`).
- `launcher/zipsa/authoring/AUTHORING.md` — PEP 723 deps convention.
- `launcher/zipsa/authoring/skill-builder.md` — feasibility reframe.
- Tests: `launcher/tests/test_exec_runner.py`.

## Verification
- Unit: `RUNNERS["py"] == ["uv", "run", "--script"]`; `_build_docker_argv`
  includes the uv-cache mount + `UV_CACHE_DIR` for `.py` phases.
- Backward-compat: existing stdlib-only example skills (`weather`,
  `hello-world`, `dad-joke`) still run via `zipsa exec … --local` and in
  docker mode (no PEP 723 block needed).
- Deps path: a fixture phase with `# /// script dependencies=[...] ///`
  installs + imports the dep and returns its result (gate as an
  integration test if it needs network; the manual proof above stands).
- Full suite green: `cd launcher && uv run --extra dev pytest`.

## Out of scope (separate follow-ups / issues)
- The bus-departure skill itself — resume its `zipsa forge` after this
  lands; it will declare `gtfs-realtime-bindings` instead of hand-rolling.
- The forge `ask`-timeout robustness, and the "an infinite-loop script
  blocks the synchronous exec test until the 10-min run_phase timeout"
  finding — both noted during the same E2E; track separately.
- Non-Python per-skill dependency stories beyond what npx/go already give.
