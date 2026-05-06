# Zipsa

> Runtime-agnostic SKILL execution system for AI agents

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Zipsa is a complete ecosystem for developing and executing SKILL-based AI agents. It provides isolated Docker runtime, multi-runtime launcher, and a skill library.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Zipsa Ecosystem                      │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────────┐   ┌──────────────┐   ┌───────────┐  │
│  │   Runtime    │   │   Launcher   │   │  Skills   │  │
│  │  (Docker)    │   │   (Python)   │   │  Library  │  │
│  │              │   │              │   │           │  │
│  │ • Claude Code│◄──│ • CLI Tool   │◄──│ • Manifests│ │
│  │ • Codex      │   │ • Executor   │   │ • SKILL.md│  │
│  │ • Gemini     │   │ • MCP Config │   │ • Examples│  │
│  └──────────────┘   └──────────────┘   └───────────┘  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## Components

### 1. Runtime ([runtime/](./runtime/))

Lightweight Docker image providing isolated execution environment:
- Pre-installed: Claude Code, Codex, Gemini CLI
- Multi-architecture (amd64, arm64)
- 1.9GB image size (54.9% reduction)

**Quick Start:**
```bash
docker pull ghcr.io/westbrookai/zipsa-runtime:latest
docker run -it --rm -v $(pwd):/workspace ghcr.io/westbrookai/zipsa-runtime:latest claude
```

📚 [Runtime Documentation](./runtime/README.md)

---

### 2. Launcher ([launcher/](./launcher/))

Python CLI for orchestrating skill execution:
- Multi-runtime support (claude, codex, gemini)
- SKILL manifest validation
- MCP server support (npx, uvx, pipx)
- Environment variable management
- Execution logging and metrics

**Quick Start:**
```bash
cd launcher
uv pip install -e ".[dev]"
zipsa run ../skills/daily-progress "summarize today's work"
```

📚 [Launcher Documentation](./launcher/README.md)

---

### 3. Skills ([skills/](./skills/))

Collection of reusable SKILL definitions:
- `daily-progress`: Summarize Claude Code sessions and log to Notion
- More coming soon...

**Quick Start:**
```bash
cd skills/daily-progress
cat manifest.yaml  # View skill definition
```

📚 [Skills Documentation](./skills/README.md)

---

## Quick Start (Full Stack)

1. **Pull Runtime:**
   ```bash
   docker pull ghcr.io/westbrookai/zipsa-runtime:latest
   ```

2. **Install Launcher:**
   ```bash
   cd launcher
   uv pip install -e ".[dev]"
   ```

3. **Configure Runtime:**
   ```bash
   cp launcher/runtime-config.yaml.example ~/.zipsa/runtime-config.yaml
   # Edit ~/.zipsa/runtime-config.yaml and add your tokens
   ```

4. **Run a Skill:**
   ```bash
   zipsa run skills/daily-progress "summarize today's work"
   ```

---

## Development

Each component has its own development guide:

- **Runtime:** [runtime/CLAUDE.md](./runtime/CLAUDE.md) - Dockerfile, hadolint, integration tests
- **Launcher:** [launcher/CLAUDE.md](./launcher/CLAUDE.md) - Python, uv, pytest, TDD
- **Skills:** [skills/README.md](./skills/README.md) - Manifest format, SKILL.md syntax

**Common Principles (All Components):**
- ✅ **TDD Required**: Write tests first
- ✅ **English Only**: All code, comments, docs in English
- ✅ **Conventional Commits**: `feat:`, `fix:`, `docs:`, etc.
- ✅ **Branch Strategy**: Feature branches, PR to main

---

## Repository Structure

```
zipsa/
├── runtime/              # Docker runtime environment
│   ├── Dockerfile
│   ├── CLAUDE.md        # Development guide
│   └── README.md        # Usage documentation
│
├── launcher/            # Python CLI orchestrator
│   ├── zipsa/           # Python package
│   ├── tests/
│   ├── CLAUDE.md        # Development guide
│   └── README.md        # Usage documentation
│
├── skills/              # SKILL library
│   ├── daily-progress/
│   └── README.md        # Authoring guide
│
├── examples/            # Docker Compose examples
│   └── minimal-agent/
│
└── docs/               # Design documents
    └── zipsa-python-design.md
```

---

## Use Cases

### Scenario 1: Daily Progress Logging
```bash
# Automatically summarize today's Claude Code work and log to Notion
zipsa run skills/daily-progress "log today's progress"
```

### Scenario 2: Custom SKILL Development
```bash
# Create new skill
cd skills
mkdir my-skill
# ... create manifest.yaml and SKILL.md
zipsa validate my-skill
zipsa run my-skill "test query"
```

### Scenario 3: Multi-Runtime Testing
```bash
# Test skill on different runtimes
zipsa run my-skill "query" --runtime claude
zipsa run my-skill "query" --runtime codex
zipsa run my-skill "query" --runtime gemini
```

---

## Roadmap

- [x] Runtime: Docker image with Claude Code, Codex, Gemini
- [x] Runtime: Multi-architecture builds (amd64, arm64)
- [x] Runtime: Image size optimization (1.9GB)
- [x] Launcher: Python CLI with manifest validation
- [x] Launcher: Runtime config system
- [x] Launcher: MCP server environment management
- [x] Launcher: Execution logging and metrics
- [x] Skills: daily-progress (Notion integration)
- [ ] Skills: More example skills
- [ ] Launcher: Skill dependency management
- [ ] Runtime: Security hardening (non-root user)
- [ ] Runtime: Container signing and verification

---

## Contributing

We welcome contributions! Please read the development guide for your target component:

1. Choose component: [runtime/](./runtime/CLAUDE.md), [launcher/](./launcher/CLAUDE.md), or [skills/](./skills/README.md)
2. Read the development guide
3. Create feature branch: `dev/your-feature`
4. Write tests first (TDD)
5. Submit PR to `main`

---

## License

MIT License - see [LICENSE](./LICENSE) file for details

---

## Support

- **Issues:** [GitHub Issues](https://github.com/westbrookai/zipsa/issues)
- **Documentation:**
  - Runtime: [runtime/README.md](./runtime/README.md)
  - Launcher: [launcher/README.md](./launcher/README.md)
  - Skills: [skills/README.md](./skills/README.md)
