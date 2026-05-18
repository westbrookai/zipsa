# Empty user_query handling + 집사 intro — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** `zipsa run <skill>` (no query) becomes a first-class flow: either substitute a skill-declared `default_query`, or pass empty and let the runtime contract instruct the agent to introduce itself as 집사 and elicit the request via HITL. CLI's hard-fail + "Error: 1" double-print also fixed.

**Architecture:** Tiny change. New `Optional[str]` field on `SkillSpec`. CLI substitution. One section added to runtime-contract.md. Example skill (hello-world) demonstrates `default_query` use.

**Tech Stack:** unchanged (Python 3.10+, pydantic, typer, pytest).

---

## Commit boundaries

| Commit | What |
|---|---|
| **1** | `feat(models): SkillSpec.default_query optional field` (Task 1) |
| **2** | `feat(cli): drop user_input hard-fail; substitute default_query` (Task 2) |
| **3** | `docs: runtime-contract empty user_query section + skills README user-facing description note` (Tasks 3 + 5 bundled — both docs) |
| **4** | `chore(hello-world): default_query example + user-facing description` (Task 4) |

---

## Task 1: `SkillSpec.default_query`

**Files:**
- Modify: `launcher/zipsa/core/models.py` (around `SkillSpec`)
- Test: `launcher/tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `launcher/tests/test_models.py`:

```python
class TestDefaultQuery:
    """SkillSpec.default_query is an optional string used by the launcher
    when the user invokes the skill with no query argument."""

    def _spec(self, **overrides):
        from zipsa.core.models import SkillSpec
        data = {
            "purpose": "test",
            "instructions": "./SKILL.md",
        }
        data.update(overrides)
        return SkillSpec.model_validate(data)

    def test_default_query_absent_is_none(self):
        spec = self._spec()
        assert spec.default_query is None

    def test_default_query_accepts_string(self):
        spec = self._spec(default_query="Say hello.")
        assert spec.default_query == "Say hello."

    def test_default_query_empty_string_treated_as_empty(self):
        """Empty string is a valid (but odd) value — it explicitly
        opts INTO the empty-input → agent-intro path. Not None."""
        spec = self._spec(default_query="")
        assert spec.default_query == ""
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_models.py::TestDefaultQuery -v
```
Expected: 3 failures — `default_query` attribute doesn't exist.

- [ ] **Step 3: Implement**

In `launcher/zipsa/core/models.py`, add a field to `SkillSpec` (alongside the other Optional fields):

```python
class SkillSpec(BaseModel):
    # ... existing fields ...
    default_query: Optional[str] = None
    # ... existing fields after ...
```

(Place it near `config` or `network` — wherever feels most natural. Order doesn't matter for behavior.)

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_models.py::TestDefaultQuery -v
uv run pytest                # full suite — no regressions
```

Expected: 3 passing in TestDefaultQuery; full suite 426 (423 baseline + 3 new).

- [ ] **Step 5: Commit (boundary 1)**

```bash
git add launcher/zipsa/core/models.py launcher/tests/test_models.py
git commit -m "feat(models): SkillSpec.default_query optional field

Skills can declare a fallback query the launcher substitutes when
the user runs 'zipsa run <skill>' with no argument. Absent → None;
explicit empty string → opts into the agent-intro flow."
```

---

## Task 2: CLI — drop hard-fail, substitute default_query, fix double-print

**Files:**
- Modify: `launcher/zipsa/cli.py` (the `run` command)
- Test: `launcher/tests/test_cli.py`

### Step 1: Locate the current behavior

Read the `run` command in cli.py. Find the block that errors when both `user_input` is missing and `--shell` isn't set. It looks roughly like:

```python
if not user_input and not shell:
    typer.echo("Error: user_input is required unless --shell is specified", err=True)
    raise typer.Exit(1)
```

The "Error: 1" double-print likely comes from the exit code propagating through some additional error wrapper (typer Exit or a top-level handler). Inspect to confirm.

### Step 2: Write failing tests

Add to `launcher/tests/test_cli.py`:

```python
class TestRunEmptyQuery:
    """`zipsa run <skill>` with no query: substitute default_query if
    declared, else pass empty string. No hard-fail at the CLI."""

    def test_no_query_with_default_query_substitutes(self, tmp_path):
        from unittest.mock import patch
        from typer.testing import CliRunner
        from zipsa.cli import app

        # Build a tiny skill manifest with default_query set
        skill_dir = tmp_path / "fixture-skill"
        skill_dir.mkdir()
        (skill_dir / "manifest.yaml").write_text("""apiVersion: zipsa.dev/v1alpha1
kind: Skill
metadata:
  name: fixture-skill
  version: 1.0.0
spec:
  purpose: Test fixture for default_query substitution.
  instructions: ./SKILL.md
  default_query: "Test default query"
  tools: { builtin: [] }
""")
        (skill_dir / "SKILL.md").write_text("# Fixture")

        runner = CliRunner()
        with patch("zipsa.cli.DockerExecutor") as exec_cls, \
             patch("zipsa.cli._resolve_skill_path", return_value=skill_dir):
            executor = exec_cls.return_value
            executor.run.return_value = iter([])

            result = runner.invoke(app, ["run", "fixture-skill"])

        assert result.exit_code == 0, result.output
        # The user_input passed to executor.run should be the default_query
        kwargs = executor.run.call_args.kwargs
        assert kwargs["user_input"] == "Test default query"

    def test_no_query_no_default_passes_empty_string(self, tmp_path):
        from unittest.mock import patch
        from typer.testing import CliRunner
        from zipsa.cli import app

        skill_dir = tmp_path / "fixture-skill"
        skill_dir.mkdir()
        (skill_dir / "manifest.yaml").write_text("""apiVersion: zipsa.dev/v1alpha1
kind: Skill
metadata:
  name: fixture-skill
  version: 1.0.0
spec:
  purpose: Test fixture for empty-query passthrough.
  instructions: ./SKILL.md
  tools: { builtin: [] }
""")
        (skill_dir / "SKILL.md").write_text("# Fixture")

        runner = CliRunner()
        with patch("zipsa.cli.DockerExecutor") as exec_cls, \
             patch("zipsa.cli._resolve_skill_path", return_value=skill_dir):
            executor = exec_cls.return_value
            executor.run.return_value = iter([])

            result = runner.invoke(app, ["run", "fixture-skill"])

        # No hard-fail anymore
        assert result.exit_code == 0, result.output
        # And the old "Error: user_input is required" message must NOT appear
        assert "user_input is required" not in result.output
        # Empty string was passed
        kwargs = executor.run.call_args.kwargs
        assert kwargs["user_input"] == ""

    def test_no_query_does_not_double_print_error(self, tmp_path):
        """Even when something downstream errors, the CLI should not show
        a bare 'Error: 1' line in addition to the actual error message."""
        from unittest.mock import patch
        from typer.testing import CliRunner
        from zipsa.cli import app

        skill_dir = tmp_path / "fixture-skill"
        skill_dir.mkdir()
        (skill_dir / "manifest.yaml").write_text("""apiVersion: zipsa.dev/v1alpha1
kind: Skill
metadata:
  name: fixture-skill
  version: 1.0.0
spec:
  purpose: Test fixture.
  instructions: ./SKILL.md
  tools: { builtin: [] }
""")
        (skill_dir / "SKILL.md").write_text("# Fixture")

        runner = CliRunner()
        with patch("zipsa.cli.DockerExecutor") as exec_cls, \
             patch("zipsa.cli._resolve_skill_path", return_value=skill_dir):
            executor = exec_cls.return_value
            # Force a downstream RuntimeError to make the run fail
            executor.run.side_effect = RuntimeError("simulated failure")

            result = runner.invoke(app, ["run", "fixture-skill"])

        assert result.exit_code != 0
        # The actual RuntimeError message should surface ONCE
        assert "simulated failure" in result.output
        # The bare 'Error: 1' (or 'Error: <exit code>') double-print must NOT appear
        assert "Error: 1\n" not in result.output
        assert "\nError: 1" not in result.output
```

Note: the tests patch a `_resolve_skill_path` helper that may or may not exist in cli.py today. The implementer should adapt to the actual structure — the contract is "use the skill the test set up, mock the executor, observe what user_input gets passed". If cli.py resolves the skill via `Skill.load(installer.find(name))` or similar, the implementer patches that path instead. Keep the assertions on `executor.run.call_args.kwargs["user_input"]`.

### Step 3: Run tests to verify they fail

```bash
uv run pytest tests/test_cli.py::TestRunEmptyQuery -v
```

Expected: at least the "no query no default passes empty string" test fails because the current CLI errors out.

### Step 4: Implement

Modify the `run` command in cli.py:

```python
# OLD (drop):
if not user_input and not shell:
    typer.echo("Error: user_input is required unless --shell is specified", err=True)
    raise typer.Exit(1)

# NEW: after loading the skill, before invoking executor.run
if not user_input and not shell:
    default = skill.manifest.spec.default_query
    user_input = default if default is not None else ""
```

(Exact placement depends on where `skill` is loaded relative to where `user_input` is used. The implementer reads the existing flow and inserts the substitution at the right spot.)

Investigate and fix the "Error: 1" double-print. Likely cause: `typer.Exit(1)` raised in the run path is being printed AGAIN by an outer error handler somewhere. Find the duplication and remove the redundant print. (If the only fix is to suppress typer's default exit-code-print on its Exit exception, do that. Don't paper over by catching and re-raising.)

### Step 5: Run tests

```bash
uv run pytest tests/test_cli.py::TestRunEmptyQuery -v
uv run pytest                # full suite
```

Expected: 3 new tests pass; no regressions.

### Step 6: Commit (boundary 2)

```bash
git add launcher/zipsa/cli.py launcher/tests/test_cli.py
git commit -m "feat(cli): drop user_input hard-fail; substitute default_query

When 'zipsa run <skill>' is invoked with no query, the CLI now
substitutes spec.default_query if set, else passes the empty string
to the agent (which the runtime contract instructs to introduce
itself as 집사 and elicit the request).

Also fixes a double-printed 'Error: 1' that was appearing alongside
the real error message in run-failure cases."
```

---

## Task 3+5: Runtime contract + skills README docs

**Files:**
- Modify: `launcher/zipsa/system-prompts/runtime-contract.md`
- Modify: `skills/README.md`

### Step 1: Add the empty-user_query section to runtime-contract.md

Find an appropriate spot (after the existing "execution_context" section and BEFORE the phase model — empty user_query is a property of execution_context, conceptually).

Append:

```markdown
## Empty `user_query`

`user_query` in `<execution_context>` may be the empty string. That
happens when the user ran `zipsa run <skill>` with no arguments AND
the manifest didn't supply a `spec.default_query`. In that case,
your FIRST action in the FIRST phase must be:

1. Introduce yourself as **집사** (in the user's language — default
   Korean; switch to English if the user later replies in English).
2. State the skill name and what it does, using `spec.purpose` or
   the SKILL.md overview. If SKILL.md has an "Examples" section,
   lift 1–2 examples into your prompt so the user knows what
   shape of input you expect.
3. Use the user-interaction tool (per the "Interacting with the user"
   intent table) to elicit the user's specific request. Treat the
   response AS the `user_query` for the rest of the run.
4. Then proceed with the skill's normal phase 1 work using that
   response.

If the ask returns a `HITL_UNATTENDED` error, end the phase with
`status=failed` and `error.code="hitl_unattended"`. Don't try to
guess what the user wanted.

Skills with a non-empty `spec.default_query` never enter this flow —
the launcher substitutes the default before the phase runs.
```

### Step 2: Update skills/README.md

Find the `## 2. manifest.yaml Format` section's metadata block (around line 53-65). Add a note about `description` being user-facing, and add `default_query` to the spec example.

In the metadata block, change/clarify the `description` comment to:

```yaml
metadata:
  name: my-skill
  version: 0.1.0
  author: your-name
  description: |
    One-line user-facing intro. The launcher uses this when the user
    runs the skill with no query and there is no default_query — the
    agent reads it and introduces itself + the skill to the user.
    Write this for end users, not for fellow devs.
  tags: [category, tags]
```

In the spec block, add a `default_query` field after `model:` (or wherever fits):

```yaml
spec:
  purpose: |
    Detailed explanation ...

  instructions: ./SKILL.md

  # Optional: a sensible default the launcher substitutes when the user
  # runs 'zipsa run <name>' with no argument. Pick this for skills that
  # have a meaningful "default behavior" (e.g. daily-progress: "yesterday").
  # Omit for skills that always need a specific user request.
  default_query: "yesterday"

  model:
    name: claude-opus-4-7
```

### Step 3: No tests for docs

(The runtime contract is consumed by agents at runtime; the only verification is the manual smoke in Task 4. The skills README is for human authors.)

### Step 4: Commit (boundary 3)

```bash
git add launcher/zipsa/system-prompts/runtime-contract.md skills/README.md
git commit -m "docs: empty user_query contract + default_query in manifest format

Runtime contract gets a new section instructing the agent to
introduce itself as 집사, name and explain the skill, then HITL ask
for the user's specific request when user_query is empty.

skills/README clarifies metadata.description is user-facing (read by
the agent for the intro) and documents the new spec.default_query
field."
```

---

## Task 4: hello-world example — default_query + user-facing description

**Files:**
- Modify: `skills/hello-world/manifest.yaml`

### Step 1: Edit the manifest

```yaml
metadata:
  name: hello-world
  version: 0.1.2   # bumped: behavior change (description rewrite + default_query)
  author: westbrookai
  description: "Greets you and confirms zipsa is up and running. A smoke test."
  tags: [example, smoke-test]

spec:
  purpose: |
    Confirm the full zipsa pipeline is working: CLI → Docker image → agent execution.
    Outputs a greeting and identifies the active runtime and model.

  instructions: ./SKILL.md

  # No specific user input needed — the smoke test is "say hi".
  default_query: "Say hi and confirm zipsa is running."

  tools:
    builtin: []

  limits:
    max_turns: 3
    max_cost_usd: 0.10
    timeout_seconds: 30
```

### Step 2: Validate

```bash
uv run zipsa validate hello-world    # if it's installed
# Or, directly:
uv run python -c "from zipsa.core.skill import Skill; s = Skill.load('skills/hello-world'); print(s.name, s.manifest.spec.default_query)"
```

Expected: `hello-world Say hi and confirm zipsa is running.`

### Step 3: Commit (boundary 4)

```bash
git add skills/hello-world/manifest.yaml
git commit -m "chore(hello-world): default_query example + user-facing description

Demonstrates the new spec.default_query field. Now 'zipsa run hello-world'
without any argument runs the smoke test directly (no intro round)
because the skill knows what to do without further input.

description rewritten to be user-facing (the agent reads it as the
intro line when no default is set on OTHER skills)."
```

---

## Wrap-up

After all 4 commits:

- [ ] `git log --oneline ffaf34d..HEAD` — 4 commits in the order above + the docs commit at the head.
- [ ] `uv run pytest` from `launcher/` — green (~426 expected).
- [ ] Manual smoke (interactive — requires Docker):
  - `zipsa install --link skills/hello-world && zipsa run hello-world` → no prompt, runs to completion using default_query
  - `zipsa run weather` (no arg, TTY) → 집사 인사 + region ask
  - `zipsa run weather < /dev/null` → status=failed, error.code=hitl_unattended
- [ ] Push branch, open PR. Reference this plan and the spec.
