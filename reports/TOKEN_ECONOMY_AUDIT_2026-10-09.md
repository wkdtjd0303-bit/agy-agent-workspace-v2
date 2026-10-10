# 🔋 토큰 경제성 감사 리포트 (Token Economy Audit)

- **작성 일시**: 2026-10-09 (KST)
- **대상**: AGY 다중 에이전트 오케스트레이션 시스템 (`!5_agy_agent`)
- **질문**: "많이 쓰는 게 문제인가, 시스템이 구조적으로 많이 쓰게 되어 있는가? 구독을 바꿔야 하나, 한도를 늘려야 하나, 절약할 수 있나?"
- **판정**: **`[STATUS: APPROVED]`** — 원인은 개인 과사용이 아니라 **구조적 증폭 + 상시(무인) 가동**. 한도 증액보다 **파이프라인 다이어트가 선행**되어야 한다.

---

## 1. 실측 데이터 (Evidence — 2026-10-09 직접 조회)

### 1-1. Command Code (cmdc / GOAT 플랜)
| 지표 | 실측값 |
| :-- | :-- |
| 5시간 한도 | $0.39 / $14.0 → **2.8% 사용** (거의 안 씀) |
| 주간 한도 | $25.52 / $35.0 → **72.9% 사용** 🔴 |
| 월간 크레딧 | $44.48 |
| 누적 토큰 | **490,310,646 토큰 (약 4.9억)** |
| 누적 크레딧 | $25.34 |

> **해석**: 5시간 창은 비어 있고 주간은 73% → **폭주형(버스트)이 아니라 시간 분산 상시 소비**. Coder + qa1 두 역할이 cmdc에 집중되어 있음.

### 1-2. OpenCode Go
| 지표 | 실측값 |
| :-- | :-- |
| 5시간 한도 | **0% 사용** |
| 주간 한도 | 39% 사용 |
| 월간 한도 | **99% 사용** 🔴 (소진) |

> **해석**: 주·월만 소진 = 역시 상시 소비. commander(mimo) + qa2(mimo)가 opencode에 배정되어 있어 마이너 역할이 마이너 플랜을 태움.

### 1-3. Antigravity (Gemini / Claude — 월 6천원 구독)
| 모델 | 5시간 잔여 | 비고 |
| :-- | :-- | :-- |
| Gemini 3 Flash | **85.1% 잔여** | 여유 |
| Gemini 2.5 Pro | 85.1% 잔여 | autocomplete 전용 |
| Claude Opus/Sonnet 4.6 | **0.92% 잔여** 🔴 | 주간 버킷 사실상 소진 |

### 1-4. Quota Firewall 실측 (state/quota_audit.jsonl, 998건)
- 총 998 이벤트 중 **191건(19.1%)이 무인 컨텍스트 Gemini 자동호출 차단**
- `unattended=True` 이벤트 **573건(57.4%)** → 절반 이상이 사람이 안 볼 때 발생
- 모델별: gemini-3.8-flash-high 574 / gemini-3.1-pro-high 233 / gpt-oss-120b-medium 191

### 1-5. 상시 가동 증거
- `agy-discord-bot.service`: `loaded active running` (상시)
- `bot.py` PID 3319: **2026-10-06부터 무중단 가동**
- crontab: quota_guardian 5분 주기 상시 실행

---

## 2. 왜 이렇게 많이 쓰이는가 (구조적 원인)

| # | 증폭 원인 | 배수/효과 |
| :-- | :-- | :-- |
| 1 | **다중 에이전트 라운드 캐스케이드** | `!mode 2+` 기준 Coder→qa1→qa2→qa3→Auditor = **회당 5콜**, 라운드 상한 20 → **요청 1건당 최대 100콜** |
| 2 | **무인 자율 순환 (mode 3/3+)** | 방치 시 자동 반복. 실측 57%가 무인 |
| 3 | **컨텍스트 캐스케이드** | 다운스트림 에이전트가 상류 출력(압축 12,000/8,000자)을 누적 주입 → 라운드마다 입력 토큰 재증폭 |
| 4 | **한국어 재작성 루프** | 비한국어 산문 감지 시 **전체 재호출 1회 추가** |
| 5 | **Vision 호출** | 스크린샷 1장마다 Gemini Vision 콜 (Visual Craft Loop, Planner Visual Gap) |
| 6 | **모델 편중** | auditor·qa3·vision·image = Gemini(agy), commander·qa2 = opencode, coder·qa1 = cmdc — 세 구독이 동시에 소비됨 |
| 7 | (완화됨) LS 폴링 누수 | CloudCode 프록시 + LS 킬러로 차단 중 (실측 191건) |

**결론**: 사용량이 많은 이유는 "내가 많이 눌러서"가 아니라 **1회 요청이 구조적으로 4~5배, 반복 시 수십~수백 배로 증폭되고, 봇이 24시간 켜져 있기 때문**.

---

## 3. 지금 당장 가능한 시스템적 토큰 절약 (즉시 적용 레버)

### 🔴 Tier 1 — 즉시(설정만, 코드 변경 0)
| 레버 | 조치 | 위치 |
| :-- | :-- | :-- |
| 무인 루프 시간대 제한 | `!mode 3/3+` 상시 가동 중지, 필요 시에만 진입. 부재중/야간엔 `!mode 2` 이하 | 운영 규칙 |
| 라운드 상한 축소 | `PAIR_MAX_ROUNDS`/`QA_MAX_ROUNDS`/`RESEARCH_MAX_ROUNDS` **20 → 6~8** | `.env` |
| 재시도 예산 축소 | `AGY_RETRY_MAX_ATTEMPTS` **2 → 1** | `.env` |
| 비전 호출 축소 | `COMMANDER_VISUAL_GAP_MAX_IMAGES` **3 → 1**, `VISUAL_CRAFT_MAX_ROUNDS` **2 → 1** | `.env` |
| QA 부대 축소 | `!mode 2+`에서 qa1·qa2만 유지 (qa3 off) | `!model` / `.env` |

### 🟠 Tier 2 — 모델 재배치 (유료 한도 직접 절감)
| 역할 | 현재 | 권장 | 효과 |
| :-- | :-- | :-- | :-- |
| commander | opencode mimo | **cmdc 무료**(`poolside/laguna-s-2.1-free`) | opencode 월간 소비 제거 |
| qa2 | opencode mimo | **cmdc 무료** 또는 opencode free tier(`space-bunny-free`) | 월간 한도 압박 해소 |
| qa3 | agy gemini-3.8-flash-high | **cmdc/opencode 무료** 로 이동 | Gemini 5h 절약 |
| auditor | agy gemini-3.8-flash-high | **gpt-oss-120b-medium**(비-Gemini 폴백) 유지 or cmdc | 무인 Gemini 차단과 정합 |
| coder | cmdc deepseek-v4.1-flash | 유지(토큰당 가성비 양호) + 무료 병행 | — |

> 무료 슬롯 실측 존재: `opencode-go/space-bunny-free`, `poolside/laguna-s-2.1-free`, `inclusionai/ling-3.0-flash-fin:free`, `opencode/nemotron-3.5-lightning-free` 등 20+종.

### 🟡 Tier 3 — 코드 레벨 (선택, 최소 변경)
| 레버 | 조치 | 파일 |
| :-- | :-- | :-- |
| 컨텍스트 압축 강화 | `compact_agent_context` 12,000/8,000 → **6,000/4,000** | `trio_agent_runner.py` |
| 한국어 재작성 오탐 감소 | `AGY_OUTPUT_KOREAN_RATIO` 0.15 → **0.08** (재작성 트리거 최소화), 또는 재작성 대상만 무료 모델로 | `.env` |
| 자율 사이클 상한 | 자율 모드 일일 콜/사이클 상한 하향 | `autonomy_guard.py` |

---

## 4. 개선율 보고서 (Measured vs Estimated 명시)

> **정직 원칙**: 실측(직접 실행/조회)과 추정(모델링)을 구분 표기. 아래 추정치는 코드 상 구조 배수에 기반한 **모델 계산**이며, 실제 절감은 실사용 패턴에 따라 달라진다.

### 4-1. 실측 (Executed & Verified)
| 항목 | 실측 결과 |
| :-- | :-- |
| Quota Firewall 누수 차단 | 998건 중 **191건(19.1%)** 무인 Gemini 자동호출 차단 실측 |
| CloudCode 프록시/LS 킬러 | 적용 중 (정적 증거: `cloudcode_cache_proxy.py`, `quota_guardian.log`) |
| 인플라이트 재시도 예산 | 최악 8콜 → 현재 **약 3콜** (config 주석 실측) |
| Command Code 소비 | 490,310,646 토큰 / 주간 72.9% |

### 4-2. 추정 (Modeled — 코드 구조 기반)
| 레버 | 계산 근거 | 예상 절감 |
| :-- | :-- | :-- |
| 라운드 상한 20→7 | 최악경로 콜 100 → 35 | **~65%** (최악 시나리오) |
| QA 3→2 | 회당 5콜 → 4콜 | **~20%** |
| 컨텍스트 12k→6k | 다운스트림 입력 토큰 | **~40~50%** (입력분) |
| 무료 모델 재배치 | 유료 역할 비중 (commander+qa2+qa3+auditor ≈ 4/7) | **유료 소비 30~50% ↓** |
| 한국어 재작성 억제 | 비한국어 비율 가정 15~30% | **~10~17%** (콜 감소) |
| **즉시 레버 종합(Tier 1+2)** | 위 조합 | **월 토큰 ≈ 40~60% 절감 추정** |

### 4-3. 기대 시나리오 (Command Code 기준)
- 현재: 주간 $25.5 / $35 (72.9%)
- Tier 1+2 적용 시: 주간 소비 **약 $10~15 구간**으로 하락 추정 → **주간 여유 55~70%p 확보**
- OpenCode: commander/qa2 이탈로 **월간 소진 제거** 추정

---

## 5. 최적 방법 제시 (구독 전략)

### 🔎 진단
- "5시간 창 비어있음 + 주/월 꽉 참" = **상시 소비 시그니처**. 개인 과사용이 아님.
- 따라서 **한도 증액은 최후 수단**. 증액하면 증폭된 구조가 그대로 더 많이 태운다.

### ✅ 권장 우선순위
1. **시스템 다이어트(0원)** — Tier 1 먼저. 무인 루프 시간대 제한 + 라운드/QA 축소.
2. **모델 재배치(0원)** — Tier 2. 유료 슬롯에 마이너 역할(commander/qa2/qa3) 몰빵 해소, 무료 슬롯으로 이동.
3. **재측정** — 1~2일 후 실측 재조회로 절감 검증.
4. **그래도 부족하면 구독 조정**:
   - **Antigravity(월 6천원)**: 가성비 최상(Claude Opus까지 접근) → **유지**.
   - **Command Code GOAT($10)**: 4.9억 토큰/$25 가성비 양호 → **유지** (단 무료 모델 병행으로 주간 여유 확보).
   - **OpenCode Go($10)**: 월간 99% 소진 + 대체 가능 역할뿐 → **역할 이전 후 다운그레이드/해지 1순위 후보**. (월 세이브 $10, 실질 손실 거의 없음)
5. **한도 증액은 1~4 이후에도 부족할 때만**. 그 전엔 구조가 새 한도도 태운다.

### 🧭 결론 한 줄
> **"많이 쓰는 상태"가 맞다. 단 원인은 사람이 아니라 상시 가동 다중 파이프라인이며, 해법은 증액이 아니라 ①무인 루프 제한 ②라운드·QA 축소 ③무료 모델 재배치 순서의 '구조 다이어트'다.**

---

## 6. 재현 명령 (Evidence Reproduction)
```bash
# Command Code / OpenCode 실측
cd !05_agy_agent/agy_agent && python3 -c "import telemetry_status_manager as t; print(t.fetch_command_code_usage()); print(t.fetch_opencode_go_usage())"

# Antigravity 실측
antigravity-usage quota --json

# 무인/차단 이력 집계
python3 - <<'PY'
import json,collections
rows=[json.loads(l) for l in open("state/quota_audit.jsonl") if l.strip()]
print("blocked:",sum(1 for r in rows if not r["allowed"]),"/",len(rows))
print("unattended:",sum(1 for r in rows if r.get("unattended")))
PY
```
