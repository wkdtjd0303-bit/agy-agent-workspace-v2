# Command Code CLI ZDR 상태 검증 보고 (2026-10-06)

## 질문
> commandcode CLI 에 ZDR 이 적용되지 않은 것 같다. `"zero data retention was enforced by you on this request."` 라고 나온다. 문서대로(`CMD_ZDR=1`) 적용해달라.

## 결론
**이미 적용되어 있으며, 현재도 정상 강제되고 있다 (별도 코드 변경 불필요).**
문제의 문구는 서버가 ZDR 요청을 인지했다는 응답이며(=ZDR 이 켜져 있다는 증거), ZDR 미지원 모델에서만 요청이 실패한다.

- 활성 경로: `agy_agent/config.py` → `os.environ["CMD_ZDR"]` → `CommandCodeAdapter.execute`(및 `Commander._run_cmdc_commander_query`)의 cmdc 서브프로세스가 상속 → CLI 가 `x-cmd-zdr: 1` 헤더로 전송.
- 러닝 봇(PID 3319, `!05_agy_agent/agy_agent`, 08:47 기동)은 `config.py` 변경(10-05 23:28) 이후 기동되어 ZDR 이 로드된 상태다. **재시작 불필요.**

## 물리적 증거 (실행 결과 그대로)

### 1) 설정 단일 출처 (`agy_agent/config.py`)
```
COMMAND_CODE_API_KEY = os.getenv("COMMAND_CODE_API_KEY", "")
...
CMD_ZDR = os.getenv("CMD_ZDR", "1").strip() or "1"
os.environ["CMD_ZDR"] = CMD_ZDR
```
`!5_agy_agent/agy_agent/config.py` 와 `!05_agy_agent/agy_agent/config.py` 는 동일 inode(22679594) → 같은 파일.

### 2) 런타임 env 전파
```
$ .venv/bin/python -c "import os, config; print('config.CMD_ZDR =', config.CMD_ZDR, '| os.environ CMD_ZDR =', os.environ.get('CMD_ZDR'))"
config.CMD_ZDR = 1 | os.environ CMD_ZDR = 1
```

### 3) 회귀 테스트
```
$ .venv/bin/python -m pytest tests/test_command_code_router.py tests/test_command_code_progress_stream.py -q
10 passed in 2.96s
```
`test_command_code_zdr_enforced_by_default`, `test_command_code_adapter_propagates_zdr_env` 포함 통과.

### 4) 실 CLI 스모크 — ZDR 지원 모델 (성공)
```
$ CMD_ZDR=1 cmdc -p "Reply with exactly: ZDR_OK" -t --yolo --tools-all --skip-onboarding --output-format json -m deepseek/deepseek-v4.1-flash
{"type":"result","subtype":"success",...,"finalText":"ZDR_OK"}   EXIT=0

$ CMD_ZDR=1 cmdc ... -m xiaomi/mimo-v2.6-flash
{"type":"result","subtype":"success",...,"finalText":"ZDR_OK"}   EXIT=0
```

### 5) 실 CLI 스모크 — ZDR 미지원 모델 (문서 사양대로 하드 실패)
```
$ CMD_ZDR=1 cmdc ... -m poolside/laguna-s-2.1-free
{"type":"event","event":{"type":"run_error","error":{"name":"TransportError",
  "message":"This model has no zero-data-retention upstream. Disable CMD_ZDR or pick a different model."}}}
Error: 400 This model has no zero-data-retention upstream. Disable CMD_ZDR or pick a different model.
EXIT=1
```

## 문구 해석 (공식 문서 기준)
문구 `"zero data retention was enforced by you on this request."` 는 Command Code **서버**가 생성하며(CLI 번들에는 존재하지 않음), 클라이언트가 ZDR 을 요청했음을 알리는 응답이다. 공식 ZDR 문서 사양:
- `CMD_ZDR=1`(CLI) 또는 `x-cmd-zdr: 1`(Provider API) 시 **ZDR 지원 업체로만 라우팅**.
- ZDR 지원 업체가 없으면 비-ZDR 로 **폴백하지 않고 실패**(Provider API 422 `cmd_zdr_no_providers`) — 모델: `poolside/laguna-s-2.1-free` 등 소수.
- 참고: https://commandcode.ai/docs/resources/zdr

즉 "적용 안 됨"이 아니라 "적용되어 있어서 비-ZDR 모델이 차단되는" 정상 동작이다.

## 운영 메모
- **비활성화가 필요한 경우**: `agy_agent/.env` 에 `CMD_ZDR=0` 추가 → 비-ZDR 라우팅으로 복귀(기본값은 `1`).
- cmdc 기본 모델 `deepseek/deepseek-v4.1-flash` 및 `xiaomi/mimo-v2.6-flash` 는 ZDR 지원 확인됨.
- ZDR 강제 시 소수 모델(예: 일부 `-free` 계열)은 사용 불가 → 해당 모델은 선택하지 않는다.
