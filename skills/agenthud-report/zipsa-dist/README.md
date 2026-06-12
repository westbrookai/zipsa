# agenthud-report (exec format)

Single deterministic phase: run agenthud for a date, write the raw
JSON to `/out/agenthud-report.json`, return counts.

Docker mode needs two kinds of mounts (agenthud reads Claude session
logs and resolves each session's `cwd` to its `.git`):

```bash
zipsa exec skills/agenthud-report today \
  --mount ~/.claude/projects:/home/agent/.claude/projects \
  --mount <your-projects-root>      # e.g. ~/WestbrookAI
```

The first mount lands the session logs at the *container* user's
home (where agenthud looks); the second keeps your project roots at
their real host paths so `--with-git` can resolve each session's
`cwd` to its `.git`.

Input: `today` (default), `yesterday`, or `YYYY-MM-DD`.
0 sessions on the date is normal, not an error.

> The legacy `SKILL.md` / `manifest.yaml` in the parent directory are
> still live — four orchestrator skills compose the legacy version.
> They go away when those orchestrators migrate.
