# 🏛️ Architecture & System Design - AGY 4.x Dynamic Collaboration Platform

## 1. 아키텍처 다이어그램 (Architecture Overview)
```text
[Discord User / Operator]
        │
        ▼
[bot.py (Gateway & Commander)] ───► [session_manager.py]
        │
        ├──► [mode_classifier.py] (Risk / Impact Classifier)
        │         ├─⚡ Fast-Path (Low Risk)   : Coder ➔ Fast QA ➔ Client Auditor 최종 승인 (전수 검증)
        │         ├─🛡️ Normal-Path (Med Risk)  : Coder ➔ QA ➔ Auditor ➔ 승인
        │         └─🚨 Deep-Path (High Risk)   : Coder ➔ Deep Pro QA ➔ Deep Pro Auditor ➔ Full E2E
        │
        ├──► [trio_agent_runner.py] (동적 협업 오케스트레이션 엔진)
        │         ├── Coder (Gemini 3.8 Flash High)
        │         ├── QA Reviewer (Adaptive: Flash for Low, Pro/Claude for Med/High)
        │         └── Client Auditor (Gemini 3.1 Pro High - PO Value & Goal Alignment)
        │
        ├──► [thermal_guard.py] (능동 복귀형 스마트 열 관리: 75°C 이하 도달 시 조기 즉시 재개)
        │
        ├──► [roadmap_manager.py] (Roadmap Ladder 피드백 루프: Value/Feasibility Pre-Check)
        │
        └──► [evidence_validator.py] (Gate 0~7 물리적 아티팩트 자동 검증)

[surface-status.service] (Node.js 데몬)
        │
        ├──► [status.js] (CPU, RAM, Temp, SSD, Quota 수집)
        └──► [Discord Channel: 1538368240089169960] (1분 실시간 갱신)
```

## 2. 모듈별 책임 및 격리 (Separation of Concerns)
1. **`bot.py`**: Discord API 게이트웨이, 이벤트 라우팅, 명령어 파싱
2. **`mode_classifier.py`**: 작업 의도 및 위험도(Low/Medium/High) 사전 분류기
3. **`trio_agent_runner.py`**: 위험도 기반 동적 Coder/QA/Auditor 라우팅 엔진
4. **`roadmap_manager.py`**: 로드맵 사다리 및 가치/피드백 루프 자율 진화 엔진
5. **`thermal_guard.py`**: 능동 모니터링 기반 CPU 고열 보호 및 조기 재개(Early-Resume) 가드
6. **`surface-status` (Node.js)**: OS 리소스 수집 및 실시간 텔레메트리 관제
7. **`evidence_validator.py`**: 정량적 품질 게이트(Quality Gate) 자동 검증기
8. **`autonomy_guard.py`**: Bounded Autonomy 10대 핵심 가드레일 및 4차원 의사결정 매트릭스 엔진
9. **`planner_visual_gap_analyzer.py`**: 실 렌더링 E2E 스크린샷 비전 분석 → COMMANDER 기획 결손 도출기

## 2.5 🎨 Visual Craft Loop (시각 완성도 폐루프 · !mode 3/3+ 전용)

> 상세: [`docs/VISUAL_CRAFT_LOOP.md`](../docs/VISUAL_CRAFT_LOOP.md)

무인 자율 모드(!mode 3/3+)에서 "기능은 동작하지만 상용 앱처럼 보이지 않는" 문제를
기계적으로 차단하는 4단계 폐루프. 사용자 주도 모드(!mode 1/2)에는 개입하지 않는다.

```text
Coder 턴 종료
   │
   ▼
[visual_craft_loop.py]
   1. Design Contract   : reports/gate2/design_contract.json (4/8pt 그리드·타이포 3단계·팔레트·터치 규격)
   2. Static Audit      : MARKETING_HERO / SMALL_TOUCH_TARGET / SPACING_OFF_GRID / TYPOGRAPHY_DRIFT / PALETTE_DRIFT / NESTED_CARDS
   3. Geometry Audit    : Android UI Hierarchy bounds → 실 dp 터치 타깃·썸존 위반
   4. Vision Critique   : 실 스크린샷 + PIL 계측 → [VISUAL_VERDICT]/[VISUAL_DEFECTS] 구조화 판정
   │
   ├─ QA 프롬프트에 감사 블록 주입 (trio_agent_runner.py)
   ├─ QA가 APPROVED 해도 MAJOR+ 잔존 시 [STATUS: REJECTED] 기계적 반려 (상한 VISUAL_CRAFT_MAX_ROUNDS)
   └─ reports/gate5/visual_craft_review.json → EvidenceValidator Gate 5가 물리 검증 (verdict=FAIL → 게이트 반려)
```

- 동일 결함 반복(stalled) 시 자동 수정을 멈추고 Auditor/인간 에스컬레이션 (무한 핑퐁 방지).
- `VISUAL_CRAFT_LOOP_ENABLED=0` 으로 즉시 비활성화 가능.

## 2.55 📸 Planner Visual Gap (스크린샷 실측 기획 결손 폐루프)

> 상세: [`docs/PLANNER_VISUAL_GAP.md`](docs/PLANNER_VISUAL_GAP.md)

Visual Craft Loop(2.5)가 "화면이 상용 앱처럼 보이는가(품질)"를 QA 관점에서 검증한다면,
Planner Visual Gap 은 **동일한 실 렌더링 스크린샷을 COMMANDER 기획 관점에서** 읽어
"이 제품에 아직 없는 기능/패널/워크플로"를 결손으로 도출한다. 소스/PRD만 보는 기획이
"전체 E2E 통과"류 일반론으로 흐르는 것을 물리 증거로 차단한다.

```text
[E2E 스크린샷 reports/gate5/screenshots/*.png]
   │
   ▼
[planner_visual_gap_analyzer.py]
   1. Local Evidence : visual_craft_review.json / visual_qa.json (결정적, 네트워크 불필요)
   2. Vision         : role="planner_gap" → [PLAN_GAPS] (CORE_FEATURE/UX_WORKFLOW/LAYOUT_PANEL/...)
   │
   ├─ reports/planner/visual_gap_review.json (물리 증거)
   ├─ plan_adaptive_next_milestone_async : planning_query 에 [VISUAL GAP] 주입 + Mandate 8
   ├─ _build_heuristic_adaptive_plan     : 일반론 종단 폴백을 결손 플랜으로 교체
   ├─ _prefer_visual_gap_over_generic    : 일반론 최종 기획 강제 교체 (피드백 플랜 불변)
   ├─ RoadmapManager.auto_expand         : 로드맵 확장 1순위 후보로 승격
   └─ ConsultingRunner (!read 자문)      : "무엇이 부족해?" 답변의 1순위 근거로 주입
```

- 물리 증거 없으면 무동작(None) · 구현 완료 기능 재제안 차단 · 프레임버퍼 신선도 검증.
- `COMMANDER_VISUAL_GAP_ENABLED=0` 으로 즉시 비활성화 가능.

## 2.6 🎨 Image generator Tool (실제 AI 이미지 생성·수정 도구 · 4번째 봇)

> 코드: `image_creator_hub.py` (오케스트레이션) · `image_generation_backend.py` (실제 AI 백엔드)

Coder/QA 가 작업 중 이미지가 필요할 때 호출하는 **도구 봇**. PIL/Canvas 도형으로
대체하지 않고 **실제 이미지 바이트를 반환하는 API** 만 사용한다.

```text
Coder/QA 본문 지시문                 이미지 봇 (4번째 클라이언트)          실제 AI 백엔드
[IMAGE_REQUEST: {...}]  ──감지──►  1. 파싱(JSON/파이프/자유/멀티라인 JSON)
[IMAGE_EDIT: {...}]        │       2. generate( ) / edit( )         ──► 1) OpenRouter 이미지 모델
   │                       │       3. 리소스 폴더 배치 + 코드 스니펫       (Nano Banana 계열)
   │                       │       4. Discord 반환 + #nano-banana 스튜디오  2) Gemini 네이티브(선택)
   └──── 다음 턴 바인딩 ◄────┘                                            3) OpenAI 호환(선택)
```

- **신규 생성**: `[IMAGE_REQUEST: {"prompt": ..., "asset_name": ..., "aspect_ratio": ...}]`
- **기존 수정**: `[IMAGE_EDIT: {"prompt": ..., "source_image": "경로 또는 파일명"}]`
  - 원본 해석: 절대경로 · 워크스페이스 상대경로 · 리소스 파일명(drawable 탐색) · 원격 URL
  - **원본을 찾지 못하면 새 그림으로 조용히 대체하지 않고 정직하게 실패**한다.
- **수동 호출**: `!image <요청>` / `!image edit <원본경로> <수정요청>`
- **모델 선택 실반영**: `!model` 에서 Image generator 의 엔진/모델을 바꾸면 즉시 실제
  생성에 반영된다. `provider/model` 형식은 그대로, agy 라벨(`gemini-3.8-flash-high`)은
  `resolve_backend_model_hint()` 가 실제 Nano Banana 모델 ID 로 해석한다
  (`IMAGE_MODEL_ALIASES` 로 매핑 확장 가능). 미지 라벨은 기본 체인으로 안전 강등한다.
- **QA 연동**: QA 반려 시 이미지 지시문은 가디언 게이트가 본문을 재작성해도
  `format_pending_requests()` 로 **Coder 피드백에 기계적으로 보존**되어 유실되지 않는다.
- **정직성**: 실제 AI 실패 시 도형으로 대체하지 않는다. 절차적 에셋은 명시 허용 시에만
  `fallback_used=True` + 실패 사유와 함께 '임시 대체'로 사용한다.

## 2.7 🛡️ 출력 품질 가드 (Output Guard · 판정 첫 줄 + 한국어 전용)

> 코드: `output_guard.py` · 연동: `agent_runner.py`, `trio_agent_runner.py`, `research_runner.py`, `consulting_runner.py`

에이전트 봇 메시지의 두 가지 운영 문제를 **결정론적으로** 차단한다.

1. **판정 태그 첫 줄 강제 (`ensure_verdict_first_line`)**
   - 리뷰어(QA1~QAN, Auditor) 답변에 판정 태그가 없으면 파이프라인의 승인/반려
     결정에 따라 `[STATUS: APPROVED|REJECTED]` / `[STATUS: AUDIT_PASSED|AUDIT_FAILED]`를
     첫 줄에 부착한다. 태그가 본문 중간/하단에 있으면 첫 줄로 승격한다.
   - `is_qa_approved`/`is_auditor_approved`/역할 전도 판정은 **가드 이전 원문**으로
     선계산하여 기존 판정 로직이 회귀하지 않는다.
2. **한국어 전용 출력 (`is_korean_output` + `guard_output_async`)**
   - 코드블록/인라인코드/URL/경로/확장자 토큰을 제거한 '산문'의 한글 비율로
     비한국어(영어/중국어/일본어) 문장을 감지한다. (기본 임계 비율 0.15 / 최소 산문 20자)
   - 비한국어로 판정되면 같은 에이전트에 **한국어 재작성 1회**를 요청한다. 재작성 실패/
     시스템 오류 시 원문을 유지하고, 판정 태그만 보정한다.
   - Coder/Analyst/Architect 재작성 시에는 `REWRITE_ONLY_NOTE`로 도구 호출/파일 수정을 금지한다.
   - 프롬프트 측면에서도 `agent_runner.TOOL_SAFETY_HEADER` 및 각 러너의 출력 지침에
     한국어 전용 규칙을 명시한다.
3. **환경변수 스위치**
   - `AGY_OUTPUT_GUARD=0` (전체 가드 비활성), `AGY_OUTPUT_KOREAN_REPAIR=0` (재작성만 비활성),
     `AGY_OUTPUT_KOREAN_MIN_PROSE`, `AGY_OUTPUT_KOREAN_RATIO` (감지 임계 조정)
   - 단위 테스트(`tests/conftest.py`)에서는 외부 LLM 재호출을 격리하기 위해
     `AGY_OUTPUT_KOREAN_REPAIR=0` 으로 고정하며, 재작성 경로는 `tests/test_output_guard.py`
     에서 명시적으로 검증한다.

## 2.75 🧭 역할 권한 계약 (Role Authority SSOT · 역할 전도 원천 차단)

> 코드: `role_authority.py` · 연동: `trio_agent_runner.py`

멀티 에이전트(Coder ➔ QA ➔ Auditor) 파이프라인에서 **판정 권한이 뒤바뀌어 보이는**
"역할 전도(Role Inversion)"를 단일 진실 공급원(SSOT)으로 차단한다.

| 주체 | 권한 | 규칙 |
| :--- | :--- | :--- |
| **Coder** | 구현(implementation) | `[STATUS: APPROVED/REJECTED]` 등 판정 태그 **선언 절대 금지**. 구현 결과/diff만 보고. |
| **QA / Auditor** | 판정(verdict) | `[STATUS: ...]` / `[BLOCKING_DEFECTS: N]` 선언 **전용 권한**. |
| **시스템 무결성 가디언** | 오버라이드 | QA/Auditor 판정을 기계적으로 오버라이드할 수 있으나, 반드시 **판정 주체를 명시**한다. |

1. **Coder 출력 정화 (`strip_coder_verdict_tags`)**
   - Coder 본문의 판정 '선언 줄'에서 태그를 물리 제거한다(본문 중 인용은 보존).
   - 1차 탐지(`detect_coder_role_inversion`)는 앞 3줄의 선언 줄만 검사하여,
     QA 피드백 인용을 오탐하지 않는다. 탐지 시 역할 복귀 프롬프트로 1회 자가 치유한다.
2. **시스템 게이트 오버라이드 (`format_system_gate_override`)**
   - Anti-Mocking / Visual Craft / 요구사항 커버리지 / 수렴 계약 오버라이드는
     `[STATUS: ...]` + `[BLOCKING_DEFECTS: N]` + **`판정 주체: 시스템 무결성 가디언`** 헤더로 생성한다.
   - 판정문은 `post_verdict_message()` 를 통해 **판정 채널(QA/Auditor)** 로 게시하며,
     Coder 채널(`main_channel`)에는 **무주체 판정문을 절대 게시하지 않는다**.
3. **회귀 방지**
   - `tests/test_role_authority.py`(시스템) 및 `tests/test_role_authority_enforcement.py`(워크스페이스)에서
     E2E 파이프라인 실주행으로 "Coder 채널 무주체 판정문 0건" 불변식을 검증한다.

## 2.8 🔬 자율 E2E 검증 루프 (Autonomous E2E Verification Loop · 협업 모드 전용)

> 코드: `autonomous_e2e_loop.py` · 연동: `trio_agent_runner.py`

기존 E2E(`cross_platform_e2e_runner`)는 수동 `!e2e` 명령에서만 동작했고, 협업 파이프라인
내부에서는 시각 스크린샷 몇 장으로 끝났다. 본 루프는 **!mode 2/2+/3/3+ 협업 파이프라인의
Coder 턴 종료 직후**에 삽입되어, 사용자가 지적하지 않아도 스스로 검증·보강한다.

```text
Coder 턴 종료
   │
   ▼
[autonomous_e2e_loop.py]
   1. 실제 테스트 실행   : pytest / npm test / gradle test / cargo / go 자동 탐지·실행 (subprocess, 주입 없는 argv)
   2. 스크린샷 실측      : reports/gate5/screenshots 등 신선 캡처 수집 + blank 프레임 계측
   3. 결함 자율 규명     : 테스트 실패/런타임 에러/시각 증거 결손 → 구조화 결함
   │
   ├─ QA 프롬프트 + Auditor 프롬프트에 리포트 블록 주입
   ├─ QA가 APPROVED 해도 BLOCKER/MAJOR 잔존 시 [STATUS: REJECTED] 기계적 반려 (상한 AUTONOMOUS_E2E_MAX_ROUNDS)
   ├─ Coder 피드백([🔬 AUTONOMOUS-E2E-FIX])으로 실패 로그·수정 스펙 환류 → 다음 라운드 재작업
   └─ 물리 증거: reports/gate4/autonomous_e2e_report.json
```

- 상한 도달 시 무한 핑퐁을 멈추고 Auditor/인간 에스컬레이션으로 전환한다.
- 환경/의존성 미비(pytest exit 5, 모듈 미설치)는 실패가 아니라 '미검증 갭'으로 정직하게 기록한다.
- `AUTONOMOUS_E2E_LOOP_ENABLED=0` 으로 즉시 비활성화 가능.

## 3. Bounded Autonomy & 10 Core Safety Guards (AGY 4.x)
무인 자율 진화(`!auto on evolve`)가 제어 불가능한 무한 루프나 시스템 고갈로 치닫지 않도록 10대 하드 가드레일을 강제합니다:

| 번호 | 가드레일 (Guard) | 한도 기준 | 트리거 시 동작 |
| :--- | :--- | :--- | :--- |
| **G1** | **Same Task Loop Trap** | 동일 태스크 3회 반복 | `ESCALATE_TO_HUMAN` (즉시 차단 및 사용자 개입) |
| **G2** | **File Churning Detector** | 단일 마일스톤 내 동일 파일 5회 수정 | `ESCALATE_TO_HUMAN` (파일 폭주 방어) |
| **G3** | **Milestone Timebox** | 단일 마일스톤 실행 30분 초과 | `ESCALATE_TO_HUMAN` (전략 재검토) |
| **G4** | **Daily LLM Call Quota** | 프로젝트당 일일 자율 호출 500회 | `STOP` (비용 및 API 쿼터 보호) |
| **G5** | **Free Disk Space Threshold** | 여유 디스크 2.0GB 미만 | `STOP` (디스크 고갈 방어) |
| **G6** | **Watchdog Runaway Brake** | 무인 감시견 6회 연속 자동 순환 | `COOLDOWN` (10분 능동 냉각 휴식) |
| **G7** | **Repeated Error Detector** | 동일 결함 2회 연속 발생 | `ESCALATE_TO_HUMAN` (자율 수렴 불가 에스컬레이션) |
| **G8** | **Roadmap Auto-Expansion Ceiling** | 로드맵 자동 확장 깊이 5차 상한 | `STOP` (무한 사다리 확장 중단) |
| **G9** | **Consecutive Failure Breaker** | 연속 3회 QA 탈락 | `ESCALATE_TO_HUMAN` (자동 롤백 권고 및 중단) |
| **G10**| **4D Decision Matrix** | Risk x Value x Confidence x Cost | 최적 파이프라인 동적 분기 (`DecisionMatrixEngine`) |

### 4D Decision Matrix Engine
- **Zero/Low Value**: 위험도와 무관하게 `STOP_LOW_VALUE` (불필요한 기술 부채 방지)
- **R0 / R1**: `PROCEED_FAST` (단일 Coder ➔ Fast QA ➔ 즉시 승인, Auditor/풀E2E 바이패스)
- **R2 Feature**: `PROCEED_NORMAL` (Coder ➔ QA ➔ Auditor ➔ 조건부 E2E)
- **R3 / R4**: `PROCEED_DEEP` (Deep Pro QA ➔ Auditor ➔ Full E2E 전수 검증)
- **R4 (Low Confidence)**: `AUDITOR_REVIEW` (Auditor 사전 수락 심사 강제)

## 4. AGY 5.0 — Autonomous Collaboration Validation & Tri-Layer Telemetry

### 1. Tri-Layer Telemetry: `Decision ≠ Action ≠ Outcome`
단순한 상태 저장을 넘어, **초기 의사결정(Decision)이 실제 실행(Action)과 최종 결과(Outcome)와 얼마나 부합했는가**를 사후 캘리브레이션(Calibration Score)으로 정량 측정합니다:
```json
{
  "mission_id": "req-1234",
  "decision": {
    "risk": "R2",
    "value": "HIGH",
    "confidence": 0.95,
    "cost": "LOW",
    "route": "PROCEED_NORMAL"
  },
  "action": {
    "coder": "completed",
    "qa": "passed",
    "auditor": "proceed",
    "rounds_taken": 1,
    "tool_calls": 4
  },
  "outcome": {
    "final": "success",
    "iterations": 1,
    "files_changed": 2,
    "llm_calls": 3,
    "elapsed": 12.4,
    "is_converged": true,
    "calibration_score": 1.0
  }
}
```

### 2. Collaboration Intelligence & 10대 적대적 미션 (A1 ~ A10)
에이전트 간의 맹목적 담합(Collusion), 요구사항 누락(Omission), 수렴 실패 핑퐁 트랩을 원천 차단합니다:
* **A1~A4 (자원/루프 차단)**: 태스크 루프(3회), 파일 폭주(5회), QA 3회 연속 탈락, 로드맵 5차 상한 강제 차단.
* **A5 (가치 차단)**: `STOP_LOW_VALUE` — 단순 작업에 10개 이상 과잉 파일 수정 시 Auditor가 기술 부채 유발 차단.
* **A6 (신뢰도 게이트)**: R4 고위험 + 저신뢰도 시 `AUDITOR_REVIEW` 사전 심사 강제.
* **A7~A9 (동적 라우팅)**: 저위험 Fast-Path(A7), 표준 컴포넌트 Normal(A8), 코어 시스템 Deep(A9).
* **A10 (협업 수렴 실패 차단)**: Coder와 QA가 동일 결함을 두고 2회 연속 평행선을 달리는 핑퐁 트랩 발생 시 즉시 서킷 브레이커 작동(`Ping-Pong Trap Breaker`).
* **C1/C2 (요구사항 누락 방어)**: Coder가 명시적 필수 요구사항(`[REQ-xx]`)을 누락하고 컴파일만 맞췄을 때 QA가 즉시 `[STATUS: REJECTED]` 선언 및 결함 통보.


