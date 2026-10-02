# 🔎 QA 봇 이상 동작 근본 원인 분석 & 수정 보고서

- 작성일: 2026-10-01
- 대상: `!05_agy_agent/agy_agent` (실행 경로: `agy-discord-bot.service`의 WorkingDirectory와 동일)
- 신고 증상: (1) qa1 메시지가 지나치게 짧음, (2) 프로젝트와 무관한 "미니PC/.NET/Firestore 시세·정책" 팩트체크, (3) 폴더별로 다르게 동작하는 듯한 불일치

---

## 1. 결론 (한 줄 요약)

**로그의 qa1은 코드 QA가 아니라 "리서치 파이프라인(ResearchRunner)의 Fact Checker"였습니다.**
사용자의 코딩 요청이 `mode_classifier`의 과도하게 넓은 리서치 정규식 때문에 **리서치 과제로 오분류**되어,
하드웨어/가격 팩트체크 전용 프롬프트(N100 미니PC·예산 10~20만원·알리 특가 예시 포함)가 주입되었습니다.

오분류를 일으킨 직접 원인은 **단독 명사 "금액"** 이었습니다.
정규식의 접미사 그룹이 `?`(선택)라서 "최종 합산 **금액**" 같은 평범한 업무 문구가 리서치 키워드로 잡혔습니다.

수정: 해당 정규식을 **실제 시세 조회/비교 동사가 뒤따를 때만** 매칭하도록 좁혔습니다. (1줄 + 회귀 테스트)

---

## 2. 증거: 디스코드 로그 = ResearchRunner 실행 흔적

로그의 전이(transit) 메시지가 `research_runner.py`의 문자열과 **문자 단위로 일치**합니다.

| 로그 메시지 | 코드 위치 |
|---|---|
| `📚 [Analyst 라운드 1 리서치 완료] Fact Checker에게 실시간 가격 및 스펙 팩트 검증을 요청합니다.` | `research_runner.py:205` |
| `❌ [Fact Checker 라운드 1 반려] Analyst에게 데이터 보강을 지시합니다.` | `research_runner.py:306` |
| `⚖️ Chief Auditor ... 실전 구매 적합성 최종 감사` | `research_runner.py:318` |

그리고 qa1의 "3~5줄 개조식" 어투와 무관한 팩트체크는 ResearchRunner의 Fact Checker 프롬프트에서 그대로 나옵니다.

`research_runner.py` 발췌:
- L138–150: Analyst 프롬프트에 **"1~2년 전 N100 미니PC 10만원대", "예산 10~20만원", "알리 직구 특가"** 예시가 하드코딩됨
- L242–243: Fact Checker 검증 관점 = **"가격 현실성 / RAM·SSD 스펙 / 실구매가"**
- L245, L251: **"3~5줄 내외의 명사형 종결 개조식으로 간결하게 작성"** ← 짧은 메시지의 원인
- L343: Auditor도 **"500~800자 이내 … 케이브맨 토큰 다이어트"**

즉, 로그의 `.NET 8 종료·Firestore 무료한도·N100 16/512 가격` 팩트체크는
**모델이 지어낸 것이 아니라, 리서치 QA 프롬프트가 시킨 검증 항목**입니다.

---

## 3. 근본 원인: 리서치 오분류 (`mode_classifier.py`)

### 3-1. 직접 원인 (수정 대상)

`RESEARCH_INDICATORS`의 금액/가격 패턴 — 접미사가 **선택(`?`)** 이라 단독 명사도 매칭:

```python
# 수정 전 (mode_classifier.py:145)
r"(금액|가격|시세|비용|실구매가|최저가|중고가)\s*(이|가|은|는|도|대|얼마|비교|확인|조회|정리|차이|다른|다르|알려)?",
```

재현(실측):
```
입력: "… 선불/착불 표기 … 최종 합산 금액에서도 …"
매칭: '금액 '  ← 단독 명사가 리서치 키워드로 검출
```

### 3-2. 왜 코딩 의도가 밀렸나

`classify_task_type()`은 아래 우선순위로 판정합니다 (L400 부근):

```python
if has_research and not (has_bug_fix or (has_dev and is_code_explicit)):
    return "research"
```

- 이 요청에는 `has_dev=True`("개선해줄래")가 있었지만,
- `is_code_explicit`(코드/API/CSS/구현/DB 등의 **문자열 키워드**)가 없어 가드가 작동하지 않았습니다.
- 결과: `has_research=True` → **research** → `classify_mode`도 "3중 에이전트 심층 리서치 협업"을 반환.

### 3-3. 파이프라인 분기 (오분류가 실제 실행으로 연결되는 지점)

- `bot.py:4785–4789, 4900–4909`: `is_research`이면 `ResearchRunner.run_pipeline(...)`
- `trio_agent_runner.py:1116–1132`: pair 파이프라인 내부에서도 research면 ResearchRunner로 위임

→ 로그의 "Coder(Gemini)로 표시된 분석가 보고 + Fact Checker qa1" 조합이 정확히 이 경로입니다.
(Analyst는 Coder와 동일 모델을 쓰고 coder 봇으로 게시되므로 "Coder"로 보였습니다.)

---

## 4. "폴더별로 다르게 동작하나?" → 아님 (설정은 전역)

- 모델 배정은 **단일 전역 파일** `state/global_model_config.json` (SSOT)입니다. 폴더별 파일이 아닙니다.
  - 현재: `qa1 = opencode / opencode-go/mimo-v2.6-flash`, `coder = opencode / opencode-go/deepseek-v4.1-flash`
  - `state/channel_sessions.json` 전 채널을 스캔해도 **채널별 qa_model 오버라이드는 0건**.
- 과거에 QA가 Gemini로 보였던 것은 **시점 차이**(전역 `!model` 로테이션)일 뿐, 프로젝트별 분기가 아닙니다.
- 실제 분기 변수는 (a) 채널별 `mode`(예: logiflow = `pair` 고정), (b) **메시지 내용 기반 task_type 분류**입니다.
  즉 이번 문제는 "폴더"가 아니라 **그 요청 문장에 '금액'이 들어 있었기 때문**입니다.
- 부수적으로 `qa1`의 표시 이름이 리터럴 `"qa1"`(모델 배지 없음)이라 더 건조하게 보입니다(`model_manager.py:800–804`).

---

## 5. 수정 내용 (최소·국소)

| 파일 | 변경 |
|---|---|
| `agy_agent/mode_classifier.py:144–148` | 금액/가격 리서치 패턴을 **시세 조회·비교 동사가 뒤따를 때만** 매칭하도록 축소 (선택 접미사 제거) |
| `agy_agent/tests/test_mode_classifier_comprehensive.py` | 회귀 테스트 추가: 비즈니스 '금액' 명사가 research로 오분류되지 않음 + 정당한 시세 리서치 recall 보존 |

```python
# 수정 후
r"(금액|가격|시세|비용|실구매가|최저가|중고가)\s*(?:이|가|은|는|도|만)?\s*(?:비교|조회|알려|정리|차이|다른|다르|얼마|확인)",
```

---

## 6. 검증 결과 (실행한 것만 기재)

| 검증 | 명령 | 결과 |
|---|---|---|
| 오분류 재현/교정 | `classify_task_type(로그 요청)` | 수정 전 `research` → 수정 후 `coding` |
| 리서치 recall 보존 | `가격만 알려줘` / `시세 확인해줘` / `다나와 최저가 비교해줘` | 전부 `research` 유지 |
| 코딩 회귀 | API/컴포넌트/버그수정/합산금액 기능 요청 | 전부 `coding` 유지 |
| 분류기 단위테스트 | `pytest tests/test_mode_classifier_comprehensive.py` | **11 passed** |
| 분류/오케스트레이션 10종 | `pytest tests/test_mode_classifier_comprehensive.py tests/test_single_plus_mode.py tests/test_multi_agent_unconditional_transition.py tests/test_runtime_orchestration.py tests/test_mode_distinction_ssot.py tests/test_qa2_plus_mode.py tests/test_dynamic_mode2_count.py tests/test_targeted_qa_selection.py tests/test_adversarial_collaboration.py tests/test_mode3_mode3plus_exact_copy.py` | **78 passed, 0 failed** |
| 전체 스위트 A/B | 27 failed (unrelated: depth_first_scope_governor 등) — **수정 전/후 동일** | 회귀 아님 입증 |

> 전체 스위트의 27건 실패는 `depth_first_scope_governor`, `e2e_200`, `opencode_latest_models` 등
> **본 변경과 무관한 기존 실패**이며, 정규식을 원복한 A/B 실행에서도 27 failed로 동일했습니다.

---

## 7. 적용 시점

- bot 서비스 WorkingDirectory가 수정 파일과 동일합니다.
- `mode_classifier`는 `bot.py`의 hot-reload 우선순위 목록에 포함되어 있어(`bot.py:383, 451–454`)
  다음 명령 처리 시 자동 반영됩니다. (완전 보장을 원하면 서비스 재시작: `service.sh restart`)
- 현재 실행 중인 bot 프로세스는 확인되지 않았습니다(재시작 시 즉시 적용).

---

## 8. 잔여 리스크 / 권고 (비차단)

1. 다른 광범위 리서치 패턴도 유사 오분류 여지가 있습니다:
   `분석\s*(…)? `, `조사\s*(…)? `, `정리\s*(…)? ` 처럼 **단독 명사 매칭(선택 접미사)**.
   다음에 실제 오분류 사례가 나오면 동일 원칙(동사 필수화)으로 좁히는 것을 권장합니다.
2. 분류 우선순위의 근본 개선안: `is_code_explicit` 하드코딩 목록에 `인터페이스/화면/탭/페이지/기능` 등 UI 개발 어휘를 추가하면
   "코드"라는 단어 없는 정상 코딩 요청의 리서치 오분류를 한층 더 줄일 수 있습니다(이번엔 미적용, 최소 변경 원칙).
3. `qa1` 표시 이름이 모델 배지를 안 달아 건조해 보입니다. 원하면 표시 포맷 개선 가능.
