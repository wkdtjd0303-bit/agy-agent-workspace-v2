# ⚙️ Profile: CLI, Daemon & Automation Agent (cli_agent_rules)

> **적용 분야**: 100% Background Automation, CLI 도구, 데몬, 봇, 데이터 파이프라인, 파이썬/노드 자동화 스크립트

## 1. 플랫폼 및 아키텍처 가드레일 (Platform Guardrails)
* **공식 플랫폼 규격**: Headless CLI / Daemon / Bot / Automation Tool (Python, Node.js, Shell)
* **🚫 엄격 금지 (Physical Block)**:
  - 단순 보여주기식 정적 웹 UI / HTML / CSS / 프론트엔드 목업 생성 엄격 금지
  - 주객전도 UI 작업 금지 (CLI/백엔드 프로젝트에 `.html`, `.css`, `frontend/` 디렉터리 생성 시 물리적 Gate 반려)
  - 불필요한 무거운 Electron/Web 브라우저 래핑 금지
* **I/O & 프로세스 표준**:
  - `argparse` / `click` / `typer` 표준 인자 파싱 및 `--help` 매뉴얼 완비
  - 명확한 표준 스트림 분리 (`stdout`: 유의미한 결과, `stderr`: 에러 및 경고)
  - 명확한 프로세스 종료 코드 준수 (`0`: 성공, `non-zero`: 실패)
  - 비동기 이벤트 루프 및 자식 프로세스 타임아웃, 예외 격리(try/finally), 시그널(`SIGINT`/`SIGTERM`) 정상 핸들링

## 2. 품질 검증 및 증적 (Verification Engine)
* **테스트 프레임워크**: `pytest` / `unittest` 단위 및 통합 테스트 전수 통과
* **검증 대상**:
  - 각 서브커맨드의 exit code, stdout/stderr 출력 포맷
  - 파일 I/O 및 데이터 영속화(SQLite/JSON/ENV) 무결성
  - 비동기 데몬 헬스체크 및 프로세스 지속성
* **최종 산출물**: 실행 가능한 CLI 바이너리/스크립트, 무중단 데몬 서비스, 100% 통과 단위 테스트 리포트
