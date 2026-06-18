---
name: zipsa-skill-builder
description: Author a zipsa skill (SKILL.md + scripts/ + zipsa/ layout) from a user's intent. Use whenever the user asks to create a new zipsa skill or migrate a legacy skill to the exec format — never hand-write skill files without this workflow.
---

# zipsa-skill-builder

The authoring workflow and the exec contract are owned by the launcher
(single source of truth), so they don't drift from the runtime:

- Workflow: `launcher/zipsa/authoring/skill-builder.md`
- Contract: `launcher/zipsa/authoring/AUTHORING.md`

Read both, then follow them. (`zipsa create` inlines these same two
files into the container agent's prompt; this project skill is the
in-repo Claude Code entry point to the same instructions.)

## Context that differs from `zipsa create`

When you author directly in a Claude Code session (not inside `zipsa
create`), the MCP tools (`mcp__zipsa__exec`/`promote`/`ask`) aren't
present. Instead:

- Test with the CLI: `zipsa exec <skill-path> "<query>" --local` (fast
  loop) then docker mode; pass `--mount HOST:CONTAINER` for credential
  files (AUTHORING §6).
- Place the finished skill at `skills/<name>/` yourself (no promote
  tool).

Everything else — the phase contract, the clarify-first discipline,
name-last, no dead files — is identical to the bundled workflow.

## Migrating a legacy skill

The old `SKILL.md` (LLM-instruction format) and `manifest.yaml` are the
requirements document — mine them for intent, edge cases, and output
shapes, then delete them (migrated skills go exec-only). Check nothing
references the skill as a child (`grep -rn "<name>" skills/*/manifest.yaml`)
and uninstall the legacy symlink (`zipsa uninstall <name>`).
