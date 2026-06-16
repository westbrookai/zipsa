# forge/create `--skills-dir` default — repo-aware (#140)

## Problem

`zipsa forge` / `zipsa create` default `--skills-dir` to `Path("skills")`,
which is resolved **relative to the current working directory**
(`skills_dir.resolve()` in `cli.py`). When the command is run from
`launcher/` (as it routinely is during development), the finished skill
is promoted to `launcher/skills/` instead of the repo's `skills/`. We hit
this for real: a forged skill landed in `launcher/skills/` and had to be
moved by hand (this is what #140 tracks; see also #142's promote).

The default must be **deterministic and independent of cwd**.

## Decision

Default `--skills-dir` to a **repo-aware** location:

- **Inside a git repository** → `<git-toplevel>/skills`. This matches the
  developer workflow: forged skills are committed to the repo (as
  bus-575, wahroonga were). Detection is `git rev-parse --show-toplevel`
  run from cwd, so running from `launcher/` still resolves to the repo
  root's `skills/`.
- **Outside any git repository** → `~/.zipsa/skills` (i.e.
  `paths.skills_dir()`). This is the runtime's skill home, where
  `zipsa run <name>` looks up installed skills — so a skill forged by an
  end user (e.g. a future `brew`-installed `zipsa`, run anywhere) lands
  where it can be run by name.

`--skills-dir <path>` always overrides (explicit wins, resolved as
before). The directory need not exist yet; promote creates it.

### Why this default (the chosen option)

This was a genuine product decision (forge as a dev tool for repo skills
vs. an end-user tool for personal skills). The repo-aware default serves
**both audiences automatically**: in-repo devs get repo `skills/`;
everyone else gets the home skill dir. The "magic" git-toplevel detection
is reliable and overridable. Rejected alternatives: always
`~/.zipsa/skills` (forces devs to pass `--skills-dir` every time to commit
a repo skill); always `<toplevel>/skills` (errors / nonsensical outside a
repo, breaks end users).

### Edge case (documented, accepted)

If `forge` is run from inside some git repo that is **not** zipsa, the
default resolves to *that* repo's `skills/`. This is the intended meaning
of "in-repo → repo skills" (you are presumably developing a skill for the
repo you are in). `--skills-dir` overrides when that is not desired.

## Implementation

### `launcher/zipsa/paths.py`

Add a helper next to `skills_dir()`:

```python
import subprocess  # add to imports

def default_forge_skills_dir() -> Path:
    """Default location where `forge`/`create` promote a finished skill.

    In a git repo -> <toplevel>/skills (dev workflow: skills committed to
    the repo). Outside a repo -> ~/.zipsa/skills (the runtime's skill
    home, so the skill is runnable by name). cwd-independent: the git
    lookup uses the repo enclosing cwd, not a literal relative path.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return skills_dir()
    toplevel = result.stdout.strip()
    return Path(toplevel) / "skills" if toplevel else skills_dir()
```

### `launcher/zipsa/cli.py`

For BOTH `create_skill` and `forge_skill`:

- Change the option to `Optional[Path] = None` and update the help text:
  `"Where the finished skill is promoted (default: <repo>/skills in a git repo, else ~/.zipsa/skills)"`.
- Resolve the destination before calling `run_forge`:
  ```python
  dest = skills_dir.resolve() if skills_dir is not None else default_forge_skills_dir()
  rc = run_forge(intent, skills_dir=dest, image=image)
  ```
- Import `default_forge_skills_dir` from `.paths`.

`run_forge` / `run_create` signatures are unchanged (still take a resolved
`skills_dir: Path`).

## Tests

`launcher/tests/` (new or existing test_paths / test_cli):

1. `default_forge_skills_dir` returns `<toplevel>/skills` when
   `subprocess.run` yields a toplevel (monkeypatch `subprocess.run`).
2. Returns `skills_dir()` (`~/.zipsa/skills`, honoring `ZIPSA_HOME`) when
   `subprocess.run` raises `CalledProcessError` (not a repo).
3. Returns `skills_dir()` when `subprocess.run` raises `FileNotFoundError`
   (git not installed).
4. Returns `skills_dir()` when toplevel is empty string.
5. CLI: `forge` and `create` with NO `--skills-dir` call `run_forge` with
   `skills_dir == default_forge_skills_dir()` (monkeypatch `run_forge` +
   `default_forge_skills_dir`; assert the kwarg).
6. CLI: `forge`/`create` WITH `--skills-dir /tmp/x` call `run_forge` with
   the resolved explicit path (default helper NOT consulted).

## Out of scope

- Changing where `zipsa install` / run-by-name look up skills (already
  `~/.zipsa/skills`).
- Auto-committing or PR-ing the promoted skill.
- The separate run-time mount work (#145, done).
