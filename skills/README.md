# Skills Library

> **IMPORTANT:** Read [/CLAUDE.md](../CLAUDE.md) first for common development rules.

Collection of reusable SKILL definitions for Zipsa runtime.

## What is a SKILL?

A SKILL is a self-contained agent definition that includes:
- **manifest.yaml**: Metadata, runtime requirements, tool allowlist
- **SKILL.md**: Instructions and behavioral guidelines for the agent
- **.zipsa/**: Runtime-generated configuration and logs

## Available Skills

### daily-progress

Summarize today's Claude Code sessions across all projects and log them to a Notion database.

**Features:**
- Reads Claude Code session logs from `~/.claude/projects`
- Summarizes work done per project
- Creates structured entries in Notion daily progress database
- Supports multiple MCP servers (filesystem, Notion, GitHub)

**Usage:**
```bash
zipsa run daily-progress "log today's progress"
```

**Requirements:**
- Notion workspace with daily progress database
- GitHub personal access token (for githubcopilot MCP)
- Claude Code OAuth token (auto-injected via runtime config)

📁 [View daily-progress](./daily-progress/)

---

## Creating a New SKILL

### 1. Directory Structure

```
skills/my-skill/
├── manifest.yaml          # Required: Skill definition
├── SKILL.md              # Required: Agent instructions
└── .zipsa/               # Auto-generated (gitignored)
    ├── .claude.json      # Runtime config
    └── runs/             # Execution logs
```

### 2. manifest.yaml Format

```yaml
apiVersion: zipsa.dev/v1alpha1
kind: Skill
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

spec:
  purpose: |
    Detailed explanation of the skill's purpose and goals.
    This helps the launcher understand when to use this skill.

  instructions: ./SKILL.md

  # Optional: a sensible default the launcher substitutes when the user
  # runs 'zipsa run <name>' with no argument. Pick this for skills that
  # have a meaningful "default behavior" (e.g. daily-progress: "yesterday").
  # Omit for skills that always need a specific user request.
  default_query: "yesterday"

  model:
    name: claude-opus-4-7  # Optional: specify model

  # MCP servers used by this skill
  mcp:
    # Local stdio server
    - name: filesystem
      type: stdio
      command: npx
      args:
        - "@modelcontextprotocol/server-filesystem@^0.5.0"
        - "/workspace"

    # Remote HTTP server
    - name: notion
      type: http
      url: https://mcp.notion.com/mcp

    # HTTP server with authentication
    - name: github
      type: http
      url: https://api.githubcopilot.com/mcp
      headersHelper: "echo \"{\\\"Authorization\\\": \\\"Bearer $GITHUB_TOKEN\\\"}\""
      env:
        - GITHUB_TOKEN

  # Tool whitelist
  # MCP tools are declared per-server inside each spec.mcp[].allowed_tools.
  # Built-in (Claude Code) tools go here:
  tools:
    builtin:
      - Read
      - Write
      - Bash(git:*)        # Bash requires explicit prefix or wildcard
      - Bash(*)            # explicit wildcard if you really need everything

  # Resource limits
  limits:
    max_turns: 30
    max_cost_usd: 0.50
    timeout_seconds: 60

  # Skill-specific configuration
  config:
    workspace_name: "My Workspace"
    db_name: "my-database"
```

### 3. SKILL.md Format

```markdown
# My Skill

## Purpose
Clear explanation of what this skill does and why.

## Instructions
Detailed step-by-step instructions for the agent:

1. First, do this
2. Then, do that
3. Finally, complete with this

## Tool Usage
Explain which tools to use and when:
- Use `filesystem:read_file` to read project files
- Use `notion:create-pages` to create database entries

## Error Handling
How to handle common errors:
- If file not found: skip and continue
- If API fails: retry once, then report error

## Output Format
Expected output format:
```json
{
  "summary": "Work completed",
  "entries": [...]
}
```

## Examples
Example inputs and expected behaviors.
```

### 4. MCP Server Configuration

#### Stdio Servers (Local Tools)

```yaml
mcp:
  - name: filesystem
    type: stdio
    command: npx
    args:
      - "@modelcontextprotocol/server-filesystem@^0.5.0"
      - "/workspace"
    mount:
      host: "~/Documents"        # Host path
      container: "/mnt/docs"     # Container path
      mode: ro                    # ro (read-only) or rw (read-write)
```

#### HTTP Servers (Remote APIs)

```yaml
mcp:
  - name: notion
    type: http
    url: https://mcp.notion.com/mcp
    env:
      - NOTION_TOKEN  # Auto-extracted and injected
```

#### HTTP with Dynamic Headers

```yaml
mcp:
  - name: github
    type: http
    url: https://api.githubcopilot.com/mcp
    headersHelper: "echo \"{\\\"Authorization\\\": \\\"Bearer $TOKEN\\\"}\""
    env:
      - GITHUB_PERSONAL_ACCESS_TOKEN
```

### 5. Environment Variables

**Skill-level env (MCP servers):**
```yaml
mcp:
  - name: my-server
    env:
      - MY_API_KEY
      - MY_TOKEN
```

**Runtime-level env (auto-inject):**
Configure in `~/.zipsa/runtime-config.yaml`:
```yaml
runtimes:
  claude:
    auto_inject_env:
      - CLAUDE_CODE_OAUTH_TOKEN
```

### 6. Tool Allowlist

The allowlist is **enforced** by a PreToolUse hook the launcher injects
into every container. Anything not on the list is denied at runtime, so
keep it tight.

**Built-in tools** (Claude Code's own tools):
```yaml
tools:
  builtin:
    - Read
    - Write
    - Grep
    - Glob
    - Bash(git:*)        # see "Bash command restriction" below
```

**MCP tools** are declared on each MCP server, not under `tools`:
```yaml
spec:
  mcp:
    - name: filesystem
      type: stdio
      command: npx
      args: ["@modelcontextprotocol/server-filesystem"]
      allowed_tools:
        - read_file
        - list_directory
    - name: notion
      type: http
      url: https://mcp.notion.com/mcp
      allowed_tools:
        - notion-create-pages
```

The hook sees them as `mcp__<server>__<tool>` (e.g. `mcp__notion__notion-create-pages`).

#### Bash command restriction

`Bash` is too powerful to whitelist as-is, so bare `Bash` is rejected at
manifest-load time. Declare what commands are actually needed:

| Form | Effect |
|---|---|
| `Bash(*)` | wildcard — every command allowed (use sparingly) |
| `Bash(git:*)` | only commands whose first word is `git` |
| `Bash` | rejected — must be one of the above |

Compound commands are checked per segment: `find . && rm /tmp/x` requires
both `Bash(find:*)` and `Bash(rm:*)`. Constructs that could circumvent
the prefix check (`bash -c`, `sh -c`, `eval`, `$(...)`, backticks) are
always denied.

### 7. Phased Execution (optional)

Skills can split work into phases, each with its own goal, tool list,
and resource budget. Each phase runs in its own container; the previous
phase's `next_phase_input` is passed to the next.

```yaml
spec:
  phases:
    - id: discover
      goal: Find session files modified today
      allowed_tools:
        - Bash(find:*)
        - Bash(touch:*)
      limits:
        max_turns: 4
        max_cost_usd: 0.10
        timeout_seconds: 30

    - id: analyze
      goal: Read each session and summarize per project
      allowed_tools:
        - mcp__sessions__read_file
      limits:
        max_turns: 25
        max_cost_usd: 1.00

  # Aggregate budget across all phases (run aborts if exceeded)
  limits:
    max_turns: 50
    max_cost_usd: 1.60
    timeout_seconds: 600
```

Without `phases:`, the skill runs once with the full `tools.builtin` +
per-server `allowed_tools` set. With phases, each phase's `allowed_tools`
overrides what the agent can call during that phase.

See `skills/daily-progress/` for a full multi-phase example.

---

## Testing a SKILL

### 1. Validate Manifest

```bash
zipsa validate skills/my-skill
```

### 2. Dry Run

```bash
zipsa run my-skill "test query" --dry-run
```

This shows:
- MCP configuration
- Tool allowlist
- Docker command that would be executed

### 3. Interactive Shell

```bash
zipsa shell skills/my-skill
```

Debug inside container:
```bash
# Check MCP config
cat ~/.claude.json

# Test claude command
claude -p "test query"

# Check mounts
ls -la /workspace
```

### 4. Full Execution

```bash
zipsa run my-skill "actual query"
```

Logs are saved to:
```
skills/my-skill/.zipsa/runs/YYYY-MM-DD_HHMMSS_ffffff/
├── output.jsonl      # Full execution log
├── summary.jsonl     # Important events only
└── metadata.json     # Metrics (cost, turns, duration)
```

---

## Best Practices

### Skill Design
- **Single purpose**: One skill, one clear task
- **Explicit instructions**: Don't assume agent knows domain knowledge
- **Error handling**: Document expected failures and recovery
- **Tool minimalism**: Only allowlist tools you actually need

### Manifest
- **Version properly**: Follow semver (0.1.0, 0.2.0, 1.0.0)
- **Descriptive metadata**: Help users understand when to use this skill
- **Set limits**: Prevent runaway costs with max_cost_usd and max_turns
- **Document config**: Explain skill-specific config fields

### SKILL.md
- **Be specific**: "Read file X and extract Y" not "process the data"
- **Show examples**: Include sample inputs and outputs
- **Tool guidance**: Explain which MCP tools to use for what
- **Output format**: Define expected output structure

### Testing
- **Test incrementally**: Validate → Dry run → Shell → Execute
- **Check logs**: Review output.jsonl to understand agent behavior
- **Monitor costs**: Check metadata.json for cost/turns
- **Iterate**: Refine instructions based on actual execution

---

## Common Patterns

### Pattern 1: File Processing

```yaml
# Read local files, process, write results
mcp:
  - name: filesystem
    type: stdio
    command: npx
    args: ["@modelcontextprotocol/server-filesystem", "/workspace"]
    allowed_tools: [read_file, write_file]

tools:
  builtin: [Read, Write]
```

### Pattern 2: API Integration

```yaml
# Call external API, process results
mcp:
  - name: myapi
    type: http
    url: https://api.example.com/mcp
    headersHelper: "echo \"{\\\"Authorization\\\": \\\"Bearer $API_KEY\\\"}\""
    env: [MY_API_KEY]
    allowed_tools: [search, create]
```

### Pattern 3: Multi-Source Data

```yaml
# Read from multiple sources, aggregate, write to destination
mcp:
  - name: filesystem
    type: stdio
    # ... filesystem config
    allowed_tools: [read_file]
  - name: notion
    type: http
    # ... notion config
    allowed_tools: [notion-search, notion-create-pages]
```

---

## Troubleshooting

### Skill not found
```bash
# Check skill directory structure
ls -la skills/my-skill/
# Should have manifest.yaml and SKILL.md
```

### Manifest validation failed
```bash
# Common issues:
# - YAML syntax error
# - Missing required fields (name, version, purpose, instructions)
# - Invalid MCP server type (must be "stdio" or "http")
```

### MCP server not working
```bash
# Debug in shell mode
zipsa shell my-skill

# Inside container:
cat ~/.claude.json  # Check MCP config
npx @modelcontextprotocol/server-filesystem --help  # Test server
```

### Tool not allowed
```bash
# Hook denial in agent output: "tool 'X' not in allowed list for this phase"
# Fix: add the tool to the current phase's allowed_tools (or tools.builtin
# for single-shot skills, or the relevant mcp server's allowed_tools).
```

### Bash command denied
```bash
# Hook denial: "command 'curl' not allowed; allowed: Bash(git:*), Bash(rm:*)"
# Fix: add Bash(curl:*) to the phase's allowed_tools, or use Bash(*) to
# permit any command (dangerous — only for trusted skills).
```

### Environment variable not set
```bash
# Warning: "MCP server 'X' requires environment variable 'Y'"
# Solution: export Y=value before running zipsa
```

---

## Contributing Skills

1. Create skill in `skills/your-skill/`
2. Test thoroughly (validate, dry-run, shell, execute)
3. Document in this README
4. Create PR with:
   - Skill directory
   - README.md update
   - Usage example

---

## Resources

- **Manifest Spec:** See [models.py](../launcher/zipsa/core/models.py) for Pydantic schemas
- **MCP Servers:** https://github.com/modelcontextprotocol
- **Claude Code:** https://docs.anthropic.com/claude-code
- **Launcher Docs:** [../launcher/README.md](../launcher/README.md)
