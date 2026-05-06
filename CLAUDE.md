# Zipsa Development Guide

> **Common development rules for all components (runtime, launcher, skills)**

## Language Requirement

**All source code, comments, commit messages, and documentation must be written in English.**

This applies to:
- Source code and inline comments
- Git commit messages
- Documentation (README, guides, specs)
- Pull request descriptions
- Issue descriptions

---

## Development Principles

### Test-Driven Development (TDD) - Required

**All features must follow TDD:**

1. **Write test first**
   - Define expected behavior in test
   - Test should fail initially

2. **Review test before implementation**
   - Show test code for approval
   - Confirm: "This is what the feature should do"

3. **Implement feature**
   - Write minimal code to pass test

4. **Verify**
   - Run tests until all pass
   - Check coverage doesn't drop

**Example TDD cycle:**
```bash
# 1. Write test
# 2. Run test (should fail)
# 3. Implement feature
# 4. Run test (should pass)
# 5. Run all tests
```

Component-specific test commands:
- **Runtime**: Integration tests in `runtime/test-integration.sh`
- **Launcher**: `cd launcher && uv run pytest`
- **Skills**: Manifest validation with `zipsa validate`

---

## Git Workflow

### Branch Strategy

- `main`: Production-ready code only
- `feat/*`: Feature development branches
- `fix/*`: Bug fix branches
- `docs/*`: Documentation updates
- `refactor/*`: Code refactoring

**Never commit directly to main.**

### Work Process

1. Create branch from main: `feat/feature-name` or `fix/bug-name`
2. Write tests for new feature (TDD)
3. Implement feature
4. Verify all tests pass
5. Create PR to main
6. Merge after review

### Bug Fix Process

When fixing bugs:

1. **Reproduce first**
   - Add a test case that reproduces the exact error
   - Run the test - it must FAIL

2. **Fix the code**
   - Implement the fix

3. **Verify**
   - Run the test again - it must PASS
   - Run all related tests to check for regressions

4. **Commit**
   - Include both the test and the fix in the same commit

---

## Commit Convention

Follow semantic commit messages:

```
<type>: <short description>

<optional detailed explanation>
```

**Types:**
- `feat:` New features or enhancements
- `fix:` Bug fixes
- `test:` Test additions or modifications
- `docs:` Documentation only changes
- `refactor:` Code restructuring without behavior change
- `deps:` Dependency updates
- `chore:` Maintenance tasks (cleanup, build, etc.)

**Examples:**

```bash
# Feature
git commit -m "feat: add runtime config system

- Add RuntimeConfig Pydantic model
- Load config from ~/.zipsa/runtime-config.yaml
- Auto-inject environment variables based on config"

# Bug fix
git commit -m "fix: handle missing manifest file gracefully

Show clear error message when manifest.yaml not found
instead of crashing with FileNotFoundError"

# Documentation
git commit -m "docs: update README with runtime config example"

# Refactor
git commit -m "refactor: extract MCP config builder to separate method"
```

---

## Pull Request Process

### Before Creating PR

- [ ] All tests pass
- [ ] Code follows component-specific guidelines
- [ ] New features have tests
- [ ] Documentation updated if needed
- [ ] Commit messages follow convention
- [ ] No secrets or sensitive data in code

### PR Description

Include:
- **What**: Summary of changes
- **Why**: Motivation and context
- **How**: Implementation approach
- **Testing**: How you verified the changes

**Example:**
```markdown
## What
Add runtime configuration system for environment variable management

## Why
Replace hardcoded CLAUDE_CODE_OAUTH_TOKEN auto-injection with
explicit, user-controlled configuration

## How
- Created ~/.zipsa/runtime-config.yaml format
- Added RuntimeConfig Pydantic model
- Modified DockerExecutor to load and apply config

## Testing
- Added 5 new tests in TestRuntimeConfig
- All 69 tests passing
- Verified with manual execution
```

---

## Code Quality Standards

### General Principles

- **YAGNI** (You Aren't Gonna Need It): Don't add features until needed
- **DRY** (Don't Repeat Yourself): Avoid code duplication
- **KISS** (Keep It Simple, Stupid): Prefer simple solutions
- **Single Responsibility**: Each module/function does one thing well

### Error Handling

```python
# Good - specific exceptions
try:
    skill = Skill.load(path)
except FileNotFoundError:
    print(f"Error: Manifest not found: {path}")
    sys.exit(1)
except ValidationError as e:
    print(f"Error: Invalid manifest: {e}")
    sys.exit(1)

# Bad - generic exceptions
try:
    skill = Skill.load(path)
except Exception as e:
    print(f"Error: {e}")
```

### Security

- **Never hardcode secrets** in code
- **Never commit secrets** to git
- Add sensitive files to `.gitignore`:
  - `*.keys.json`
  - `.env`
  - `env.txt`
  - `servers.json` (if contains tokens)
- Use environment variables for secrets
- Use runtime config for credential management

---

## Component-Specific Guidelines

Each component has its own CLAUDE.md with specific development guides:

- **Runtime** ([runtime/CLAUDE.md](./runtime/CLAUDE.md)): Docker, hadolint, integration tests
- **Launcher** ([launcher/CLAUDE.md](./launcher/CLAUDE.md)): Python, uv, pytest, Pydantic
- **Skills** ([skills/README.md](./skills/README.md)): Manifest format, SKILL.md syntax

**IMPORTANT:** Always read the component-specific guide when working in that directory.

---

## Common Tasks

### Starting New Work

```bash
# 1. Update main
git checkout main
git pull origin main

# 2. Create feature branch
git checkout -b feat/my-feature

# 3. Read component-specific guide
cd runtime && cat CLAUDE.md
# or
cd launcher && cat CLAUDE.md
# or
cd skills && cat README.md
```

### Running Tests

```bash
# Runtime (Docker integration tests)
cd runtime
./test-integration.sh

# Launcher (Python tests)
cd launcher
uv run pytest
uv run pytest --cov=zipsa

# Skills (Manifest validation)
zipsa validate skills/my-skill
```

### Before Committing

- [ ] Tests pass
- [ ] Linter/formatter run (if configured)
- [ ] No debugging code left (console.log, print statements)
- [ ] Commit message follows convention
- [ ] Changes align with component guidelines

---

## Resources

- **Project Overview**: [README.md](./README.md)
- **Runtime Guide**: [runtime/CLAUDE.md](./runtime/CLAUDE.md)
- **Launcher Guide**: [launcher/CLAUDE.md](./launcher/CLAUDE.md)
- **Skills Guide**: [skills/README.md](./skills/README.md)

---

## Questions?

If you're unsure about:
- Which component to work in
- How to structure a feature
- Whether to use TDD for a specific change

**Ask first, then code.** It's better to clarify than to redo work.
