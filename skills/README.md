# Skills Library

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
    Brief description of what this skill does
  tags: [category, tags]

spec:
  purpose: |
    Detailed explanation of the skill's purpose and goals.
    This helps the launcher understand when to use this skill.

  instructions: ./SKILL.md

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
  tools:
    builtin:
      - Read
      - Write
    mcp:
      - filesystem:read_file
      - notion:notion-create-pages

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

**Builtin tools:**
```yaml
tools:
  builtin:
    - Read
    - Write
    - Bash
    - Grep
    - Glob
```

**MCP tools:**
```yaml
tools:
  mcp:
    - server-name:method-name
    - filesystem:read_file
    - notion:notion-create-pages
```

Format: `server-name:method-name` (converted to `mcp__server-name__method-name` internally)

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

tools:
  builtin: [Read, Write]
  mcp: [filesystem:read_file, filesystem:write_file]
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

tools:
  mcp: [myapi:search, myapi:create]
```

### Pattern 3: Multi-Source Data

```yaml
# Read from multiple sources, aggregate, write to destination
mcp:
  - name: filesystem
    type: stdio
    # ... filesystem config
  - name: notion
    type: http
    # ... notion config

tools:
  mcp:
    - filesystem:read_file
    - notion:notion-search
    - notion:notion-create-pages
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
# Error: "Tool X is not allowed"
# Add to tools.builtin or tools.mcp in manifest.yaml
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
