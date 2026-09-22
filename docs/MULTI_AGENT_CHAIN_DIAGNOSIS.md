# 🔍 다중 협업 모드(!mode 2, 2+) Coder ➔ QA ➔ Auditor 연계 누락 원인 정밀 진단 보고서

## 1. 개요
사용자가 디스코드 환경에서 `!mode 2`, `!mode 2+`와 같은 다중 협업 모드를 지정했음에도 불구하고, **Coder만 작업을 수행하고 QA(qa1, qa2~qa7) 및 Auditor로 파이프라인이 연계되지 않고 조기 종료되거나 Coder만 겉도는 현상**이 간헐적으로 발생하는 원인을 코드베이스 전수 정적 분석 및 시스템 로그를 통해 정밀 진단하였습니다.

---

## 2. 6대 핵심 원인 상세 분석

### ① [원인 1] 프롬프트 키워드 오분류에 의한 `ResearchRunner` 탈취 및 다중 QA 누락
- **관련 코드**:
  - [`classify_task_type()`](file:///home/jangsung/Desktop/autocoding/!05_agy_agent/agy_agent/mode_classifier.py#L367)
  - [`RESEARCH_INDICATORS`](file:///home/jangsung/Desktop/autocoding/!05_agy_agent/agy_agent/mode_classifier.py#L109-L144)
  - [`bot.py` ResearchRunner 분기](file:///home/jangsung/Desktop/autocoding/!05_agy_agent/agy_agent/bot.py#L4499-L4507)
  - [`ResearchRunner.run_pipeline()`](file:///home/jangsung/Desktop/autocoding/!05_agy_agent/agy_agent/research_runner.py#L56-L65)
- **메커니즘**:
  - 사용자가 `!mode 2+`로 설정해 두었더라도 프롬프트에 `"원인을 찾아봐줄래"`, `"분석해줘"`, `"조사해줘"`, `"비교해줘"`, `"추천해줘"`, `"알아봐줘"`, `"가격/스펙"` 등이 포함되면, `classify_task_type`이 작업을 `research`로 분류합니다.
  - `bot.py`는 `task_type == "research"`인 경우 `TrioAgentRunner` 대신 `ResearchRunner.run_pipeline`을 호출합니다.
  - **결과적 결함**:
    1. `ResearchRunner`는 `extended_qa_clients` 파라미터가 없어서 `!mode 2+`의 핵심인 다중 확장 QA(qa2 Solar, qa3 DeepSeek, qa4 Qwen 등)가 100% 누락됩니다.
    2. Coder에게 "수석 실시간 리서치 애널리스트 / 최신 시세 수집 / .md 및 .html 파일 저장 필수" 프롬프트가 주입되어 실제 코드나 시스템 버그 분석 대신 엉뚱한 리서치 행동을 요구받게 됩니다.

---

### ② [원인 2] Coder 응답 본문 이모지(`🚨`, `🛑`, `⚠️`)에 의한 `is_system_error()` 오판정 및 조기 탈출
- **관련 코드**:
  - [`TrioAgentRunner.is_system_error()`](file:///home/jangsung/Desktop/autocoding/!05_agy_agent/agy_agent/trio_agent_runner.py#L217-L250)
  - [`TrioAgentRunner` Coder 조기 탈출문](file:///home/jangsung/Desktop/autocoding/!05_agy_agent/agy_agent/trio_agent_runner.py#L925-L926)
- **메커니즘**:
  - Coder(LLM)는 답변 작성 시 가독성을 높이기 위해 `🚨 **[긴급 버그 수정 내역]**`, `🚨 **[원인 분석 결과]**`, `⚠️ **[오류 원인 해결 안내]**` 등의 이모지를 헤더로 자주 사용합니다.
  - 그러나 `TrioAgentRunner.is_system_error(text)`는 본문이 `🚨` 또는 `🛑`로 시작하거나 `⚠️`와 함께 "오류/error/실패"가 포함되면 **시스템 치명적 오류**로 판정합니다.
  - `trio_agent_runner.py` 925행에서 `if cls.is_system_error(latest_coder_output): return latest_coder_output` 구문이 동작하여, **QA1, 확장 QA, Auditor를 전혀 호출하지 않고 Coder 출력만 디스코드에 내보낸 뒤 파이프라인을 즉시 종료**해 버립니다.

---

### ③ [원인 3] `Zero-Production-Diff` 기계적 차단 게이트에 의한 QA 이관 원천 거부
- **관련 코드**:
  - [`EvidenceValidator.inspect_production_diff()`](file:///home/jangsung/Desktop/autocoding/!05_agy_agent/agy_agent/evidence_validator.py#L557-L620)
  - [`Zero-Production-Diff 차단 게이트`](file:///home/jangsung/Desktop/autocoding/!05_agy_agent/agy_agent/trio_agent_runner.py#L1070-L1098)
- **메커니즘**:
  - Coder가 작업을 완료했으나 소스코드 변경점이 설정 파일(`.env`, `package.json`, `build.gradle`, `Dockerfile`), 문서(`README.md`, `ARCHITECTURE.md`), 또는 `test_*.py`(테스트 파일)에만 존재하는 경우:
  - `inspect_production_diff`는 이를 프로덕션 소스코드(`*.kt`, `*.ts`, `*.py` 등) 변경으로 인정하지 않아 `is_zero_production_diff = True`로 판정합니다.
  - 이 게이트가 발동하면 **"QA 및 Auditor 검증 단계로 넘어가지 않고, Coder에게 즉시 Fail-Fast 재작업을 지시"**하며, 최대 라운드에 도달하면 QA/Auditor 없이 작업을 중단합니다.

---

### ④ [원인 4] Pre-Flight 컴파일 사전 점검 실패에 따른 자가수정 루프
- **관련 코드**:
  - [`Pre-Flight Compile Check Gate`](file:///home/jangsung/Desktop/autocoding/!05_agy_agent/agy_agent/trio_agent_runner.py#L1116-L1150)
- **메커니즘**:
  - 안드로이드/Gradle 프로젝트 등에서 Coder가 수정한 코드가 `./gradlew compileDebugSources`에서 컴파일 에러나 참조 에러를 발생시키는 경우, QA/Auditor 단계로 이관하지 않고 Coder 단계에서 Fail-Fast 재작업을 반복합니다.

---

### ⑤ [원인 5] OpenRouter / 서드파티 LLM 모델 쿼터 고갈 및 타임아웃
- **관련 코드**:
  - [`Extended QA 실행부 및 타임아웃`](file:///home/jangsung/Desktop/autocoding/!05_agy_agent/agy_agent/trio_agent_runner.py#L1613-L1639)
- **메커니즘**:
  - `!mode 2+`의 확장 QA는 OpenRouter API를 통해 다중 모델을 호출합니다.
  - 시스템 저널 로그 확인 결과, 일부 고사양 모델(Claude Opus/Sonnet, GPT-OSS 등)의 쿼터 소진 알림이 누적되고 있었습니다.
  - QA 단계에서 180초 타임아웃이나 API 오류가 발생하면 시스템 에러로 처리되어 Auditor로 넘어가지 못하고 중단될 수 있습니다.

---

### ⑥ [원인 6] 디스코드 봇 클라이언트 채널 권한 누락 (예외 크래시)
- **관련 코드**:
  - [`QA 채널 전송부`](file:///home/jangsung/Desktop/autocoding/!05_agy_agent/agy_agent/trio_agent_runner.py#L1243-L1248)
- **메커니즘**:
  - `qa_status_msg = await qa_target_channel.send(...)` 실행 시, 해당 채널에 QA 봇의 '메시지 보내기' 권한이 없거나 Discord API 403 Forbidden 오류가 발생할 경우, 비방어적 send 호출로 인해 예외가 상위로 전파되어 파이프라인이 즉시 크래시됩니다.

---

## 3. 권장 해결 방안 (패치 방향)

1. **`is_system_error()` 판정 로직 정교화**:
   - `stripped.startswith(("🛑", "🚨"))` 단순 접두사 체크 대신, 시스템 내부 에러 배너 규격(`🚨 **[Zero-Production-Diff`, `🛑 **[자율 안전 가드레일`, `🛑 사용자의 요청으로`)과 실제 Coder의 마크다운 헤더를 명확히 구분하도록 수정.
2. **`mode_classifier.py`의 과도한 리서치 오분류 방어**:
   - 질문형/원인조사형 프롬프트("원인을 찾아봐줄래", "분석해줘")가 시스템 질의나 코드 디버깅인 경우 `research`가 아닌 `coding` 또는 `diagnostic`으로 분류하도록 예외 키워드 추가.
3. **`ResearchRunner`에 Extended QA 호환 레이어 추가**:
   - `!mode 2+`에서 `ResearchRunner`가 실행되더라도 확장 QA가 누락되지 않도록 매개변수 연계 보강.
4. **`Zero-Production-Diff` 게이트의 설정/인프라/문서 작업 인식 확장**:
   - 설정 파일(`.json`, `.gradle`, `.env`, `.yml`) 및 테스트 전용 작성 요구 시 기계적 차단이 아닌 검증 모드로 유연하게 전환.
5. **Discord send 호출부에 `safe_send_discord_message` 적용**:
   - QA 및 Auditor 상태 메시지 전송부를 `try-except` 및 `safe_send_discord_message`로 감싸 권한 오류 시 메인 채널로 안전하게 폴백.
