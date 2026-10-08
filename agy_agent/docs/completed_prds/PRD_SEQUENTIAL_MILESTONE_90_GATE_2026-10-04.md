# 📋 [완료] PRD — 순차 마일스톤 완결 게이트 (Sequential 90-Point Completion Gate)

- **상태**: ✅ DONE (2026-10-04)
- **대상**: `!mode 2` / `!mode 3` / `!mode 3+` 자율 개발 파이프라인
- **한 줄 요약**: "다음 단계로 넘어가는 것"보다 "현재 단계의 완결"을 우선하도록,
  90점 완결 게이트 + A→B→C 순차 잠금 + backlog 이관 금지 + 회귀 잠금을 도입.

---

## 1. 배경 (해결한 문제)

기존 파이프라인은 마일스톤이 50~60%만 구현돼도(critical dimension 55~75점) 다음 단계로 진화했고,
`[BLOCKING_DEFECTS: 0]` 만으로 강제 승인(force-accept)이 발생했으며, UX/시각/안정성/성능/테스트
같은 필수 항목이 "차기 백로그"로 밀려 누락됐다. 결과는 "만들어지긴 했지만 말하지 않은 부분은 빠진" 제품.

## 2. 핵심 원칙

```
PRD → Milestone A 정의 → A 구현 → 독립 QA → FAIL 시 A 수정 → 재QA
   → (Overall>=90 AND 모든 Critical>=90 AND Exit Criteria 100% AND E2E PASS AND Critical Defect 0 AND 회귀 PASS)
   → A PASS → A LOCK → B UNLOCK → ...
   → 전체 통합 검증 → 최종 90점 → Release
```

## 3. 구현 (수정 범위)

| 항목 | 변경 |
|---|---|
| 90점 게이트 SSOT | `product_gate.py` 신설 (`evaluate_milestone_gate` / `evaluate_final_gate`) |
| Milestone PASS | `product_planner.critique_product` — critical 차원 임계 `max(base,90)`, full tier 92점, overall>=90 |
| Final PASS | `is_final_complete` = 90점 게이트 + (남은 마일스톤 없음). `auto_pilot` 이 실제 주입 |
| Critical dim | 모든 Critical 차원 >= 90 |
| QA rejection | `convergence_policy.convergence_is_sufficient` — blocking=0 은 필요조건일 뿐(제품 PASS 아님) |
| Backlog | `product_gate.may_defer_to_backlog` — Exit Criteria 필수 항목(UX/시각/안정성/성능/테스트/에러처리) 이관 금지 |
| Sequential lock | `roadmap_manager` — `get_current_milestone` 순차 잠금, 부분 완성의 강제 DONE 승격 차단, PASS=LOCK |
| Regression | `auto_pilot` 승급 게이트에 회귀 PASS 포함 |
| 완료 PRD 아카이브 | `prd_archive.py` + `docs/completed_prds/` |

## 4. 검증 (물리적 증거)

- 신규 테스트: `tests/test_milestone_sequential_gate.py` — **18 passed** (PRD §15의 7개 시나리오 전부 포함)
  1. A=89 → B 진입 불가 · 2. A=90 + Exit Criteria 누락 → 진입 불가 · 3. A PASS 후 B 해금(잠금 검증)
  4. 회귀 시 FAIL · 5. Critical Defect 0이어도 <90이면 PASS 불가 · 6. 필수 항목 백로그 이관 금지
  7. 최종은 전체 마일스톤 + 통합 E2E 필요
- 영향권 회귀: `test_autonomous_product_planning` / `test_convergence_policy` / `test_roadmap_manager_and_infinite_evolve` /
  `test_commander_*` / `test_trio_resilience` 등 **123 passed**.
- 전체 스위트: 실패 20건은 변경 전(stash baseline)과 **완전히 동일한 기존 환경 이슈**(quota/brand/engine/network/테스트 경로 버그)로 확인 —
  본 변경이 도입한 신규 실패는 **0건**.

## 5. 산출물 (커밋 대상)

- 신규: `agy_agent/product_gate.py`, `agy_agent/prd_archive.py`, `agy_agent/tests/test_milestone_sequential_gate.py`
- 수정: `agy_agent/product_planner.py`, `agy_agent/roadmap_manager.py`, `agy_agent/convergence_policy.py`,
  `agy_agent/auto_pilot.py`, `agy_agent/trio_agent_runner.py`
