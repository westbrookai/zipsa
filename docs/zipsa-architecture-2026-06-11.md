# Zipsa Architecture — Hybrid Phases (2026-06-11)

> ⚠️ **SUPERSEDED (2026-06-12).** 이 문서의 설계 (pyproject.toml 메타,
> envelope contract, zipsa.hitl, Python-중심 phase) 는 Phase 0 reset 으로
> 대체됨. 현행 contract 는 [skills/AUTHORING.md](../skills/AUTHORING.md),
> 구현은 PR #109/#110/#111 (zipsa exec). 이 문서는 설계 이력으로만 보존.

> 2026-05-15 [rethink doc](./zipsa-rethink-2026-05-15.md) 후속.
> Rethink 는 *방향* 만 가리켰고 (B' = "Python authoring, Python runtime"), 이
> 문서는 user 와의 대화에서 정제된 *형태* — **Hybrid Phases** — 를 spec
> 으로 못 박는다.

## 1. 무엇이 바뀌었나 (rethink → 이 spec)

**Rethink doc 의 B':** LLM 은 authoring 만, runtime 은 전부 Python.

**이 spec 의 정제된 B':** LLM 도 phase 의 일부 도구로 쓰임. 단,

> 결정론적 단계는 Python 으로, 추론이 필요한 단계는 LLM 으로.
> 둘이 **peer** 로 같은 skill 안에서 섞인다.

→ 한 skill 이 `.py` (Python phase) 와 `.md` (LLM phase) 를 모두 가질 수
있고, 작가가 step 단위로 결정론 / 비결정론을 선택한다.

이게 옛 zipsa (전부 LLM phase) 와 순수 B' (전부 Python) 사이의
**옳은 절충점**.

## 2. 어디서 출발했나

### Rethink 의 핵심 통찰

> 95% 결정론적 작업에 LLM 을 orchestrator 로 씌웠고, 그 mismatch 를
> 메꾸느라 manifest / SKILL.md / hook / phase / contract 라는 거대한
> 보호 장치를 매주 만들었다.

옛 모델 (envelope-based, LLM-orchestrator) 에서 한 skill 의 모든 phase
는 LLM 호출이고, phase 사이 인터페이스 = envelope JSON. 결정론
작업까지 LLM 으로 wrap 한 결과:

- `daily-progress` 매 실행 $0.66, 평균 22 turn, 1-2 분
- 같은 작업 결정론 Python: 30 줄, ms, $0.005

이 mismatch 를 메꾸느라 우리가 만든 보호 장치:
`runtime-contract.md`, envelope schema, `parse_envelope_strict`,
`PreToolUse hook`, `phase-allow.json`, multi-runtime plugin,
`spec.children` allowlist, ...

→ **이 보호 장치 대부분이 결정론 부분에서 불필요**. LLM phase 에만 적용
되면 됨. 결정론 phase 는 그냥 Python 함수.

### 사용자 정체성 (2026-06-11)

| 차원 | 답 | 함의 |
|---|---|---|
| Identity | Production-grade | distribution / versioning / install 진지하게 |
| 작성자 | 파워유저 (Python 가능) | `.py` 코드 editable. docstring 최소화 OK |
| Authoring | zipsa 가 Claude Code 부려서 도움 | skill-builder skill 아닌 **`zipsa create` CLI 명령** + Claude Code wrap |

→ Production: API stability, install/upgrade, doc 다 진지하게.
→ 파워유저: Python 코드 노출 OK, `.py` 가 시각적으로 자연스러움.
→ Authoring: zipsa 는 templating + sandbox + iteration 만. **Claude Code 가
   실제 코드 생성 엔진**. 우리가 skill-builder 라는 별 skill 만들 필요 없음.

## 3. Skill 의 새 모양

```
skills/morning-notion-log/
├── SKILL.md                  ← 작가 source (자연어 intent)
└── zipsa-dist/
    ├── pyproject.toml        ← deps + [tool.zipsa] config
    ├── 1.preflight.py        ← Python phase (첫 phase 는 .py 권장)
    ├── 2.llm-decide.md       ← LLM phase (envelope-aware)
    ├── 3.1.fetch-from-db.py  ← branch sibling
    ├── 3.2.fetch-from-web.py ← branch sibling
    └── 4.persist.py          ← 마지막 phase
```

**상위 두 file:**
- `SKILL.md` — 작가의 자연어 intent. 사람이 읽음. zipsa runtime 은 안
  봄. `zipsa list` 의 description 은 pyproject 가 source.
- `zipsa-dist/` — "compiled artifact" 자리. runtime 이 보는 모든 것.

`zipsa-dist/` 안 ([Cargo `target/`](https://doc.rust-lang.org/cargo/guide/build-cache.html) /
[webpack `dist/`](https://webpack.js.org/configuration/output/) convention):

- `pyproject.toml` — Python 표준 + `[tool.zipsa]` 섹션
- `<dotted-int>.<id>.{py,md}` — phase 파일들

### 파일 명명 규칙

- prefix `<dotted-int>` = phase id (`1`, `2`, `3.1`, `3.2`, `10`, ...)
- 중간 = phase 이름 (식별 가독성용)
- 확장자 = phase 종류 (`.py` = Python, `.md` = LLM)
- Sort key = dotted-int tuple → `1` < `2` < `3.1` < `3.2` < `4` < `10`

### Discovery 규칙

`pyproject.toml` 에 phase 목록 안 둠. **파일이 곧 선언**. runtime 이
디렉토리 listing → prefix parse → sort → phase 목록.

```python
# 의사 코드
def discover_phases(dist_dir):
    phases = []
    for path in dist_dir.glob("*.{py,md}"):
        m = re.match(r"^([\d.]+)\.(.+)\.(py|md)$", path.name)
        if m:
            phases.append((parse_dotted_int(m[1]), m[2], m[3], path))
    return sorted(phases, key=lambda p: p[0])
```

### Mixing rules

- **첫 phase 는 `.py` 권장**. 이유: preflight (auth / config / env 검증) 은
  결정론적이고, LLM 비용 들기 전에 환경을 검증해야 함. **예외**: authoring
  도구 (예: built-in `skill-builder`) 처럼 본질이 LLM 작업이고 사전 검증할
  decisional 환경이 없는 경우 `.md` 로 시작해도 됨. Production 스킬에는
  강력 권장.
- 이후는 자유. `.py` 와 `.md` 임의로 섞임.
- Sub-phase (`3.1`, `3.2`) = **조건 분기** (siblings 중 guard 통과한 하나만
  실행). 세부는 §6 Branching.

## 4. Phase Types

### 4.1 Python phase (`.py`)

```python
"""
1.preflight.py — Notion 인증과 DB resolve.
"""

def should_run(prev: dict) -> bool:
    """Branching 시만 정의. 없으면 default True."""
    return True

def run(ctx: dict, prev: dict) -> dict:
    """
    Args:
        ctx: skill 메타 (name, version, user_query, env, run_id, ...)
        prev: 이전 phase 의 return value. 첫 phase 면 {}.

    Returns:
        dict — 다음 phase 의 prev 로 주입됨.
    """
    notion_token = os.environ["NOTION_TOKEN"]
    notion = Client(auth=notion_token)
    db_id = resolve_data_source(notion, ctx["user_query"])
    return {"db_id": db_id, "user_query": ctx["user_query"]}
```

**계약:**
- `run(ctx, prev) -> dict` 가 entry. zipsa runtime 이 호출.
- 정상 종료 = return value, 실패 = exception (stack trace 가 log 에).
- `ctx` 는 skill 메타 + user_query + env. **read-only.**
- `prev` 는 직전 phase 의 return. branching 시 sibling 들 중 실행된
  하나의 return.
- side effect (file I/O, DB write, subprocess) 자유.
- LLM 호출이 필요하면 phase 내부에 한 줄: `anthropic.messages.create(...)`.

### 4.2 LLM phase (`.md`)

옛 `instruction.md` 와 똑같다 (PR #95 의 strict envelope contract).
**단, skill 전체가 아닌 `.md` phase 단위로만 적용.**

```markdown
---
guard: prev.source == "db"   # optional, branching 시만
---

# 데이터 의도 분류

[envelope-aware 지시 — agent 가 호출 결과를 envelope 의 result /
next_phase_input / user_facing_summary 로 emit]

## What to put in result

- `intent`: "fetch" | "post" | "summarize"
- `confidence`: 0.0 ~ 1.0

## What to put in user_facing_summary

한 줄로 사용자에게 알릴 내용.
```

**Envelope 계약:** `{status, phase, result, state_updates,
next_phase_input, user_facing_summary, error}` — `core/envelope.py` 의
`parse_envelope_strict` 그대로 작동.

**LLM phase 의 default 도구:**
- 기존 HitlServer 의 MCP tools (`mcp__zipsa__ask`, `confirm`, `choose`,
  `ask_once`, `recall`, `remember`, `get_artifact`)
- `allowed_tools` 는 pyproject 의 phase config 에서 declare.

## 5. State Passing

`runs/<id>/phases/<n>-<id>/state.json` 이 phase 간 공통 ground. 어떤
조합이든 동작:

| 전이 | 메커니즘 |
|---|---|
| Python → Python | return value → 다음 phase 의 `prev` 인자 |
| Python → LLM | return value → state.json → 다음 LLM phase 의 `execution_context.previous_phase_output` |
| LLM → Python | envelope 의 `next_phase_input` → state.json → 다음 Python phase 의 `prev` 인자 |
| LLM → LLM | envelope 의 `next_phase_input` (현 작동 그대로) |

기존 `core/phase_state.py` + `core/executor.py` 의 state 읽기/쓰기 로직
거의 그대로 재사용. 새 코드는 Python phase 만 추가하면 됨.

## 6. Branching

`3.1.fetch-from-db.py` 와 `3.2.fetch-from-web.py` 처럼 같은 dotted level
의 sibling 들은 **조건 분기 (XOR)**.

- 각 sibling 에 guard 정의:
  - Python phase: 모듈에 `should_run(prev: dict) -> bool` 함수. 없으면
    default `True`.
  - LLM phase: frontmatter 의 `guard: <python expression>`. expression
    은 `prev` 변수 접근 가능. 없으면 default `True`.

- Runtime 동작:
  1. 같은 dotted level 의 sibling 들 enumerate
  2. 각 sibling 의 guard 평가
  3. **정확히 하나가 True** → 그 sibling 실행
  4. 전부 False → error `BRANCH_NONE_MATCHED`
  5. 둘 이상 True → error `BRANCH_AMBIGUOUS`

- guard 실패 (예: Python 함수가 exception) → fail-safe 로 False 취급.

**왜 XOR 만? AND (모두 실행) 또는 OR (임의 다중) 은?**
- AND = parallel/sequential 실행 → state.json 합치는 정책 필요. 복잡.
- OR = 작가 의도 모호. 어느 게 다음 phase 의 prev 가 됨?

→ XOR (정확히 하나) 만 first-class 지원. parallel 이 필요해지면 future.

## 7. zipsa Python helpers

Python phase 작가의 boilerplate 를 줄이는 두 모듈. 필수 아님. 작가가
SDK 직접 써도 됨.

### 7.1 `zipsa.hitl`

```python
from zipsa import hitl

city = hitl.ask("어느 도시?")
ok = hitl.confirm("정말 진행?", default=True)
choice = hitl.choose("어느 모드?", ["fast", "slow"])
city = hitl.ask_once("default_city", "default city?")
```

- HitlServer 의 client-side Python wrapper. 옛 MCP tool 과 같은 backend.
- CLI 모드면 stdin/stdout, web 모드면 modal — 라우팅은 server 가 알아서.
- Non-interactive 모드면 `HitlUnattended` 예외.

### 7.2 `zipsa.llm`

```python
from zipsa import llm
from pydantic import BaseModel

class Summary(BaseModel):
    headline: str
    key_points: list[str]
    minutes_spent: int

s: Summary = llm.ask(
    prompt=f"Summarize:\n{text}",
    schema=Summary,
    model="claude-haiku-4-5",
)
```

- anthropic SDK 위의 얇은 wrapper.
- credential 자동 주입 (`ANTHROPIC_API_KEY` 또는 zipsa OAuth wallet).
- pydantic schema → SDK tool-use → validation. 결과는 pydantic
  instance.
- schema 없이도 호출 가능 → 그러면 raw text 반환.

## 8. pyproject.toml

```toml
[project]
name = "morning-notion-log"
version = "0.1.0"
description = "어제 코딩 작업을 Notion 에 정리"   # zipsa list 의 description source
dependencies = [
    "notion-client>=2.0",
    "anthropic>=0.30",
]

[tool.zipsa]
credentials = ["notion"]             # OAuth wallet names → env 주입
schedule = "0 8 * * *"               # optional, cron expr
allows_staging_run = true            # PR #102 의 gate (필요 시)

[tool.zipsa.limits]
max_cost_usd = 1.0
timeout_seconds = 600

# Per-phase override (optional)
[tool.zipsa.phases."2.llm-decide"]
model = { name = "claude-sonnet-4-6" }
max_turns = 10
allowed_tools = ["mcp__zipsa__ask", "mcp__zipsa__confirm"]
```

**폐기:** `apiVersion`, `kind`, `spec.purpose`, `spec.instructions`,
`spec.children`, `spec.tools`, `spec.mcp` 다 없음. PEP 621 표준 +
`[tool.zipsa]` 만.

## 9. `zipsa create` — authoring CLI

```bash
zipsa create "오전 8시 우산 알림 telegram"
```

순서:
1. `~/.zipsa/staging/<inferred-name>/` 디렉토리 생성
2. Skill template 복사 (`launcher/zipsa/skill_template/`)
3. Claude Code 호출 — `claude` subprocess 또는 `claude -p <intent>`
4. Claude Code 가 staging 안에 phase 파일 작성 + 작가 iterate
5. 만족 시 작가가 `zipsa install --link ./staging/<name>` 으로 promote

**zipsa 의 책임:**
- Skill template
- Sandbox 실행 (Docker)
- 결과 보고 + 다음 명령 받음
- Install / schedule

**Claude Code 의 책임:**
- 자연어 → phase 파일 작성
- iteration (작가 변경 요청 → 코드 수정)
- 디버그 (실패 시 stack trace 보고 수정)

→ 둘이 명확히 책임 분리. zipsa 는 runtime + lifecycle, Claude Code 는
authoring.

## 10. 무엇이 살고 무엇이 죽나

### ✅ 살림 (현 코드의 ~50-60%)

| 컴포넌트 | 변경 |
|---|---|
| Multi-phase orchestration | + filename discovery, + Python phase 실행 |
| Envelope contract + PR #95 strict parser | **`.md` phase 에만 적용** |
| HitlServer + ask/confirm/choose | LLM phase 는 MCP 로, Python phase 는 `zipsa.hitl.*` 로 — 같은 backend |
| State.json + phase_state.py | 그대로 |
| Resume from failed phase | 그대로 |
| OAuth manager + vault | 그대로 |
| Docker executor | + `uv pip install -e ./zipsa-dist` |
| Run logging | 그대로 |
| `zipsa install` | + pyproject 인식 |
| `zipsa list` | pyproject 의 description 표시 |
| `dev_overlay` | 그대로 |
| Web UI (#96-98) | minor adjust |
| Built-in skill discovery (#103) | 그대로 — Python phase 의 starter skill 위치 |
| `run_staging_skill` (#102), `read_run_log` (#101) | 그대로 |
| `list_skills_catalog` / `validate_skill` (#100) | pyproject 기반으로 reframe |

### ❌ 죽음

| 컴포넌트 | 사유 |
|---|---|
| Manifest schema (apiVersion/kind/metadata/spec) | pyproject + filename 으로 대체 |
| `spec.instructions: ./SKILL.md` 포인터 | filename 으로 직접 discover |
| `spec.children` 명시 | composition = Python import / subprocess. allowlist 무의미 |
| PreToolUse hook + `phase_allow.json` | Python 코드라 tool allowlist 무의미. LLM phase 는 phase 단위 `allowed_tools` 그대로 |
| `spec.tools` (builtin / mcp 분리) | Python phase = 마음대로 import |
| `mcp__zipsa__write_skill_files` (#100) | `zipsa create` CLI 가 처리 |
| skill-builder skill (PR-2 plan) | `zipsa create` 가 대체. Claude Code 가 authoring 엔진 |
| PR #104 (kind rename + SKILL.md 분할) | manifest 폐기로 의미 0 — close |
| Multi-runtime plugin (Claude/Codex/Gemini) | LLM phase 는 Claude 단일. Python phase 는 Python 자체가 런타임 |
| `runtime-contract.md` 의 일부 | LLM phase 에 한정. envelope 외 룰 일부 폐기 |

## 11. 옛 envelope vs 새 hybrid 비교

| | 옛 (전부 envelope LLM) | 새 (hybrid) |
|---|---|---|
| Phase 간 contract | 모든 phase = envelope | LLM phase = envelope. Python phase = return value |
| 실행 단위 | LLM call per phase | 함수 call per phase. LLM call 은 phase 내부에서 (필요 시) |
| 비용 | $0.10+ per phase (Sonnet) | LLM phase 만 비용. Python phase = $0 |
| 디버깅 | turn 로그 분석 | stack trace 또는 turn log (phase 별로) |
| 테스트 | E2E only (LLM 비결정) | `pytest test_fetch.py` 결정론 (Python phase) |
| HITL | MCP only | MCP (LLM phase) 또는 Python helper (Python phase) |

## 12. Migration 비용 (현실 추정)

| 작업 | 규모 |
|---|---|
| Hybrid runtime 추가 (Tier 1) | 4-6 PR, 점진적 |
| `zipsa.hitl` + `zipsa.llm` helper | 2 PR, 작음 |
| 기존 skill 마이그 (hello-world, weather 등) | 1 PR 당 1-3 skill |
| `zipsa create` CLI + template | 1-2 PR |
| 폐기 코드 cleanup (manifest schema, phase_allow 등) | 1-2 PR |

총 ~10-12 PR, fulltime 4-6 주.

## 13. 풀어야 할 design 결정 (이 spec 후속)

- **`should_run` 의 sandbox** — Python phase 의 guard 가 임의 코드 실행
  가능. Branching 평가 시 import overhead 또는 separate process? MVP
  는 in-process import 로 시작.
- **`zipsa.llm` 의 multi-provider** — Claude 만 vs OpenAI / Gemini 도
  지원? MVP 는 Claude (anthropic SDK) 만. 필요해지면 wrapper 확장.
- **Phase 별 docker per phase vs single docker for skill** — 옛 모델은
  per-phase. Python phase 가 추가되면 deps install 비용 (uv pip install)
  이 매 phase 마다 발생. 같은 skill 의 phase 들이 같은 container 를 공유
  하는 게 자연스러움. 그래도 LLM phase 의 fresh-context 가 필요하면 그건
  agent 호출 단위에서 처리.
- **Cron scheduling** — `[tool.zipsa].schedule` 을 zipsa 가 직접 실행
  vs host cron 으로 위임? 본인 도구라면 host cron 충분, production 시
  `zipsa-scheduler` 같은 daemon 필요할 수도.

## 14. Out of scope

- Skill marketplace / 공유 / 버전 관리
- Multi-runtime backend (Codex / Gemini)
- Skill template gallery (작가가 시작점 고르는 UI)
- 비-Claude-Code authoring path (Cursor / Copilot)

## 15. 결정 lock 후 다음 step

1. 이 spec + `skills/AUTHORING.md` 작가 가이드 review
2. PR #104 close
3. Tier 1 (Hybrid runtime) 의 첫 PR — filename-based phase discovery
4. ...
