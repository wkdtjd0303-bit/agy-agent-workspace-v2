# 🚀 Profile: Backend API & Microservices (backend_api_rules)

> **적용 분야**: 100% High-Performance Backend API, Microservices (FastAPI, NestJS, Go, Express)

## 1. 플랫폼 및 아키텍처 가드레일 (Platform Guardrails)
* **공식 플랫폼 규격**: Headless REST / GraphQL / gRPC Backend API (FastAPI, NestJS, Node.js, Go)
* **🚫 엄격 금지 (Physical Block)**:
  - 단순 정적 프론트엔드 HTML/CSS 목업 생성 엄격 금지 (API 전용 프로젝트에 불필요한 UI 파일 생성 금지)
  - 입력값 검증 없는 날것의 요청 처리 금지 (Pydantic / DTO 필수)
  - 동기 블로킹 I/O로 인한 이벤트 루프 정체 방치 금지
* **API & 데이터베이스 표준**:
  - OpenAPI 3.0 / Swagger 문서와 코드 100% 동기화
  - 표준 HTTP 상태 코드 준수 (200, 201, 400, 401, 403, 404, 422, 500) 및 표준 에러 응답 포맷
  - 비동기 DB 커넥션 풀링 관리 및 커넥션 누수 0건

## 2. 품질 검증 및 증적 (Verification Engine)
* **테스트 프레임워크**: `pytest` + `httpx` (Python) / `Jest` + `supertest` (Node.js)
* **검증 대상**:
  - 모든 엔드포인트의 입력 유효성 검사 및 엣지 케이스 테스트
  - DB 마이그레이션(Alembic, Prisma 등) 적용 및 롤백 무결성
  - 동시 요청 부하 시의 안정성 및 응답 시간
* **최종 산출물**: 실행 가능한 서버 스크립트, DB 마이그레이션 파일, Dockerfile, API 테스트 전수 통과 리포트
