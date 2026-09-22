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
