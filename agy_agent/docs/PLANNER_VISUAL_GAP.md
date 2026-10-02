# 📸 Planner Visual Gap — COMMANDER 스크린샷 실측 기획 결손 폐루프

> **"E2E 스크린샷은 CODER와 QA만의 검증 재료가 아니다. COMMANDER가 다음에 무엇을 기획해야 하는지
> 판단하는 1차 물리 증거다."**

---

## 1. 문제 정의 (왜 필요했는가)

기획(COMMANDER)이 디스크의 **소스코드 / PRD / ROADMAP** 만 보고 다음 마일스톤을 정하면,
다음과 같은 자기참조적(superficial) 과제가 나온다:

- ❌ "전체 E2E 시나리오 테스트 100% 무결점 통과"
- ❌ "상용 배포 품질 하드닝 및 E2E 회귀 검증"
- ❌ "응답 속도 최적화 및 불필요한 연산 제거"

겉보기에는 그럴듯하지만, **정작 사용자가 화면에서 체감하는 부족함(레이어 패널이 없다, 선택/크롭
도구가 없다, 작업 내역 패널이 없다)** 은 하나도 해결하지 못한다. 왜냐하면 "무엇이 부족한가"는
소스코드가 아니라 **실제로 렌더링된 화면**에 드러나기 때문이다.

## 2. 해결 (What's new)

`planner_visual_gap_analyzer.py` 가 다음 사이클을 닫는다:

```
[실 렌더링 E2E 스크린샷]  (reports/gate5/screenshots/*.png)
        │
        ├─ (1) 로컬 시각 증거: visual_craft_review.json / visual_qa.json  (네트워크 불필요, 결정적)
        └─ (2) 멀티모달 Vision: role="planner_gap"  (CPO 관점 제품 결손 진단)
        │
        ▼
[구조화된 제품 결손]  [PLAN_GAPS] → PlannerGap(category, severity, feature, missing, plan_hint)
        │
        ├─ 물리 증거 영속화: reports/planner/visual_gap_review.json
        │
        └─ COMMANDER 기획 프롬프트 + 결정론적 폴백에 주입
                │
                ▼
        [차기 원자적 마일스톤]  "스크린샷 실측 결손 해소: 레이어 패널"
```

### 결손 카테고리 (가치 가중치)

| 카테고리 | 의미 | 가중치 |
|---|---|---|
| `CORE_FEATURE` | 화면 텍스트/상태는 있으나 실제 UI/패널/도구가 없는 핵심 기능 | 1.00 |
| `UX_WORKFLOW` | 작업 흐름이 끊기는 누락 단계 (내역/선택/크롭/내보내기) | 0.82 |
| `ACCESSIBILITY` | 48dp 미터치 타깃, 상단 조작 과밀, 잘림/겹침 | 0.70 |
| `LAYOUT_PANEL` | 패널 구조가 확장 불가 (탭/아코디언/속성 패널 부재) | 0.62 |
| `PERFORMANCE` | 미니맵(내비게이터) 부재, 프레임 저하 등 | 0.60 |
| `POLISH` | 시각 완성도/빈 화면(Empty State) 등 마감 | 0.40 |

`select_top_gap()` 은 `severity_weight × category_weight` 로 "가장 가치 높은 단일 결손"을 고른다.

## 3. COMMANDER 통합 지점

| 경로 | 파일/함수 | 동작 |
|---|---|---|
| 차기 마일스톤 (비동기, 주 경로) | `Commander.plan_adaptive_next_milestone_async` | Vision 분석 실행 → `[VISUAL GAP]` 섹션을 기획 프롬프트에 주입 + Mandate 8 `VISUAL GAP FIRST` |
| 차기 마일스톤 (동기/폴백) | `Commander._build_heuristic_adaptive_plan` | 도메인 사다리의 **일반론 종단 폴백**(게임 최종/PDF 캐시/SaaS 최종)을 결손 플랜으로 교체 |
| 일반론 차단 가드 | `Commander._prefer_visual_gap_over_generic` | AI/휴리스틱 기획이 "전체 E2E 통과/품질 하드닝"류인데 화면 결손이 남아 있으면 강제 교체 (피드백 플랜은 불변) |
| 5W1H 마일스톤 기획 | `Commander.plan_milestone_5w1h_async` | `[VISUAL GAP]` 블록 주입 + 플랜에 진단 동봉 |
| 로드맵 자동 확장 | `RoadmapManager` auto-expand | GapSignal/DepthGap 보다 먼저 스크린샷 결손을 로드맵 후보로 승격 |
| 읽기/기획 자문 (`!read`, "무엇이 부족해?") | `ConsultingRunner.run_pipeline` | 자문 시작 전 스크린샷 결손 사전 진단을 채널에 게시 + Architect/Strategist 프롬프트에 주입 |

### 표시

`Milestone5W1HPlan.visual_gap_analysis` 필드가 Discord 메시지/임베드에
**"📸 스크린샷 실측 결손 진단"** 블록으로 표시된다. (source 배지: 📸 `visual_gap`)

## 4. 물리 증거 & 설정

```
<workspace>/reports/planner/visual_gap_review.json   # 리뷰 원본 (재사용/감사용)
<workspace>/reports/gate5/screenshots/*.png          # 입력 (E2E 렌더링 프레임버퍼)
<workspace>/reports/gate5/visual_craft_review.json   # 입력 (로컬 시각 결함 증거)
<workspace>/reports/gate5/visual_qa.json             # 입력 (Gate5 시각 QA 증거)
```

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `COMMANDER_VISUAL_GAP_ENABLED` | `1` | 기획 단계 스크린샷 결손 분석 ON/OFF |
| `COMMANDER_VISUAL_GAP_MAX_IMAGES` | `3` | 분석할 최대 스크린샷 수 (기획 경로에서는 지연 bound를 위해 2 사용) |

## 5. 오탐/할루시네이션 방어 (Zero-False-Gap)

1. **물리 증거 없으면 무동작**: 스크린샷도, 시각 증거 JSON도 없으면 `None` → 초기 프로젝트에서 오탐 없음.
2. **구현 완료 기능 재기획 차단**: 결손 feature 명이 디스크 `implemented_components` 에 이미 있으면 폐기.
3. **프레임버퍼 신선도**: 리뷰 생성 이후 새 스크린샷이 생기면 캐시 무효화 (이전 화면 재사용 금지).
4. **diff/합성 카드/빈 프레임 제외**: 실 프레임버퍼만 입력으로 사용.
5. **피드백 불변**: 사용자 피드백으로 고정된 플랜은 스크린샷 결손으로 절대 덮어쓰지 않는다.
6. **테스트 격리**: `UNITTEST_RUNNING=1` 에서는 외부 Vision 호출을 차단하고, NO_GAP 리뷰는 영속화하지 않는다.

## 6. 예시 (사용자 관점)

> "포토샵 프로그램을 만들고 있는데 사진을 보기에 무엇이 부족해?"

이 질문이 `!read`/자문 경로로 들어오면, COMMANDER는 실 렌더링 스크린샷을 먼저 비전 분석하여:

- **[MAJOR/CORE_FEATURE] 레이어 패널** — 상단에 `1개 레이어` 텍스트만 있고 관리 UI 부재 → 레이어 추가/삭제/숨김/투명도/블렌드 패널 기획
- **[MAJOR/UX_WORKFLOW] 선택 및 크롭 도구** — 마퀴/올가미/매직완드/크롭 부재 → 선택 툴 기획
- **[MINOR/LAYOUT_PANEL] 우측 패널 모듈화** — 브러시 옵션이 단독 점유 → 탭/아코디언 재설계

처럼 **화면 증거에 근거한 구체적 결손**을 제시하고, 그 1순위를 다음 마일스톤으로 승격한다.
