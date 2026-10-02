# 레퍼런스·델타·우선순위 개선 플랜 (2026-10-01)

> **질문**: "'레퍼런스+델타 / 하우스 룰 3~5개 / 우선순위 3줄 / 발산-수렴 / 수용 기준 / 외부 그라운딩' 처방 중
> (1) 지금 우리 시스템에서 **먼저 고칠 것**과 (2) **내가 정보를 주면 도움이 될 것**은 무엇인가?"
>
> **가정(자율 진행)**: 대상 = `agy_agent` 파이프라인(Coder/QA/Auditor + Visual Craft Loop + Planner Visual Gap).
> "개선"은 새 프레임워크 도입이 아니라 **기존 모듈에 최소 배선**을 추가하는 것을 뜻한다.
> 진단은 추측이 아니라 디스크의 실제 코드/자산을 `file:line` 증거로 확인했다.

---

## 결론 (한 줄)

지금 시스템은 **"우리 계약 위반"은 기계적으로 잡지만, "레퍼런스 대비 얼마나 다른가"는 아무도 안 본다.**
그리고 우선순위는 사용자 가치가 아니라 **하드코딩 카탈로그 순서**에서 나온다.
이 두 구멍이 디테일 공백의 실체다. 에이전트/감사표를 더 붙여도 이 구멍은 안 메워진다.

---

# Part 1. 지금 먼저 고칠 것 (우선순위 순)

## P0-1. 레퍼런스 "이미지"가 Coder의 눈에 안 들어간다 (자동/디스크 레퍼런스)

**현재 상태 (증거)**

- `plan_wizard.py:158` `benchmark_reference` — 레퍼런스가 **문자열("토스(Toss) - 큼직한 타이포…")**로만 존재.
  이후 `plan_wizard.py:458,503,515`에서 **텍스트로만** 기획 프롬프트에 주입된다.
- `design_refs/ref_0.jpg … ref_4.jpg` (실제 UI 스크린샷 5장, 2026-09-04)이 디스크에 있으나
  **이를 읽는 코드가 단 하나도 없다** (`grep "design_refs"` / `grep "ref_0"` = 0 consumers). → 죽은 자산.
- `design_vision_pipeline.py:27`은 Behance에서 **이미지 1장**(`reports/gate2/design_reference.png`)을 긁어
  `design_tokens.json`(`:59`)을 쓰지만, 이 JSON을 **읽어 쓰는 로직이 없다**(쓰기 전용).
  이미지 자체는 Coder/QA 프롬프트에 첨부되지 않는다.
- 사용자가 **직접 첨부한** 레퍼런스 이미지는 첨부된다(`bot.py:6118, 6180-6195`). 단, 프레이밍이
  `"외곽선 추출(Tracing) 및 모킹 절대 금지"`(`bot.py:6174`) — 즉 **"베끼지 마라"는 위협**으로만 쓰이고,
  **"이 밀도/리듬/여백을 따라하라"는 목표**로는 쓰이지 않는다.

**왜 문제인가**: "레퍼런스 + 델타"의 핵심은 모델이 이미지에서 **디테일을 보간/복제**하는 것이다.
그런데 자동/디스크 레퍼런스는 아예 안 들어가고, 사용자 레퍼런스는 "금지 대상"으로만 제시되니
모델은 결국 자기 사전확률(평균 UI)로 그린다 → 50%에서 멈추는 직접 원인.

**최소 개선 (새 기능 아님, 배선 1개)**

1. 기획/라운드 루프에서 레퍼런스 이미지 **1~2장**을 Coder·QA **vision 입력에 스타일 타깃으로 첨부**.
   - 소스 우선순위: 사용자 첨부 → `design_refs/*` → `design_reference.png`.
2. 프롬프트에 **델타 지시 1줄** 추가:
   `"정체성(로고·문구·색)은 바꾸되 [밀도/여백/타이포 위계/모션 리듬]은 타깃과 일치시켜라."`
3. 기존 anti-plagiarism 해시/dhash 로직(`trio_agent_runner.py:1667`, `evidence_validator`)은 **그대로 유지**하고 추가만 한다.

---

## P0-2. 디자인 SSOT가 이중화되어 있고, 사용자 스타일이 검증에 안 닿는다

**현재 상태 (증거)**

- `reports/gate2/design_tokens.json` (2026-09-04): `style_archetype`, `palette`, `card_radius: 32px`, `motion` 등
  **사용자/레퍼런스 유래 스타일**을 담고 있으나 **어디서도 읽히지 않음**.
- `visual_craft_loop.py:127 build_design_contract()`는 도메인별 **하드코딩 일반 계약**(4/8pt 그리드, 48dp, 6 타이포 단계…)을
  별도로 생성 → 토큰과 **무관하게** 검증이 돌아간다.

**왜 문제인가**: 사용자가 준 팔레트/무드와 기계 검증 기준이 따로 논다.
"우리 제품다움"이 계약에 반영될 경로가 없다.

**최소 개선**: `design_tokens.json`을 SSOT로 두고 `build_design_contract()`가 이를 **상속/오버라이드**하도록
optional 입력 1개만 추가(팔레트·radius·motion). 새 모듈 만들지 말 것.

---

## P0-3. 우선순위가 "하드코딩 카탈로그"에서 나온다 (사용자 가치함수 부재)

**현재 상태 (증거)**

- `product_value_planner.py:131` — **도메인별 고정 가치 기능 카탈로그(가치 순)**. `select_roadmap_plans`가
  상위 미구현 항목을 다음 마일스톤으로 선택.
- `roadmap_manager.py:527` — `auto_expand`가 위 카탈로그 결과를 **1순위로 승격**.
- `roadmap_manager.py:297,329,776` — PRD는 **명시적으로 우선순위 소스에서 제외**("실행 일기").
- `plan_wizard.py`의 `target_persona` / `killer_feature`는 초기 기획 텍스트 생성에만 쓰이고
  (`plan_wizard.py` 바깥 사용처 = 테스트뿐), **이후 우선순위 결정에 입력되지 않는다**.

**왜 문제인가**: "누가 / 단 하나의 Job / 틀리면 비용"이 시스템에 없다.
그래서 우선순위 = 카탈로그 정렬 순서 = 누구에게나 그럴듯한 것. 사용자 맥락이 반영될 슬롯이 없다.

**최소 개선 (엔진 추가 금지)**: 기획 상태에 **입력 슬롯 3개**를 추가하고
`product_value_planner` 동점/정렬 시 **tie-break 가중치**로만 반영.
`("user": …, "one_job": …, "cost_of_wrong": …)` — 이 3줄이 유일한 외부 신호다.

---

## P0-4. "수용(acceptance)"이 아니라 "리뷰(review)"만 있다 — 레퍼런스 델타 0

**현재 상태 (증거)**

- Gate 5 = 스크린샷 + 자체 루브릭(`visual_craft_loop`), Planner Visual Gap도 **산출 스크린샷만** 읽는다
  (`planner_visual_gap_analyzer.py:236 collect_screenshots`).
- 비교 대상이 **"우리 계약"뿐**이고 **레퍼런스 대비 delta가 없다**.

**왜 문제인가**: 절대 규칙은 지켜지지만 "레퍼런스처럼 보이는가"는 아무도 안 본다.
그래서 "나쁜 건 아닌데 상용 앱 같지 않다"가 통과된다.

**최소 개선 (효과 대비 비용 최고)**: 기존 vision 호출을 재사용해
`레퍼런스 1장 + 산출 1장`을 나란히 주고 **"밀도/여백/타이포 리듬/색 사용량의 차이만"** 서술시키는
delta 판정 1개를 Gate 5에 추가. 새 프레임워크 금지.

---

## P1-5. 발산 단계가 없다 (Coder 1회 → 수렴만)

`trio_agent_runner`는 Coder ➔ QA ➔ Auditor로 **수렴만** 한다.
"20개 변형 생성 후 선택"이 없어 **첫 안이 곧 최종안**이다.

**최소 개선**: 전면 도입 금지. 디테일 결함이 잡힌 **특정 화면(스페이싱/CTA/빈 상태)에 한해서만**
Coder에게 대안 2~3개 + 근거를 받고 QA가 선택. (발산은 좁게, 수렴은 기존 루프 재사용)

---

## P1-6. 감사표가 자기참조적이다 (레퍼런스가 0회 등장)

`E2E_200_Point_Audit_Sheet.md` 전체에서 `레퍼런스/참고/reference` **0회**(grep -c = 0).
이 문서는 *제품 디테일*이 아니라 *E2E 엔진 자체*의 품질 매트릭스다("Zero-Config", "어댑터 분리"…).

**최소 개선**: **새 감사표를 만들지 말 것.** 200포인트는 엔진용으로 그대로 두고,
제품 디테일은 **레퍼런스 델타 + 하우스 룰 3~5개**로 대체한다(규칙 증식이 곧 무미건조함).

---

## P2-7. 계약에 "카피 톤"이 없다

`visual_craft_loop.py:127` 계약에 spacing/typography/color/motion은 있으나 **문구 톤이 없다**.
`visual_craft_loop.py:114 MARKETING_PHRASE_PATTERNS`는 "마케팅 문구 금지" **블랙리스트일 뿐**
긍정 톤 가이드가 아니다 → 버튼 라벨/에러/빈 상태 문구가 평균적·어색해진다.

**최소 개선**: 하우스 룰에 `카피 톤 1줄 + 금지어/선호어 각 3개`만 추가.

---

# Part 2. 네(사용자)가 정보를 주면 도움이 되는 것

시스템이 지금 **가질 수 없는** 입력 = 외부 오라클. 아래는 그대로 슬롯이 된다.

| # | 제공할 정보 | 지금 상태 | 주면 어디에 쓰이는가 |
|---|---|---|---|
| 1 | **실제 레퍼런스 이미지** (또는 화면 캡처 1~2장/화면유형) — "이 느낌" | 텍스트만 존재(`plan_wizard.py:158`), 디스크 `ref_*.jpg`는 미배선 | P0-1: Coder/QA vision 스타일 타깃 |
| 2 | **하우스 룰 3~5개** (밀도·라운드·모션·카피 톤) — "우리 제품은 이렇게 생겼다" | 없음(도메인 하드코딩 계약만) | P0-2/P2-7: 계약 상속 |
| 3 | **사용자 가치 3줄** — 누가 / 단 하나의 Job / 틀리면 비용(치명도) | 없음(고정 카탈로그가 대체) | P0-3: 우선순위 tie-break |
| 4 | **수용 기준 1~2개** — "X를 3탭 안에 못 하면 실패" | 리뷰 루브릭만 | P0-4: 리뷰→수용 전환 |
| 5 | (가능하면) **지표 1개** — 전환/완료/이탈 이벤트명 | 없음 | P0-4: 실사용자 대체 신호 |
| 6 | **표절 경계** — "이건 따라해도 됨 / 이건 금지" | 전부 표절 위협으로만 취급(`bot.py:6174`) | P0-1: 델타 범위 확정 |

**예시 포맷 (그대로 붙여넣기 가능)**

```
[하우스 룰]
- 밀도: 여백 넉넉(화면당 Primary CTA 1개), 카드 최대 2중
- 형태: radius 16, 그리드 4pt
- 모션: 200ms, ease-out 계열, 과한 바운스 금지
- 카피 톤: 동사구 라벨("저장", "추가"), 감탄/마케팅 문구 금지, 에러는 원인+해결 한 줄

[가치 3줄]
- 사용자: 1인 개발자, 이동 중 폰으로 기록
- one_job: 30초 안에 오늘 기록 1건 저장
- cost_of_wrong: 저장 실패하면 신뢰 붕괴 → 저장 동선은 P0

[수용 기준]
- 첫 화면 진입 후 3탭 안에 저장 완료. 못 하면 FAIL.
```

---

# Part 3. 하지 말 것 (같은 함정 재진입 방지)

1. **규칙/감사표/에이전트를 더 늘려서 메우려 하지 말 것.** 늘리면 **분산만 줄고 편향(빠진 정보)은 그대로**다.
2. **죽은 자산 방치 금지.** `design_refs/ref_*.jpg` 5장은 지금 아무도 안 읽는다 → 배선하거나 삭제.
3. **수렴(리뷰) 루프 강화 금지.** 이미 QA/Auditor/Visual Craft가 많다. 부족한 건 리뷰가 아니라 **외부 신호**다.

---

## 부록. 진단 근거 (검증된 물리 증거)

| 항목 | 증거 |
|---|---|
| 레퍼런스가 텍스트뿐 | `plan_wizard.py:158,187,458,503,515` |
| `design_refs/*` 미배선 | `grep "design_refs"` / `grep "ref_0"` (non-venv `.py`) = 0 |
| 긁은 레퍼런스 이미지 미첨부·토큰 쓰기전용 | `design_vision_pipeline.py:27,59`; `grep "design_tokens"` = 쓰기+게이트 목록만 |
| 사용자 레퍼런스는 "표절 금지"로만 프레이밍 | `bot.py:6118,6174,6180-6195` |
| 검증 계약이 토큰과 무관하게 하드코딩 | `visual_craft_loop.py:106-172` |
| 우선순위 = 고정 카탈로그 | `product_value_planner.py:131`; `roadmap_manager.py:527` |
| PRD는 우선순위 소스 제외 | `roadmap_manager.py:297,329,776` |
| Planner Gap은 산출 스크린샷만 읽음 | `planner_visual_gap_analyzer.py:236` |
| 감사표에 레퍼런스 0회 | `E2E_200_Point_Audit_Sheet.md` grep -c = 0 |
| 마케팅 블랙리스트만 존재(톤 가이드 없음) | `visual_craft_loop.py:114` |

---

# Part 4. 구현 완료 기록 (2026-10-01, 모두 동의 후 실행)

## 신규: `craft_inputs.py` — 크래프트 입력 SSOT
`<workspace>/reports/gate2/craft_inputs.json` 단일 파일에 사용자 입력을 모은다.
- `reference_images` / `house_rules` / `copy_tone` / `value_brief{who,one_job,cost_of_wrong}`
- 레퍼런스 해석 우선순위: 명시 입력 → 세션 첨부(`.references/user_inputs`) → `design_refs/*`
  → `design_reference.png` → 패키지 동봉 `design_refs/*`(전역 폴백).
- CLI: `python craft_inputs.py --workspace <ws> --reference ... --house-rule ... --copy-tone ... --who ... --one-job ... --cost ...`

## 항목별 구현 현황

| 항목 | 상태 | 변경 지점 | 검증 |
|---|---|---|---|
| P0-1 레퍼런스 이미지 배선 + 델타 1줄 | ✅ | `craft_inputs.build_reference_target_block()` → `trio_agent_runner.py` Coder/QA 프롬프트 주입. 죽은 자산 `design_refs/*` 전역 폴백 배선. | `test_craft_inputs.py::test_reference_target_block_*`, `test_package_design_refs_revives_dead_assets` |
| P0-2 디자인 SSOT 이중화 해소 | ✅ | `craft_inputs.load_style_tokens()` → `visual_craft_loop.build_design_contract(style=...)` 상속. `design_contract.json` 에 `style_fingerprint` 캐시 무효화. | `test_style_tokens_merge_and_contract_inherit`, `test_contract_cache_invalidated_by_style_change` |
| P0-3 우선순위 = 사용자 가치 | ✅ | `product_value_planner._value_brief_tokens()` + `select_capability` 2순위 tie-break. `plan_wizard` 가치 3줄 영속화. | `test_product_value_planner_value_brief_tiebreak` |
| P0-4 레퍼런스 델타 실측(수용) | ✅ | `visual_craft_loop.critique_reference_delta()` — 레퍼런스∥산출 좌우 합성 → 스타일 델타 5종 판정, `REF_*` 결함/증거(`reference_delta`) 기록. **검증은 프로젝트 고유 레퍼런스만**(전역 폴백 제외) 사용해 공정성 유지. | `test_parse_reference_delta` (+ run_round 비전 비활성 경로는 기존 테스트가 커버) |
| P1-5 발산 지시 | ✅ | `craft_inputs.build_divergence_block()` → Coder 프롬프트 국소 주입(디테일 화면 한정). | `test_divergence_block_present` |
| P1-6 감사표 자기참조 | ✅(문서) | **새 감사표를 만들지 않음.** 200포인트는 엔진용으로 유지, 제품 디테일은 레퍼런스 델타+하우스 룰로 대체(본 문서). | 문서 조치 |
| P2-7 카피 톤 | ✅ | 계약에 `copy_tone`/`house_rules` 포함, `_format_contract_block` 에 노출. | `test_style_tokens_merge_and_contract_inherit` |

## 검증 (물리 증거)
- 신규 테스트: `tests/test_craft_inputs.py` **11 passed**.
- 영향 모듈 회귀: `test_visual_craft_loop` / `test_plan_wizard_game_and_quality` /
  `test_planner_visual_gap_analyzer` / `test_product_value_planner` /
  `test_commander_mode3_dynamic_planning` / `test_autonomous_product_planning` **76 passed**.
- 전체 스위트(네트워크 3파일 제외): **1233 passed, 3 failed, 11 skipped**.
  실패 3건은 `test_opencode_latest_models.py`(단독 실행 시 6 passed) — 모델 카탈로그
  전역 상태/라이브 네트워크 아티팩트로, 본 변경과 무관(수정 모듈 미접촉).
- `python3 -m py_compile` 및 import 스모크 OK.

## 환경 플래그 (즉시 비활성화)
`CRAFT_REFERENCE_TARGET_ENABLED`, `CRAFT_DIVERGENCE_ENABLED`,
`CRAFT_PACKAGE_REFS_ENABLED`, `VISUAL_CRAFT_REFERENCE_DELTA_ENABLED`.
