# Zipsa Launcher

Multi-runtime skill launcher for Claude Code, Codex, and Gemini CLI.

## Installation

```bash
pip install -e ".[dev]"
```

## Configuration

### Runtime Configuration

Create `~/.zipsa/runtime-config.yaml` to configure runtime-specific settings:

```yaml
runtimes:
  claude:
    auto_inject_env:
      - CLAUDE_CODE_OAUTH_TOKEN
```

**How it works:**
- Only environment variables listed in `auto_inject_env` are automatically passed to the container
- If the config file doesn't exist or a runtime is not configured, no auto-injection occurs
- User-provided environment variables (via CLI) always take precedence
- If a listed env var is not set in the host environment, a warning is shown

Example file is provided at `runtime-config.yaml.example`.

## Usage

```bash
# Run a skill
zipsa run weather "Seoul weather"

# With specific runtime
zipsa run weather "Seoul" --runtime claude

# With environment variables (overrides auto-inject)
zipsa run weather "Seoul" --env CLAUDE_CODE_OAUTH_TOKEN=custom-token

# Validate manifest
zipsa validate ../zipsa-skills/weather

# List skills
zipsa list ../zipsa-skills

# List runtimes
zipsa runtimes
```

## Development

See [Design Document](../docs/zipsa-python-design.md) for architecture details.
