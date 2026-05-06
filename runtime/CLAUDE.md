# Runtime Development Guide

> **IMPORTANT:** Read [/CLAUDE.md](../CLAUDE.md) first for common development rules.

This guide covers Runtime-specific development practices.

---

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

## TDD for Docker Images

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
```

---

## Quality Checklist

**Runtime-specific checks** (see [/CLAUDE.md](../CLAUDE.md) for common checks):

- [ ] Dockerfile passes hadolint
- [ ] All integration tests pass
- [ ] Image builds successfully
- [ ] Image size is acceptable (target: <500MB)
- [ ] All tools execute without errors
- [ ] Comments explain complex Dockerfile steps

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
