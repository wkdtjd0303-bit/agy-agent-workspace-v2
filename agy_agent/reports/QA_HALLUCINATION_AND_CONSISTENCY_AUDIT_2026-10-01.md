# QA 환각 재발요소 & 프로젝트 폴더별 동작 불일치 감사 (2026-10-01)

대상: AGY 멀티에이전트 러너 (`!mode 2` 사용자 주도 협업)
증거 채널: 1번(LogiFlow 코딩 요청이 리서치 파이프라인으로 오라우팅된 사건), 2번(Photoshop류 앱, 정상 코딩 파이프라인)

---

## 1. 결론 (TL;DR)

- **환각 자체는 "모델이 갑자기 미쳤다"가 아니라, 코딩 요청이 `ResearchRunner`(Analyst ➔ Fact Checker ➔ Auditor)로
  잘못 라우팅되어 생긴 구조적 사고였다.**
- 기존 수정(Analyst 판정태그 제거, 출력 가드, 라운드 상한 안내)은 재발요소를 **줄였지만 제거하지 못했다.**
  `ResearchRunner`에는 `trio_agent_runner`에 이미 있던 **수렴 & 정직성 계약(Convergence & Honesty Contract)** 이
  전혀 배선되어 있지 않았고, 리서치 오분류를 만드는 키워드 규칙도 그대로 남아 있었다.
- 두 재발요소를 모두 수정했다.
  1. `!mode 2`에서 코딩 요청이 리서치로 오분류되던 규칙 (실사용 프롬프트로 재현/수정 확인)
  2. `ResearchRunner` Fact Checker의 무한 반려 루프 (수렴 계약 + `[BLOCKING_DEFECTS]` + 조기 에스컬레이션)
- 폴더별로 봇의 흐름/양식이 달라 보인 원인도 확인해 수정했다.
  - **원인 A(주원인)**: 같은 `!mode 2`라도 프롬프트가 research로 분류되면 역할 체인이
    `Analyst ➔ Fact Checker ➔ Auditor`로, coding으로 분류되면 `Coder ➔ QA ➔ Auditor`로 갈라져
    메시지 양식이 통째로 달라짐 → 위 1번 오분류가 채널별로 다르게 발동한 것.
  - **원인 B**: 워크스페이스 그라운딩이 그 폴더의 **첫 ROADMAP 미완료 항목(또는 첫 PRD REQ)을
    "집중 과제"로 고정 주입**해, 동일한 사용자 요청에도 폴더마다 다른 과제로 답변이 오염됨.
    (예: photoshop_window 폴더는 `[차기 백로그] 텍스트 툴 · …`가 항상 주입됨)
- 도메인 프로파일 제약(web/android/cli)이 폴더별로 다른 것은 **의도된 격리(헌법 제2조)** 이며 버그가 아니다.

---

## 2. 재현 증거 (수정 전 → 수정 후)

### 2.1 1번 채널 코딩 요청이 리서치로 오분류

```bash
# 수정 전
python - <<'PY'
import mode_classifier as mc
p = open("ch1_prompt.txt", encoding="utf-8").read()
print(mc.classify_task_type(p), mc.classify_mode(p, "pair"))
PY
# → research  ('pair', '수동 고정 모드 (👥 3중 에이전트 심층 리서치 협업)')
```

- 오분류 원인: `RESEARCH_INDICATORS`의 `추천\s*(해줘|해|…)` 가
  사용자의 **불만 서술** "1235나 2345를 선택하도록 **추천해**주는 알고리즘적 오류"에 매칭됨.
- 수정 후:

```
task_type: consulting
mode(pair): ('pair', '수동 고정 모드 (👥 3중 에이전트 사용자 주도 협업 · !mode 2)')
```

→ `is_research=False` 가 되어 `Coder ↔ QA ↔ Auditor` 코딩 파이프라인으로 진입.
→ 정당한 리서치 프롬프트("미니 PC 가성비 추천해줘", "시세 확인해줘", "다나와 최저가 비교해줘")는
   여전히 `research` 로 유지(recall 보존).

### 2.2 Fact Checker 무한 반려 루프

1번 채널 로그의 반려 사유는 전부 **비차단 항목**이었다.

- `.firebaserc prod 별칭 삭제` (요구사항 무관)
- `클리핑 스크립트가 390px만 고정이라 1280px 스캔 증거 미비` (문서/증거 포맷)
- `라이브 E2E는 브라우저 미연동으로 재실행 불가` (환경 제약)
- `.NET 10 종료일 표기 1일 차` (정보 정확성 무관)

수정 후에는 Fact Checker 프롬프트가
- `[BLOCKING_DEFECTS: N]` 마커를 강제하고,
- 위 항목을 `[차기 백로그]`로만 기록하도록 명시하며,
- 반려 시 차단 결함이 0이면 **수렴 승인(강제 승인)**, 2회 연속 감소하지 않으면 **조기 중단**한다.

---

## 3. 변경 파일 (국소 변경)

| 파일 | 변경 |
|---|---|
| `agy_agent/mode_classifier.py` | `추천` 리서치 패턴을 실제 요청형만 매칭하도록 축소 (서술형 오탐 제거) |
| `agy_agent/research_runner.py` | Fact Checker에 수렴·정직성 계약/환각 금지 규칙 주입, `[BLOCKING_DEFECTS]` 파싱·기록, 비차단 반려 강제 승인, 정체 시 조기 에스컬레이션, 텔레메트리 export |
| `agy_agent/agent_runner.py` | `[차기 백로그]`는 집중 과제로 주입 금지 + 현재 프롬프트와 관련된 과제만 주입 |
| `agy_agent/tests/test_mode_classifier_comprehensive.py` | 실사용 프롬프트 회귀 테스트 추가 |
| `agy_agent/tests/test_convergence_policy.py` | ResearchRunner 배선 고정 + 1번 채널 메타 니트픽 = 차단 0건 회귀 테스트 |
| `agy_agent/tests/test_domain_profile_and_prompt_diet.py` | 무관한 고정 과제 미주입 회귀 검증 추가 |

---

## 4. 검증

### 4.1 대상 서브셋 (변경 모듈을 직접 검증하는 9개 파일)

```bash
.venv/bin/python -m pytest \
  tests/test_domain_profile_and_prompt_diet.py tests/test_convergence_policy.py \
  tests/test_mode_classifier_comprehensive.py tests/test_mode_distinction_ssot.py \
  tests/test_role_authority.py tests/test_caveman_compression.py \
  tests/test_qa_reviewer_output_freedom.py tests/test_round_continuation.py \
  tests/test_autonomy_no_question_mandate.py -q
# → 109 passed
```

- 실사용 프롬프트 회귀: `classify_task_type("…추천해주는 알고리즘적 오류…") != "research"`, `classify_mode(..., "pair")`가 리서치 라벨을 달지 않음.
- 수렴 계약 회귀: 1번 채널 로그의 메타 니트픽(디스크 증거 미비/브라우저 미연동/1일 차)이 `[BLOCKING_DEFECTS: 0]`(heuristic)으로 해석되어 강제 승인 조건을 만족.
- 그라운딩 회귀: 무관한 요청에서는 폴더별 고정 과제가 주입되지 않고, 관련된 요청에서만 주입됨.

### 4.2 전체 스위트

```bash
.venv/bin/python -m pytest tests -q -p no:cacheprovider -rf \
  --deselect "tests/test_session_manager_comprehensive.py::TestSessionManagerComprehensive::test_session_manager_reset_and_stop_all"
# → 4 failed, 1246 passed, 12 skipped, 1 deselected in 204.60s (0:03:24)
```

실패 4건은 이번 변경과 무관한 기존/환경 이슈:

| 실패 | 성격 | 근거 |
|---|---|---|
| `test_ling_real_execution::test_01_direct_openrouter_api_call` | 실제 OpenRouter 외부 API 호출 테스트 | 네트워크/키 필요. 오프라인 검증 대상 아님 |
| `test_opencode_latest_models` 3건 | 전체 실행 순서에서만 실패(테스트 간 상태 오염) | 동일 파일 단독 실행 시 `6 passed` / 변경 모듈과 무관 |

### 4.3 알려진 기존 이슈 (이번 변경과 무관, HEAD에도 동일)

`test_session_manager_comprehensive.py::test_session_manager_reset_and_stop_all` 실행 시
`reset_all_conversations() → stop_all_sessions() → kill_child_processes(os.getpid())` 가
pytest 프로세스 그룹에 SIGTERM을 보내 실행이 중단된다. HEAD(`git show HEAD:agy_agent/session_manager.py`)
에도 동일한 `kill_child_processes(os.getpid())` 경로가 있어 기존 문제이며, 이번 회귀 검증에서는
해당 1건만 제외(deselect)하고 측정했다. (별도 수정 필요 시 `kill_process_tree`에서 자기 PGID 보호 가드 추가)