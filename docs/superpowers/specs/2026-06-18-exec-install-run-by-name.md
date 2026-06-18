# install-by-name + run-by-name for exec skills (#157)

Part of epic **#155 — First-class exec skills**. Depends on the #156
loader (`load_exec_skill`, merged in PR #163).

## Problem
exec skills can only be referenced **by path** today:
- `zipsa install` peeks `Skill.load(src)` for the version → fails for an
  exec skill (no `manifest.yaml`).
- `zipsa run <name>` does `skill_path = Path(name)`; a bare installed
  NAME is not a dir path, so it falls through to `Skill.load`
  (legacy/manifest) → fails for an exec skill.
- The `zipsa <name>` shortcut rewrites to `run <name>`, inheriting the
  same failure.

Note `resolve_skill(name)` already returns the installed dir
(`~/.zipsa/skills/<name>`) layout-agnostically — the gap is purely in the
install peek and the run dispatch.

## Decisions

### D1 — `install` learns the exec branch
Where install currently peeks `Skill.load(src)` for identity (the
broken-link replacement path AND `install_local`/`install_from_github`),
add an exec branch: if the source dir is exec-format (no manifest), use
`load_exec_skill(src)` to get `name` + `version`. Everything else is
unchanged: dest = `~/.zipsa/skills/<name>`, link or copy, `install.json`
records the version + source + link/copy mode. Legacy manifest install is
untouched. A source missing `version`/`name` surfaces the loader's clear
`ExecSkillError`.

### D2 — `run` resolves a NAME to an exec dir
Resolution order in the `run` command:
1. **Explicit path** — if `Path(name)` is a dir: exec-format →
   `run_skill_llm`; else legacy `Skill.load`/DockerExecutor. (unchanged)
2. **Installed/builtin name** — else `resolve_skill(name)` → dir; if
   exec-format → `run_skill_llm(dir, …)`; else legacy `Skill.load`.
3. Not found → `resolve_skill`'s `SkillNotInstalledError`.

So an installed exec skill runs by name; legacy manifest skills keep their
DockerExecutor path. The exec-format `run` flags rejection (`--dry-run`
/`--shell`/`--env` not supported) already exists — keep it on the
name-resolved exec path too.

### D3 — `_is_exec_format` recognizes the new layout
`_is_exec_format(dir)` must treat a skill as exec when it has SKILL.md and
EITHER `scripts/` or legacy `zipsa-dist/`, AND no `manifest.yaml`.
(Equivalently: it loads via `load_exec_skill` / has `zipsa/package.yaml`.)
Today it only checks `zipsa-dist/`; after #156 a skill may be
`scripts/` + `zipsa/`. Keep manifest.yaml as the legacy marker (its
presence → NOT exec, route to legacy).

### D4 — `zipsa <name>` shortcut
Already layout-agnostic (`installed_skill_dir(name).exists()`), so once D2
lands it works for exec skills. Just verify with a test; no change
expected.

## Coexistence
Legacy manifest skills: unchanged (Skill.load + DockerExecutor for run,
manifest peek for install). Dispatch everywhere by `_is_exec_format` /
manifest.yaml presence. Both worlds installable/runnable side by side.

## Out of scope
- `list`/`runs` (#158), `validate` (#159), `discover` (#160),
  `configure`/`connect` (#161).
- Migrating existing skills to the new layout (remaining #156 work).
- Retiring the legacy DockerExecutor `run` path / `create` alias (#162).
- Registry / GitHub-exec-skill resolution beyond what `install_from_github`
  already does structurally (exec skills install from a local dir or a
  GitHub repo the same way; only the identity peek changes).

## Tests
- `install --link <exec-fixture>` (SKILL.md + scripts/ + zipsa/package.yaml,
  no manifest) → installs to `~/.zipsa/skills/<name>`, `install.json` has
  the package.yaml version; `resolve_skill(name)` finds it. (honor
  `ZIPSA_HOME` tmp.)
- `install` of an exec source missing `version` → clear error (loader's
  ExecSkillError), no partial install.
- `run <name>` for an installed exec skill → dispatches to `run_skill_llm`
  with the resolved dir (mock `run_skill_llm`, assert called with the dir);
  does NOT call `Skill.load`.
- `run <name>` for an installed legacy/manifest skill → still the
  DockerExecutor path (unchanged).
- `run ./path/to/exec-skill` (explicit path) still works (regression).
- `zipsa <name>` shortcut for an installed exec skill → rewrites to `run`
  and dispatches to `run_skill_llm`.
- `_is_exec_format`: true for SKILL.md + scripts/ (new) AND SKILL.md +
  zipsa-dist/ (legacy), both without manifest; false when manifest.yaml
  present.
- Full launcher suite stays green.
