# Launcher Development Guide

> **IMPORTANT:** Read [/CLAUDE.md](../CLAUDE.md) first for common development rules.

This guide covers Launcher-specific development practices.

---

## Project Purpose

Python CLI tool for orchestrating SKILL execution across multiple runtimes (Claude Code, Codex, Gemini).

**Goals:**
- Runtime-agnostic execution
- Manifest validation
- Environment variable management
- Execution logging and metrics

## Technology Stack
- **Language:** Python 3.12+
- **Package Manager:** uv
- **Testing:** pytest with coverage
- **Validation:** Pydantic models
- **CLI:** Click framework

---

## TDD for Python

**Example TDD cycle with pytest:**

```bash
# 1. Write test
# tests/test_new_feature.py

# 2. Run test (should fail)
uv run pytest tests/test_new_feature.py -v

# 3. Implement feature
# zipsa/core/new_feature.py

# 4. Run test (should pass)
uv run pytest tests/test_new_feature.py -v

# 5. Run all tests
uv run pytest --cov=zipsa
```

**Coverage Requirements:**
- Minimum: 70% overall coverage
- New code: 80%+ coverage preferred
- Don't sacrifice test quality for coverage %

---

## Code Guidelines

### Project Structure
```
launcher/
├── zipsa/              # Main package
│   ├── __init__.py
│   ├── cli.py          # Click commands
│   ├── core/           # Core logic
│   │   ├── executor.py # Docker orchestration
│   │   ├── skill.py    # Skill loading
│   │   └── models.py   # Pydantic models
│   └── runtimes/       # Runtime plugins
│       ├── base.py
│       ├── claude.py
│       └── ...
├── tests/              # Test files
│   ├── test_cli.py
│   ├── test_executor.py
│   └── fixtures/       # Test data
└── pyproject.toml
```

### Pydantic Models
Use Pydantic for all configuration and validation:

```python
from pydantic import BaseModel, Field

class SkillMetadata(BaseModel):
    """Skill metadata with validation."""
    name: str
    version: str
    author: str | None = None
```

### Error Handling
```python
# Good - specific exceptions
try:
    skill = Skill.load(path)
except FileNotFoundError:
    print(f"Error: Manifest not found: {path}")
    sys.exit(1)

# Bad - generic exceptions
try:
    skill = Skill.load(path)
except Exception as e:
    print(f"Error: {e}")
```

---

## `spec.requires:` — per-user host-side config

When a skill needs host-side values that vary per user (project
directory paths, vault locations, etc.) AND the launcher needs them
*before the container starts* (e.g. to set mount flags), declare them
in `spec.requires:`.

**Manifest:**

```yaml
spec:
  requires:
    project_roots:
      type: "list[directory]"
      prompt: |
        Which directories contain your git projects?
        (one path per line, ~ is expanded)

  mounts:
    - source: requires.project_roots
      container_prefix: /projects/
      mode: ro
```

**Types (v1):** `string`, `directory`, `list[directory]`.

**Dynamic mount forms:**
- `source: requires.X` + `container: /path` — single directory at fixed container path
- `source: requires.X` + `container_prefix: /prefix/` — list expanded as `prefix/basename` per item
- `source: requires.X` + `preserve_host_path: true` — each value mounts at its own absolute host path inside the container. Use when downstream tools embed host paths (e.g. Claude session JSONL `cwd` for `agenthud --with-git`).

**Flow:** On first `zipsa run`, the launcher prompts the user inline,
validates each value, and saves to
`~/.zipsa/<skill>@<version>/requires.yaml`. Subsequent runs read the
saved file. Use `zipsa configure <skill>` to update values later.

**Use `spec.requires` for:** mount paths, env-file paths, anything the
launcher reads pre-container. NOT for values the agent uses at run
time (those still belong in skill memory via `ask_once`).

See spec for full details: `docs/superpowers/specs/2026-05-20-requires-config-design.md`.

---

## Testing Strategy

### Running Tests

```bash
# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/test_executor.py -v

# Run with coverage
uv run pytest --cov=zipsa --cov-report=term-missing

# Run only failed tests
uv run pytest --lf
```

### Test Structure

```python
class TestFeatureName:
    """Test suite for feature."""

    def test_basic_behavior(self):
        """Test basic expected behavior."""
        result = function(input)
        assert result == expected

    def test_edge_case(self):
        """Test edge case handling."""
        with pytest.raises(ValueError):
            function(invalid_input)

    def test_with_fixture(self, tmp_path):
        """Test using pytest fixture."""
        file_path = tmp_path / "test.yaml"
        # ... test implementation
```

### Coverage Requirements
- Minimum: 70% overall coverage
- New code: 80%+ coverage preferred
- Don't sacrifice test quality for coverage %

---

## Quality Checklist

**Launcher-specific checks** (see [/CLAUDE.md](../CLAUDE.md) for common checks):

- [ ] All tests pass: `uv run pytest`
- [ ] Coverage ≥ 70%: `uv run pytest --cov=zipsa`
- [ ] No linting errors (if configured)
- [ ] Pydantic models validate correctly
- [ ] CLI commands work as expected

---

## Common Tasks

### Adding a New Runtime

1. Create runtime plugin:
   ```python
   # zipsa/runtimes/newruntime.py
   from .base import RuntimeBase

   class NewRuntime(RuntimeBase):
       name = "newruntime"
       # ... implement methods
   ```

2. Register in `runtimes/__init__.py`

3. Write tests:
   ```python
   # tests/test_runtimes.py
   class TestNewRuntime:
       def test_runtime_name(self):
           runtime = NewRuntime()
           assert runtime.name == "newruntime"
   ```

### Adding a New CLI Command

1. Add command in `cli.py`:
   ```python
   @click.command()
   def mycommand():
       """Description of command."""
       # implementation
   ```

2. Add to CLI group

3. Write tests:
   ```python
   # tests/test_cli.py
   def test_mycommand():
       result = runner.invoke(cli, ["mycommand"])
       assert result.exit_code == 0
   ```

### Updating Pydantic Models

1. Write test with new field:
   ```python
   def test_new_field():
       data = {"name": "test", "new_field": "value"}
       model = MyModel.model_validate(data)
       assert model.new_field == "value"
   ```

2. Update model:
   ```python
   class MyModel(BaseModel):
       name: str
       new_field: str | None = None
   ```

3. Verify tests pass

---

## Development Commands

```bash
# Install dependencies
uv pip install -e ".[dev]"

# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=zipsa --cov-report=html
open htmlcov/index.html

# Format code (if configured)
uv run black zipsa/ tests/

# Type checking (if configured)
uv run mypy zipsa/

# Interactive Python with package
uv run python
>>> from zipsa.core.skill import Skill
>>> skill = Skill.load("../skills/daily-progress")
```

---

## Debugging Tips

### Dry Run Mode
```bash
# See what Docker command would be executed
zipsa run my-skill "query" --dry-run
```

### Interactive Shell in Container
```bash
# Debug inside container
zipsa shell ../skills/daily-progress
```

### Test Specific Scenario
```bash
# Run single test with verbose output
uv run pytest tests/test_executor.py::TestRuntimeConfig::test_auto_inject_env_from_config -vv
```

### Check Logs
```bash
# Execution logs are saved in ~/.zipsa/<skill>@<version>/runs/
cat ~/.zipsa/daily-progress@*/runs/*/output.jsonl
cat ~/.zipsa/daily-progress@*/runs/*/summary.json
```

---

## Troubleshooting

### Issue: Tests failing with import errors

**Solution:**
```bash
# Reinstall in development mode
uv pip install -e ".[dev]"
```

### Issue: Coverage too low

**Solution:**
- Focus on critical paths first
- Add tests for error cases
- Don't skip integration tests

### Issue: Pydantic validation error

**Solution:**
```bash
# Check model definition matches test data
# Use .model_validate() not direct instantiation
data = {"field": "value"}
model = MyModel.model_validate(data)  # Good
model = MyModel(field="value")        # Also works but less explicit
```

---

## Resources

- **Python Docs:** https://docs.python.org/3.12/
- **Pydantic:** https://docs.pydantic.dev/
- **pytest:** https://docs.pytest.org/
- **Click:** https://click.palletsprojects.com/
- **uv:** https://github.com/astral-sh/uv

---

## Notes

- This is a **CLI orchestrator**, not a runtime environment
- Focus on clean interfaces and testability
- Keep Docker logic isolated in executor
- Runtime plugins should be minimal and focused
