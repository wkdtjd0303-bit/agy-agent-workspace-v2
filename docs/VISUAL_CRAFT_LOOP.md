# 🎨 Visual Craft Loop — `!mode 3` 무인 자율 시각 완성도 폐루프

> "기능은 되는데 왜 스토어 앱처럼 안 보이는가?"
> 원인은 단 하나입니다. **아무도 렌더링된 화면을 보지 않았기 때문**입니다.

`!mode 1/2`(사용자 주도)는 사람이 화면을 보고 디렉팅하므로 문제가 없습니다.
하지만 `!mode 3/3+`(무인 자율 순환)에서는 Coder·QA·Auditor가 모두 **코드 트리의 논리적 완결성**만
검증했기 때문에, 아래와 같은 'AI 슬롭 UI'가 그대로 통과했습니다.

| 실제 통과된 결함 (PDF 뷰어 사례) | 원인 |
|---|---|
| 상단 절반이 "문서를 열면, 바로 집중할 수 있게" 마케팅 히어로 카드 | 웹 랜딩페이지 구조를 앱에 이식 |
| 보라색 `PDF 열기` + 그 아래 `열기` 아이콘 + 카드 버튼 중복 | 액션 위계 미정리 |
| 회색 카드 > 흰 버튼 > 카드 타일 > 배지 4중 중첩 | 컨테이너 남용, 여백/구분선 부재 |
| 잘못된 엄지 동선, 48dp 미만 터치 타깃 | 인체공학 검증 부재 |

Visual Craft Loop는 이 4가지 실패 유형을 **기계적으로 적발하고, Coder를 다시 돌려서 고칠 때까지 승인을 막는** 장치입니다.
사용자 없이 봇끼리 개발하는 `!mode 3/3+`에만 작동하며, 기존 파이프라인(Coder → QA → Auditor)은 그대로 유지됩니다.

---

## 1. 4단계 구조

```
[1] 디자인 계약 (수치 고정)      → reports/gate2/design_contract.json
        │  4/8pt 그리드 · 타이포 3단계 · Primary+Neutral · 터치 최소 규격 · 카드 중첩 한도
        ▼
[2] 정적 감사 (소스 실측)        → MARKETING_HERO / SMALL_TOUCH_TARGET / SPACING_OFF_GRID
        │                          TYPOGRAPHY_DRIFT / PALETTE_DRIFT / NESTED_CARDS
        ▼
[3] 기하 감사 (런타임 계층)      → Android UI Hierarchy XML(bounds/clickable)
        │                          실제 dp 터치 타깃 · 상단 1/3 조작 과밀집(썸존)
        ▼
[4] Vision 비평 (프레임버퍼)     → 실 스크린샷 + PIL 계측치 → 멀티모달 판정
        │                          [VISUAL_VERDICT]/[VISUAL_SCORE]/[VISUAL_DEFECTS]
        ▼
[판정] FAIL(MAJOR+) → QA 프롬프트 주입 + 승인 시 강제 반려 → Coder 구조 재작업
        PASS        → Gate 5 통과 (reports/gate5/visual_craft_review.json)
```

### 1.1 디자인 계약 (Design Contract)
`visual_craft_loop.py::build_design_contract()` 가 도메인별 토큰을 수치로 고정합니다.
LLM이 "예쁘게"를 해석하며 15px/22px/3색 파스텔을 남발하는 것을 막습니다.

### 1.2 정적 감사 규칙 (소스)
| Rule ID | Severity | 적발 대상 |
|---|---|---|
| `MARKETING_HERO` | MAJOR(모바일) | 엔트리 화면/히어로 마커 + 홍보성 카피 2건 이상 |
| `SMALL_TOUCH_TARGET` | MAJOR | 인터랙티브 위젯의 44dp 미만 크기 |
| `TYPOGRAPHY_DRIFT` | MINOR~MAJOR | 폰트 크기 7단계 이상 증식 |
| `PALETTE_DRIFT` | MINOR~MAJOR | 하드코딩 HEX 색상 13종 이상 |
| `SPACING_OFF_GRID` | MINOR | 여백/패딩이 4pt 그리드 이탈 (파일당 4회 이상) |
| `NESTED_CARDS` | MINOR | 25줄 내 카드/컨테이너 3개 이상 근접 중첩 |

### 1.3 기하 감사 (런타임 계층)
`ui_dump*.xml` / `hierarchy*.xml` 이 존재하면 실제 `bounds` 와 density로 계산합니다.
- 클릭 가능 노드의 최소 변 < 48dp → `SMALL_TOUCH_TARGET` (MAJOR)
- 클릭 요소 50% 이상이 상단 33%에 몰림 → `THUMB_ZONE_OVERLOAD` (MINOR)

### 1.4 Vision 비평 (Vision-in-the-Loop)
`VisionAttachmentAnalyzer` 에 추가된 `role="design_critique"` 가
디자인 계약 + PIL 물리 계측치 + 스크린샷을 함께 보고 아래 형식만 출력합니다.

```
[VISUAL_VERDICT]: FAIL
[VISUAL_SCORE]: 61
[VISUAL_SUMMARY]: 히어로 카드가 첫 화면을 점유
[VISUAL_DEFECTS]:
- MAJOR | MARKETING_HERO | 상단 40% | 감성 카피가 화면 절반 점유 | 히어로 제거 후 목록 상단 배치
```

- PIL 계측(`measure_screenshot_metrics`)이 밴드별 '잉크 비율'을 계산해 **눈대중 판정을 보정**합니다.
- 빈/검은 프레임, 합성 diff 카드(`00_*`, `*diff*`)는 실 프레임이 아니므로 제외합니다.
- 최신 UI 소스 mtime > 최신 스크린샷 mtime 이면 `STALE_FRAMEBUFFER` 경고로 재캡처를 유도합니다.

---

## 2. 파이프라인 결선 (`trio_agent_runner.py`)

`!mode 3/3+` + GUI 도메인(`mobile_app`/`saas_web`/`game`) 에서만 작동합니다.

| 단계 | 동작 |
|---|---|
| **예방** | Coder 첫 턴 프롬프트에 디자인 계약 요약 주입 (Commander 5W1H 지침에도 계약 명시) |
| **감사** | Coder 턴 종료 직후 `VisualCraftLoop.run_round()` 실행 → QA 프롬프트에 감사 블록 주입 |
| **강제** | QA가 기능만 보고 APPROVED 해도 MAJOR+ 결함이 남아 있으면 `[STATUS: REJECTED]` 로 기계적 반려 |
| **상한** | `VISUAL_CRAFT_MAX_ROUNDS`(기본 2) 초과 또는 동일 결함 반복(stalled) 시 자동 수정 중단 → Auditor/인간 에스컬레이션 |
| **물리 검증** | `evidence_validator.py` Gate 5가 `visual_craft_review.json`(verdict=FAIL)을 읽으면 게이트 자체를 반려 |

토큰/쿼터 보호: 라운드당 스크린샷 최대 3장, 감사 1회, 상한 2회. `VISUAL_CRAFT_LOOP_ENABLED=0` 으로 즉시 비활성화할 수 있습니다.

---

## 3. 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `VISUAL_CRAFT_LOOP_ENABLED` | `1` | 폐루프 전체 On/Off |
| `VISUAL_CRAFT_VISION_ENABLED` | `1` | 4단계 Vision 비평 On/Off (0이면 결정적 감사만) |
| `VISUAL_CRAFT_SCORE_THRESHOLD` | `80` | Vision 점수 미달 시 FAIL |
| `VISUAL_CRAFT_MAX_ROUNDS` | `2` | 자동 반려(재작업) 상한 |
| `VISUAL_CRAFT_TIMEOUT` | `150` | Vision 비평 타임아웃(초) |
| `VISUAL_CRAFT_CAPTURE_URL` | (없음) | 웹 프로젝트 실 프레임버퍼가 없을 때 Playwright 헤드리스 캡처할 프리뷰/배포 URL |
| `ANDROID_DENSITY` | `3.0` | UI Hierarchy px→dp 환산 밀도 |

웹 프로젝트의 자동 캡처는 `VISUAL_CRAFT_CAPTURE_URL` 또는 `reports/gate5/deploy_url.txt` / `.agy_web_url` 파일이 있을 때만 동작하며,
캡처 이미지는 `reports/gate5/screenshots/visual_craft_auto_*.png` 로 저장되어 Gate 5 증거로도 함께 쓰입니다.
(`Canvas/PIL 합성 금지` 원칙에 따라 오직 실제 브라우저 렌더링만 캡처합니다.)

---

## 4. 산출물/디버깅

```bash
# 수동 1회 실행 (특정 워크스페이스)
cd agy_agent && python3 visual_craft_loop.py /path/to/project

# 결과 확인
cat /path/to/project/reports/gate5/visual_craft_review.json   # 판정/결함/지시문
cat /path/to/project/reports/gate5/visual_craft_state.json    # 라운드 이력(stalled 감지)
cat /path/to/project/reports/gate2/design_contract.json       # 수치 토큰 계약

# 테스트
python3 -m pytest tests/test_visual_craft_loop.py -q
```

---

## 5. 기대 효과 (무인 자율 개발 한정)

1. **첫 화면이 도구가 된다** — 마케팅 히어로가 사라지고 최근 문서/작업 공간이 상단을 차지한다.
2. **액션 위계가 하나로 수렴한다** — FAB/상단바 1개 액션 + 하단 Thumb Zone.
3. **여백/타이포/색상이 규격화된다** — 4/8pt 그리드, 3단계 타이포, Primary 1종 + Neutral.
4. **"예쁘게 만들어줘" 프롬프트에 의존하지 않는다** — 수치 계약 + 실측 + Vision 3중 검증이 항상 돌아간다.
