# Skills Development Guide

> **IMPORTANT:** Read [/CLAUDE.md](../CLAUDE.md) first for common development rules.

This guide covers Skills-specific development practices.

---

## Component Purpose

Skills are self-contained agent definitions. Each skill is a directory containing:
- **`manifest.yaml`**: What the agent is, what it can do, and what it costs
- **`SKILL.md`**: Instructions the agent follows during execution

Skills are executed via the released PyPI package:

```bash
uvx zipsa run ./my-skill "query"
```

**No local launcher installation needed.** Always use `uvx zipsa` when working in this directory.

---

## Skill Authoring Workflow

Skills are not code, so TDD applies differently. Follow this incremental verification cycle — each step must pass before moving to the next.

### Step 1: Define Intent (SKILL.md first)

Write `SKILL.md` before touching `manifest.yaml`. Think of this as writing the spec:
- What should the agent do, step by step?
- What tools does it need?
- What is the expected output?

If you can't write clear instructions, the skill isn't ready to implement.

### Step 2: Write manifest.yaml

Only after SKILL.md is clear, write the manifest. The manifest should reflect exactly what SKILL.md requires — no more, no less.

**Required fields:**
```yaml
apiVersion: zipsa.dev/v1alpha1
kind: Skill
metadata:
  name: my-skill          # must match directory name
  version: 0.1.0
  author: westbrookai
  description: |
    One clear sentence.
  tags: [category]

spec:
  purpose: |
    One paragraph: what this skill does and when to use it.
  instructions: ./SKILL.md
  # ... rest of spec
```

### Step 3: Validate (must pass)

```bash
uvx zipsa validate ./my-skill
```

Fix all errors before proceeding. Common failures:
- Missing required fields
- Invalid MCP server type (`stdio` or `http` only)
- Env vars listed in `mcp[].env` but not referenced in `SKILL.md`

### Step 4: Dry Run (verify config)

```bash
uvx zipsa run ./my-skill "test query" --dry-run
```

Check:
- MCP servers are configured correctly
- Tool allowlist matches what SKILL.md instructs
- Docker command looks correct
- Environment variables are resolved

### Step 5: Shell Debug (interactive)

```bash
uvx zipsa shell ./my-skill
```

Inside the container:
```bash
cat ~/.claude.json           # verify MCP config
claude -p "test query"       # test agent directly
ls /workspace                # check mounts
```

### Step 6: Full Execution

```bash
uvx zipsa run ./my-skill "actual query"
```

Review logs:
```
./my-skill/.zipsa/runs/YYYY-MM-DD_HHMMSS/
├── output.jsonl    # full execution log
├── summary.jsonl   # key events only
└── metadata.json   # cost, turns, duration
```

---

## manifest.yaml Guidelines

### Tool Allowlist — Minimal by Default

Only add tools the agent actually uses. Start with none and add as needed.

```yaml
tools:
  builtin:
    - Read          # only if SKILL.md says to read files
  mcp:
    - notion:notion-create-pages   # format: server-name:method-name
```

**Never add tools speculatively.** If SKILL.md doesn't instruct the agent to use it, don't allow it.

### Limits — Always Set

```yaml
limits:
  max_turns: 10        # start low, increase if needed
  max_cost_usd: 0.10   # protect against runaway costs
  timeout_seconds: 60
```

Start conservative. Observe `metadata.json` after execution and adjust.

### MCP Servers — Only What's Needed

```yaml
mcp:
  - name: filesystem
    type: stdio
    command: npx
    args:
      - "@modelcontextprotocol/server-filesystem@^0.5.0"
      - "/workspace"
    mount:
      host: "~/Documents"
      container: "/mnt/docs"
      mode: ro              # prefer read-only unless writes are required

  - name: notion
    type: http
    url: https://mcp.notion.com/mcp

  - name: github
    type: http
    url: https://api.githubcopilot.com/mcp
    headersHelper: "echo \"{\\\"Authorization\\\": \\\"Bearer $GITHUB_PERSONAL_ACCESS_TOKEN\\\"}\""
    env:
      - GITHUB_PERSONAL_ACCESS_TOKEN
```

---

## SKILL.md Writing Guidelines

SKILL.md is what the agent reads at runtime. Write for an AI executor, not a human reader.

### Principles

**Be specific, not general.**
```markdown
# Bad
Process the data and create a summary.

# Good
1. Read `/host-claude-projects` directory tree to find all project directories
2. For each project directory, read `projects/<id>/chat.jsonl` files created today
3. Summarize what was worked on in 2-3 sentences per project
```

**Specify tools explicitly.**
```markdown
Use `sessions:read_file` to read each chat.jsonl file.
Use `notion:notion-create-pages` to write the daily log entry.
Do NOT use any other tools.
```

**Define output format.**
```markdown
## Output Format
After execution, output:
```json
{
  "projects_processed": 3,
  "notion_page_url": "https://notion.so/..."
}
```

**Handle errors in the instructions.**
```markdown
## Error Handling
- If a project has no activity today: skip it silently
- If Notion API fails: retry once, then report the error and stop
- If no projects had activity: output a message saying so and exit cleanly
```

### Structure Template

```markdown
# Skill Name

## Purpose
One paragraph explaining what this skill does.

## Instructions
Step-by-step numbered list of what the agent must do.

## Tool Usage
Which tools to use for which steps.

## Error Handling
How to handle expected failure cases.

## Output Format
What the agent should output when done.

## Examples
Sample inputs and expected behaviors (optional but helpful).
```

---

## Directory Structure

```
skills/
├── hello-world/          # Reference example — start here
│   ├── manifest.yaml
│   └── SKILL.md
│
├── daily-progress/       # Production skill
│   ├── manifest.yaml
│   └── SKILL.md
│
└── my-new-skill/
    ├── manifest.yaml
    └── SKILL.md
```

**`hello-world`** is the canonical reference skill. When creating a new skill, use it as a template.

---

## Quality Checklist

Before committing a new or modified skill:

- [ ] `uvx zipsa validate ./my-skill` passes with no errors
- [ ] `uvx zipsa run ./my-skill "test" --dry-run` shows correct MCP config and tool allowlist
- [ ] `uvx zipsa run ./my-skill "test"` completes successfully
- [ ] `metadata.json` shows cost and turns within limits
- [ ] SKILL.md instructions are specific and complete
- [ ] Tools in manifest match tools referenced in SKILL.md
- [ ] No secrets or tokens committed
- [ ] Skill directory updated in `../skills/README.md`

---

## Common Patterns

### Pattern 1: Read-Only File Processing

```yaml
mcp:
  - name: filesystem
    type: stdio
    command: npx
    args: ["@modelcontextprotocol/server-filesystem@^0.5.0", "/workspace"]
    mount:
      host: "~/target-directory"
      container: "/workspace"
      mode: ro

tools:
  mcp: [filesystem:read_file, filesystem:list_directory]
```

### Pattern 2: External API (HTTP MCP)

```yaml
mcp:
  - name: myapi
    type: http
    url: https://api.example.com/mcp
    headersHelper: "echo \"{\\\"Authorization\\\": \\\"Bearer $MY_API_KEY\\\"}\""
    env:
      - MY_API_KEY

tools:
  mcp: [myapi:search, myapi:create]
```

### Pattern 3: Web Fetch (No MCP)

```yaml
tools:
  builtin:
    - WebFetch

network:
  allow:
    - api.example.com    # allowlist only what's needed
```

---

## Anti-Patterns

| Avoid | Instead |
|-------|---------|
| Generic instructions ("process the data") | Specific steps with file paths and tool names |
| Allowing all builtin tools | Allowlist only what SKILL.md instructs |
| No limits set | Always set `max_turns`, `max_cost_usd`, `timeout_seconds` |
| Committing `.zipsa/` directory | It's gitignored — execution artifacts stay local |
| Read-write mounts when read-only works | Default to `mode: ro` |

---

## Debugging Tips

### Skill doesn't execute as expected

1. Add more explicit instructions to SKILL.md
2. Shell in and test the agent directly: `uvx zipsa shell ./my-skill`
3. Check `output.jsonl` — find where the agent deviated

### Environment variable not found

```bash
# Export before running
export MY_API_KEY=...
uvx zipsa run ./my-skill "query"
```

Check `manifest.yaml` lists the var in `mcp[].env`.

### MCP server not connecting

```bash
uvx zipsa shell ./my-skill
# Inside:
cat ~/.claude.json    # verify server config
```

### Cost or turns exceeded

Check `metadata.json` for actual usage, then adjust limits in manifest.

---

## Resources

- **Manifest Spec:** [../launcher/zipsa/core/models.py](../launcher/zipsa/core/models.py) — Pydantic schemas
- **Skills README:** [./README.md](./README.md) — full format reference
- **MCP Servers:** https://github.com/modelcontextprotocol
- **zipsa on PyPI:** `uvx zipsa --help`
