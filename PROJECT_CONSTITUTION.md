# 🏛️ PROJECT CONSTITUTION (프로젝트 개발 헌법)

> 본 프로젝트는 **AGY 자율 제품 생산 공장 체계**를 따르며, 모든 에이전트(Coder, QA Reviewer, Auditor)는 아래 5대 시스템 불변원칙을 강제 준수합니다.
> 상세 플랫폼 기술 규칙은 바인딩된 `profiles/active_profile.md`를 단독 로드하여 적용합니다.

---

## 1. ⚙️ 기계적 증거 최우선 원칙 (Mechanical Evidence > Semantic Claims)
* **Evidence 필수성**: "PASS"는 AI의 주관적 주장이 아니라 디스크 상의 검증 가능한 물리적 산출물(Exit code 0, 단위/E2E 테스트 성공 로그, 실 프레임버퍼)로만 인정됩니다.
* **Anti-Mocking & 위조 금지 (Anti-Tracing Law 포함)**: 
  - 프로덕션 코드 수정 없는 테스트 전용 패치(Test-only patch) 금지
  - 임의의 Canvas/PIL 가짜 이미지 합성, 레퍼런스 이미지 외곽선 모사/표절(Anti-Tracing Law), 또는 가짜 리포트(`{"status": "PASSED"}`) 작성 적발 시 즉시 Gate 반려

## 2. 🎯 도메인 프로파일 격리 원칙 (Domain Profile Binding)
* 워크스페이스는 단 1개의 활성 도메인 프로파일(`profiles/active_profile.md`)에 바인딩됩니다.
* 해당 프로파일의 가드레일(CLI/데몬 시 웹 UI 금지, 네이티브 앱 시 웹 래핑 금지 등)은 `evidence_validator.py`를 통해 물리적 코드로 강제 검증됩니다.

## 3. 🛡️ 7대 Quality Gate 체계
* **Gate 0 (초기화 & 헌법)**: 워크스페이스 및 프로파일 무결성 확인
* **Gate 1 (기획)**: `PRD.md` 명세 (REQ-001~)
* **Gate 2 (설계)**: `ARCHITECTURE.md` 또는 `DESIGN_SPEC.md`
* **Gate 3 (조립)**: 프로덕션 소스코드 물리적 구현 (도메인 금지 확장자 차단)
* **Gate 4 (기능 검증)**: `reports/gate4/e2e_test.json` 실제 테스트 통과
* **Gate 5 (시각/상태 검증)**: 실기기/브라우저 프레임버퍼 또는 CLI/데몬 상태 무결성
* **Gate 6 (회귀 방지)**: `reports/gate6/regression_test.json` (기존 REQ 회귀 잠금)
* **Gate 7 (릴리즈)**: 최종 산출물 해시 및 결함 0건 검증

## 4. 🔄 다단계 자율 복구 & 롤백 (Self-Healing & Rollback)
* 게이트 검증 실패 또는 과잉 수정 적발 시 마지막 정상 체크포인트로 즉시 롤백(`git checkout -- .`)합니다.
* 단계별 자율 복구(`Attempt 1`: 인라인 패치 ➔ `Attempt 2`: 구조 변경 ➔ `Attempt 3`: 대체 구현 ➔ `Attempt 4`: 롤백 후 재설계)를 수행합니다.

## 5. 💬 사용자 질문 최우선 답변 (User Question Direct Answer First)
* 사용자의 질의/진단/평가 요청 시, 내부 에이전트 로그나 회의록보다 **사용자 질문에 대한 명확하고 전문적인 답변을 최상단 1순위로 제공**합니다 (User Question Direct Answer First).

## 6. 📉 출력 토큰 다이어트 및 고밀도 개조식 보고 (Output Token Diet)
* 모든 에이전트 보고 및 메시지는 인사말, 서론, 결론 미사여구를 배제하고 **명사형 종결 개조식(1. 목적 / 2. 변경 [A➔B] / 3. 검증 결과)**으로 고밀도 작성합니다.
* 불필요한 다중 롤플레잉 회의록 노출을 지양하고 최종 요약과 물리적 검증 증거만 간결하게 브리핑하여 컨텍스트 팽창을 방지합니다.

## 7. 🎯 원자적 단일 마일스톤 및 내실 우선 헌법 (Atomic Milestone & Substance over Breadth)
* **단일 책임 원자성 (Atomic Scope)**: Commander는 1회차 마일스톤에 오직 1개의 핵심 도메인 역량만 발주합니다. 전문 검색 + 주석 드로잉 + 개인정보 마스킹 + 인쇄 내보내기 등 서로 다른 복합 서브시스템을 한 마일스톤에 묶어 발주하는 스코프 과밀(Scope Creep)을 전면 금지합니다.
* **기능 구현 ⇄ 담금질 1:1 교차 원칙 (Feature-Hardening Cadence)**: 신규 기능 추가 후에는 반드시 대용량 데이터/엣지 케이스 방어, 60fps 인터랙션 튜닝, 메모리 누수 제로화를 검증하는 담금질(Hardening) 단계를 거칩니다.
* **가짜 완성(Stub/Mock) 차단**: 디스크 소스코드 상의 `// TODO`, 빈 핸들러 함수, 더미 반환값(Mock) 등 껍데기 코드는 적발 시 Auditor가 즉시 Gate 탈락 처리합니다. 겉만 번지르르한 10개의 부실한 기능보다 완벽히 동작하는 1개의 코어 기능을 절대적으로 우선합니다.

## 8. 🎨 시각 완성도 계약 (Visual Craft Contract · GUI 프로젝트 강제)
* **작업 도구 우선 (Tool-first)**: 앱/웹의 첫 화면은 마케팅 랜딩페이지가 아니라 작업 공간입니다. 상단 히어로/홍보 배너를 1차 콘텐츠 위에 배치하지 않습니다.
* **수치 규격화**: 여백은 4/8pt 그리드, 타이포는 3단계(Display/Body/Caption), 색상은 Primary 1종 + Neutral 계조로 제한합니다.
* **공개 표준 그라운딩 (Public Standard First)**: 플랫폼별 공개 디자인 표준(Google Material 3 · Apple HIG · Microsoft Fluent 2 · IBM Carbon)을 기본값으로 삼습니다. 사용자 취향 입력이 없어도 형태(radius)·엘리베이션·인터랙션 상태·접근성(명암비 4.5:1)·필수 상태 커버리지(로딩/빈 상태/에러/비활성) 디테일을 표준에서 결정적으로 채우며, 사용자 입력은 오버라이드로만 반영합니다. 기준은 `design_systems_kb.py` 이고 계약의 `design_system` 섹션으로 Coder/QA 프롬프트에 강제 주입됩니다.
* **인체공학**: 주요 액션은 엄지 도달 영역(화면 하단 1/3·하단바·FAB)에 두고 최소 터치 타깃을 확보합니다.
* **중첩 금지**: 컨테이너(카드/박스) 3중 이상 중첩을 금지하고, 여백·구분선·타이포 위계로 구획합니다.
* **자동 검증 (무인 자율 `!mode 3/3+`)**: `reports/gate2/design_contract.json` + `reports/gate5/visual_craft_review.json`(verdict=PASS)이 Gate 5 통과 조건이며, MAJOR 결함 잔존 시 Coder 구조 재작업을 강제합니다.


## 9. 🎯 QC 수렴 & 정직성 계약 (Convergence & Honesty Contract · !mode 3 필수 적용)
* **수렴 지향 검증**: QA는 라운드가 반복될수록 잔여 결함이 감소하도록 판정한다. 이전 라운드에서 이미 APPROVED된 항목을 재반려하거나, 골대를 이동하는 행위(Goalposts Moving)는 금지된다.
* **차단/비차단 분리 (Proportionality Rule)**: REJECTED 사유는 사용자가 실제로 체감하는 차단 결함에만 국한된다:
  - **차단(REJECTED 가능)**: 빌드/컴파일 실패, 앱/런타임 크래시, 핵심 기능 미동작·클릭 불가·화면 잘림, 사용자 요청 미구현, Mock/Stub/TODO 껍데기·표절, 사용자 체감에 직결되는 도달 불가/오작동 경로
  - **비차단([차기 백로그]로만 기록, REJECTED 불가)**: 증거 문서 포맷 불일치, 게이트 JSON 내용 불일치, APK 해시 재현성, 캡처 타임스탬프 신선도 등 **메타 증거 인프라**, 리팩토링 권장, 코드 스타일, 미사용 토큰
* **정직성 게이트 (Honesty Gate · 상시 엄격)**: 위 비차단 면제는 **거짓 주장**에는 적용되지 않는다. 실행하지 않은 테스트의 통과 기재, 상태를 표현하지 않는 캡처의 근거 제출, 재현 불가 수치의 재현 주장 등은 즉시 REJECTED 대상이다. 계측이 불가하면 `BLOCKED`로 정직하게 기록하고 `PASS` 위장을 금지한다.
* **증거 범위 제한 (Evidence Scope Limit)**: Gate 5(시각/상태 검증)는 실제 앱 화면과 핵심 상태 전이에 한정된다.
* **진척 기반 수렴 (Progress-based Convergence)**: QA는 반려 시 `[BLOCKING_DEFECTS: N]`(사용자 체감 차단 결함 수) 마커를 표기한다. 시스템은 차단 결함 수가 2회 연속 감소하지 않으면 라운드 상한 전에 조기 에스컬레이션하고, 잔존 항목이 비차단뿐이면(`[BLOCKING_DEFECTS: 0]`) 백로그로 이관하고 승인으로 수렴한다.
* **라운드 상한 (Round Cap)**: 기본 `PAIR_MAX_ROUNDS=20`, `QA_MAX_ROUNDS=20`, `RESEARCH_MAX_ROUNDS=20` (`.env`로 조절 가능). 상한에 도달하면 곧바로 스탑하지 않고 **"N회가 끝났습니다. 이어서 진행하시겠습니까?"** 를 Discord 예/아니요 버튼으로 확인한다. '예' 선택 시 다음 라운드 배치를 이어서 진행하고, '아니요'/무응답 시 Commander가 사용자에게 현황 보고 후 안전하게 종료한다. 수렴 실패 시 `convergence_policy`가 상한 전에 조기 에스컬레이션한다.
