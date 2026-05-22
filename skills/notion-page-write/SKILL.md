# notion-page-write Skill

Write one or more pre-formed pages to a Notion data source. Atomic
persistence skill — no formatting decisions, no summarization.

## Atomic skill contract

This is an **atomic** skill:

- One input: a JSON payload describing where to write and what to write.
- One output: a JSON artifact at `artifacts/notion-pages.json` listing
  the created/updated pages.
- NO ask, ask_once, confirm, choose, or any user prompts.
- NO knowledge of the data's domain (daily logs, tweets, etc.).
- Stateless across runs — no skill memory.

Orchestrator skills (or any other caller) build the payload, invoke
this skill via `mcp__zipsa__run_skill`, and read the artifact.

## Input

`user_query` MUST be a JSON object with this shape:

```json
{
  "data_source_id": "...",
  "pages": [
    {
      "properties": {"Name": "..."},
      "children": [
        {"type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "..."}}]}}
      ]
    }
  ],
  "upsert_by": null
}
```

Field rules:

- `data_source_id` (string, required): the target data source's ID
  (Notion's `data_source_id`, NOT `database_id` — that distinction
  matters for the Notion API and is the caller's responsibility).
- `pages` (array, required, non-empty): one entry per page to create.
  Each entry is a partial Notion page object — `properties` and
  optional `children` — that gets passed through to
  `notion-create-pages` without modification.
- `upsert_by` (string|null, optional): if non-null, a property name
  to match against when deciding update vs create. v0.1 IGNORES this
  field and always creates (upsert support is BACKLOG); reject the
  request with `error.code="upsert_unsupported"` if a non-null value
  is provided, so callers don't silently get duplicates.

If `user_query` is not valid JSON, or any required field is missing
or has the wrong type, fail with `error.code="invalid_payload"` and
a message naming the first problem found.

## Phase: persist

1. Parse `user_query` as JSON. Validate the shape per Input rules.

2. If `upsert_by` is non-null, fail per the contract above.

3. Call `mcp__notion__notion-create-pages` with:
   - `parent.data_source_id` = the `data_source_id` from input
   - `pages` = the `pages` array from input

   The Notion MCP returns a list of created page objects; capture
   each one's `id` and the page URL (Notion returns URL as `url` on
   the page object).

4. Write the artifact to `/home/agent/runs/current/artifacts/notion-pages.json`:

   ```json
   {
     "data_source_id": "...",
     "created": [
       {"page_id": "...", "url": "https://www.notion.so/..."}
     ]
   }
   ```

   The order of `created` matches the input `pages` order.

5. Emit the skill-envelope:

   ```json
   {
     "status": "ok",
     "phase": "persist",
     "result": {
       "page_count": <N>,
       "artifact": "notion-pages.json"
     },
     "state_updates": {},
     "user_facing_summary": "notion: wrote {page_count} page(s)"
   }
   ```

## Error handling

- Invalid JSON or shape → `error.code="invalid_payload"`,
  `user_facing_summary="invalid payload: <problem>"`.
- `upsert_by` non-null → `error.code="upsert_unsupported"`.
- Notion MCP call fails → `error.code="notion_write_failed"`,
  `user_facing_summary="notion write failed: <first 200 chars>"`.
- Partial success (some pages created, then a failure): write the
  artifact for what succeeded, fail with
  `error.code="notion_write_partial"` and include both counts in the
  summary. This makes the partial work recoverable.

## Constraints

- This skill does NOT call any user-facing MCP tool.
- This skill does NOT read or write skill memory.
- This skill does NOT format, summarize, or transform page content —
  it passes `properties`/`children` through to Notion verbatim.
- Output language is English.
