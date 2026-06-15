# Skills Backlog

Known follow-ups for the **skills** in this repo — migrations, cleanups,
and authoring-content debt. Add new items at the bottom; remove items
when they ship.

For launcher/runtime work, see the component backlogs
([`../launcher/BACKLOG.md`](../launcher/BACKLOG.md)); for cross-cutting
items, the repo-root [`../BACKLOG.md`](../BACKLOG.md).

---

## Migrate the legacy skills to the `zipsa exec` format (2026-06-15)

**Context.** Several skills still ship in the legacy `zipsa run` format
(manifest + LLM-instruction `SKILL.md` + `spec` phases) rather than the
`zipsa exec` format (`zipsa-dist/<n>.<slug>.<ext>` deterministic
phases). `hello-world`, `weather`, `dad-joke`, `agenthud-report`, and
`wahroonga-umbrella-alert` are already exec-format; the remaining
orchestrator/automation skills (e.g. `daily-progress`, `bip-daily-x`)
are not.

**Blocker.** The heavier skills depend on composition features the exec
platform doesn't have yet (parent → child skill orchestration, HITL
mid-pipeline, declared state across phases). Migrating them now would
mean re-implementing those gaps ad hoc. The skill-composition spec
(`docs/superpowers/specs/2026-05-21-skill-composition-design.md`) is the
prerequisite.

**Direction.** Migrate per the `zipsa-skill-builder` workflow: mine the
old `SKILL.md` + `manifest.yaml` as the requirements doc, rewrite into
deterministic code phases + LLM (`.md`) phases where inference is
genuinely needed, then delete the legacy files in the same change
(policy: migrated skills go exec-only) and `zipsa uninstall` the legacy
link.

**Do this when.** The composition platform gap closes, or a specific
legacy skill becomes painful enough to migrate solo (accepting a
hand-rolled stand-in for the missing platform feature).

---

## Decide example-skill retention (2026-06-15)

**Context.** Several skills exist primarily as examples / E2E fixtures
created while building `zipsa exec` and `zipsa create`:

- `hello-world` — hybrid (code + LLM) teaching example
- `weather` — fetch + LLM-report pattern
- `dad-joke` — fetch + LLM-report, near-duplicate of weather's shape
- `wahroonga-umbrella-alert` — real E2E artifact (committed without
  credentials; telegram.json is mounted at run time)

**Question.** Which of these stay as canonical examples vs. get removed
once the registry / examples story is settled? `dad-joke` and `weather`
overlap heavily (both are "fetch JSON → LLM writes a sentence"), so one
may be redundant. `wahroonga-umbrella-alert` was kept deliberately to
revisit ("commit now, decide later whether to delete").

**Decide when.** The skill distribution model lands (see root BACKLOG)
— that's when "what's a shipped example vs. a user's own skill" gets a
real home, and redundant examples can be pruned without losing
documentation value.
