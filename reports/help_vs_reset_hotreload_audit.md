# `!help` vs `!reset` — 최신 코드 실시간 반영 여부 감사

**질문:** `!reset`처럼 `!help`로도 최신 agy_agent 코드가 실시간 반영되나? 아니면 reset을 해야만 하나?

**결론:** `!help`도 `!reset`과 **동일한 핫리로드 파이프라인**을 실행한다.
따라서 **코드 반영만을 목적으로 하면 `!help` 만으로 충분하며 `!reset`은 필수가 아니다.**
`!reset`은 코드 반영에 더해 **대화 세션/대기열/실행 태스크(상태)까지 초기화**한다는 점만 다르다.

---

## 1. 근거: 두 명령 모두 동일한 함수를 호출한다

### `!reset` 경로
- `bot.py:2700` `@bot.command(name="reset", ...)`
- `bot.py:2701` `reset_command()` → `execute_all_in_one_hot_reset()`
- `bot.py:2605` `reloaded_count = reload_helper_modules()`  ← **핫리로드 진입점**

### `!help` 경로 (2곳 모두)
- 정식 커맨드: `bot.py:979` `help_command()`
  - `bot.py:984` `reload_helper_modules()`   ← **동일한 핫리로드 진입점**
  - `bot.py:986` `importlib.reload(help_manager)` (도움말 모듈 추가 리로드)
- `on_message` 직접 인터셉트: `bot.py:5533`
  - `bot.py:5535` `importlib.reload(help_manager)`
  - 진입 전 `bot.py:5527` `reload_helper_modules()` 호출

즉 `!help`는 호출될 때마다 `reload_helper_modules()` + `help_manager` 리로드를 수행한다.

## 2. `reload_helper_modules()`가 실제로 하는 일

`bot.py:306` `_reload_helper_modules_impl()`:
1. `bot.py:329-360` 우선순위 모듈 목록 + 로컬 `*.py` 전체를 `importlib.reload()`.
   (`help_manager`, `commander`, `engine_manager`, `agent_runner`, `qa_hub`, `config` 등 포함)
2. `bot.py:461-464` `hot_reload_bridge.sync_in_memory_state()` 호출.
   - `hot_reload_bridge.py:12` 부터: `bot.py`는 `__main__`이라 `importlib.reload` 불가하므로,
     `bot.py`의 top-level `FunctionDef/AsyncFunctionDef/Import`를 AST로 재컴파일해 `__main__`에 재주입하고,
     `on_message`/`on_ready` 등 이벤트와 커맨드 콜백을 재바인딩한다.
3. `bot.py:469-574` `_safe_rebind_command(...)` 로 `bot.all_commands`의 모든 명령 콜백/별칭을 최신 코드로 재바인딩.

→ 이 경로는 `!reset`과 `!help`가 **완전히 동일**하다. (둘 다 같은 함수를 부른다.)

---

## 3. 그럼에도 `!reset`이 필요한 경우 (차이점)

| 구분 | `!help` | `!reset` |
|---|---|---|
| 헬퍼 모듈 핫리로드 | ✅ | ✅ |
| `bot.py` 함수 재컴파일/커맨드 재바인딩 | ✅ | ✅ |
| 대화 컨텍스트(conversation_id) 초기화 | ❌ | ✅ |
| 실행 중 태스크/대기열 취소·초기화 | ❌ | ✅ |
| `gc.collect()` 메모리 정리 | ❌ | ✅ |
| 봇 presence 재동기화 | ❌ | ✅ |

**정리:** "최신 코드를 반영"이라는 목적만 보면 둘은 같다.
`!reset`은 여기에 **상태 초기화**가 추가된 상위 호환일 뿐이다.

## 4. 두 명령 모두 반영하지 못하는 예외 (동일 한계)

- `bot.py`의 **모듈 최상위 `Assign`(새 전역 상수 등)**: `sync_in_memory_state()`는 함수/import만 재컴파일한다.
  단, 자주 쓰이는 일부 전역은 `_reload_helper_modules_impl`에서 별도 재초기화한다.
- **`_safe_rebind_command(...)` 목록에 등록되지 않은 신규 커맨드**: 리로드로는 콜백이 재바인딩되지 않아
  프로세스 재시작(systemd)이 필요하다. 이 한계는 `!help`/`!reset` 모두 동일하다.
- 즉 "reset을 해야만 반영되는데 help로는 안 되는" 코드는 없다.

---

## 5. 실무 권장

- 코드만 고쳤고 진행 중인 작업을 유지하고 싶다 → **`!help`** (또는 `!리셋` 없이 아무 트리거)로 충분.
- 진행 중 작업/대기열/세션까지 깨끗이 비우고 최신 코드로 새로 시작하고 싶다 → **`!reset`**.
- `bot.py`의 새 전역/신규 커맨드 등록 변경이면 → 두 방법으로도 일부 미반영될 수 있으니 **프로세스 재시작** 권장.

*감사 기준 소스: `/home/jangsung/Desktop/autocoding/!05_agy_agent/agy_agent/{bot.py, hot_reload_bridge.py, help_manager.py}`*
