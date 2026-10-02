# 🧹 불필요한 하드코딩 & 오탈자 전수 점검 보고서

- 작성일: 2026-10-01
- 대상: `/home/jangsung/Desktop/autocoding/!05_agy_agent/agy_agent` (실행 경로, `!5_agy_agent` 는 동일 트리로 연결된 심볼릭 뷰)
- 요청: "이 외에도 불필요한 하드코딩 요소나 오탈자 같은거로 인한 문제 없는지 점검"

---

## 1. 점검 방법 (실행한 것만)

| 점검 | 명령/수단 | 결과 |
|---|---|---|
| 하드코딩 디스코드 ID(멘션/채널) 전수 | `rg '<@!?1[0-9]{17,19}>|<#...>'` (테스트 제외) | 21건 → 아래 4건이 실제 결함 |
| 절대경로(사용자명) 하드코딩 | `rg '/home/jangsung/' -g '*.py'` | config/CLI 후보 경로 외 2건 결함 |
| 하드코딩 모델 표시명 | `rg 'Gemini 3\..*|Claude|GPT-OSS'` | AutoPilot 12곳 drift |
| 환경변수 이름 오탈자 | `config.py`의 `os.getenv(...)` ↔ `.env.example` 대조 | 오탈자 0 (동적 `f"QA{i}_MODEL"` 는 정상) |
| 영문 오탈자(단어경계) | `rg -P '\b(recieve|seperate|occured|...)\b'` | 0건 |
| 한글 오탈자 | `rg '역활|되요|몇일|데이타|메세지|...'` | 0건 |

---

## 2. 수정한 결함 (FIXED)

### F1. `trio_agent_runner.py` — 최종 감사 배너의 stale 하드코딩 멘션 ID (실버그)

- **증상**: 최종 감사 시작 트랜짓 배너가 `<@1539459595133521930>` 를 하드코딩. 이 ID는 코드베이스 어디에도 정의/설정이 없고, 프로젝트 SSOT(`config.AUDITOR_MENTION_USER_ID = 1257525029609017367`)와 **다른 사람을 핑**하고 있었음.
- **수정**: SSOT 변수로 교체 → `<@{AUDITOR_MENTION_USER_ID}>` (동일 파일 성공 태그가 이미 쓰던 것과 통일).

### F2. `discord_cli.py` — 채널 표시명 오표기 (오탈자)

- **증상**: `1538529408703266902` 를 `"!5_agy_agent"` 로 표기. 실제로는 admin/ANY QUESTION 채널(`config.ADMIN_CHANNEL_ID`, `DEFAULT_CHANNEL_WORKSPACES` = `!10_ANY QUESTION`, `telemetry_status_manager` = `ANY QUESTION`).
- **수정**: `"ANY QUESTION"` 으로 교정 (다른 SSOT 표기와 일치).

### F3. `safe_trash.py` — 기본 베이스 경로에 사용자명 하드코딩

- **증상**: `DEFAULT_AUTOCODING_BASE = "/home/jangsung/Desktop/autocoding"` — 다른 계정/머신에서 상대경로 해석·`!05_agy_agent` 보호 가드가 깨짐.
- **수정**: `os.path.join(os.path.expanduser("~"), "Desktop", "autocoding")` (config 등 다른 모듈과 동일한 홈 유도 방식).

### F4. `qa_hub.py` — 폴백 워크스페이스 절대경로 하드코딩

- **증상**: `exec_session.workspace or "/home/jangsung/Desktop/autocoding/!9_qa_hub"`.
- **수정**: `config.DEFAULT_CHANNEL_WORKSPACES.get(config.QA_HUB_CHANNEL_ID, os.path.join(config.DEFAULT_WORKSPACE, "!9_qa_hub"))` — config SSOT 사용.

### F5. `auto_pilot.py` — AutoPilot 감사 모델 표시명 하드코딩 (SSOT drift)

- **증상**: 배너/프롬프트에 `Gemini 3.1 Pro High` 가 12곳 하드코딩. 실제 모델은 `config.QA_AUTO_MODEL`(기본 `gemini-3.1-pro-high`)이며, `!model` SSOT 포맷터(`GlobalModelManager.format_model_name`)는 `Gemini 3.1 Pro` 로 표기 → **기본 설정에서도 `!model` 과 불일치**(직전 라운드 QA 표기 버그와 동일 계열).
- **수정**: `get_autopilot_model_display()` 헬퍼 신설 후 배너/프롬프트 전량 치환. 기본 출력 변화: `Gemini 3.1 Pro High` → `Gemini 3.1 Pro` (= `!model` 표기와 일치).

### F6. `bot.py` — qa2 봇 멘션 ID 하드코딩 (2곳)

- **증상**: `raw_user_prompt.replace("<@1545711652987600937>", ...)` 로 qa2 봇 멘션을 하드코딩 제거. 다른 모든 봇(qa1/auditor/image/extended)은 `bot.user.id` 로 동적 처리하는데 qa2만 리터럴 → 봇 재생성/ID 변경 시 멘션 제거 실패.
- **수정**: `if qa2_bot.user:` 가드 + `f"<@{qa2_bot.user.id}>"` 동적 치환(형제 봇과 동일 패턴).

---

## 3. 점검했으나 "문제 아님"으로 판정 (변경 안 함)

| 항목 | 판정 |
|---|---|
| `bot.py` 표시명 폴백(`if GlobalModelManager else "Gemini 3.7 Flash"` 등) | 예외/미초기화 시의 방어적 폴백. 기존 스타일 존중, 결함 아님 |
| `qa_wizard.py` 프리셋 라벨(`Gemini 3.1 Pro High 고정` 등) | 사용자가 **선택 가능한 프리셋 설명**으로 실제 모델과 대응됨 |
| `engine_manager.py` 후보 실행경로(`/home/jangsung/.local/bin/...`) | `shutil.which`/존재검사 폴백 체인. 의도된 탐색 목록 |
| `config.DEFAULT_CHANNEL_WORKSPACES` 절대경로 | `Path.home()` 기반 프리셋 매핑(포터블) |
| `commander.py`/`research_runner.py` docstring·주석의 `Gemini 3.7 Flash` | 코드 주석/문서(런타임 표시 아님) |
| 환경변수 이름 오탈자 | 0건 (동적 `QA{i}_*` 조회 포함 전수 대조) |
| 영/한 오탈자 | 0건 |

---

## 4. 잔여(비차단) — 후속 후보

1. **qa2 봇 ID 표시 문자열**: `bot.py:3156`(도움말), `mode_classifier.py:658`, `commander.py:634` 이 `1545711652987600937` 을 표시용으로 하드코딩. `config.QA2_BOT_ID` 상수를 신설하면 완전 SSOT화 가능(이번 최소변경 범위에서는 미적용).
2. `directory_manager.py` UI placeholder 의 `/home/jangsung/...` 예시 문자열(사용자 안내 문구, 기능 영향 없음).
3. **`agy_quota_guardian.py::parse_quota_buckets` 의 정확 일치 하드코딩 (검증 보류)**:
   `mid == "gemini-3-flash"` / `mid == "claude-sonnet-4-6"` 로 **정확 일치**만 인정하여, 카탈로그 모델명이 바뀌면 `{}` 를 반환 → 누수 감지가 조용히 무력화될 수 있음.
   또한 `m.get("remainingPercentage", 0.0) * 100` 은 값이 `None`(Claude/GPT-OSS 는 스펙상 None)일 때 `TypeError` 가능.
   → 시스템 표준(`telemetry_status_manager` 의 부분 일치 + `None` 스킵)으로 맞추는 것을 권장.
   **미적용 사유**: 이 샌드박스에서 `antigravity-usage quota --json` 실행이 read-only FS(`~/.config/antigravity-usage/tokens.json`)로 불가하여 실제 반환 스키마를 물리 검증할 수 없었음. 정확 ID를 추측으로 바꾸는 것은 오히려 위험하므로 보고만 함.

---

## 5. 검증 결과 (실행한 것만 PASS로 기재)

| 검증 | 명령 | 결과 |
|---|---|---|
| 수정 파일 문법 | `.venv/bin/python -m py_compile trio_agent_runner.py discord_cli.py safe_trash.py qa_hub.py bot.py auto_pilot.py` | **Exit 0** |
| 신규 회귀 테스트 | `pytest tests/test_hardcode_ssot_audit.py -q` | **6 passed** |
| 리스크 직결 스위트 | `pytest test_hardcode_ssot_audit, test_directory_manager_and_safe_trash, test_engine_manager, test_autopilot_idle_e2e_dispatch, test_autopilot_cancellation_and_stop, test_mode2_auditor_conclude_flow, test_qa2_plus_mode, test_trio_resilience, test_dynamic_model_synchronization, test_mode_classifier_comprehensive` | **131 passed, 2 skipped** |
| 핫리로드/리셋/프레즌스/QA허브 회귀 | `env PATH=/nonexistent pytest test_hot_reload_bridge, test_reset_context_and_code_reflection, test_bot_presence_models, test_agent_identity, test_command_error_and_suggestions, test_dynamic_qa_expansion, test_in_channel_qa_execution` | **100 passed** |

> 참고: `test_engine_manager::test_local_binary_detection_on_host` 는 `env PATH=/nonexistent` 로 실행할 때만 실패(hermes 버전 조회). 정상 PATH 에서는 **1 passed** — 본 변경과 무관한 환경 아티팩트.

---

## 6. 변경 파일

- `agy_agent/trio_agent_runner.py` (F1)
- `agy_agent/discord_cli.py` (F2)
- `agy_agent/safe_trash.py` (F3)
- `agy_agent/qa_hub.py` (F4)
- `agy_agent/auto_pilot.py` (F5, `get_autopilot_model_display()` 신설)
- `agy_agent/bot.py` (F6)
- `agy_agent/tests/test_hardcode_ssot_audit.py` (신규 회귀 6건)
