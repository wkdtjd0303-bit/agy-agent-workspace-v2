# 🔧 `!model` 봇 ≠ 실제 동작 봇 문제 — 원인 분석 및 수정 보고서

- **작성일**: 2026-09-29
- **대상 시스템**: `!05_agy_agent` / Discord 자율 협업 AI 봇 (`agy_agent`)
- **증상(사용자 보고)**: "`!model` 로 **OpenCode** 봇으로 세팅해뒀는데 실제로는 **OpenRouter(Hermes)** 봇이 동작했다."

---

## 1. 진단 요약 (Root Cause)

런타임에서 **"표시되는 봇(설정)"과 "실제 실행 엔진"이 분리**되어 있었다. 원인은 서로 맞물린 3가지다.

| # | 원인 | 위치 | 결과 |
|---|------|------|------|
| **①** | 엔진-모델 정합성 가드가 **OpenCode 엔진에 Hermes(OpenRouter) 전용 slug** (`upstage/solar-pro4`, `deepseek/deepseek-chat` 등)를 "호환"으로 오인 | `model_manager.py` `is_model_compatible_with_engine()` | OpenCode 엔진 + OpenRouter 모델이라는 **실행 불가 조합이 그대로 저장**되고, 런타임에서 OpenRouter(Hermes) 분기로 샘 |
| **②** | QA1/QA2/Auditor 런타임이 **채널 세션의 오래된 `qa_engine` / `qa2_engine` / `auditor_engine` 값**을 `!model`(GlobalModelManager SSOT) 보다 먼저 읽음 | `trio_agent_runner.py` `run_qa_turn` / `run_qa2_turn` / `run_auditor_turn` | 화면(`!model`)에는 OpenCode 인데 실제 호출은 세션값(`agy`/`openrouter`)으로 결정 |
| **③** | 기동 로그/상태 라벨이 **`agy` 가 아니면 무조건 "Hermes"** 로 표기, QA2는 **"OpenRouter" 하드코딩** | `bot.py` `on_ready` / `on_qa2_ready` | OpenCode 로 돌아도 로그·표시가 "Hermes / OpenRouter" 로 찍혀 **오동작으로 오인** |

### 실측 증거 (진단 스크립트 실행 결과)

`state/global_model_config.json`·`.env`(=`!model` SSOT)는 이미 **전 역할 OpenCode** 로 정상이었다.
그러나 채널 세션 58개에 **과거 값이 잔존**하고 있었다.

```
 1) 전역 역할 설정 (GlobalModelManager = !model SSOT)
  coder      opencode   opencode-go/deepseek-v4.1-flash   ✅
  qa1        opencode   opencode-go/mimo-v2.6-flash       ✅
  qa2        opencode   opencode-go/deepseek-v4-flash     ✅
  auditor    opencode   opencode-go/deepseek-v4.1-flash   ✅
  ...

 2) 채널 세션의 stale 엔진 값 (런타임 덮어쓰기 위험)
  qa_engine      : 불일치 58건  (세션=agy         ≠ !model=opencode)
  qa2_engine     : 불일치 58건  (세션=openrouter  ≠ !model=opencode)   ← ★ 오픈라우터 봇의 정체
  auditor_engine : 불일치 58건  (세션=agy         ≠ !model=opencode)
```

> 즉, `!model` 은 OpenCode 를 가리키는데 채널 세션에는 `qa2_engine=openrouter` 가 남아 있었고,
> QA2 런타임이 이 세션값을 우선 사용하면서 **OpenRouter 봇이 실제로 실행**되었다.

---

## 2. 수정 내용

### 2-1. 엔진-모델 정합성 가드 강화 — `agy_agent/model_manager.py`

```python
if eng == "opencode":
    # [FIX] OpenCode CLI 는 'opencode-go/*' · 'opencode/*' 모델만 실행 가능하다.
    return owner == "opencode"        # 기존: ("/" in m) and (owner != "agy")
```

- 이제 OpenCode 엔진에는 OpenCode 소속 모델만 저장 가능.
- 정합성 위반 조합은 저장 시(`set_role_engine`) / 로드 시(`initialize`) 모두 **자동 교정**된다.
- Hermes slug 를 고르면 엔진이 모델 소속(Hermes)으로 되돌아가므로 **거짓 OpenCode 표시가 사라진다.**

### 2-2. 런타임 엔진 결정을 `!model` SSOT 우선으로 — `agy_agent/trio_agent_runner.py`

`run_qa_turn`(QA1) / `run_qa2_turn`(QA2) / `run_auditor_turn`(Auditor) 3곳에서
**GlobalModelManager(`!model`) → 채널 세션 → config** 순으로 우선순위를 변경했다.

```python
# run_qa_turn
target_engine = GlobalModelManager.get_role_engine("qa1") or getattr(session, "qa_engine", "agy") or "agy"
# run_auditor_turn
target_engine = GlobalModelManager.get_role_engine("auditor") or getattr(session, "auditor_engine", None) or "agy"
# run_qa2_turn
"engine": GlobalModelManager.get_role_engine("qa2") or getattr(session, "qa2_engine", config.QA2_ENGINE) or "agy",
```

### 2-3. 표시 정직성 + 세션 정합화 — `agy_agent/bot.py`

- **`_engine_display_label()` 신설**: `agy` 외 전부 "Hermes" 로 뭉뚱그리던 표기를 실제 엔진명으로 정정
  (예: `🚀 [1/3] OpenCode Coder Bot ...`, `🕵️♂️ [2/3] OpenCode qa2 Bot ...`).
- **`sync_sessions_with_global_model()` 신설**: `!model` 변경 시 **모든 채널 세션의 stale 엔진 필드**를
  SSOT 로 즉시 정합화. 기동 시(`on_ready`)에도 1회 치유 → `설정한 봇 ≠ 실제 봇` 근원 제거.

### 2-4. 재발 방지 테스트 — `agy_agent/tests/test_model_routing_ssot.py` (신규, 9 tests)

- OpenCode 엔진이 Hermes slug 를 호환으로 오인하지 않음
- `!model` 로 OpenCode 선택 시 모델이 OpenCode 모델로 교체됨 / Hermes 모델 선택 시 엔진이 Hermes 로 정합화
- QA1/QA2/Auditor 런타임이 stale 세션값 대신 `!model` 엔진을 사용함
- `sync_sessions_with_global_model()` 이 세션을 치유함

### 2-5. 진단 도구 — `agy_agent/scripts/diagnose_model_routing.py` (신규)

```bash
cd agy_agent && ./.venv/bin/python scripts/diagnose_model_routing.py
```
역할별 엔진-모델 정합성과 채널 세션 stale 값을 한 번에 출력한다.

---

## 3. 검증 결과

| 항목 | 결과 |
|------|------|
| 신규 회귀 테스트 `test_model_routing_ssot.py` | ✅ 9 passed |
| 정합성 테스트 `test_model_engine_coherence.py` | ✅ 10 passed |
| 전체 테스트 스위트 | ✅ **1170 passed**, 11 skipped |
| 실패 2건 (기존/환경 의존, 본 수정과 무관) | ⚠️ `test_ling_real_execution`(OpenRouter HTTP 404), `test_opencode_latest_models`(라이브 CLI 모델 30개<35) |
| 진단 스크립트 | ✅ 전 역할 정합성 100% |

> 위 2건은 네트워크/외부 CLI 의존 테스트로 수정 전에도 동일하게 실패하던 항목이다.

---

## 4. 적용(반영) 방법

코드 수정은 이미 워크스페이스에 반영되었다. **실행 중인 봇 프로세스가 새 코드를 로드**하도록 아래 중 하나를 수행한다.

1. **무중단 핫리로드 (권장)**: Discord 에서 `!reset` (또는 `!리셋`) → 모듈 재로드 + 세션 정합화
2. **서비스 재시작**:
   ```bash
   systemctl --user restart agy-discord-bot
   # 또는: !05_agy_agent/agy_agent/service.sh restart
   ```
3. 적용 확인:
   ```bash
   journalctl --user -u agy-discord-bot -n 50 --no-pager | grep -iE 'OpenCode|ModelRouting|Healed'
   ```
   기동 로그에 `OpenCode Coder Bot` / `Healed stale engine fields ...` 가 보이면 정상.

---

## 5. 요약

- **원인**: `!model`(SSOT)과 채널 세션의 stale 엔진값이 충돌 + OpenCode 엔진에 Hermes 모델이 저장 가능 + 표시 라벨 오류.
- **수정**: 정합성 가드 강화 + 런타임 SSOT 우선 + 세션 자동 치유 + 표시 정정 + 회귀 테스트/진단도구.
- **결과**: 이제 `!model` 에서 고른 엔진(OpenCode)이 표시·실행·로그 모두에서 일치한다.
