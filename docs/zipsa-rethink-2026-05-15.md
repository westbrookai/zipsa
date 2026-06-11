# Zipsa Rethink — 2026-05-15

> 이 문서는 *결정문*이 아니라 *생각 정리*. 결정은 며칠 묵힌 뒤.

## 1. 원래 vision

zipsa의 광고 카피:
> "도메인 전문가가 자연어로 만드는 skill"

기대한 사용 경험:
- "Claude로 어제 한 일 정리해서 노션에 올려" 정도의 자연어
- SKILL.md 한두 페이지 + 약간의 매니페스트
- LLM이 알아서 도구를 사용해 일을 수행
- 코딩 지식 없이도 자동화 작성

## 2. 실제로 만들어진 것

`daily-progress@0.3.1` 매니페스트와 SKILL.md를 보면:
- 매니페스트 60+ 라인 (apiVersion, kind, metadata, spec, phases, mounts, tools, mcp, limits …)
- SKILL.md 200+ 라인 (phase 별 contract, MCP tool 이름, JSON payload 예시, `data_source_id` 같은 protocol-leak)
- runtime-contract.md (LLM에게 행동 강제하는 인질극 문서)
- PreToolUse hook + Bash 패턴 제한
- 4 phase (precheck → discover → analyze → persist), 후엔 3 phase
- 매 실행 $0.66, 평균 22턴, 약 1-2분
- 같은 함수를 결정론적으로 짜면: 30줄 Python, ms 단위, 비용 $0.005

**핵심 관찰**: daily-progress 작업의 *95%가 결정론적*. 그런데 우리는 LLM에게 그 95%를 시킴.

## 3. 어떻게 여기까지 왔나 (drift 회고)

매 단계마다 *지역적*으론 합리적인 PR이 누적된 결과:

| 결정 | 그 시점 명분 | 누적된 문제 |
|---|---|---|
| Multi-phase | LLM이 한 번에 못 끝냄 | Phase contract, JSON schema 작업 |
| PreToolUse hook | LLM이 도구 오남용 가능 | `phase-allow.json`, mount, 권한 문법 |
| 매니페스트 K8s 모양 | "익숙해 보임" | 의미 없는 보일러플레이트 |
| Multi-runtime plugin | 미래 유연성 | Claude/Codex/Gemini 3개 코드 경로 유지 |
| `spec.mounts` | 일반 mount 필요 | (이건 generic primitive라 OK) |
| `dev_overlay` | 개발 편의 | (OK, 작음) |
| SKILL.md 두꺼움 | agent reliability 부족 | 도메인 전문가 진입장벽 ↑↑ |

각 PR은 *증상 치료*. 질병("LLM을 잘못된 layer로 씀")은 한 번도 명시적으로 안 다룸.

## 4. 사용자가 4번 옳았고 매번 반대 방향을 잡아당겼다

이 세션 동안 사용자의 푸시백:

1. **"git 정보는 따로 처리"** — Claude는 generic `claude_session_git` provider 인프라 제안 → 사용자: "그러지 마" → 결과: 더 좁은 fix
2. **"skill이 agenthud 전용 스펙 갖지 마라"** — Claude는 manifest 새 필드 제안 → 사용자: "구멍 그만 뚫어" → 결과: `spec.mounts`라는 generic primitive 합의
3. **"호스트 node 가정 X"** — Claude는 host execution 제안 → 사용자: 현실 점검 → 결과: 컨테이너 안 npm link
4. **"이게 자연어냐"** — Claude는 "natural language for domain experts" 마케팅 반복 → 사용자: vision violation 명시 → 결과: 이 문서

매번 Claude의 방향은 *infrastructure 증식*이었고, 사용자의 방향은 *결정/스코프 축소*였다.

**시사점**: AI 가 짠 코드/설계는 default로 over-engineer + abstraction-add 편향. zipsa는 그 함정의 표본이다. 다른 LLM에 맡겨도 같은 함정 가능성 높음.

## 5. 깨달은 것: 두 종류의 "자연어 vision"

원래 광고가 가리키던 vision:
- **Runtime LLM**: SKILL.md prose를 LLM이 실행시간에 해석. 매 실행 = LLM = $$$ + 불안정.

사용자가 가리키는 진짜 vision:
- **Authoring LLM**: 자연어 요구사항을 LLM이 한 번 코드로 변환. 이후 결정론적 실행.

**같은 vision의 두 해석**. 우리는 첫 번째로 갔는데, 사용자는 두 번째를 의미했음.

## 6. 세 갈래 경로

### A. 정직한 reposition

- 마케팅: "도메인 전문가 친화" 빼고 "LLM-driven workflow sandbox"로 reposition.
- 기존 코드 95% 유지.
- daily-progress 같은 잘못된 use case는 demo에서 빼고, 진짜 LLM 가치 있는 skill로 교체.
- 비용 작음 (1-2주 마케팅/문서).
- 시장 포지션 *약함* (LangChain/Claude Code SDK 등과 직접 경쟁, 차별점 흐림).

### B. 근본 재설계 (`zipsa.libs.*` wrapper 노선)

- Skill = Python module.
- zipsa가 자체 라이브러리 (`zipsa.libs.notion` 등) 제공해서 MCP quirk 흡수.
- LLM 호출은 코드 안에서 helper로.
- 비용 큼 (라이브러리 작성 부담).
- **사용자가 이 안을 일축**: "Notion 라이브러리는 이미 있는데 왜 우리가 또 만드냐"

### B'. 사용자의 명료한 버전 (가장 정직)

> 사용자 요구사항 → LLM이 Python 프로그램 작성 → 결정론적 실행. MCP 안 씀. 기존 SDK / REST API 그대로 씀. LLM 혹은 개발자 감독하에 여러 번 시도해서 결과 다듬음.

핵심 원칙:
- **결정론적 부분 = 코드**
- **비결정론적 부분만 = LLM 호출 (한 줄)**
- **MCP 안 씀** (LLM이 orchestrator 아니니 protocol 불필요)
- **기존 ecosystem 라이브러리 활용** (`notion-client`, `PyGithub`, `anthropic` SDK)
- **agenthud처럼 CLI 도구는 `subprocess`로**

## 7. B' 상세

### Skill의 새 모양

```
skills/daily-progress/
├── pyproject.toml      # deps + zipsa config (10줄)
├── skill.py            # 메인 로직 (40-80줄)
└── README.md           # 선택, 자연어 docstring 정도
```

`pyproject.toml`:
```toml
[project]
name = "daily-progress"
version = "0.4.0"
dependencies = ["notion-client>=2.0", "anthropic>=0.30"]

[tool.zipsa]
entry = "skill:run"
credentials = ["notion"]   # zipsa가 NOTION_TOKEN 주입
```

`skill.py`:
```python
import os, subprocess, json
from datetime import date, timedelta
from notion_client import Client
import anthropic

def run(query: str = "yesterday"):
    target = (date.today() - timedelta(days=1)
              if query == "yesterday"
              else date.fromisoformat(query))

    # 결정론: agenthud
    out = subprocess.run(
        ["npx", "agenthud@0.8.4", "report",
         "--date", target.isoformat(), "--format", "json"],
        capture_output=True, text=True, check=True,
    )
    report = json.loads(out.stdout)

    # 결정론: Notion (기존 SDK)
    notion = Client(auth=os.environ["NOTION_TOKEN"])
    data_source_id = resolve_data_source(notion, "zipsa-daily-log")

    # 비결정론: LLM 한 번씩 (project별 summary)
    llm = anthropic.Anthropic()

    for project in report["sessions"]:
        summary = llm.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            messages=[{"role": "user",
                       "content": f"Summarize in 2-3 sentences:\n{project['activities'][:5000]}"}],
        ).content[0].text

        upsert_row(notion, data_source_id, target, project, summary)
```

### Authoring 흐름

```
[사용자]
   "어제 한 작업 정리해서 노션에 올려"
            ↓
[Claude Code / Cursor / 외부 LLM authoring tool]
   - Python 스크립트 생성
            ↓
[Iteration loop]
   zipsa로 sandbox 실행 → 결과 보고 "이거 잘못됐어"
   → LLM이 수정 → 다시 실행 → … 만족까지
            ↓
[Save]
   skills/daily-progress/skill.py
            ↓
[zipsa run / zipsa schedule]
   매번 결정론적, 빠름, 싸다
```

**핵심 분리**: authoring tool (Claude Code 등)이 *생산*. zipsa는 *post-authoring* (run/sandbox/cred/schedule). 두 책임을 같은 도구가 짊어지지 않음.

### 새 zipsa가 제공

1. Sandboxed Python runner (Docker per run)
2. Credential vault (OAuth flow + 토큰 저장 + env var inject)
3. 자동 dep install (uv pip install + cache volume)
4. Run observability (stdout/stderr/exit code/metadata)
5. Skill registry (`install`, `list`, github distribution)
6. (선택) Schedule
7. (선택) Authoring helper — `zipsa scaffold "<requirement>"` → Claude API 호출해서 초기 script 생성

### 새 zipsa에서 제거

- ❌ Manifest (apiVersion/kind/metadata/spec): pyproject.toml로 대체
- ❌ SKILL.md prose contract: 함수 docstring으로 충분
- ❌ Multi-phase execution: 함수 호출 트리
- ❌ PreToolUse hook + tool restriction: LLM이 orchestrator 아니니 무의미
- ❌ MCP server 관리 (.claude.json, headersHelper, allowed_tools): MCP 안 씀
- ❌ phase-allow.json
- ❌ runtime-contract.md
- ❌ Claude/Codex/Gemini runtime plugin: skill은 그냥 Python 스크립트, 런타임은 Python 자체

### 남는 기존 자산

- ✅ Docker executor (대폭 단순화)
- ✅ OAuth manager + credential storage
- ✅ Run logging
- ✅ Install from github
- ✅ `dev_overlay` (Python dep 개발 시 mount/preamble)
- ✅ CLI 패턴 (typer + click)

대략 launcher 코드의 **60-70% 정리**, 남는 30-40%가 진짜 핵심에 집중.

## 8. B'의 정체성 한 줄

> **zipsa = AI 보조로 작성된 Python automation을 안전하게 실행/스케줄링하는 personal-scale 플랫폼.**

비교:
- LangChain/LangGraph: agentic runtime. *다름*.
- GitHub Actions: CI/CD 중심, OAuth 사용자 자동화 X. *다름*.
- Modal: Python 클라우드 실행, OAuth 없음. *인접*.
- Zapier/n8n: no-code 중심. *다름*.
- Claude Code: authoring tool. *상호 보완*.

zipsa wedge: **OAuth pre-wired + LLM authoring 친화 + Docker sandbox + 개인 규모**.

## 9. B' 트레이드오프 (정직하게)

### 잘 들어맞는 부분
- daily-progress 류의 95% 결정론 작업: 압도적 개선
- 비용/속도/신뢰성: 100~1000x 개선
- 진입장벽: 매니페스트 60줄 → 10줄
- 디버깅: agent turn 로그 → 평범한 stack trace
- vision 일관성: 진짜 일치

### 안 들어맞을 수 있는 부분
- *순수 agentic* use case (long-form research, tool exploration 등)에는 부적합 → 그 시장은 LangChain/Claude Code가 가져가도 OK
- Python 작성 능력 *어느 정도* 필요. "전혀 코드 안 봐도 됨"은 거짓말 (단, LLM authoring 도움으로 진입장벽 낮춤)
- 라이브러리 ecosystem에 의존: Python ecosystem 외 도메인은 약함

### 비용 (정직 추산)
- 새 core: 2주 (200-400줄 새 코드)
- daily-progress 재작성: 1일
- weather, hello-world 재작성: 0.5일
- 기존 코드 정리: 1주 (60% deprecate, 30% migrate, 10% 유지)
- 문서/마케팅: 1주

총 4-6주 진지하게 한 사람이 일하면 마이그레이션 끝.

### 매몰비용 솔직히
- multi-phase, PreToolUse hook, SKILL.md schema, runtime-contract, MCP 관리 코드 — 폐기
- 일부 인사이트(보안 boundary, dev overlay 사용 모델)는 새 model에 응용 가능
- 절대량 미미하지 않지만, *지금* 사용자 1명이라 외부 비용 0

## 10. 이번 세션 PR 4개 자기 평가

| PR | 의미 | 정직한 평가 |
|---|---|---|
| #10 dev_overlay | dev 편의 mount/preamble | 기존 시스템 진통제. *증상 치료*. |
| #12 spec.mounts | generic mount primitive | *그 자체로는 OK*. 다만 "왜 mount 필요?"의 답이 LLM 가정에 묶임. |
| #13 daily-progress agenthud | analyze phase → 결정론 | **유일한 architectural progress**. 방향 옳음. |
| #14 Notion 페이로드 fix | 페이로드 박음 | *증상 치료*. 진짜 답은 "Notion 호출 코드로 짜기". |

4개 중 1개만 의미 있는 architectural progress. 나머지 3개는 *기존 잘못된 가정을 더 단단히 굳힘*.

## 11. 풀어야 할 질문 (며칠 묵히기)

### 정체성 질문
- zipsa는 *production-grade tool*인가, *본인 도구*인가? 답에 따라 risk tolerance 달라짐.
- 다른 사람이 zipsa skill을 *작성*하는 시나리오가 정말 있는가? 아니면 사용자 본인만?
- "AI authoring loop"이 *zipsa의 핵심*인가, 아니면 *외부 도구* (Claude Code 등)에 위임?

### 기술 질문
- B'에서 schedule이 진짜 필요한가? cron + zipsa run으로 충분?
- Skill 간 의존성 / 공유 라이브러리 어떻게? (단순한 답: pip로 충분)
- Multi-runtime (Codex/Gemini) 지원이 필요한가? B'에선 *대부분 불필요* — skill author가 자기 코드에서 골라 import.
- Hook system 완전 폐기? 아니면 "Python script 내 LLM 호출에 cost cap" 같은 형태로 살아남?

### 마케팅 질문
- "Claude Code의 출력물을 deploy하는 도구" 포지션은 *Anthropic 의존* 메시지. 위험?
- 같은 컨셉이지만 Claude뿐 아니라 Cursor / Copilot / GPT까지 친화로 가야?
- "Personal-scale automation" 시장 크기?

### 실행 질문
- 1주 실험으로 thesis 검증? 어떤 metric으로 성공 판정?
- 기존 코드 어떻게 deprecate? `v2` 브랜치 새로 시작? 같은 main에서 점진 migration?
- 외부 약속 (README, GitHub repo description) 언제 바꿀까?

## 12. 한 줄 요약

> *우리는 95% 결정론적인 작업에 LLM을 orchestrator로 씌웠고, 그 mismatch를 메꾸느라 manifest/SKILL.md/hook/phase/contract 이라는 거대한 보호 장치를 매주 만들었다. 진짜 답은 LLM을 authoring time에만 쓰고, runtime은 Python + 기존 라이브러리로 결정론으로 만드는 것이다.*

## 13. 이 문서의 한계

- 위 분석은 **현재 사용자 1명 기준**. 사용자 base 확장 가정시 결론 달라질 수 있음 (특히 마이그레이션 비용 측면).
- B'이 "더 작은 zipsa"라 *흥미는 덜할 수 있음*. 야망 vs 정직함의 균형은 사용자 본인 판단.
- "AI authoring loop"이 zipsa 핵심으로 가면 *그 부분 자체가 또 한 가지 product surface*. 진짜 다른 방향. 여기까지 안 다룸.

---

*결정은 며칠 묵힌 뒤. 이 문서는 그동안 손에 쥘 도구.*
