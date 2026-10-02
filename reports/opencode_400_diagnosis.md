# OpenCode HTTP 400 진단 리포트

- 생성 시각: 2026-09-30 00:27:07
- opencode: `/home/jangsung/.opencode/bin/opencode` (['opencode v2.0.12'])
- 라이브 모델 수: 479

## 반복 400 세션

- `ses_f12688526ffevZo3SM8kUnaXH4` ×3

## 원인별 발생 횟수

- D. opencode 백그라운드 서비스 정체: 29회
- E. 네트워크/전송 실패 (Upstream 연결 불가): 10회
- C. 잘못된 모델 ID: 5회
- A. 누적 컨텍스트 초과/손상 등으로 인한 provider 400 (세션 재사용 시 재발): 5회
- B. OpenCode Zen 프라이버시: 요청 데이터 학습 엔드포인트 미허용: 2회

## 설정 모델

- `CODER_MODEL` = `opencode-go/deepseek-v4.1-flash` ✅
- `QA_MODEL` = `opencode-go/mimo-v2.6-flash` ✅
- `QA2_MODEL` = `opencode-go/deepseek-v4-flash` ✅
- `QA3_MODEL` = `opencode-go/mimo-v2.5` ✅
- `QA4_MODEL` = `opencode-go/glm-5.3-flash` ✅
- `QA5_MODEL` = `opencode-go/qwen3.8-flash` ✅
- `QA6_MODEL` = `gemini-3.8-flash-high` ✅
- `AUDITOR_MODEL` = `gemini-3.8-flash-high` ✅
- `COMMANDER_MODEL` = `opencode-go/mimo-v2.6-flash` ✅
- `IMAGE_MODEL` = `gemini-3.8-flash-high` ✅
