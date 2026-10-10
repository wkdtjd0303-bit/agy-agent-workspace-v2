# Command Code CLI ZDR 적용 보고 (2026-10-04)

## 질문
> 우리 commandcode CLI 라우터는 ZDR 설정되어 있어?

**적용 전 상태: 미설정.** 프로세스 환경에 `CMD_ZDR` 가 존재하지 않았고(`CMD_ZDR=<unset>`),
`CommandCodeAdapter.execute` / `Commander._run_cmdc_commander_query` 어디에도 ZDR 플래그가 없었다.

## 적용 내용 (최소 변경)
Command Code CLI 는 `CMD_ZDR=1` 환경변수 또는 `x-cmd-zdr: 1` 헤더로 ZDR 을 강제한다
(https://commandcode.ai/docs/resources/zdr). cmdc 서브프로세스가 `os.environ` 을 상속하므로
설정 단일 출처인 `config.py` 에서 전역 반영했다.

| 파일 | 변경 |
| --- | --- |
| `agy_agent/config.py` | `CMD_ZDR` 기본값 `1` + `os.environ["CMD_ZDR"]` 전역 반영 (6줄) |
| `agy_agent/.env.example` | `CMD_ZDR` 사용법/폴백(422) 문서화 (주석 블록) |
| `tests/test_command_code_router.py` | ZDR 강제 + 어댑터 서브프로세스 env 전달 회귀 테스트 2건 추가 |

- 기본 활성(`CMD_ZDR=1`). `.env` 에 `CMD_ZDR=0` 을 넣으면 기존 비-ZDR 라우팅으로 복귀.
- ZDR 지원 업체가 없으면 비-ZDR 로 폴백하지 않고 `422 (cmd_zdr_no_providers)` 로 실패한다(문서 사양과 동일).

## 물리적 증거
### 1) 단위 테스트 (실행 결과 그대로)
```
$ python3 -m pytest tests/test_command_code_router.py tests/test_command_code_progress_stream.py -q
12 passed in 5.38s
```

### 2) 런타임 env 전파
```
$ python3 -c "import os, config; print(config.CMD_ZDR, os.environ.get('CMD_ZDR'))"
1 1
```

### 3) 실 CLI ZDR 스모크 테스트 (ZDR 업체 라우팅 성공 = 422 아님)
```
$ CMD_ZDR=1 cmdc -p "Reply with exactly: ZDR_OK" -t --yolo --tools-all \
    --skip-onboarding --output-format json -m deepseek/deepseek-v4.1-flash
...
{"type":"result","subtype":"success",...,"finalText":"ZDR_OK"}
EXIT=0
```

## 반영 시점
`config.py` 는 프로세스 시작 시 로드되므로, 실행 중인 봇은 **재시작 후** ZDR 이 적용된다.
