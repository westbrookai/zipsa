# list / runs surface exec skills (#158)

Part of epic **#155 — First-class exec skills**. Builds on the #156 loader
(`load_exec_skill`, `is_exec_format` in `core/exec_skill.py`).

## Problem
After migrating `weather` to the new layout, `zipsa list` reports it
`✗ broken — manifest.yaml not found`, even though it loads and runs fine
(`zipsa run weather` works). Two manifest-bound spots:
- `core/install_health.py::check_install` treats a skill as healthy only
  if it has `zipsa-dist/manifest.yaml` OR root `manifest.yaml`. A
  new-layout exec skill (SKILL.md + `scripts/` + `zipsa/package.yaml`, no
  manifest) has neither → false "broken".
- `cli.py` `list` reads `skill.manifest.metadata.version` via `Skill.load`
  for display, and counts run stats from the legacy run-dir layout.

`runs` (#151) already reads the exec run-dir layout (`<name>/runs/`); only
`list` + `check_install` need exec awareness.

## Decisions

### D1 — `check_install` recognizes exec skills as healthy
A linked/installed dir is healthy if EITHER:
- legacy: has `manifest.yaml` (root or `zipsa-dist/manifest.yaml`) — unchanged; OR
- exec: `is_exec_format(path)` is true AND `load_exec_skill(path)` succeeds
  (i.e. SKILL.md frontmatter + `zipsa/package.yaml` parse, `name`+`version`
  present).
Keep the existing dangling-symlink / missing-target checks. For an exec
dir that fails to load (e.g. bad package.yaml), return `ok=False` with the
loader's reason — a REAL broken state, not a false manifest one.

### D2 — `list` displays exec skills via the loader
Per installed dir, branch on `is_exec_format`:
- exec → identity (`name`, `version`) + tags from `load_exec_skill`; show
  `name@version`. exec skills have no orchestrator/children concept — show
  them plainly (no child tree).
- legacy → `Skill.load` as today (version, children, orchestrator badge).
Never call `Skill.load` on an exec skill (that's the bug). A skill whose
load fails shows the broken line (from check_install) instead of crashing
the whole `list`.

### D3 — run stats per layout
`list`'s run counting must read the right run-dir layout:
- exec skills: `~/.zipsa/<name>/runs/<ts>/` with `result.json` (mode
  exec/run; #151).
- legacy skills: `~/.zipsa/<name>@<version>/runs/<id>/` with `summary.json`.
Count total + success per the skill's layout. (Success = exit_code 0 /
status ok, matching each layout's record.)

## Out of scope
- `validate` (#159), `discover` (#160), `configure`/`connect` (#161).
- Migrating the remaining skills (remaining #156 work).
- Changing the run-dir formats themselves.

## Tests
- `check_install` on a new-layout exec fixture (SKILL.md + scripts/ +
  zipsa/package.yaml, no manifest) → `ok=True`. On a legacy manifest
  fixture → `ok=True` (unchanged). On a dangling symlink → `ok=False`
  (unchanged). On an exec dir with malformed package.yaml → `ok=False`
  with a clear reason (not "manifest not found").
- `list` includes an installed exec skill showing `name@version` (version
  from package.yaml), no crash, no "broken" line; a legacy skill still
  shows its version + orchestrator/children. Mixed install (one exec + one
  legacy) renders both. Honor `ZIPSA_HOME` tmp.
- `list` run stats: exec skill counts runs from `<name>/runs/`; legacy from
  `<name>@<version>/runs/`.
- Regression: full launcher suite green.
