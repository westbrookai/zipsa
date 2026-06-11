# Skill Authoring Guide

> Skill 작성자를 위한 실용 가이드. 아키텍처 결정의 배경 / 이유는
> [docs/zipsa-architecture-2026-06-11.md](../docs/zipsa-architecture-2026-06-11.md)
> 를 참조.

## 1. Skill 의 모양 (10 초 요약)

```
skills/my-skill/
├── SKILL.md                  ← 당신이 직접 쓰는 자연어 intent doc
└── zipsa-dist/
    ├── pyproject.toml        ← Python deps + zipsa 설정
    ├── 1.preflight.py        ← 첫 phase: 항상 .py (env / auth 검증)
    ├── 2.fetch.py            ← Python phase
    ├── 3.summarize.md        ← LLM phase (envelope JSON 출력)
    └── 4.persist.py          ← Python phase
```

- **`SKILL.md`**: 당신의 자연어 doc. 무엇을 / 언제 / 왜. 사람이 읽음.
- **`zipsa-dist/`**: zipsa runtime 이 실행하는 것. compiled artifact 자리.

## 2. 파일 명명 규칙

```
<dotted-int>.<phase-name>.{py,md}
```

| 예 | id | 종류 | 의미 |
|---|---|---|---|
| `1.preflight.py` | 1 | Python | 첫 phase |
| `2.fetch.py` | 2 | Python | 두번째 |
| `3.summarize.md` | 3 | LLM | 세번째, LLM phase |
| `4.1.write-db.py` | 4.1 | Python | 4 의 sibling 분기 |
| `4.2.write-file.py` | 4.2 | Python | 4 의 sibling 분기 |
| `5.done.md` | 5 | LLM | 마지막 |

**규칙:**
- 파일이 **곧 phase 선언**. pyproject 에 phases 목록 안 둠
- Sort by dotted-int tuple. `4.1` < `4.2` < `5`
- 첫 phase 는 `.py` **권장** (LLM 비용 들기 전에 결정론적 env / auth 검증).
  Authoring 도구처럼 본질이 LLM 작업인 경우 (예: built-in `skill-builder`)
  는 `.md` 로 시작해도 됨. 단 production 스킬에는 강력 권장.
- 이후는 `.py` 와 `.md` 자유롭게 섞임
- Sub-phase (`4.1`, `4.2`) = **XOR 조건 분기** (정확히 하나만 실행)

## 3. pyproject.toml 골격

```toml
[project]
name = "morning-notion-log"
version = "0.1.0"
description = "어제 코딩 작업을 Notion 에 정리"
dependencies = [
    "notion-client>=2.0",
    "anthropic>=0.30",
]

[tool.zipsa]
credentials = ["notion"]              # OAuth wallet 이름들 → env var 주입
schedule = "0 8 * * *"                # 선택, cron 식
allows_staging_run = true             # 선택, draft 실행 권한

[tool.zipsa.limits]
max_cost_usd = 1.0
timeout_seconds = 600

# Per-phase override (선택)
[tool.zipsa.phases."3.summarize"]
model = { name = "claude-sonnet-4-6" }
max_turns = 10
allowed_tools = ["mcp__zipsa__ask", "mcp__zipsa__confirm"]
```

**필수:** `[project].name`, `version`, `description`.
**나머지 다 선택.** deps 없으면 `dependencies = []`.

## 4. Python phase 작성

### 기본 골격

```python
"""1.preflight.py — Notion 인증 + DB resolve."""

import os
from notion_client import Client


def run(ctx: dict, prev: dict) -> dict:
    """
    Args:
        ctx: skill 메타. {name, version, user_query, env, run_id, ...}
        prev: 이전 phase 의 return. 첫 phase 면 {}

    Returns:
        dict — 다음 phase 의 prev 로 주입됨.
    """
    notion = Client(auth=os.environ["NOTION_TOKEN"])
    db_id = resolve_db(notion, ctx["user_query"])
    return {"db_id": db_id, "user_query": ctx["user_query"]}
```

### 계약

- `run(ctx, prev) -> dict` 가 entry. zipsa runtime 이 호출.
- **정상 종료** = return value, **실패** = exception (stack trace 가 log 에).
- `ctx` 는 read-only. mutate 금지.
- `prev` 는 직전 phase 의 return. branching 시 sibling 들 중 실행된 하나의 return.
- side effect (file I/O, DB write, subprocess) 자유.

### LLM 호출 (인라인)

Python phase 내부에서 LLM 호출이 필요하면:

```python
from anthropic import Anthropic
from pydantic import BaseModel

class Summary(BaseModel):
    headline: str
    key_points: list[str]


def run(ctx, prev) -> dict:
    client = Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5",
        tools=[{"name": "summary", "input_schema": Summary.model_json_schema()}],
        tool_choice={"type": "tool", "name": "summary"},
        messages=[{"role": "user", "content": prev["text"]}],
    )
    summary = Summary.model_validate(response.content[0].input)
    return summary.model_dump()
```

또는 zipsa helper (편의):

```python
from zipsa import llm

def run(ctx, prev) -> dict:
    s: Summary = llm.ask(
        prompt=prev["text"],
        schema=Summary,
        model="claude-haiku-4-5",
    )
    return s.model_dump()
```

### 사용자에게 묻기 (HITL)

```python
from zipsa import hitl

def run(ctx, prev) -> dict:
    city = hitl.ask("어느 도시?")
    ok = hitl.confirm("정말 진행?", default=True)
    choice = hitl.choose("어느 모드?", ["fast", "slow"])

    # 한 번 답하면 기억 (skill 별)
    workspace = hitl.ask_once("notion_workspace", "Notion workspace?")

    return {"city": city, "mode": choice, "workspace": workspace}
```

- CLI 모드: terminal stdin/stdout
- Web 모드: modal popup
- Non-interactive: `HitlUnattended` 예외

## 5. LLM phase 작성

### 기본 골격

```markdown
---
guard: prev.source == "agenthud"   # 선택, branching 시
---

# 일일 활동 요약

당신은 어제 코딩 활동을 요약하는 agent 다. `prev` 에 agenthud 가
가져온 sessions 가 들어있다.

## 무엇을 result 에 담나

- `headline`: 한 문장 요약 (50자 이내)
- `per_project`: project 별 활동 list. 각각 {name, summary, minutes}
- `total_minutes`: 합계

## 무엇을 user_facing_summary 에 담나

한 줄로 "오늘 N 시간, M 개 프로젝트 작업했습니다" 형태.
```

### 계약

LLM phase 는 **envelope JSON** 을 최종 메시지로 emit. PR #95 의 strict
parser 가 검증. 자세한 envelope 규칙은
[runtime-contract.md](../launcher/zipsa/system-prompts/runtime-contract.md).

phase 의 return 형태 (envelope 의 `result` 또는 `next_phase_input`) 가
다음 phase 의 `prev` 로 들어감.

### Frontmatter

| 필드 | 의미 | 예 |
|---|---|---|
| `guard` | branching 시 평가될 Python expression | `prev.source == "db"` |

## 6. Branching (조건 분기)

같은 dotted level 의 sibling 파일들 (`4.1.x`, `4.2.y`) = XOR. 정확히
하나만 실행.

### Python sibling

```python
# 4.1.write-db.py
def should_run(prev: dict) -> bool:
    return prev.get("target") == "db"

def run(ctx, prev) -> dict:
    ...
```

```python
# 4.2.write-file.py
def should_run(prev: dict) -> bool:
    return prev.get("target") == "file"

def run(ctx, prev) -> dict:
    ...
```

### LLM sibling

```markdown
---
guard: prev.target == "db"
---

# DB 에 쓰기 instruction ...
```

### 동작

- Runtime 이 4.1, 4.2 의 guard 평가
- **정확히 하나 True** → 그 sibling 실행
- 전부 False → `BRANCH_NONE_MATCHED` 에러
- 둘 이상 True → `BRANCH_AMBIGUOUS` 에러
- guard 가 예외 발생 → False 취급 (fail-safe)

`should_run` / `guard` 없으면 default `True`.

## 7. State Passing

`prev` 가 어떻게 들어오나:

| 직전 phase 종류 | `prev` |
|---|---|
| 첫 phase | `{}` |
| Python phase | 그 함수의 return value |
| LLM phase | envelope 의 `next_phase_input` (없으면 `result`) |

**Cross-type** (Python ↔ LLM) 도 자연스럽게 동작. state.json 이 둘의
공통 ground.

## 8. 흔한 패턴

### A. 외부 API 호출 (결정론)

```python
# 2.fetch.py
import requests

def run(ctx, prev) -> dict:
    r = requests.get(f"https://wttr.in/{prev['city']}?format=j1")
    r.raise_for_status()
    return {"weather": r.json()}
```

### B. CLI 도구 wrap

```python
# 1.agenthud.py
import subprocess
import json

def run(ctx, prev) -> dict:
    out = subprocess.run(
        ["npx", "agenthud@latest", "report", "--date", ctx["user_query"]],
        capture_output=True, text=True, check=True,
    )
    return {"sessions": json.loads(out.stdout)}
```

### C. LLM 으로 자연어 → 구조화

```python
# 3.classify.py
from zipsa import llm
from pydantic import BaseModel

class Intent(BaseModel):
    action: str   # "fetch" | "post" | "summarize"
    target: str

def run(ctx, prev) -> dict:
    intent: Intent = llm.ask(
        prompt=f"Classify the intent: {ctx['user_query']}",
        schema=Intent,
    )
    return intent.model_dump()
```

### D. 분기 (LLM 이 결정 → Python sibling 들이 실행)

```
3.classify.py        ← LLM 으로 intent 결정 (Python 안에서 llm.ask 사용)
4.1.do-fetch.py      ← guard: prev.action == "fetch"
4.2.do-post.py       ← guard: prev.action == "post"
4.3.do-summarize.py  ← guard: prev.action == "summarize"
5.report.py          ← 합류 후 보고
```

### E. 검토 loop (HITL)

```python
# 3.draft.py
from zipsa import llm, hitl

def run(ctx, prev) -> dict:
    draft = llm.ask(prompt=f"Draft tweet about: {prev['summary']}")

    while True:
        if hitl.confirm(f"이 트윗 OK?\n\n{draft}\n"):
            break
        feedback = hitl.ask("어떻게 바꿀까요?")
        draft = llm.ask(prompt=f"Revise: {draft}\n\nFeedback: {feedback}")

    return {"final": draft}
```

### F. 비용 cap

```toml
# pyproject.toml
[tool.zipsa.limits]
max_cost_usd = 0.50         # 전체 skill 한도
timeout_seconds = 300

[tool.zipsa.phases."3.summarize"]
max_cost_usd = 0.10         # 이 phase 만
```

## 9. ctx 안에 뭐가 들어있나

```python
ctx = {
    "name": "morning-notion-log",
    "version": "0.1.0",
    "user_query": "yesterday",        # CLI 에 주어진 인자
    "run_id": "2026-06-11_080000_...",
    "env": {                          # OAuth wallet + system env
        "NOTION_TOKEN": "...",
        "ANTHROPIC_API_KEY": "...",
        ...
    },
}
```

- **`user_query`**: `zipsa run my-skill "<여기>"` 의 그 string
- **`env`**: pyproject 의 `credentials` 가 매핑된 token + system env
  - `NOTION_TOKEN` 은 `os.environ["NOTION_TOKEN"]` 로도 접근 가능

## 10. 디버깅 / 실행 / 테스트

### 로컬 실행

```bash
zipsa install --link ./skills/my-skill
zipsa run my-skill "hello world"
```

### Phase 단위 테스트

Python phase 는 pytest 로 결정론 테스트:

```python
# tests/test_fetch.py
from zipsa_dist import fetch  # 1.fetch.py

def test_fetch_returns_sessions():
    ctx = {"user_query": "yesterday", "env": {}}
    result = fetch.run(ctx, prev={})
    assert "sessions" in result
```

(`1.fetch.py` → module name 은 dotted-int 가 underscore 로 변환되거나
별도 import path. 자세한 import 규칙은 별도 문서.)

### Run log

매 실행 후 `~/.zipsa/<name>@<version>/runs/<run_id>/` 에:
- `summary.json` — 최종 결과
- `phases/<n>-<id>/state.json` — phase 별 state
- `phases/<n>-<id>/output.jsonl` — LLM phase 의 turn 별 stream
- `phases/<n>-<id>/stderr.log` — Python phase 의 stderr

### Resume

phase 가 실패하면 다음 실행이 그 phase 부터 재시작 가능:

```bash
zipsa run my-skill "..."  # 첫 실행, phase 3 에서 실패
zipsa run my-skill "..."  # 자동으로 phase 3 부터 재시작 안내
```

## 11. 추천 워크플로

1. **`SKILL.md` 부터**: "내가 만들 skill 이 무엇을 하는지" 자연어로 적기
2. **결정론 / 비결정론 분리**: 어느 step 이 deterministic 한가? Python.
   어느 step 이 추론이 필요한가? LLM.
3. **첫 phase 는 `.py` 권장**: preflight (env, auth, config 검증). authoring
   도구 같은 예외는 `.md` 로 시작해도 됨
4. **각 phase 는 단일 책임**: phase 가 너무 길면 분리
5. **`zipsa run --dry-run`** 으로 phase discovery 만 검증
6. **점진적 실행 + iterate**: 한 phase 씩 추가 → 실행 → 결과 보고 → 다음
7. **만족 시 `[tool.zipsa].schedule` 추가** 로 cron 등록

## 12. 더 알아보기

- 아키텍처 결정 배경: [docs/zipsa-architecture-2026-06-11.md](../docs/zipsa-architecture-2026-06-11.md)
- 옛 envelope contract (LLM phase 의 emit 규칙): [runtime-contract.md](../launcher/zipsa/system-prompts/runtime-contract.md)
- 기존 skill 예시: [skills/hello-world](./hello-world), [skills/weather](./weather)
