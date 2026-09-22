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


