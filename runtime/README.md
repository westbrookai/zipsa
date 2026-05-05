# Zipsa Runtime

[![CI](https://github.com/westbrookai/zipsa-runtime/actions/workflows/ci.yml/badge.svg)](https://github.com/westbrookai/zipsa-runtime/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> Lightweight Docker runtime for executing SKILLs with Claude Code, Codex, and Gemini CLI

## Overview

This Docker image provides a ready-to-use environment for running SKILL-based agents without worrying about complex dependency installation. It includes:

- **Claude Code**: Official Anthropic CLI
- **Codex**: Alternative agent runtime
- **Gemini CLI**: Google's Gemini agent
- **MCP Support**: Pre-configured MCP server support (`npx`, `uvx`, `pipx`)

**Base Image:** Debian Slim (Multi-stage Build)
**Image Size:** ~1.9GB (54.9% reduction from initial 4.2GB)
**Use Case:** Runtime-agnostic SKILL execution

---

## Quick Start

### Pull and Run

```bash
# Pull from GitHub Container Registry
docker pull ghcr.io/westbrookai/zipsa-runtime:latest

# Run Claude Code interactively
docker run -it --rm \
  -v $(pwd):/workspace \
  -w /workspace \
  ghcr.io/westbrookai/zipsa-runtime:latest claude

# Run with MCP config
docker run -it --rm \
  -v $(pwd):/workspace \
  -v $(pwd)/servers.json:/app/servers.json \
  -w /workspace \
  ghcr.io/westbrookai/zipsa-runtime:latest claude --mcp-config /app/servers.json
```

### Build Locally

```bash
# Clone repository
git clone https://github.com/westbrookai/zipsa-runtime.git
cd zipsa-runtime

# Build image
docker build -t zipsa-runtime:latest .

# Verify installation
docker run --rm skill-runtime:latest claude --version
docker run --rm skill-runtime:latest npx --version
docker run --rm skill-runtime:latest uvx --version
```

---

## Usage Examples

### 1. Claude Code with Local Project

```bash
docker run -it --rm \
  -v $(pwd):/workspace \
  -w /workspace \
  ghcr.io/westbrookai/zipsa-runtime:latest claude
```

### 2. Codex Execution

```bash
docker run -it --rm \
  -v $(pwd):/workspace \
  -w /workspace \
  ghcr.io/westbrookai/zipsa-runtime:latest codex
```

### 3. Gemini CLI Execution

```bash
docker run -it --rm \
  -v $(pwd):/workspace \
  -w /workspace \
  ghcr.io/westbrookai/zipsa-runtime:latest gemini
```

### 4. MCP Server Configuration

Create `servers.json`:

```json
{
  "mcpServers": {
    "time": {
      "command": "uvx",
      "args": ["mcp-server-time", "--local-timezone=America/New_York"]
    },
    "fetch": {
      "command": "uvx",
      "args": ["mcp-server-fetch"]
    },
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
    }
  }
}
```

Run with MCP:

```bash
docker run -it --rm \
  -v $(pwd):/workspace \
  -v $(pwd)/servers.json:/app/servers.json \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -w /workspace \
  ghcr.io/westbrookai/zipsa-runtime:latest claude --mcp-config /app/servers.json
```

---

## Docker Compose Deployment

For easier setup, use the pre-configured Docker Compose example:

```bash
cd examples/minimal-agent
./setup.sh  # One-command setup
```

**Features:**
- Automated environment setup
- Pre-configured MCP servers
- Volume mounts for workspace
- Auto-restart on failures

See [examples/minimal-agent/README.md](./examples/minimal-agent/README.md) for detailed documentation.

---

## Configuration

### Environment Variables

Create `env.txt` (add to `.gitignore`):

```bash
export ANTHROPIC_API_KEY="your-api-key-here"
export GOOGLE_OAUTH_CLIENT_ID="your-client-id"
export GOOGLE_OAUTH_CLIENT_SECRET="your-client-secret"
```

Load and run:

```bash
# Load environment variables
source env.txt

# Run with env vars
docker run -it --rm \
  -e ANTHROPIC_API_KEY \
  -v $(pwd):/workspace \
  -w /workspace \
  ghcr.io/westbrookai/zipsa-runtime:latest claude
```

### Volume Mounts

| Mount Point | Purpose | Example |
|-------------|---------|---------|
| `/workspace` | Your project directory | `-v $(pwd):/workspace` |
| `/app/servers.json` | MCP server config | `-v $(pwd)/servers.json:/app/servers.json` |
| `/root/.claude` | Claude Code settings | `-v ~/.claude:/root/.claude` |

---

## Building from Source

### Prerequisites

- Docker 20.10+
- (Optional) hadolint for linting

### Build Steps

```bash
# 1. Clone repository
git clone https://github.com/westbrookai/zipsa-runtime.git
cd zipsa-runtime

# 2. (Optional) Lint Dockerfile
hadolint Dockerfile

# 3. Build image
docker build -t zipsa-runtime:latest .

# 4. Test build
docker run --rm skill-runtime:latest claude --version
docker run --rm skill-runtime:latest npx --version
docker run --rm skill-runtime:latest uvx --version

# 5. Check image size
docker images skill-runtime:latest
```

### Custom Build Arguments

```bash
# Specify Node.js version
docker build --build-arg NODE_VERSION=20.12.0 -t skill-runtime:custom .
```

---

## Troubleshooting

### Issue: "command not found"

**Problem:** Tool not in PATH

**Solution:**
```bash
# Verify installation
docker run --rm skill-runtime:latest which claude
docker run --rm skill-runtime:latest which npx
```

### Issue: MCP servers not loading

**Problem:** `servers.json` not mounted or invalid

**Solution:**
1. Check file exists: `cat servers.json`
2. Validate JSON: `cat servers.json | jq .`
3. Mount correctly: `-v $(pwd)/servers.json:/app/servers.json`

### Issue: Permission denied

**Problem:** File permissions in container

**Solution:**
```bash
# Run as current user
docker run --rm --user $(id -u):$(id -g) \
  -v $(pwd):/workspace \
  ghcr.io/westbrookai/zipsa-runtime:latest claude
```

### Issue: Large image size (>600MB)

**Problem:** Unnecessary dependencies

**Solution:**
- Check Dockerfile for cleanup commands
- Verify multi-stage builds
- Remove unused packages

---

## Development

See [CLAUDE.md](./CLAUDE.md) for development guidelines, TDD process, and contribution workflow.

### Quick Development Commands

```bash
# Run tests
./test-integration.sh

# Lint Dockerfile
hadolint Dockerfile

# Build for testing
docker build -t skill-runtime:test .

# Interactive debugging
docker run -it --rm skill-runtime:test /bin/bash
```

---

## Roadmap

- [x] Basic Debian Slim image
- [x] Claude Code support
- [x] Codex integration
- [x] Gemini CLI integration
- [x] Multi-architecture builds (amd64, arm64)
- [x] CI/CD pipeline
- [x] Multi-stage build optimization
- [x] Image size optimization (1.9GB, 54.9% reduction)
- [x] GitHub Container Registry publishing (ghcr.io/westbrookai/zipsa-runtime)
- [ ] Security hardening
- [ ] Automated security scanning
- [ ] Container signing and verification

---

## License

MIT License - see [LICENSE](./LICENSE) file for details

## Contributing

1. Read [CLAUDE.md](./CLAUDE.md)
2. Create feature branch: `dev/your-feature`
3. Write tests first (TDD)
4. Submit PR to `main`

---

## Support

- **Issues:** [GitHub Issues](https://github.com/westbrookai/zipsa-runtime/issues)
- **Discussions:** [GitHub Discussions](https://github.com/westbrookai/zipsa-runtime/discussions)
- **Documentation:** [CLAUDE.md](./CLAUDE.md)
- **Repository:** https://github.com/westbrookai/zipsa-runtime

---

**Note:** This is a runtime environment. For development, install tools locally or use a dedicated development container.
