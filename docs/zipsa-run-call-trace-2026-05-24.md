# `zipsa run` — Call Trace (happy path)

> Date: 2026-05-24
> Scenario: `uvr run zipsa run weather "..."` — a **single-phase** skill,
> top-level run (not a child), no resume, runtime = claude (default),
> everything succeeds.
> Format: `file:function:line  — what it does`.
> `❌ / ⚠️` markers cross-reference the Claude-coupling points from
> `zipsa-launcher-runtime-dependency-2026-05-24.md`.

> **Key mental model:** `executor.run()` returns a **generator**. Nothing
> below step 9 actually executes until `render()` (step 19) starts pulling
> events. The whole pipeline is lazy and streaming, driven by the renderer.

---

## Stage 1 — CLI setup (`cli.py`)

```
1.  cli.py:main():939                          — console-script entry; builds the typer app
2.  cli.py:run():197                            — the `run` subcommand handler fires
3.  cli.py:run():256  _check_call_trace(name)   — reject cyclic / depth-capped child invocations
4.  cli.py:run():265  Skill.load(_resolve_skill_path(name))
                                                — resolve installed skill dir + load/validate manifest (Pydantic)
5.  cli.py:run():275  default_query substitution — empty input → "" (intro/HITL signal), else manifest default
6.  cli.py:run():287  find_resumable_run(...)   — scan runs/ for a recoverable failed run; none here → skip
7.  cli.py:run():314  parse --env KEY=value     — build env_dict
8.  cli.py:run():329  resolve_requires(...)     — spec.requires host-side prompts; weather has none → {}
9.  cli.py:run():344  DockerExecutor(runtime, image)
        └─ executor.py:__init__:48
           └─ :59  self.runtime = get_runtime("claude")
                   └─ runtimes/__init__.py:get_runtime:18  — registry lookup → ClaudeRuntime()   ✅ clean seam
10. cli.py:run():351  executor.run(skill, ...)  — returns a GENERATOR (lazy; not yet executed)
```

## Stage 2 — executor.run() pre-flight (`executor.py`)

```
11. executor.py:run():103                        — generator body begins on first .next() (driven by render)
12. :135  for server in spec.mcp: auto-pull env  — copy required MCP env vars from host os.environ
13. :146  self._ensure_oauth_credentials(skill, env)
              └─ executor.py:_ensure_oauth_credentials:1317 — mint/refresh oauth2 tokens for HTTP MCP servers
14. :150  self._get_image_env(self.image)
              └─ executor.py:_get_image_env:615   — cache image ENV (ZIPSA_RUNTIME_VERSION, CLAUDE_CODE_VERSION)  ⚠️
15. :153  skill_data_dir(...).mkdir             — ~/.zipsa/weather@<ver>/
16. :162  run_dir = .../runs/<timestamp>/       — per-run log dir; _ensure_run_artifacts_dir
17. :167  skill.build_claude_json(output_dir, container_workspace)
              └─ skill.py:build_claude_json:129  — write Claude's private .claude.json (mcpServers, onboarding) ❌ leak #1
18. :208  return self._execute_with_hitl(...)   — hand off to the HITL-wrapped generator
```

## Stage 3 — HITL wrap + dispatch (`executor.py:_execute_with_hitl:222`)

```
19. cli.py:run():370  render(_tee(output), mode) — STARTS iterating the generator → everything above+below now runs
              └─ renderer.py:render:93           — pulls events one at a time (see Stage 6)
20. executor.py:_execute_with_hitl:302  _detect_parent_mcp()
              └─ executor.py:_detect_parent_mcp:90 — top-level run → (None, None)
21. :311  HitlServer(...).start()               — host-side HTTP MCP server for human-in-the-loop tools
22. :327  skill.build_claude_json(..., hitl_port=...)
              └─ skill.py:build_claude_json:129  — REBUILD .claude.json, now injecting the `zipsa` MCP entry ❌ leak #1
23. :335  if skill.manifest.spec.phases:        — weather has NO phases → take single-phase branch (:383)
24. :385  _write_default_phase_allow_file_impl(...)  — write phase-allow.json for the PreToolUse hook
25. :386  docker_cmd = self._build_docker_command(...)   — see Stage 4
26. :394  new_state("main")                     — fresh LimitsState for token/cost accounting
27. :397  for event in self._execute_skill(docker_cmd, ...):   — see Stage 5
28. :401-414  inspect each event:               — etype=="assistant" → grab text; "system"/init → model + claude_code_version ❌ leak #2
```

## Stage 4 — build the docker command (`executor.py:_build_docker_command:1338`)

```
29. executor.py:_build_docker_command:1338      — assemble `docker run …` array; write env_file (secrets)
30. :1561  cp_preamble = "cp /.zipsa/.claude.json /home/agent/.claude.json && …"
                                                — stage Claude config into the container overlay FS ❌ leak #1
31. :1584  extra_dirs = [...]                    — stdio MCP mount paths (for Claude ListRoots) ⚠️
32. :1595  runtime_cmd = self.runtime.build_command(...)
              └─ runtimes/claude.py:build_command:30
                 └─ :52  ["claude","--print",input,"--append-system-prompt",sp,
                         "--allowedTools",…,"--output-format=stream-json","--verbose"]  ✅ via seam
33. :1607  cmd += ["bash","-c", f"{cp_preamble} && {shlex.join(runtime_cmd)}"]  — final container command
```

## Stage 5 — launch + stream (`executor.py:_execute_skill:659`)

```
34. executor.py:_execute_skill:659              — entered as a generator
35. :709  model = spec.model.name or "claude-opus-4-7"  — default model for pricing ⚠️
36. :712  agg_limits / phase_limits / new_state — limits bookkeeping setup
37. :773  process = subprocess.Popen(docker_cmd, stdout=PIPE, stderr=STDOUT, text=True)  — START container
38. :780  raw_stream = iter(process.stdout.readline, "")  — line iterator over container stdout
39. :784  yield from _stream_with_limits(raw_stream, f)   — stream + write runs/output.jsonl

    Inner loop — executor.py:_stream_with_limits:737 (one iteration per stdout line):
    40. :745  for event in self.runtime.parse_output([line]):
                  └─ runtimes/claude.py:parse_output:72 — JSON line → dict (RAW Claude schema) ⚠️ leak #2
    41. :747  if event["type"]=="result": total_cost_usd  — per-event cost peek ❌ leak #2
    42. :754  update_for_event(limits_state, event, model)
                  └─ limits.py:update_for_event:93 — sum message.usage tokens, dedupe by message id ❌ leak #2a
    43. :755  breach = check_limits(...)
                  └─ limits.py:check_limits:191 — turn/cost/time caps; None here (no breach)
    44. yield event                              — event flows UP to Stage 3 (:397) then to render

45. :789  process.wait()                         — reap the container after stdout EOF
46. :827  self._save_events(run_dir)             — write runs/events.jsonl (filtered)
47. :834  env_file.unlink()                      — delete secrets file
```

## Stage 6 — completion, summary, render, exit

```
48. executor.py:_execute_with_hitl:499  PhaseSummary(id="main", status, cost, turns)  — single-phase summary
49. :506  yield {"type":"zipsa_run_complete","status","exit_code","run_dir"}  — terminal event
50. :521  hitl_server.stop()                     — finally: tear down HITL MCP server
51. :531  build_summary(...) → write_summary(run_dir/"summary.json")
              └─ summary.py:build_summary:36 / write_summary:122  — persist per-run outcome (claude_version field) ⚠️

    Each event yielded above is consumed live by the renderer:
52. cli.py:_tee:363                              — passes events through; captures zipsa_run_complete.exit_code
53. renderer.py:render:93                        — main consume loop
        :105  event["type"]=="assistant" → message.content[].text  — track last text ❌ leak #2b
        :114  _format(event, …) → print(...)     — pretty/answer/json output to stdout
        :286  event_type=="zipsa_run_complete"   — render final status line

54. cli.py:run():375  optional shutil.copy(summary.json → --summary-to)
55. cli.py:run():388  raise typer.Exit(exit_code)  — process exits with the run's exit code
```

---

## One-line summary of the path

`cli.run` → `Skill.load` → `DockerExecutor(get_runtime)` → `executor.run`
(build_claude_json, run_dir) → `_execute_with_hitl` (HitlServer, rebuild
config, single-phase branch) → `_build_docker_command`
(`runtime.build_command`) → `_execute_skill` (`subprocess.Popen`) →
`_stream_with_limits` (`runtime.parse_output` → `update_for_event` /
`check_limits`) → `zipsa_run_complete` + `write_summary` → `render` →
`typer.Exit`.

## Where Claude-coupling sits on this path (see audit doc)

| Step | Site | Coupling |
|------|------|----------|
| 17, 22, 30 | `build_claude_json` + cp_preamble | ❌ #1 config |
| 32 | `runtime.build_command` | ✅ seam |
| 40 | `runtime.parse_output` (raw dicts) | ⚠️ seam exists but un-normalized |
| 41–42 | `result`/`total_cost_usd`, `update_for_event` | ❌ #2a accounting |
| 28 | system.init `model`/`claude_code_version` | ❌ #2 metadata |
| 53 | renderer `message.content[].text` | ❌ #2b output |
| 14, 35, 51 | image env, model default, `claude_version` | ⚠️ cosmetic/low |
