# bip-daily-x Redesign — agenthud Optional + Web-Sourced Inspiration

**Date:** 2026-05-21
**Skill:** `skills/bip-daily-x`
**Version bump:** `0.2.4 → 0.3.0`
**Branch:** `feat/bip-daily-x-rework`

## Motivation

The current bip-daily-x always runs `agenthud` to summarize the user's
Claude Code activity and uses that as the sole tweet source. Two
problems:

1. On days with little or no Claude Code work, the skill has nothing
   to draw from.
2. Users may want to tweet about broader topics (industry chatter,
   their general interests) without always anchoring on what they
   shipped that day.

This redesign keeps agenthud as an opt-in enrichment, and makes
public BIP chatter + user interest web search the always-on default
content source.

## Goals

- Make `agenthud` opt-in per run (user is asked, not forced).
- Always surface what other build-in-public tweets look like today
  (tone/structure insights — not verbatim copies).
- Always offer a web-searched summary of the user's interest topics
  for the draft to draw on.
- Keep tweet output **English**; keep all user-facing interaction
  **Korean**.
- Require explicit user confirmation before posting.
- Keep the existing OAuth 1.0a `post.py` and the vendored agenthud
  wrapper as-is.

## Non-goals

- Threads, replies, attachments — single tweet only (v0.1 constraint
  preserved).
- Auto-scheduling — manual `zipsa run` invocation only.
- Cross-skill state sharing.
- Replacing `agenthud` as the activity source (the wrapper at
  `scripts/agenthud` and pinned version `0.9.2` stay).

## Phase Flow

```
precheck → discover → interests → ask_agenthud
                                       ├─ yes → choose period (in same phase)
                                       │         → report (real agenthud call)
                                       └─ no  → report (no-op, returns null)
                                                      ↓
                                                    draft
                                                      ↓
                                                   review
                                                      ↓
                                                    post
```

Phases (8 total):

| Phase | Goal | Model | User-facing language |
|---|---|---|---|
| `precheck` | X creds check + ask_once for `voice` and `interests` + resolve initial target_date | Haiku 4.5 | Korean |
| `discover` | WebSearch BIP tweets; extract 3-5 tone/structure insights | Haiku 4.5 | Korean status only |
| `interests` | Show stored interests, offer override; WebSearch + summarize (English summary text) | Haiku 4.5 | Korean prompt |
| `ask_agenthud` | Ask whether to use agenthud; if yes, ask period | Haiku 4.5 | Korean menu |
| `report` | If `use_agenthud=true`, run agenthud + jq slicing. If false, no-op return `report=null` | Haiku 4.5 | none |
| `draft` | Write ONE English tweet ≤ 280 chars combining insights + interests_summary + (optional) report | Sonnet 4.6 | English tweet displayed |
| `review` | Korean conversation loop (≤5 iterations); final "X에 게시할까요?" prompt | Sonnet 4.6 | Korean |
| `post` | Run `scripts/post.py` with approved draft; return URL | Haiku 4.5 | Korean summary |

## Language Policy

- All agent reasoning, phase goals, field names: **English**.
- All user-facing strings (prompts, summaries, errors visible to
  user): **Korean**.
- The final tweet text: **English**.
- SKILL.md will state this rule explicitly at the top.

## Memory Keys (ask_once)

| Key | Asked when | Prompt (Korean) | Notes |
|---|---|---|---|
| `voice` | precheck, first run only | "1–2 문장으로 트윗 톤을 알려주세요." | Existing — unchanged |
| `interests` | precheck, first run only | "관심 주제 3-5개를 쉼표로 입력해주세요. 예: AI agents, MCP, build-in-public" | New |

`config.default_interests` (in manifest) is used only as the
**example** in the prompt. The persisted value comes from the user.

## Per-Phase Data Flow

```
precheck   → { voice, interests, target_date_default }
discover   → { ...prev, insights: ["…", "…", …] }
interests  → { ...prev, interests_summary: "…",
                         interests_used: ["…", …] }
ask_agenthud → { ...prev, use_agenthud: bool,
                          target_date?: "YYYY-MM-DD" }
report     → { ...prev, report: { projects:[…] } | null }
draft      → { ...prev, draft: "<English tweet ≤ 280 chars>" }
review     → { ...prev, draft, approved_for_post: true }
post       → result: { status, tweet_id, url, text }
```

## Conditional `report` Execution

The runtime contract does not (today) support declarative `when:`
phase conditions. We implement skip via short-circuit:

- `ask_agenthud` always sets `next_phase_input.use_agenthud`.
- `report` phase first action: read `use_agenthud`. If `false`,
  immediately emit `status=ok`, `result.report=null`, end phase
  (1 turn, ~$0.001).
- If `true`, follow the current `report` logic verbatim
  (agenthud → jq → slice).

## manifest.yaml Changes

### Phases (new and updated)

```yaml
phases:
  - id: precheck
    model: { name: claude-haiku-4-5-20251001 }
    allowed_tools: [Bash(python3:*)]
    limits: { max_turns: 6, max_cost_usd: 0.10, timeout_seconds: 90 }

  - id: discover                     # new
    model: { name: claude-haiku-4-5-20251001 }
    allowed_tools: [WebSearch]
    limits: { max_turns: 8, max_cost_usd: 0.10, timeout_seconds: 120 }

  - id: interests                    # new
    model: { name: claude-haiku-4-5-20251001 }
    allowed_tools: [WebSearch]
    limits: { max_turns: 8, max_cost_usd: 0.10, timeout_seconds: 180 }

  - id: ask_agenthud                 # new
    model: { name: claude-haiku-4-5-20251001 }
    allowed_tools: []
    limits: { max_turns: 4, max_cost_usd: 0.05, timeout_seconds: 600 }

  - id: report                       # unchanged tools; behaviour now conditional
    model: { name: claude-haiku-4-5-20251001 }
    allowed_tools:
      - Bash(/skill/scripts/agenthud:*)
      - Bash(echo:*)
      - Bash(jq:*)
      - Bash(wc:*)
      - Read
    limits: { max_turns: 14, max_cost_usd: 0.20, timeout_seconds: 240 }

  - id: draft
    allowed_tools: []
    limits: { max_turns: 3, max_cost_usd: 0.05, timeout_seconds: 60 }

  - id: review
    allowed_tools: []
    limits: { max_turns: 10, max_cost_usd: 0.20, timeout_seconds: 1800 }

  - id: post
    model: { name: claude-haiku-4-5-20251001 }
    allowed_tools: [Bash(python3:*)]
    limits: { max_turns: 4, max_cost_usd: 0.05, timeout_seconds: 60 }
```

### Aggregate limits

```yaml
limits:
  max_turns: 57          # 6 + 8 + 8 + 4 + 14 + 3 + 10 + 4
  max_cost_usd: 0.85
  timeout_seconds: 3150  # 90+120+180+600+240+60+1800+60
```

### Config (new keys + existing)

```yaml
config:
  default_target_date: today
  max_review_iterations: 5
  max_tweet_chars: 280
  agenthud_version: "0.9.2"
  discover_query: "#buildinpublic OR #buildinpublic AI"
  default_interests: ["AI agents", "MCP", "build-in-public"]
```

`default_interests` here is the **prompt example** only — the stored
user value lives in `ask_once` memory.

### state_schema (extended for documentation)

```yaml
state_schema:
  insights:
    type: list[string]
  interests_summary:
    type: string
  use_agenthud:
    type: bool
  target_date:
    type: string
  report:
    type: object
  draft:
    type: string
```

### Mounts / requires

Unchanged. `project_roots` remains required at first-run as today;
this lets agenthud's `--with-git` work when the user opts in.

## SKILL.md Changes

New top-level "Language Policy" section. New phase sections for
`discover`, `interests`, `ask_agenthud`. Updated `precheck` to also
ask_once for `interests`. Updated `report` to short-circuit when
`use_agenthud=false`. Updated `draft` to consume the new inputs.
Updated `review` to enforce Korean conversation + English tweet
text. Updated `post` to emit Korean user_facing_summary.

Key prompt texts (Korean):

- `interests` first-run ask_once: `"관심 주제 3-5개를 쉼표로 입력해주세요. 예: AI agents, MCP, build-in-public"`
- `interests` per-run confirmation: `"현재 저장된 관심사: [list]\n오늘은 이대로 검색할까요, 아니면 다른 주제로 갈까요? (다른 주제면 쉼표로 나열, 그대로면 엔터)"`
- `ask_agenthud` yes/no: `"오늘 작업한 Claude Code 내용도 트윗에 반영할까요? (y/N)"`
- `ask_agenthud` period menu: `"어떤 기간을 볼까요?\n1) today (오늘)\n2) yesterday (어제)\n3) 직접 입력 (YYYY-MM-DD)"`
- `review` per-iteration: `"이대로 갈까요? 수정 요청? (엔터=확정)"`
- `review` final: `"X에 게시할까요? (y/N)"`
- `post` success: `"게시 완료: <url>"`
- `post` failure: `"게시 실패: <reason>"`

## Error Handling

| Situation | Behaviour |
|---|---|
| X env vars missing | precheck `status=failed`, `error.code=x_credentials_missing` |
| WebSearch returns 0 results | Proceed with empty `insights` or `(검색 결과 없음)` summary |
| WebSearch API error | Retry once; on second failure, proceed with empty result and note in `user_facing_summary` |
| Malformed user input on interests override | Reprompt once; on second failure, fall back to stored default |
| Malformed period input | Reprompt once; on second failure, default to `today` |
| `use_agenthud=true` but agenthud returns 0 sessions | `report={projects:[]}`; draft falls back to web data |
| Review exceeds 5 iterations | Force y/N decision |
| User declines final post confirmation | `status=failed`, `error.code=user_declined` |

## Migration (0.2.4 → 0.3.0)

- Minor version bump (new phases, new behaviour, no breaking schema).
- Existing `~/.zipsa/bip-daily-x@0.2.4/requires.yaml` does **not**
  auto-migrate to the `@0.3.0` directory; users will be re-prompted
  for `project_roots` on first 0.3.0 run. Intentional — low burden,
  cleanly versioned per-skill state directory.
- `voice` ask_once memory key is preserved (same key name).
- `interests` ask_once memory key is new — asked on first 0.3.0 run.

## Testing Plan

Per the skills CLAUDE.md "skill authoring workflow":

1. `uvx zipsa validate skills/bip-daily-x` — must pass.
2. `uvx zipsa run skills/bip-daily-x "today" --dry-run` — verify
   mounts, env injection, tool allowlists per phase.
3. `uvx zipsa shell skills/bip-daily-x` — confirm `~/.claude.json`
   has WebSearch enabled in the right phases.
4. Live run with `agenthud=no` path: WebSearch only, English tweet
   produced, Korean review loop, final post.
5. Live run with `agenthud=yes` path including period menu choice
   `today` and `yesterday` and a custom ISO date.
6. Live run after declining the final post confirmation —
   `status=failed` with `error.code=user_declined`.

## Cost Estimates

| Scenario | Expected cost |
|---|---|
| agenthud=no, single pass | $0.10 – $0.25 |
| agenthud=yes, single pass | $0.25 – $0.50 |
| agenthud=yes, 5 review iterations | $0.50 – $0.85 (hits aggregate cap) |

The `max_cost_usd: 0.85` aggregate cap is the safety net.

## Deliverables

- `skills/bip-daily-x/manifest.yaml` — updated phases, config,
  state_schema, aggregate limits, version bump.
- `skills/bip-daily-x/SKILL.md` — language policy header, new
  phase sections, updated existing sections.
- No changes to `skills/bip-daily-x/scripts/*` — `agenthud` wrapper
  and `post.py` stay as-is.
- `skills/README.md` — one-line update if the user-visible behaviour
  description there needs adjusting.
- This design doc + a follow-up implementation plan under
  `docs/superpowers/plans/`.
