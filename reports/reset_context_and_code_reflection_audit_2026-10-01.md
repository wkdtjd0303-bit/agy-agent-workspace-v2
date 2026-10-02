# `!reset` 초기화 & 코드반영 실버그 감사 및 수정 (2026-10-01)

**사용자 보고:** "`!reset` 기능도 제대로 초기화 및 코드반영이라는 동작이 안 되는 경우가 많은 것 같다."

**결론:** 재현 가능한 실버그 **2건**을 규명하고 수정했다. (초기화 1건, 코드반영 1건)

| # | 축 | 결함 | 수정 위치 |
|---|---|---|---|
| D1 | 코드반영 | 이벤트 핸들러가 일부만 재바인딩되어 `on_command_error`/`on_error` 변경이 `!reset` 후에도 반영 안 됨. `commander_bot.on_ready` 가 메인 봇 핸들러로 잘못 덮어써짐 | `agy_agent/hot_reload_bridge.py:102-130` |
| D2 | 초기화 | `reset_conversation()` 이 대화 ID만 비우고 `root_user_goal`, `mode3_pending_prompt`, `wait_for_green`, `commander_macro_history` 를 남겨, `!reset` 후에도 옛 목표/보류 마일스톤이 되살아남 | `agy_agent/session_manager.py:485`, `1178`, `1192` |

---

## 1. 근본 원인 (재현 증거)

### D1 — 코드반영: 이벤트 핸들러 재바인딩 누락

`hot_reload_bridge.sync_in_memory_state()` 는 bot.py 최상위 함수를 AST로 재컴파일해 `__main__`에 재주입한 뒤, 커맨드 콜백은 자동 치유하지만 **discord 이벤트 핸들러는 3개만 하드코딩**하고 있었다:

```python
for evt in ["on_message", "on_ready", "on_guild_channel_update"]: ...
...
for evt in ["on_ready"]:  # commander_bot
    setattr(commander_inst, evt, main.__dict__[evt])   # ← main.on_ready 로 덮어씀(결함)
```

- 실제 bot.py 이벤트는 `on_message`, `on_ready`, `on_guild_channel_update`, **`on_command_error`**, **`on_error`** 5종.
  → 에러 핸들러 2종은 `!reset`(및 매 명령마다 도는 `reload_helper_modules()`) 후에도 **구(舊) 코드 그대로** 실행됨.
- 추가로 `commander_bot.on_ready` 를 `main.on_ready`(메인 봇 핸들러)로 덮어써, 각 보조 봇의 `on_commander_ready`/`on_qa_ready`/… 배선이 유실됨.

**재현(수정 전) 실측:**
```
on_message                live is main_fn? True
on_guild_channel_update   live is main_fn? True
on_ready                  live is main_fn? True
on_command_error          live is main_fn? False   ← 반영 안 됨
on_error                  live is main_fn? False   ← 반영 안 됨
```

### D2 — 초기화: 하드 리셋에서도 목표/보류 컨텍스트 잔존

`!reset` 단일 채널 경로는 `SessionManager.reset_channel_session()`, `!reset all` 은 `reset_all_conversations()` 를 호출한다. 두 메서드 모두 `session.reset_conversation()` 만 호출했는데, 이 함수는 대화 ID 계열만 비우고 아래를 **남긴다**:

- `root_user_goal` : Commander가 다음 기획에서 계속 참조하는 **지속 목표**
- `mode3_pending_prompt` : 일시정지된 **차기 마일스톤 프롬프트** (재개 시 그대로 실행됨)
- `wait_for_green` : 발열 대기 플래그
- `commander_macro_history` : 이전 프로젝트의 매크로 메모리

**재현(수정 전) 실측 — `!reset` 단일 채널:**
```
conversation_id            CLEARED
qa/qa2/auditor/engine ...  CLEARED
root_user_goal             ** REMAINS **  ← 옛 목표 되살아남
mode3_pending_prompt       ** REMAINS **  ← 보류 마일스톤 재개 위험
wait_for_green             ** REMAINS **
commander_macro_history    ** REMAINS **
```

> 주의: `reset_conversation()` 은 마일스톤 사이클 전환(`reset_cycle_workers`)에서도 호출되므로,
> 여기서 지속 목표를 지우면 Mode 3가 매 사이클마다 목표를 상실한다. 따라서 **`reset_conversation()` 자체는 건드리지 않고**, 하드 리셋 전용 메서드를 신설했다.

---

## 2. 수정 내용

### 2.1 `hot_reload_bridge.sync_in_memory_state()` — 이벤트 핸들러 전수 재바인딩

하드코딩 목록을 제거하고, 모든 봇 클라이언트(`bot`, `commander_bot`, `qa_bot`, `qa2_bot`, `auditor_bot`, `server_manager_bot`, `image_bot`, `extended_qa_bots` 값들)의 `on_*` 인스턴스 속성을 순회하며, **핸들러 함수의 `__name__` 을 기준으로** `main`에 재컴파일된 최신 함수로 교체한다. (커맨드 콜백 치유와 동일 원리)

효과:
- `on_command_error` / `on_error` 변경도 `!reset` 후 즉시 반영.
- `commander_bot.on_ready` 는 `main.on_ready` 가 아니라 **자기 함수(`on_commander_ready`)** 의 최신본으로 재바인딩.

### 2.2 `session_manager.ChannelSession.reset_for_hard_reset()` 신설 + 하드 리셋 경로에 적용

```python
def reset_for_hard_reset(self):
    self.reset_conversation()
    self.root_user_goal = None
    self.user_directive = None
    self.mode3_pending_prompt = None
    self.wait_for_green = False
    self.stop_after_current_cycle = False
    self.commander_macro_history = []
```

- `reset_channel_session()` / `reset_all_conversations()` 의 `reset_conversation()` 호출을 이 메서드로 교체.
- `reset_cycle_workers()`(사이클 전환)는 기존대로 `reset_conversation()` 을 사용 → **지속 목표/매크로 메모리 보존**(회귀 방지).
- `selected_qa_indices`, mode, engine, model 등 **사용자 설정은 보존** (초기화 대상 아님).

---

## 3. 검증 (실행한 것만 PASS로 기재)

### 3.1 신규 회귀 테스트 — `tests/test_reset_context_and_code_reflection.py` (4건)

```bash
cd /home/jangsung/Desktop/autocoding/!05_agy_agent/agy_agent
.venv/bin/python -m pytest tests/test_reset_context_and_code_reflection.py -q
# => 4 passed
```

검증 항목:
1. `reset_channel_session()` 이 목표/보류/그린/매크로 컨텍스트를 모두 비움
2. `reset_all_conversations()` 가 모든 채널에서 동일하게 비움
3. `reset_cycle_workers()`(사이클 전환)는 `root_user_goal`/매크로 메모리를 **보존**
4. `sync_in_memory_state()` 가 `on_command_error`/`on_error` 포함 모든 이벤트를 최신 함수로 재바인딩하고, 보조 클라이언트의 `on_ready` 를 잘못 덮어쓰지 않음

### 3.2 리셋/핫리로드 관련 기존 테스트 + 신규 (33 passed)

```bash
env PATH=/nonexistent .venv/bin/python -m pytest \
  tests/test_reset_context_and_code_reflection.py \
  tests/test_reset_requirements.py tests/test_reset_dependency_order.py \
  tests/test_hot_reload_bridge.py tests/test_session_manager_comprehensive.py \
  tests/test_mkdir_and_command_recursion_immunity.py -q
# => 33 passed
```

> `env PATH=/nonexistent` 를 쓴 이유: `stop_all_sessions()` 가 `kill_child_processes(os.getpid())` 로
> `pgrep` 을 호출하는데, 이 샌드박스에서는 공유 PGID 를 SIGTERM 해 테스트 러너 자신을 종료시킨다.
> (프로덕션에서는 CLI 자식이 `start_new_session=True` 라 안전. **기존 환경 이슈**이며 이번 수정과 무관.)

### 3.3 라이브 프로덕션 런타임 실측 (봇 모듈 대상, 격리 상태파일 사용)

수정 전/후를 실제 `bot` 모듈 + `reload_helper_modules()` 로 측정:

```
# 수정 후
on_message / on_ready / on_guild_channel_update   live is main_fn? True
on_command_error / on_error                        live is main_fn? True   ← 신규 반영
commander_bot.on_ready -> 'on_commander_ready'     is-latest? True          ← 덮어쓰기 해소
qa/qa2/auditor/server_manager/image bot on_ready   is-latest? True
모든 등록 커맨드 callback                           callback-is-raw? True    ← 최신 함수
```

`!reset` 단일 채널 필드 실측:
```
root_user_goal / mode3_pending_prompt / wait_for_green / commander_macro_history
  before=값   after=None/[]  → CLEARED
selected_qa_indices          → REMAINS (사용자 설정, 의도적 보존)
```

### 3.4 확장 회귀 (124 passed)

```bash
env PATH=/nonexistent .venv/bin/python -m pytest \
  tests/test_hot_reload_bridge.py tests/test_reset_* \
  tests/test_session_manager_comprehensive.py tests/test_mkdir_and_command_recursion_immunity.py \
  tests/test_bot_presence_models.py tests/test_command_error_and_suggestions.py \
  tests/test_dynamic_qa_expansion.py tests/test_mode3_done_command.py tests/test_agent_identity.py -q
# => 124 passed, 0 failed
```

---

## 4. 남은 한계 (정직 고지 — 이번에 고치지 않음)

`!reset`(핫리로드)은 **함수/Import 재컴파일**만 수행하므로 아래는 여전히 프로세스 재시작이 필요하다. (설계상 한계, `help_vs_reset_hotreload_audit.md` 와 동일)

1. **bot.py 최상위 `Assign`(전역 상수/변수)** 값 변경 → 재반영 안 됨.
   - 예: 기존 `WATCHDOG_CIRCUIT_BREAKER_THRESHOLD = 3` 을 다른 값으로 바꿔도 반영 안 됨(현재 코드도 항상 `3`으로만 초기화).
2. **새 커맨드 등록 추가** → 재바인딩 목록 밖이면 재시작 필요.
3. **`model_manager.py`** 는 리로드 대상에서 의도적으로 제외(싱글턴 상태 보존 목적) → 코드 변경 미반영.
4. 최상위에서 등록하는 **콜백 배선**(예: `GlobalModelManager.register_change_callback(sync_sessions_with_global_model)`)은 함수 객체가 import 시점에 고정되어, 해당 함수 변경은 재시작 전까지 반영 안 됨.

→ 위 항목에 해당하는 변경은 `!reset` 대신 **프로세스 재시작**(`systemctl --user restart agy-discord-bot.service`)을 사용할 것.

---

## 5. 변경 파일

- `agy_agent/hot_reload_bridge.py` (이벤트 핸들러 전수 재바인딩)
- `agy_agent/session_manager.py` (`reset_for_hard_reset()` 신설, 하드 리셋 2경로 적용)
- `agy_agent/tests/test_reset_context_and_code_reflection.py` (신규 회귀 테스트 4건)
