# Test fixture skills

Skills under this directory exist to support unit tests and end-to-end
verification of zipsa runtime features. They are NOT meant for user
consumption — install them only when running the corresponding tests.

## Unit-test fixtures

Used by `pytest` files via `Skill.load(Path(__file__).parent / "fixtures/skills/<name>")`:

- **test-skill** — minimal valid manifest, used as a generic loader fixture
- **requires-demo** — manifest with `spec.requires` declarations for testing the requires-config flow

## E2E fixtures (Phase 1 — artifacts/ + get_artifact)

Install with `zipsa install --link <path>` to actually run them. Each pairs with a Phase 1 scenario.

- **artifact-echo** — single-shot. Reads `<skill> <version> <run_id> <name>` from user_query, calls `mcp__zipsa__get_artifact`, echoes the result. Proves cross-skill artifact read.
- **artifact-roundtrip** — two phases. Phase A writes a deterministic JSON to `/home/agent/runs/current/artifacts/roundtrip.json`; phase B reads it back via `get_artifact` using `execution_context.run_id`. Proves intra-skill multi-phase data sharing.

## E2E fixtures (Phase 2 — run_skill + parent-server reuse)

- **test-parent** — single-shot. Calls `mcp__zipsa__run_skill("hello-world")` and echoes the routing fields. Smoke test for run_skill.
- **orchestrator-demo** — calls `run_skill("weather", city)` then chains to `get_artifact` using the returned `{skill, version, run_id}`. Proves orchestrator pattern end-to-end.
- **hitl-demo** + **voice-asker** — parent (hitl-demo) calls run_skill on a child that does `mcp__zipsa__ask`. Proves HITL routes through parent's HitlServer when the child reuses parent's MCP server. (Requires a real TTY; piped stdin will surface `HITL_UNATTENDED`.)
- **memory-isolation-demo** + **memory-peek** — parent writes `remember(key="color", value="red")` to its own memory file, then invokes child memory-peek which calls `recall(key="color")` and gets `null`. Proves per-skill memory scoping via the caller-context contextvar.

## How to run e2e fixtures

```bash
# One-time install (idempotent)
for s in artifact-echo artifact-roundtrip test-parent orchestrator-demo \
         hitl-demo voice-asker memory-isolation-demo memory-peek; do
  zipsa install --link launcher/tests/fixtures/skills/$s
done

# Then run individual scenarios — see PR #58, #60 comments for transcripts.
zipsa run test-parent
zipsa run orchestrator-demo "Sydney"
# ...
```

These are linked installs; editing the source updates the installed skill immediately.
