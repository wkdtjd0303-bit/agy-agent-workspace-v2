# 🛡️ StallGuard — 채널 오케스트레이션 정지(무응답/대기열 교착) 감시 가드 (관찰 전용)

> **강제 회수는 제거되었습니다.** 이 가드는 어떤 경우에도 실행 중인 작업을 회수하지 않습니다
> — 프로세스 kill, 오케스트레이션 태스크 cancel, `is_busy` 해제, 채널 대기열 락 교체를
> 일절 수행하지 않습니다. 오직 감시·경보·증거 기록만 남깁니다.

---

## 1. 왜 강제 회수를 없앴나 (오판 실증)

Think 모델(Claude Opus / DeepSeek R1 등)의 긴 추론, 장시간 QA(E2E 176+ 항목),
장시간 도구 실행은 stdout 이 오래 조용한 **정상 작업**입니다. stdout 침묵만으로 "멈춤"을
단정해 회수하면 정상 작업을 오판해 죽이게 됩니다.

`reports/watchdog/stalls.json` 에 실제로 기록된 사례:

```json
{"action": "stall_recover", "silence_seconds": 906.7, "task_alive": true,
 "status_desc": "🕵️ qa1 코드 리뷰 중 (1/20R · HIGH-Path)"}
```

qa1 코드 리뷰(Thinking 모델)가 **정상 동작 중**이었지만 한동안 stdout 이 조용했다는
이유만으로 강제 회수됐습니다. 그래서 회수 동작 자체(`stall_recover` / `zombie_recover`)를
제거했습니다.

---

## 2. 무엇을 감시하나 (10초 주기 상시 스캔)

```text
StallGuard.run() ── 매 10초 ──► 전 채널 세션 스캔
   │
   ├─ (C) 대기열 정체 : 작업은 비어 있는데 큐 요청이 60초 이상 방치
   │       → 디스패치 재호출(dispatch_next_in_queue)
   │
   └─ (W) 무응답 경보 : 실제 진행(last_progress_at) 이 3000초 이상 끊김
           → 채널에 1회 경보 (중복 발송 플래그로 차단, 진행 재개 시 재무장)
             ※ 작업은 계속 실행됨 (강제 회수 없음)
```

* `WARN`(3000초, 기본) 을 넘으면 **경보만** 발송합니다. 임계값을 아무리 넘겨도 작업을
  죽이지 않습니다.
* `HARD`(4000초) / `DEEP`(5000초) 는 이제 회수 트리거가 아니라 `!stall` 화면의 **표시 등급**
  (아이콘 색)일 뿐입니다.
* 좀비 `busy`(태스크가 이미 종료됐는데 `is_busy=True`)는 StallGuard 가 건드리지 않고,
  대기열 디스패치 시 `dispatch_next_in_queue` 가 별도로 정리합니다.

### 핵심: 가짜 하트비트 차단
`last_progress_at`(하트비트) 는 **진짜 진행** 에서만 갱신됩니다.

* ✅ 갱신: CLI stdout 출력(`agent_runner`), **도구 상태 전이(ACTIVE/DONE)**, `set_busy()` / `set_status()` 상태 전이, `touch()`
* ❌ 갱신 안 함: 디스코드 8초 주기 "⏳ 도구 실행 중..." 임베드

---

## 3. 함께 배선된 것 (Wiring)

| 파일 | 역할 |
| :-- | :-- |
| `stall_guard.py` | 감시·경보·증거 기록 엔진 (관찰 전용, 회수 없음) |
| `session_manager.py` | `busy_since`, `last_progress_at`, `touch()` 하트비트 |
| `bot.py` | `!stall` 관찰 명령, `start_all_bots()` 에 StallGuard 태스크 기동, 대기열 락 취득 타임아웃(`_acquire_queue_lock_or_recover`) |
| `agent_runner.py` / `engine_manager.py` | 실제 stdout 출력 시 `session.touch()` 로 하트비트 갱신 |
| `tests/test_stall_guard.py` | 회귀 테스트 (경보 1회성 · **강제 회수 없음** · 큐 재호동 · 증거 기록) |

> 대기열 락 취득 타임아웃(`QUEUE_LOCK_TIMEOUT_SECONDS`, bot.py)은 락 **취득** 단계의
> 별개 방어로, 실행 중인 작업을 회수하지 않습니다.

---

## 4. 물리적 증거 (Evidence)

감지·경보는 언제나 디스크에 남습니다.

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
    {"channel_id": 1549..., "action": "queue_nudge", "queue_depth": 1, ...}
  ]
}
```

로그 레벨로도 즉시 확인 가능합니다:

```bash
grep "\[StallGuard\]" <봇 로그>
# [StallGuard] 기동(관찰 전용 · 강제 회수 없음): 무응답 경보 3000s / 대기열 정체 60s ...
# [StallGuard] 채널 1549...: 무응답 3012초 경과 (기준 3000초) → 경보 발송 (강제 회수 없음 — 작업 계속 실행)
# [StallGuard] 이벤트 루프 블로킹 의심: 4.2s 지연 — 동기 호출(blocking I/O) 임시 점검 필요
```

---

## 5. 환경변수 튜닝

| 변수 | 기본값 | 설명 |
| :-- | --: | :-- |
| `STALL_GUARD_WARN_SECONDS` | `3000` | 무응답 경보 기준 (채널 1회 알림) |
| `STALL_GUARD_HARD_SECONDS` | `4000` | 심층 관찰 표시 등급 기준 (회수 동작 없음) |
| `STALL_GUARD_DEEP_SECONDS` | `5000` | 심층 표시 상한 (회수 동작 없음) |
| `STALL_GUARD_QUEUE_WAIT_SECONDS` | `60` | 대기열 정체 → 디스패치 재호동 기준 |
| `STALL_GUARD_TICK_SECONDS` | `10` | 스캔 주기 |
| `STALL_GUARD_LOOP_LAG_SECONDS` | `3` | 이벤트 루프 블로킹 경보 임계값 |
| `QUEUE_LOCK_TIMEOUT_SECONDS` | `20` | 채널 대기열 락 취득 타임아웃 (bot.py) |
| `QUEUE_LOCK_THERMAL_GRACE_SECONDS` | `320` | 열 쿨다운 중 락 점유 추가 대기 시간 |

> `STALL_GUARD_HARD_SECONDS` < `STALL_GUARD_WARN_SECONDS` 로 설정하면 **구조적으로 교정**되어
> 항상 `경보 < 심층관찰 < 심층상한` 표시 순서가 보장됩니다.

---

## 6. 관찰 명령: `!stall`

```text
!stall        (별칭: !정지감시, !교착, !stallguard)
```
임계값, 작업 중 채널별 **실제 진행 끊김 초수**, 최근 이력 5건, 증거 파일 경로를 한 화면에 보여줍니다.

## 7. 회귀 테스트

```bash
cd agy_agent
python3 -m pytest tests/test_stall_guard.py -q
# 17 passed
```

검증 항목: 진행 중 무조치 / 경보 1회성 / **강제 회수 없음(태스크·busy·락 회수 금지)** /
**DEEP 초과에도 회수 금지** / 좀비 busy 미회수 / 정체 큐 재호동 / 락 타임아웃 교체 /
쿨다운 락 보호 / 증거 파일 물리 기록 / 깨진 세션에도 루프 생존.
