# Zipsa Launcher

Multi-runtime skill launcher for Claude Code, Codex, and Gemini CLI.

## Installation

```bash
pip install -e ".[dev]"
```
## Usage

```bash
# Run a skill
zipsa run weather "Seoul weather"

# With specific runtime
zipsa run weather "Seoul" --runtime claude

# Validate manifest
zipsa validate ../zipsa-skills/weather

# List skills
zipsa list 

# List runtimes
zipsa runtimes
```

## Development

See [Design Document](../docs/zipsa-python-design.md) for architecture details.
