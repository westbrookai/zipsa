# Artifact Echo Skill

Single purpose: read an artifact written by another skill and echo it back. End-to-end test for `mcp__zipsa__get_artifact`.

## Input format

The user query is exactly four whitespace-separated tokens:

```
<skill> <version> <run_id> <name>
```

Example:
```
weather 0.4.0 2026-05-21_130217_53516 weather.json
```

## Steps

1. Parse the four tokens from the user query. If the query doesn't have exactly four tokens, return an error in `user_facing_summary` and a non-ok status.

2. Call `mcp__zipsa__get_artifact` with those four tokens as the named arguments (`skill`, `version`, `run_id`, `name`).

3. The tool returns `{"name": ..., "size": ..., "content": ...}`. Put the entire response into `result` and a one-line summary into `user_facing_summary`.

## Failure handling

If `mcp__zipsa__get_artifact` raises (ARTIFACT_NOT_FOUND, ARTIFACT_BAD_NAME, ARTIFACT_TOO_LARGE, ARTIFACT_BAD_JSON), return:
- `status: "failed"`
- `error: {code: "<the error code prefix>", message: "<the rest of the error>"}`
- `user_facing_summary`: short human description.

## Constraints

- Use ONLY `mcp__zipsa__get_artifact`. No WebFetch, no Bash, no Read.
- Do not invent data. If the tool fails, report the failure verbatim.
- Be concise. No preamble.
