# SKILL Runtime Docker Image

## Project Purpose
Lightweight Docker runtime environment for executing SKILLs independently of specific agent runtimes (Claude Code, Codex, OpenClaw).

**Goals:**
- Runtime-agnostic SKILL execution
- Minimal footprint with essential dependencies
- Easy setup for end users

## Base Image
- **Debian Slim**: Chosen for glibc compatibility and package availability
- Supports: `npx` (Node.js), `uvx`/`pipx` (Python)

---

## Development Workflow

### TDD Process
**Docker images require integration-level TDD:**

1. **Write integration tests first**
   - Define expected container behavior
   - Test tools are installed and functional
   - Verify Claude Code/Codex/OpenClaw can execute

2. **Review test before Dockerfile**
   - Show test code for approval
   - Confirm: "This is what the container must do"

3. **Write Dockerfile**
   - Implement to pass tests

4. **Verify**
   - Run tests until all pass
   - Lint Dockerfile with `hadolint`

**Example test structure:**
```bash
# Test 1: Build succeeds
docker build -t skill-runtime:test .

# Test 2: Required tools exist
docker run --rm skill-runtime:test npx --version
docker run --rm skill-runtime:test uvx --version

# Test 3: Claude Code runs
docker run --rm skill-runtime:test claude --version
```

### Branch Strategy
- `main`: Production-ready images only
- `dev/*`: Feature development branches
- **Never commit directly to main**

### Work Process
1. Create branch from main: `dev/feature-name`
2. Write integration tests for new feature
3. Implement in Dockerfile
4. Verify all tests pass
5. Lint Dockerfile
6. Create PR to main
7. Merge after review

---

## Dockerfile Guidelines

### Multi-Stage Builds
Use multi-stage builds when possible to reduce final image size:
```dockerfile
FROM debian:slim as builder
# Build dependencies

FROM debian:slim
# Copy only runtime artifacts
```

### Version Pinning
**Always pin versions for reproducibility:**
```dockerfile
# Good
RUN apt-get install -y nodejs=18.20.2-1

# Bad - version can change
RUN apt-get install -y nodejs
```

### Layer Optimization
- Combine related `RUN` commands
- Put frequently changing layers at the bottom
- Clean up in the same layer:
```dockerfile
RUN apt-get update && \
    apt-get install -y package && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
```

### Comments
Add clear comments for complex operations:
```dockerfile
# Install Node.js 20.x LTS for npx support
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
```

### Secrets Management
- **Never hardcode secrets** in Dockerfile
- Use build arguments for build-time secrets
- Use environment variables for runtime secrets
- Add `env.txt`, `*.keys.json` to `.gitignore`

---

## Testing Strategy

### 1. Dockerfile Linting
```bash
hadolint Dockerfile
```

### 2. Build Test
```bash
docker build -t skill-runtime:latest .
```

### 3. Integration Tests
Test each runtime tool:
```bash
# Claude Code
docker run --rm skill-runtime:latest claude --version

# Codex (if installed)
docker run --rm skill-runtime:latest codex --version

# OpenClaw (if installed)
docker run --rm skill-runtime:latest openclaw --version

# MCP servers work
docker run --rm -v $(pwd)/servers.json:/app/servers.json \
  skill-runtime:latest claude --mcp-config /app/servers.json
```

---

## Commit Convention
Follow semantic commit messages:

- `feat:` New runtime features or tool additions
  - `feat: add OpenClaw support`
- `fix:` Build or runtime bug fixes
  - `fix: resolve npx PATH issue`
- `deps:` Dependency version updates
  - `deps: upgrade Node.js to 20.12.0`
- `docs:` Documentation only changes
  - `docs: update README with new examples`
- `test:` Test additions or modifications
  - `test: add integration test for uvx`
- `refactor:` Dockerfile restructuring without changing behavior
  - `refactor: use multi-stage build`

**Format:**
```
<type>: <short description>

<optional detailed explanation>
<optional breaking changes>
```

---

## Quality Checklist
Before committing:
- [ ] All integration tests pass
- [ ] Dockerfile passes hadolint
- [ ] Image builds successfully
- [ ] Image size is acceptable (target: <500MB)
- [ ] All tools execute without errors
- [ ] No secrets in code or Dockerfile
- [ ] Comments explain complex steps
- [ ] README.md is up to date

---

## Build & Test Commands

```bash
# Lint Dockerfile
hadolint Dockerfile

# Build image
docker build -t skill-runtime:latest .

# Check image size
docker images skill-runtime:latest

# Run integration tests
./test-integration.sh  # (to be created)

# Interactive test
docker run -it --rm skill-runtime:latest /bin/bash
```

---

## TODO (Future Enhancements)
- [ ] Security hardening (non-root user, minimal packages)
- [ ] Secret management strategy
- [ ] Multi-architecture builds (amd64, arm64)
- [ ] CI/CD pipeline integration
- [ ] Image size optimization (<400MB target)
- [ ] Vulnerability scanning (Trivy)

---

## Notes
- This is a **runtime environment**, not a development environment
- Prioritize stability over cutting-edge versions
- All changes must maintain backward compatibility
- Document breaking changes clearly in PR
