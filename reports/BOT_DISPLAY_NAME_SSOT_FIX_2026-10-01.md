# 🏷️ 봇 표시 이름 SSOT 정합화 — "Gemini 3.7 Flash High" 오표기 수정 보고서

- 작성일: 2026-10-01
- 대상: `/home/jangsung/Desktop/autocoding/!5_agy_agent/agy_agent`
- 신고 증상: 디스코드에서 `🕵️ [Fact Checker(Gemini 3.7 Flash High) 검증 중]` 이 뜨는데,
  실제 세팅은 Gemini 3.7 Flash High 가 아님. → 봇 동작 배너의 모델 이름을 `!model` 표기와 동일하게 맞춰달라.

---

## 1. 원인 (한 줄)

`QuotaManager.resolve_qa_model()` 이 **모델 문자열에 `"flash"` 가 포함되면 무조건 `"Gemini 3.7 Flash High"` 로 하드코딩**해서
표시명을 만들고 있었습니다. 현재 `qa1` 은 `opencode-go/mimo-v2.6-flash` 인데, 이 문자열에 `flash` 가 들어 있어 Gemini 로 오표기됐습니다.

```python
# 수정 전 (trio_agent_runner.py:242)
display_name = (
    "Gemini 3.1 Pro High (Fixed QA)" if "3.1" in base_model.lower()
    else ("Gemini 3.7 Flash High" if "flash" in base_model.lower() else base_model)
)
```

`!model`(및 디스코드 봇 닉네임/프레즌스)은 `GlobalModelManager.get_role_model_display()` →
`format_model_name()` 을 사용하므로 `qa1 = "MiMo 2.6 Flash"` 로 표기됩니다.
즉 **런타임 작업 배너만 다른 포맷터(하드코딩)를 써서 `!model` 과 어긋났습니다.**

### 실측 (수정 전/후)

| 경로 | 값 |
|---|---|
| `!model` qa1 (`get_role_model_display("qa1")`) | `MiMo 2.6 Flash` |
| 수정 전 배너 (`resolve_qa_model` display) | `Gemini 3.7 Flash High` ❌ |
| 수정 후 배너 (`resolve_qa_model` display) | `MiMo 2.6 Flash` ✅ |

---

## 2. 수정 내용 (표시명 SSOT 통일)

원칙: **봇이 실제 사용하는 모델의 표시명은 `!model` 과 동일한 `GlobalModelManager.format_model_name()` 을 단일 소스로 사용한다.**

| 파일 | 변경 |
|---|---|
| `agy_agent/trio_agent_runner.py` | `resolve_qa_model()` 의 `display_name`/`fallback_display` 를 `format_model_name()` 기반으로 변경. `_pick_qa_fallback()` 의 백업/원본 유지 표시명도 동일 포맷 적용. |
| `agy_agent/research_runner.py` | Chief Auditor 배너의 하드코딩 `Gemini 3.1 Pro High` → `get_role_model_display("auditor")` |
| `agy_agent/consulting_runner.py` | Chief Strategist 배너의 하드코딩 `Gemini 3.1 Pro High` → `get_role_model_display("auditor")` |
| `agy_agent/tests/test_engine_manager.py` | 기존 기대값을 SSOT 표기로 갱신 + `flash` 오표기 회귀 테스트 추가 |

> 참고: `trio_agent_runner`/`research_runner`/`consulting_runner` 의 **Coder/Auditor/QA 프레즌스 및 닉네임**은
> 이미 `GlobalModelManager.get_role_model_display()` 를 쓰고 있어(`bot.py:sync_all_bot_presences` 등) 수정 불필요했습니다.
> QA 계열만 예외적으로 하드코딩 포맷터를 써서 이번 불일치가 발생했습니다.

---

## 3. 검증 (실행한 것만 기재)

| 검증 | 명령 | 결과 |
|---|---|---|
| 수정 파일 문법 | `python -m py_compile trio_agent_runner.py research_runner.py consulting_runner.py bot.py model_manager.py auto_pilot.py` | **Exit 0** |
| 실제 설정 대조 | `resolve_qa_model()` vs `get_role_model_display("qa1")` | 둘 다 `MiMo 2.6 Flash` 일치 |
| 핵심 회귀 | `pytest tests/test_engine_manager.py tests/test_runtime_orchestration.py tests/test_unblock_opus_and_gpt_oss.py tests/test_mode_classifier_comprehensive.py -q` | **42 passed** |
| 모델/엔진/프레즌스 스위트 | `pytest test_bot_presence_models, test_dynamic_model_synchronization, test_model_manager, test_model_routing_ssot, test_model_engine_coherence, test_trio_resilience, test_qa2_plus_mode, test_dynamic_qa_expansion, test_in_channel_qa_execution, test_token_engineering_and_path_diet` | **171 passed** |
| 리서치/자문 파이프라인 | `pytest test_mode_distinction_ssot, test_caveman_compression, test_mode2_auditor_conclude_flow, test_role_authority` | **39 passed** |

---

## 4. 적용 시점

- 현재 `agy-discord-bot.service` 는 **inactive** (실행 중 아님).
- 변경한 3개 러너 모듈은 `bot.py` 의 **hot-reload 대상 목록에 포함**되어 있어, 다음 기동/명령 처리 시 자동 반영됩니다.
  (`bot.py` hot-reload 목록: `trio_agent_runner`, `research_runner`, `consulting_runner`)

---

## 5. 잔여 (비차단 / 범위 외)

- `auto_pilot.py` 의 AutoPilot 배너는 하드코딩 `Gemini 3.1 Pro High` 를 표기하지만,
  이는 `!model` 역할 모델이 아니라 **AutoPilot 전용 `config.QA_AUTO_MODEL`** 을 사용하는 별도 자율 기능입니다.
  이번 요청(역할 봇 배너 ↔ `!model` 일치) 범위 밖이라 변경하지 않았습니다.
