# Zipsa Python Implementation Design

**Date:** 2026-05-04
**Version:** 1.0
**Status:** Draft

## Overview

Port zipsa.sh (Bash-based skill launcher) to Python with multi-runtime support. The Python implementation will orchestrate Docker containers running different agent runtimes (Claude Code, Codex, Gemini CLI) while maintaining the same manifest-based skill definition system.

### Goals

1. **Feature parity** with zipsa.sh
2. **Multi-runtime support** (Claude, Codex, Gemini)
3. **Type safety** (Pydantic models)
4. **Extensibility** (plugin-based runtimes)
5. **Better DX** (clear error messages, validation)

### Non-Goals

1. Managing MCP server processes (Claude/Codex/Gemini does this)
2. Runtime-specific abstractions (manifest uses MCP spec directly)
3. Phase 1: Server/API (CLI only)

---

## Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────┐
│  CLI (typer)                                    │
│  └── run, validate, list commands               │
└─────────────┬───────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────┐
│  Skill Loader                                   │
│  └── Parse manifest.yaml (Pydantic)             │
│  └── Load SKILL.md instructions                 │
│  └── Build MCP config (passthrough)             │
└─────────────┬───────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────┐
│  Docker Executor                                │
│  └── Build docker run command                   │
│  └── Mount volumes (workspace, MCP config)      │
│  └── Inject environment variables               │
└─────────────┬───────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────┐
│  Runtime Plugin (selected)                      │
│  ├── ClaudeRuntime                              │
│  ├── CodexRuntime                               │
│  └── GeminiRuntime                              │
│                                                  │
│  Responsibilities:                              │
│  └── Build runtime CLI command                  │
│  └── Parse runtime output                       │
└─────────────┬───────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────┐
│  Docker Container                               │
│  └── Runtime CLI (claude/codex/gemini)          │
│      └── Executes MCP servers                   │
│      └── Runs skill logic                       │
└─────────────────────────────────────────────────┘
```

### Directory Structure

```
zipsa/                          # Python package root
├── __init__.py
├── __main__.py                 # Entry point (python -m zipsa)
│
├── core/
│   ├── __init__.py
│   ├── models.py               # Pydantic manifest models
│   ├── skill.py                # Skill loader
│   └── executor.py             # Docker orchestrator
│
├── runtimes/                   # Runtime plugins
│   ├── __init__.py             # Registry
│   ├── base.py                 # Abstract base class
│   ├── claude.py               # Claude Code runtime
│   ├── codex.py                # Codex runtime (TBD)
│   └── gemini.py               # Gemini CLI runtime (TBD)
│
├── cli.py                      # Typer CLI app
├── config.py                   # Config management
└── utils.py                    # Helpers (path validation, etc.)

tests/
├── test_models.py
├── test_skill.py
├── test_executor.py
├── test_runtimes/
│   ├── test_claude.py
│   ├── test_codex.py
│   └── test_gemini.py
└── fixtures/
    └── manifests/
        ├── weather.yaml
        └── daily-progress.yaml
```

---

## Components

### 1. Manifest Models (models.py)

**Responsibility:** Type-safe manifest parsing and validation

```python
from pydantic import BaseModel, Field
from typing import Literal, Optional

class SkillMetadata(BaseModel):
    name: str
    version: str
    author: Optional[str] = None
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)

class MCPMount(BaseModel):
    host: str                      # ~/.claude/projects
    container: str                 # /host-claude-projects
    mode: Literal["ro", "rw"] = "ro"

class MCPServerStdio(BaseModel):
    name: str
    type: Literal["stdio"]
    command: str                   # npx, uvx, python, etc.
    args: list[str]                # ["-y", "@pkg/name", ...]
    mount: Optional[MCPMount] = None

class MCPServerHTTP(BaseModel):
    name: str
    type: Literal["http"]
    url: str
    connection: Optional[str] = None

MCPServer = MCPServerStdio | MCPServerHTTP

class SkillTools(BaseModel):
    builtin: list[str] = Field(default_factory=list)
    mcp: list[str] = Field(default_factory=list)

class SkillSpec(BaseModel):
    purpose: str
    instructions: str              # Path to SKILL.md
    tools: SkillTools = Field(default_factory=SkillTools)
    mcp: list[MCPServer] = Field(default_factory=list)
    # ... other fields

class SkillManifest(BaseModel):
    apiVersion: str
    kind: Literal["Skill"]
    metadata: SkillMetadata
    spec: SkillSpec
```

**Key Design Decisions:**
- Use Pydantic discriminated unions for `MCPServer` (stdio vs http)
- Validate paths, URLs at parse time
- No runtime/package abstraction (direct MCP spec)

### 2. Skill Loader (skill.py)

**Responsibility:** Load and prepare skills for execution

```python
class Skill:
    def __init__(self, manifest: SkillManifest, skill_dir: Path):
        self.manifest = manifest
        self.skill_dir = skill_dir

    @classmethod
    def load(cls, skill_path: str | Path) -> "Skill":
        """Load skill from directory or manifest path"""
        # Parse YAML
        # Validate with Pydantic
        # Return Skill instance

    def build_mcp_config(self) -> dict:
        """Generate MCP config JSON (passthrough)"""
        # Convert manifest.spec.mcp to Claude Code format
        # No transformation, just restructure

    def get_allowed_tools(self) -> str:
        """Build --allowedTools comma-separated string"""
        # builtin tools + mcp__server__method format
```

**Key Design Decisions:**
- Lazy load SKILL.md (read on first access)
- MCP config generation is pure passthrough (no abstraction)
- Tools list converts `server:method` to `mcp__server__method`

### 3. Runtime Plugin System (runtimes/)

**Responsibility:** Abstract different agent runtime CLIs

#### Base Class (runtimes/base.py)

```python
from abc import ABC, abstractmethod

class AgentRuntime(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Runtime identifier (claude, codex, gemini)"""
        pass

    @abstractmethod
    def build_command(
        self,
        skill_name: str,
        user_input: str,
        system_prompt: str,
        allowed_tools: str,
        mcp_config_path: Path,
        workspace: Path,
        env: dict[str, str],
    ) -> list[str]:
        """Build CLI command for this runtime"""
        pass

    @abstractmethod
    def parse_output(self, stream: Iterator[str]) -> Iterator[dict]:
        """Parse runtime-specific output into common format"""
        pass
```

#### Claude Runtime (runtimes/claude.py)

```python
class ClaudeRuntime(AgentRuntime):
    @property
    def name(self) -> str:
        return "claude"

    def build_command(self, ...) -> list[str]:
        return [
            "claude",
            "--print", user_input,
            "--append-system-prompt", system_prompt,
            "--allowedTools", allowed_tools,
            "--mcp-config", str(mcp_config_path),
            "--dangerously-skip-permissions",
            "--output-format=stream-json",
        ]

    def parse_output(self, stream: Iterator[str]) -> Iterator[dict]:
        """Parse Claude Code stream-json format"""
        for line in stream:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                yield {"type": "text", "content": line}
```

#### Codex Runtime (runtimes/codex.py)

```python
class CodexRuntime(AgentRuntime):
    @property
    def name(self) -> str:
        return "codex"

    def build_command(self, ...) -> list[str]:
        # TODO: Investigate actual Codex CLI flags
        return [
            "codex",
            "run",
            "--input", user_input,
            # ... other flags TBD
        ]

    def parse_output(self, stream: Iterator[str]) -> Iterator[dict]:
        # TODO: Implement Codex output parsing
        for line in stream:
            yield {"type": "raw", "content": line}
```

#### Gemini Runtime (runtimes/gemini.py)

```python
class GeminiRuntime(AgentRuntime):
    @property
    def name(self) -> str:
        return "gemini"

    def build_command(self, ...) -> list[str]:
        # TODO: Investigate actual Gemini CLI flags
        return [
            "gemini",
            "--prompt", user_input,
            # ... other flags TBD
        ]

    def parse_output(self, stream: Iterator[str]) -> Iterator[dict]:
        # TODO: Implement Gemini output parsing
        for line in stream:
            yield {"type": "raw", "content": line}
```

#### Runtime Registry (runtimes/__init__.py)

```python
_RUNTIMES: Dict[str, Type[AgentRuntime]] = {
    "claude": ClaudeRuntime,
    "codex": CodexRuntime,
    "gemini": GeminiRuntime,
}

def get_runtime(name: str) -> AgentRuntime:
    if name not in _RUNTIMES:
        raise ValueError(f"Unknown runtime: {name}")
    return _RUNTIMES[name]()

def list_runtimes() -> list[str]:
    return list(_RUNTIMES.keys())
```

**Key Design Decisions:**
- Plugin pattern for extensibility
- Each runtime handles its own CLI flags
- Common output format: `{"type": "...", "content": "..."}`
- Registry pattern for runtime discovery

### 4. Docker Executor (executor.py)

**Responsibility:** Orchestrate Docker container execution

```python
class DockerExecutor:
    def __init__(
        self,
        runtime: str = "claude",
        image: str = "ghcr.io/westbrookai/zipsa-runtime:latest",
        workspace: Path = Path.cwd(),
    ):
        self.runtime = get_runtime(runtime)
        self.image = image
        self.workspace = workspace

    def run(
        self,
        skill: Skill,
        user_input: str,
        env: dict[str, str] | None = None,
        dry_run: bool = False,
    ) -> Iterator[dict]:
        """Execute skill in Docker container"""

        # 1. Create temp MCP config file
        # 2. Build docker run command
        # 3. Execute subprocess
        # 4. Stream output through runtime parser
        # 5. Cleanup temp files

    def _build_docker_command(self, ...) -> list[str]:
        """Build full docker run command"""

        cmd = ["docker", "run", "--rm", "--name", ...]

        # Environment variables
        for key, value in env.items():
            cmd.extend(["-e", f"{key}={value}"])

        # Volume mounts
        cmd.extend([
            "-v", f"{workspace}:/workspace",
            "-v", f"{mcp_config_path}:/tmp/mcp.json:ro",
        ])

        # MCP stdio mounts (from manifest)
        for server in skill.manifest.spec.mcp:
            if server.type == "stdio" and server.mount:
                host = Path(server.mount.host).expanduser().resolve()
                container = server.mount.container
                mode = server.mount.mode
                cmd.extend(["-v", f"{host}:{container}:{mode}"])

        # Image
        cmd.append(self.image)

        # Runtime-specific command (from plugin)
        runtime_cmd = self.runtime.build_command(...)
        cmd.extend(runtime_cmd)

        return cmd
```

**Key Design Decisions:**
- Use `subprocess.Popen` for streaming output
- Temp MCP config file (cleanup in finally block)
- Runtime plugin generates the actual CLI command
- Path expansion (~ to home) for mounts

### 5. CLI (cli.py)

**Responsibility:** User-facing commands

```python
import typer

app = typer.Typer()

@app.command()
def run(
    skill_name: str,
    user_input: str,
    runtime: str = "claude",
    skills_dir: Path = Path("./skills"),
    dry_run: bool = False,
):
    """Run a skill with selected runtime"""

    # Validate runtime
    # Load skill
    # Setup environment
    # Execute
    # Stream output

@app.command()
def validate(skill_path: str):
    """Validate a skill manifest"""
    try:
        skill = Skill.load(skill_path)
        typer.echo(f"✓ {skill.name} is valid")
    except ValidationError as e:
        typer.echo(f"✗ Validation failed: {e}", err=True)
        raise typer.Exit(1)

@app.command()
def list(skills_dir: Path = Path("./skills")):
    """List available skills"""
    # Iterate skills directory
    # Load and display metadata

@app.command()
def runtimes():
    """List available agent runtimes"""
    for runtime in list_runtimes():
        typer.echo(runtime)
```

---

## Implementation Phases

### Phase 1: Core Infrastructure (Claude Runtime Only)

**Goal:** Replace zipsa.sh functionality completely

**Tasks:**
1. Project setup
   - `pyproject.toml` (dependencies: pydantic, typer, pyyaml)
   - Directory structure
   - Git setup

2. Models (`models.py`)
   - Pydantic schemas
   - Unit tests

3. Skill Loader (`skill.py`)
   - YAML parsing
   - MCP config generation
   - Unit tests

4. Claude Runtime (`runtimes/claude.py`)
   - CLI command builder
   - stream-json parser
   - Unit tests

5. Docker Executor (`executor.py`)
   - Docker orchestration
   - Volume mounting
   - Integration tests

6. CLI (`cli.py`)
   - `run`, `validate`, `list` commands
   - Integration tests

7. End-to-end testing
   - Test with existing skills (weather, daily-progress)
   - Compare output with zipsa.sh

**Success Criteria:**
- `zipsa run weather "Seoul"` produces same result as `./zipsa.sh weather "Seoul"`
- All existing manifests validate successfully
- DRY_RUN mode works

### Phase 2: Multi-Runtime Support

**Goal:** Support Codex and Gemini runtimes

**Tasks:**
1. Research Codex CLI
   - Document flags and options
   - Understand output format

2. Implement Codex Runtime
   - `runtimes/codex.py`
   - Output parser
   - Tests

3. Research Gemini CLI
   - Document flags and options
   - Understand output format

4. Implement Gemini Runtime
   - `runtimes/gemini.py`
   - Output parser
   - Tests

5. Runtime selection UX
   - `--runtime` flag
   - Default runtime config (~/.zipsa/config.yaml)

**Success Criteria:**
- `zipsa run weather "Seoul" --runtime codex` works
- `zipsa run weather "Seoul" --runtime gemini` works
- Output format is consistent across runtimes

### Phase 3: Advanced Features (Future)

**Deferred to later:**
- FastAPI server
- WebSocket streaming
- Parallel execution
- Result caching
- Better logging/debugging

---

## Testing Strategy

### Unit Tests

```python
# test_models.py
def test_manifest_validation():
    """Valid manifest should parse successfully"""
    data = {...}
    manifest = SkillManifest.model_validate(data)
    assert manifest.metadata.name == "weather"

def test_invalid_manifest():
    """Invalid manifest should raise ValidationError"""
    data = {"invalid": "data"}
    with pytest.raises(ValidationError):
        SkillManifest.model_validate(data)

# test_skill.py
def test_build_mcp_config():
    """MCP config should match Claude Code format"""
    skill = Skill.load("fixtures/weather")
    config = skill.build_mcp_config()
    assert "mcpServers" in config

# test_claude.py
def test_build_command():
    """Claude runtime should generate correct CLI"""
    runtime = ClaudeRuntime()
    cmd = runtime.build_command(...)
    assert cmd[0] == "claude"
    assert "--mcp-config" in cmd
```

### Integration Tests

```python
def test_dry_run_weather():
    """DRY_RUN should print config without executing"""
    result = subprocess.run(
        ["zipsa", "run", "weather", "Seoul", "--dry-run"],
        capture_output=True,
    )
    assert "mcpServers" in result.stdout

def test_execute_weather():
    """Actual execution should return weather data"""
    result = subprocess.run(
        ["zipsa", "run", "weather", "Seoul"],
        capture_output=True,
    )
    assert "°C" in result.stdout
```

### Test Fixtures

```
tests/fixtures/
├── manifests/
│   ├── minimal.yaml          # Bare minimum manifest
│   ├── with-mcp-stdio.yaml   # MCP stdio server
│   ├── with-mcp-http.yaml    # MCP HTTP server
│   └── invalid.yaml          # Should fail validation
└── skills/
    ├── test-skill/
    │   ├── manifest.yaml
    │   └── SKILL.md
```

---

## Migration Path

### From zipsa.sh to Python

**Step 1:** Parallel execution
- Keep zipsa.sh working
- Test Python implementation alongside

**Step 2:** Validation
- Run both on same skills
- Compare outputs
- Fix discrepancies

**Step 3:** Deprecation
- Mark zipsa.sh as deprecated
- Update documentation
- Provide migration guide

**Step 4:** Removal
- Archive zipsa.sh
- Python becomes canonical implementation

---

## Dependencies

### Required
```toml
[project]
dependencies = [
    "pydantic>=2.0",
    "typer>=0.9",
    "pyyaml>=6.0",
]
```

### Development
```toml
[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-cov>=4.0",
    "mypy>=1.0",
    "ruff>=0.1",
]
```

---

## Open Questions

1. **Codex/Gemini CLI flags**: Need to research actual syntax
2. **Output format standardization**: How much normalization needed?
3. **Error handling**: What should happen when Docker/runtime fails?
4. **Credential management**: Should we encrypt credentials.json?
5. **Config file**: Do we need ~/.zipsa/config.yaml for defaults?

---

## References

- Current implementation: `examples/minimal-agent/zipsa.sh`
- Manifest examples: `examples/minimal-agent/skills/*/manifest.yaml`
- MCP specification: https://modelcontextprotocol.io/
- Claude Code CLI: https://docs.anthropic.com/en/docs/claude-code
