# 🛡️ StallGuard — 채널 오케스트레이션 교착(정지) 자동 감시·해제 가드

> **"요청했는데 답이 안 와요", "상태 타이머가 3506초에서 멈춰 있어요", "1시간 넘게 무응답이에요"**
> 를 발생 원인부터 제거하기 위한 상시 가동 감시 계층입니다.

---

## 1. 왜 멈추는가 (기존 방어의 사각지대 3가지)

| # | 사각지대 | 기존 동작 | 결과 |
| :-- | :-- | :-- | :-- |
| **1** | **채널 대기열 락 무제한 대기** | `dispatch_next_in_queue()` 가 `async with lock:` 으로 **타임아웃 없이** 대기 | 락 점유 태스크가 멈추는 순간 그 채널의 모든 후속 요청이 **영구 블락** |
| **2** | **좀비 `busy`** | 좀비 회수는 `current_task.done()` 일 때만 수행 | **살아는 있고 멈춰 있는(hung) 태스크는 영원히 통과 불가** → 큐에 요청이 쌓이고 답이 안 옴 |
| **3** | **프로세스 와치독 ≠ 오케스트레이션 회수** | `agent_runner` 와치독은 자식 CLI 프로세스만 kill | 프로세스가 죽어도 `session.is_busy=True` 와 락은 그대로 → 채널이 계속 점유 |

즉, 기존 와치독은 **"프로세스"** 를 살렸지만 **"채널"** 은 살리지 못했습니다.

---

## 2. StallGuard 동작 (10초 주기 상시 스캔)

```text
StallGuard.run() ── 매 10초 ──► 전 채널 세션 스캔
   │
   ├─ (C) 대기열 정체 : 작업은 비어 있는데 큐 요청이 60초 이상 방치
   │       → 디스패치 재호동(dispatch_next_in_queue 재귀 호출)
   │
   ├─ (A) 좀비 busy   : is_busy=True 인데 current_task 가 없거나 이미 종료 (30초)
   │       → 즉시 회수
   │
   ├─ (W) 무응답 경보 : 실제 진행(last_progress_at) 이 3000초 이상 끊김
   │       → 채널에 1회 경보 (중복 발송 플래그로 차단, 진행 재개 시 재무장)
   │
   ├─ (D) 심층 작업 관찰 : 진행이 4000초 이상 끊겼지만 태스크가 살아있음
   │       → 즉시 죽이지 않고 1회 알림 후 절대 상한(5000초)까지 관찰
   │         (Thinking 모델의 긴 추론 = stdout 조용함 → 오판 방지)
   │
   └─ (R) 무응답 회수 : 진행이 회수 임계값 이상 끊김
            (살아있는/추적 불가 작업=5000초, 추적 프로세스가 죽은 경우=4000초)
           → ① 프로세스 트리 강제 kill  ② 오케스트레이션 태스크 cancel
             ③ is_busy 해제            ④ 채널 대기열 락 강제 교체
             ⑤ 채널 알림                ⑥ 대기열 재가동
```

### 왜 `HARD(4000)` 에서 죽이지 않는가 (오판 실증)

`reports/watchdog/stalls.json` 에 실제 기록된 사례:

```json
{"action": "stall_recover", "silence_seconds": 906.7, "task_alive": true,
 "status_desc": "🕵️ qa1 코드 리뷰 중 (1/20R · HIGH-Path)"}
```

qa1 코드 리뷰(Thinking 모델)가 **정상 동작 중**이었지만 15분간 stdout 이 조용했다는
이유만으로 강제 회수됐습니다. stdout 침묵만으로 "멈춤"을 단정하면 긴 추론/장시간
도구 실행을 정상 작업인데도 죽입니다. 그래서:

* `WARN`(3000초, 기본) = 무응답 **경보** (채널 1회 알림)
* `HARD`(4000초) = **심층 작업 관찰**(즉시 회수 보류) 신호
* `DEEP`(5000초, 기본) = 살아있거나 추적 불가한 작업의 **절대 상한** → 이때 강제 회수
* 예외(유예 취소): 추적 중인 프로세스가 이미 종료(returncode 세팅)됐다면 `HARD`(4000초)에서 회수
* 예외(즉시 회수): 태스크 자체가 없거나 종료(좀비)면 30초에 즉시 회수 (기존 동작 유지)

`WARN`(3000) < `HARD`(4000) < `DEEP`(5000) 순서로 완만하게 두어, 장시간 QA(E2E 176+ 항목)나
Thinking 모델의 긴 추론을 성급하게 회수하지 않습니다.

### 핵심: 가짜 하트비트 차단
`last_progress_at`(하트비트) 는 **진짜 진행** 에서만 갱신됩니다.

* ✅ 갱신: CLI stdout 출력(`agent_runner`), **도구 상태 전이(ACTIVE/DONE)**, `set_busy()` / `set_status()` 상태 전이, `touch()`
* ❌ 갱신 안 함: 디스코드 8초 주기 "⏳ 도구 실행 중..." 임베드

디스코드 임베드가 계속 올라가는데 **프로세스가 멈춘 경우**를 잡아내기 위해,
페이크 하트비트는 절대 연결하지 않는 것이 이 설계의 전제입니다.

---

## 3. 함께 수정된 배선 (Wiring)

| 파일 | 변경 | 이유 |
| :-- | :-- | :-- |
| `stall_guard.py` **(신규)** | 감시·경보·회수·증거 기록 엔진 | 오케스트레이션 층 방어 신설 |
| `session_manager.py` | `busy_since`, `last_progress_at`, `touch()` 하트비트 도입, `set_busy/set_status` 가 시각 기록 | 무응답 판정 기준 확보 |
| `bot.py` | ① `_acquire_queue_lock_or_recover()` (락 취득 **타임아웃 20초 + 강제 교체**) ② `dispatch_next_in_queue()` 의 `try/finally` 락 반환 ③ 락 취득 **전** 열 쿨다운 처리 ④ `start_all_bots()` 에 StallGuard 태스크 기동 | 채널 영구 블락 원천 차단 |
| `agent_runner.py` | turn 시작·stdout 출력·도구 상태 전이(ACTIVE/DONE) 시 `session.touch()` | 진짜 진행을 하트비트로 반영 (오판 최소화) |
| `tests/test_stall_guard.py` **(신규)** | 17개 회귀 테스트 | 경보/심층관찰/회수/락/증거 물리 검증 |

### 열 관리(ThermalGuard) 보호
비상 쿨다운(300초)은 **정상 대기**이므로, 락 타임아웃이 걸려도
`_thermal_cooldown_holds_lock()` 가 EMERGENCY 티어를 확인해 **락을 교체하지 않고 320초를 추가 대기**합니다.
(쿨다운으로 인한 오판 재발 방지)

---

## 4. 물리적 증거 (Evidence)

감지·조치는 언제나 디스크에 남습니다.

```
agy_agent/reports/watchdog/stalls.json
```

```json
{
  "schema": "stall_guard/v1",
  "generated_at": "2026-09-27T00:00:00Z",
  "thresholds": {"warn_seconds": 3000, "hard_seconds": 4000, "deep_seconds": 5000, "queue_wait_seconds": 60, ...},
  "total_events": 2,
  "events": [
    {"channel_id": 1549..., "action": "stall_warn", "silence_seconds": 3012.0, "task_alive": true, ...},
    {"channel_id": 1549..., "action": "deep_work_notice", "silence_seconds": 4003.4, "task_alive": true, ...},
    {"channel_id": 1549..., "action": "stall_recover", "silence_seconds": 5012.7, "queue_depth": 1, ...}
  ]
}
```

로그 레벨로도 즉시 확인 가능합니다:

```bash
grep "\[StallGuard\]" <봇 로그>
# [StallGuard] 채널 1549...: 무응답 3012초 경과 (기준 3000초) → 경보 발송
# [StallGuard] 채널 1549... 대기열 락 강제 교체 완료
# [StallGuard] 이벤트 루프 블로킹 의심: 4.2s 지연 — 동기 호출(blocking I/O) 임시 점검 필요
```

---

## 5. 환경변수 튜닝

| 변수 | 기본값 | 설명 |
| :-- | --: | :-- |
| `STALL_GUARD_WARN_SECONDS` | `3000` | 무응답 경보 기준 (채널 1회 알림) |
| `STALL_GUARD_HARD_SECONDS` | `4000` | 심층 작업 관찰 기준 (살아있는 작업은 즉시 회수 안 함) |
| `STALL_GUARD_DEEP_SECONDS` | `5000` | 살아있는 작업의 절대 상한·강제 회수 기준 |
| `STALL_GUARD_QUEUE_WAIT_SECONDS` | `60` | 대기열 정체 → 디스패치 재호동 기준 |
| `STALL_GUARD_ZOMBIE_SECONDS` | `30` | 좀비 busy 판정 기준 |
| `STALL_GUARD_TICK_SECONDS` | `10` | 스캔 주기 |
| `STALL_GUARD_LOOP_LAG_SECONDS` | `3` | 이벤트 루프 블로킹 경보 임계값 |
| `QUEUE_LOCK_TIMEOUT_SECONDS` | `20` | 채널 대기열 락 취득 타임아웃 |
| `QUEUE_LOCK_THERMAL_GRACE_SECONDS` | `320` | 열 쿨다운 중 락 점유 추가 대기 시간 |

> `STALL_GUARD_HARD_SECONDS` < `STALL_GUARD_WARN_SECONDS` 로 설정하면 **구조적으로 교정**되어
> 항상 `경보 < 심층관찰 < 회수` 순서가 보장됩니다. `STALL_GUARD_DEEP_SECONDS` 는
> `HARD` 보다 반드시 커야 하며(아니면 `HARD + 1` 로 자동 교정), 기본 5000초는 장시간 QA·긴 추론을
> 성급하게 회수하지 않도록 완만하게 잡은 값입니다.

---

## 6. 관찰 명령: `!stall`

```text
!stall        (별칭: !정지감시, !교착, !stallguard)
```
임계값, 작업 중 채널별 **실제 진행 끊김 초수**, 최근 교착 이력 5건, 증거 파일 경로를 한 화면에 보여줍니다.

## 7. 회귀 테스트

```bash
cd agy_agent
python3 -m pytest tests/test_stall_guard.py -q
# 17 passed
```

검증 항목: 진행 중 무조치 / 경보 1회성 / 회수 시 태스크·busy·락·대기열 동시 해제 /
**HARD~DEEP 구간에서 살아있는 태스크 보존(오판 방지)** / **고아 프로세스 즉시 회수** /
**임계값 순서(경보<관찰<회수) 강제** / 좀비 busy 즉시 회수 / 정체 큐 재호동 /
락 타임아웃 교체 / 쿨다운 락 보호 / 증거 파일 물리 기록 / 깨진 세션에도 루프 생존.
