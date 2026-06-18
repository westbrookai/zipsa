# validate for exec skills (#159)

Part of epic **#155 — First-class exec skills**. Depends on the metadata
keystone (#156): the loader + schema already exist (`load_exec_skill`,
`ExecSkill`); `discover_phases` already parses the phase directory. This
issue makes `zipsa validate` work for exec-format skills, which today have
no validation path (the skill-builder leans on `zipsa exec` runs as the
de-facto check).

## Problem
`zipsa validate <name>` runs `Skill.load` (manifest schema). Exec skills
have no `manifest.yaml`, so `Skill.load` fails and validate is closed to
them. We want a static, fast structural check — no container, no exec.

## Scope — static checks only
"Does it exec cleanly" is what `zipsa exec` already covers. `validate` is
the **static** pre-flight: schema + structure, collecting *all* problems
in one pass (not fail-fast) so the author sees everything at once.

Checks (errors fail validation; warnings don't):
1. **Metadata schema** (error) — `load_exec_skill`: SKILL.md +
   `zipsa/package.yaml` present, frontmatter `name` present, package
   `version` present, `requires` shape valid. (Reuses the loader; its
   `ExecSkillError` messages name the field + file.)
2. **`description` present** (error) — the Agent-Skills standard requires
   `description`; the loader is lenient (Optional) but validate is strict,
   since the whole north star is standard-compatibility.
3. **Phases parse** (error) — `discover_phases`: a phase directory
   (`scripts/`, or legacy `zipsa-dist/`) exists, ≥1 phase file matches
   `<int>.<slug>.<ext>`, no duplicate phase ids. (Covers "phases parse"
   and "phase ordering".)
4. **PEP 723 blocks valid** (error) — every `.py` phase that carries a
   `# /// script` … `# ///` block must parse as TOML. A malformed block
   would make `uv run --script` fail at run time; catch it statically.
5. **First phase is `.py`** (warning) — production skills should start
   with a deterministic preflight, not an LLM phase. Mirrors the warning
   `discover_phases` already logs.

Out of scope (YAGNI): dotted sub-id rejection (no skill uses them; the
runner would still run them), SKILL.md prose linting, mount/credential
reachability, anything requiring a container.

## Deliverable
- `launcher/zipsa/core/exec_validate.py` — `validate_exec_skill(skill_dir)
  -> ExecValidation` (`errors`, `warnings`, `ok`, plus the loaded
  `skill`/`phases` for the CLI to print identity). Pure, no I/O beyond
  reading the skill dir.
- Wire `validate` (cli.py) to dispatch on `_is_exec_format`: exec skills
  go through `validate_exec_skill`; legacy skills keep `Skill.load`. The
  argument resolves a path if it is an existing directory, else an
  installed name (so authors can `zipsa validate ./my-skill`).

## Tests
- Valid exec skill → `ok`, no errors.
- Missing `version` / missing `name` / missing `description` → distinct errors.
- No phase dir / no phases / duplicate phase id → errors.
- Malformed PEP 723 TOML → error; valid PEP 723 → ok.
- First phase `.md` → warning, still `ok`.
- CLI: `validate ./exec-skill` exits 0 and prints identity; a broken one
  exits 1 listing every error. Legacy manifest validate unchanged.
