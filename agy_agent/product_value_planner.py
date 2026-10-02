#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
👑 ProductValuePlanner — Commander 전용 **제품 가치 기반 차기 기능 기획기**.

문제 정의:
- Commander의 결정론적 폴백은 도메인 사다리가 소진되면 곧바로 "상용 배포 품질 하드닝",
  "경계 조건/대용량 데이터 크래시 방어", "응답 속도 최적화 및 불필요한 연산 제거",
  "전체 E2E 시나리오 테스트 100% 무결점 통과" 같은 **자기참조적 품질 과제**로 수렴했다.
- 이는 기획자의 역할이 아니다. 기획자는 "사용자가 화면에서 체감할 아직 없는 기능"을
  다음 마일스톤으로 발주해야 한다. 품질 담금질은 신규 가치 기능에 딸린 부산물이어야 한다.
- 스크린샷 실측 결손(planner_visual_gap_analyzer)은 '이미 렌더링된 화면'이 있어야 동작한다.
  초기/헤드리스/CLI/백엔드 프로젝트에서는 증거가 없어 곧바로 하드닝 폴백으로 떨어진다.

해결:
- 도메인별 **사용자 체감 가치 기능 카탈로그**(Product Value Catalog)를 물리 디스크
  증거(구현 컴포넌트/소스 파일/완료·진행 마일스톤)와 대조하여, 아직 구현되지 않은
  가장 가치 높은 사용자 기능 1건을 다음 마일스톤 후보로 선택한다.
- "하드닝 전용(hardening-only)" 기획을 판정하여, 사용자 가치 기능이 남아 있는 한
  품질 하드닝 단독 마일스톤을 원천 차단한다.

이 모듈은 **읽기 전용**이다. 제품 소스코드를 수정하지 않는다.

핵심 API:
    ProductValuePlanner.enabled() -> bool
    ProductValuePlanner.is_hardening_only_plan(title, tasks) -> bool
    ProductValuePlanner.select_capability(domain, reality=None, workspace=None, root_goal="", existing_titles=None) -> Optional[ValueCapability]
    ProductValuePlanner.build_seed(domain, root_goal="", reality=None, workspace=None, existing_titles=None) -> Optional[dict]
    ProductValuePlanner.select_roadmap_plans(domain, workspace, existing_titles=None, limit=2) -> List[Tuple[str, List[str]]]
    ProductValuePlanner.format_digest(cap) -> str
"""

from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("ProductValuePlanner")

# ---------------------------------------------------------------------------
# 환경 스위치
# ---------------------------------------------------------------------------
def _env_flag(name: str, default: str = "1") -> bool:
    return (os.getenv(name, default) or default).strip().lower() not in ("0", "false", "off", "no")


def product_value_enabled() -> bool:
    """Commander 기획 단계의 제품 가치 기능 기획 ON/OFF (기본 ON)."""
    return _env_flag("COMMANDER_PRODUCT_VALUE_ENABLED", "1")


# ---------------------------------------------------------------------------
# 하드닝 전용 기획 판정 (자기참조적 품질 과제 차단)
# ---------------------------------------------------------------------------
# 제목/과제에 하나라도 있으면 '품질/검증 하드닝'으로 간주하는 표지.
_HARDENING_MARKERS = (
    # 기존 PlannerVisualGapAnalyzer와 동일 계열 (한국어)
    "무결점 통과", "시나리오 테스트 100%", "시나리오 테스트", "회귀 검증", "회귀 테스트",
    "품질 하드닝", "품질 안정화", "상용 배포 품질", "안정성 하드닝", "E2E 회귀", "e2e 회귀",
    "100% 통과", "배포 품질", "하드닝", "품질 검증", "안정화",
    # 사용자가 지적한 자기참조적 하드닝 과제 표지
    "경계 조건", "대용량 데이터", "크래시 방어", "응답 속도", "불필요한 연산",
    "메모리 누수", "프레임 드롭", "누수 0건", "예외 처리 방어", "장애 격리",
    "부하 방어", "내구성", "OOM 선제", "성능 프로파일링",
    # English (product_planner 5단계 사다리 등)
    "production hardening", "hardening", "regression", "stress", "edge case",
    "e2e integration", "headless e2e",
)

# 사용자에게 '보이는' 새 기능/가치로 보기 어려운 순수 방어/검증 동사.
_TASK_HARDENING_MARKERS = (
    "경계 조건", "대용량", "크래시", "응답 속도", "불필요한 연산", "메모리 누수",
    "프레임 드롭", "누수", "예외 처리", "장애 격리", "부하", "내구성", "OOM",
    "회귀", "테스트 100%", "100% 통과", "무결점", "하드닝", "안정화", "최적화 및 제거",
    "리팩토링", "프로파일링", "부하 방어",
)


def is_hardening_only_plan(title: str, tasks: Optional[Sequence[str]] = None) -> bool:
    """
    '상용 배포 품질 하드닝 / 경계 조건 크래시 방어 / 응답 속도 최적화 / E2E 100% 통과'류의
    사용자 체감 가치가 없는 자기참조적 품질 과제만으로 구성된 기획인지 판정한다.

    - 제목 자체가 하드닝 표지면 즉시 True (도메인 사다리 종단 폴백 표식).
    - 그 외에는 '모든 태스크'가 하드닝/방어/검증 성격일 때만 True.
      (가치 기능 1개 + 방어 1개처럼 혼합된 정상 기획은 차단하지 않는다.)
    """
    title_l = (title or "").lower()
    if any(m.lower() in title_l for m in _HARDENING_MARKERS):
        return True

    task_list = [str(t).strip() for t in (tasks or []) if str(t).strip()]
    if not task_list:
        return False
    for t in task_list:
        low = t.lower()
        if not any(m.lower() in low for m in _TASK_HARDENING_MARKERS):
            return False
    return True


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ValueCapability:
    """도메인별 사용자 체감 가치 기능 1건."""
    id: str
    feature: str                     # 짧은 결손 기능명 (예: '일시정지 및 옵션 메뉴')
    title: str                       # 마일스톤 타이틀
    tasks: Tuple[str, ...]           # 구현 태스크 (사용자 가치 중심)
    how: str                         # 구현 방법론
    why: str                         # 전략적 당위성 (사용자 가치 관점)
    detect: Tuple[Tuple[str, ...], ...]  # 이미 구현됨 판정용 필수 토큰 묶음(들)
    keywords: Tuple[str, ...] = ()
    category: str = "PRODUCT_VALUE"

    def detect_tokens(self) -> List[str]:
        seen: List[str] = []
        for phrase in self.detect:
            for tok in phrase:
                if tok not in seen:
                    seen.append(tok)
        return seen


# ---------------------------------------------------------------------------
# 도메인별 사용자 가치 기능 카탈로그 (가치 순)
#   detect: 각 항목은 '필수 토큰 묶음'. 해당 묶음의 토큰이 모두 디스크 증거에 있으면 구현됨.
# ---------------------------------------------------------------------------
def _cap(id, feature, title, tasks, how, why, detect, keywords=()) -> ValueCapability:
    return ValueCapability(
        id=id, feature=feature, title=title, tasks=tuple(tasks), how=how, why=why,
        detect=tuple(tuple(p) for p in detect), keywords=tuple(keywords),
    )


_WEB_CATALOG: Tuple[ValueCapability, ...] = (
    _cap(
        "web.export_share", "저장·내보내기 및 공유",
        "원클릭 내보내기·공유 및 결과물 저장",
        [
            "작업 결과물을 원본 무손실로 내보내기(PDF/이미지/문서/CSV 등) 기능 구현",
            "링크·파일 공유 액션 및 공유 시 미리보기 카드 제공",
            "내보내기 진행률 표시와 실패 시 재시도 안내",
        ],
        "직렬화/렌더 파이프라인을 재사용해 내보내기 포맷별 인코더를 추가하고, 공유는 OS/브라우저 공유 시트에 연결합니다.",
        "사용자가 결과물을 저장·공유할 수 없으면 제품의 가치가 외부로 전달되지 않습니다. 상용 서비스의 기본 출구(exit) 흐름을 확보합니다.",
        [("export",), ("save", "as"), ("share",), ("download",), ("내보내기",), ("공유",)],
        keywords=("내보내기", "공유", "저장"),
    ),
    _cap(
        "web.search_filter", "검색·필터 및 정렬",
        "실시간 검색·필터 및 정렬 컨트롤",
        [
            "제목/본문/태그 실시간 검색 및 결과 하이라이트",
            "카테고리·상태·날짜 필터와 정렬(최신/이름/크기) 컨트롤",
            "검색 결과 없음(Empty State) 안내와 필터 초기화",
        ],
        "인덱스 또는 스트림 필터링을 상태 관리 레이어에 연결하고, 디바운스된 비동기 검색으로 반응성을 보장합니다.",
        "데이터가 늘어날수록 검색·필터가 없으면 사용자는 원하는 항목에 도달하지 못합니다. 탐색 효율이 곧 체류 시간입니다.",
        [("search",), ("filter",), ("sort",), ("검색",), ("필터",), ("정렬",)],
        keywords=("검색", "필터"),
    ),
    _cap(
        "web.settings", "사용자 설정 및 환경설정",
        "사용자 설정 화면 및 환경설정 영속화",
        [
            "설정 화면 UI 및 항목별 즉시 적용/저장",
            "재접속 후에도 유지되는 환경설정 영속화",
            "기본값 복원(Reset) 및 잘못된 설정 방어",
        ],
        "설정 상태를 단일 스토어로 관리하고 로컬/서버에 영속화하여 재기동 후에도 복원합니다.",
        "사용자가 자신의 사용 환경을 통제할 수 있어야 재방문합니다. 개인화는 리텐션의 최소 조건입니다.",
        [("setting",), ("preference",), ("설정",)],
        keywords=("설정", "옵션"),
    ),
    _cap(
        "web.theme", "다크 모드 및 테마 전환",
        "다크 모드 및 테마 전환",
        [
            "라이트/다크 테마 토큰 분리 및 시스템 설정 자동 연동",
            "테마 즉시 전환 시 색 대비(WCAG AA) 유지",
            "사용자 선택 테마 영속화",
        ],
        "디자인 토큰을 CSS 변수/테마 오브젝트로 승격하고 시스템 prefers-color-scheme 과 사용자 선택을 병합합니다.",
        "야간/장시간 사용 시 다크 모드는 상용 앱의 기본 기대치이며, 이탈을 줄이는 체감 품질입니다.",
        [("dark", "mode"), ("theme",), ("다크",), ("테마",)],
        keywords=("다크", "테마", "테마 전환"),
    ),
    _cap(
        "web.notification", "알림 및 리마인더",
        "알림·리마인더 및 활동 요약",
        [
            "주요 이벤트(완료/실패/마감 임박) 알림 표시",
            "사용자 지정 리마인더 등록 및 해제",
            "알림 센터에서 읽음/전체 삭제 관리",
        ],
        "이벤트 버스에 알림 구독자를 붙이고, 브라우저/OS 알림 권한 흐름과 인앱 알림 센터를 함께 제공합니다.",
        "사용자가 앱을 다시 열 이유를 만들어 주는 재방문 트리거입니다. 알림이 없으면 서비스는 잊힙니다.",
        [("notif",), ("remind",), ("알림",), ("리마인더",)],
        keywords=("알림", "리마인더"),
    ),
    _cap(
        "web.history_collab", "작업 이력·협업 및 댓글",
        "작업 이력(History)·댓글 및 협업",
        [
            "변경 이력 타임라인 및 특정 시점 비교/복원",
            "항목별 댓글·멘션 및 읽지 않음 배지",
            "동시 편집 충돌 방지 및 최신 상태 동기화",
        ],
        "변경을 커맨드/이벤트 로그로 기록하고, 항목에 연결된 댓글 스레드 모델을 추가합니다.",
        "협업과 이력은 개인 도구를 팀 도구로 확장하는 핵심 가치이며, 상용 서비스의 유료 전환 축입니다.",
        [("history",), ("comment",), ("collaborat",), ("이력",), ("댓글",), ("협업",)],
        keywords=("이력", "댓글", "협업"),
    ),
    _cap(
        "web.onboarding_help", "온보딩·가이드 및 도움말",
        "온보딩 투어·도움말 및 빈 상태 가이드",
        [
            "첫 방문 3단계 온보딩 투어 및 건너뛰기",
            "기능별 도움말/단축 안내 팝오버",
            "데이터가 없을 때 다음 행동을 제시하는 Empty State",
        ],
        "투어 스텝을 화면 요소 앵커에 연결하고, 도움말 콘텐츠를 코드와 함께 버전 관리합니다.",
        "첫 사용 3초 안에 가치를 전달하지 못하면 사용자는 이탈합니다. 온보딩은 활성화율의 직접 원인입니다.",
        [("onboard",), ("tutorial",), ("guide",), ("help",), ("온보딩",), ("튜토리얼",), ("도움말",)],
        keywords=("온보딩", "도움말", "튜토리얼"),
    ),
    _cap(
        "web.a11y_shortcut", "접근성 및 키보드 단축키",
        "접근성(A11y) 및 키보드 단축키 체계",
        [
            "핵심 동작 키보드 단축키 및 단축키 안내",
            "포커스 가시화·ARIA 라벨·스크린리더 대응",
            "최소 터치 타깃(48dp)과 색 대비 준수 점검",
        ],
        "전역 키 핸들러와 접근성 속성을 컴포넌트 단위로 일괄 적용하고, 자동 대비 검사를 CI에 연결합니다.",
        "키보드/스크린리더 사용자를 배제하면 도달 가능한 사용자 풀이 줄어듭니다. 접근성은 품질이 아니라 도달성입니다.",
        [("shortcut",), ("keyboard",), ("accessib",), ("a11y",), ("단축키",), ("접근성",)],
        keywords=("접근성", "단축키"),
    ),
    _cap(
        "web.i18n", "다국어 및 로컬라이제이션",
        "다국어(i18n) 및 로케일 전환",
        [
            "문자열 리소스 분리 및 언어 전환 UI",
            "날짜·숫자·통화 로케일 포맷 적용",
            "언어 선택 영속화 및 누락 키 폴백",
        ],
        "하드코딩 문자열을 리소스 키로 추출하고 로케일 사전을 로딩하는 i18n 레이어를 도입합니다.",
        "글로벌 사용자 확장의 전제 조건이며, 초기부터 분리해야 나중의 대규모 재작업을 막습니다.",
        [("i18n",), ("locale",), ("translat",), ("다국어",), ("번역",)],
        keywords=("다국어", "번역"),
    ),
    _cap(
        "web.backup_import", "데이터 백업·복원 및 가져오기",
        "데이터 백업·복원 및 외부 가져오기",
        [
            "전체 데이터 백업 파일 내보내기/불러오기",
            "외부 파일(CSV/JSON/이미지) 가져오기 및 매핑",
            "가져오기 검증 실패 시 부분 롤백 안내",
        ],
        "데이터 스냅샷을 버전 태그와 함께 직렬화하고, 가져오기는 검증→스테이징→커밋 순으로 처리합니다.",
        "데이터를 신뢰하고 맡길 수 있어야 사용자는 서비스를 장기 사용합니다. 이동성은 신뢰의 증거입니다.",
        [("backup",), ("restore",), ("import",), ("백업",), ("복원",), ("가져오기",)],
        keywords=("백업", "복원", "가져오기"),
    ),
)

_MOBILE_CATALOG: Tuple[ValueCapability, ...] = (
    _cap(
        "mobile.settings", "설정 화면 및 환경설정",
        "설정 화면 및 사용자 환경설정",
        [
            "설정 화면 UI 및 즉시 적용되는 항목별 토글",
            "앱 재기동 후에도 유지되는 환경설정 영속화",
            "기본값 복원 및 잘못된 설정 방어",
        ],
        "설정을 DataStore/Preference 로 영속화하고 화면 상태와 단방향으로 바인딩합니다.",
        "사용자가 앱을 자신에게 맞게 조정할 수 있어야 장기 사용으로 이어집니다.",
        [("setting",), ("preference",), ("설정",)],
        keywords=("설정", "옵션"),
    ),
    _cap(
        "mobile.bookmark", "즐겨찾기·북마크 및 최근 항목",
        "즐겨찾기·북마크 및 최근 항목",
        [
            "항목별 즐겨찾기 추가/해제 및 전용 목록 화면",
            "최근 사용 항목 기록과 원탭 재진입",
            "즐겨찾기 정렬·검색 및 영속화",
        ],
        "즐겨찾기/최근 항목을 로컬 DB 테이블로 관리하고 목록 화면에 빠른 진입 섹션을 추가합니다.",
        "반복 방문 사용자의 재접근 마찰을 제거하는 핵심 리텐션 기능입니다.",
        [("favorite",), ("bookmark",), ("즐겨찾기",), ("북마크",), ("최근",)],
        keywords=("즐겨찾기", "북마크", "최근"),
    ),
    _cap(
        "mobile.search_filter", "검색·필터 및 정렬",
        "실시간 검색·필터 및 정렬",
        [
            "실시간 검색 및 결과 하이라이트",
            "카테고리·태그 필터와 정렬 옵션",
            "검색 결과 없음 안내와 초기화",
        ],
        "비동기 인덱싱/필터링을 Flow 로 연결하고 입력 디바운스로 반응성을 확보합니다.",
        "항목이 많아질수록 검색은 필수 탐색 수단이 됩니다.",
        [("search",), ("filter",), ("검색",), ("필터",)],
        keywords=("검색", "필터"),
    ),
    _cap(
        "mobile.share_export", "공유 및 내보내기",
        "공유 시트 연동 및 결과물 내보내기",
        [
            "OS 공유 시트 연동 및 파일/링크 공유",
            "무손실 내보내기 포맷 선택과 저장 위치 지정",
            "공유 실패 시 재시도 안내",
        ],
        "Android Sharesheet/SAF 를 연결하고 내보내기 인코더를 기존 파이프라인에 추가합니다.",
        "결과물을 외부로 전달하는 출구 흐름이 없으면 앱의 가치가 갇힙니다.",
        [("share",), ("export",), ("공유",), ("내보내기",)],
        keywords=("공유", "내보내기"),
    ),
    _cap(
        "mobile.dark_theme", "다크 모드 및 테마",
        "다크 모드 및 테마 전환",
        [
            "라이트/다크 테마 토큰 및 시스템 설정 자동 연동",
            "전환 즉시 대비 유지 및 깜빡임 없는 핫스왑",
            "사용자 선택 테마 영속화",
        ],
        "Material 3 테마 토큰을 분리하고 시스템 다크 모드 변경을 수신해 재구성합니다.",
        "야간 사용성과 배터리 효율을 좌우하는 현대 앱의 기본 기대치입니다.",
        [("dark", "mode"), ("theme",), ("다크",), ("테마",)],
        keywords=("다크", "테마"),
    ),
    _cap(
        "mobile.notification", "알림 및 리마인더",
        "알림·리마인더 및 재방문 트리거",
        [
            "주요 이벤트 알림 표시 및 권한 요청 흐름",
            "사용자 지정 리마인더 등록/해제",
            "알림 탭 시 해당 화면 딥링크",
        ],
        "NotificationChannel 과 WorkManager 를 연결해 예약 리마인더를 로컬에서 처리합니다.",
        "재방문 유도의 가장 직접적인 수단이며 리텐션의 핵심입니다.",
        [("notif",), ("remind",), ("알림",), ("리마인더",)],
        keywords=("알림", "리마인더"),
    ),
    _cap(
        "mobile.offline", "오프라인 모드 및 캐시",
        "오프라인 모드 및 로컬 우선 동작",
        [
            "네트워크 없이도 핵심 기능 동작하는 로컬 우선 설계",
            "온라인 복귀 시 변경 동기화 및 충돌 해소",
            "오프라인 상태 명확한 배지 안내",
        ],
        "로컬 DB를 single source of truth 로 두고 동기화 큐로 서버 반영을 지연 처리합니다.",
        "이동 중 네트워크 단절은 일상입니다. 오프라인에서 멈추면 사용자는 이탈합니다.",
        [("offline",), ("cache",), ("오프라인",), ("캐시",)],
        keywords=("오프라인", "캐시"),
    ),
    _cap(
        "mobile.a11y", "접근성 및 큰 글씨 모드",
        "접근성·큰 글씨 및 TalkBack 대응",
        [
            "동적 글꼴 크기 및 화면 확대 대응 레이아웃",
            "TalkBack contentDescription 및 포커스 순서 정리",
            "최소 터치 타깃 48dp 및 색 대비 준수",
        ],
        "접근성 속성을 컴포넌트 단위로 적용하고 폰트 스케일 변경 시 레이아웃을 재검증합니다.",
        "접근성 미지원은 사용자 도달성 상실이며 상용 앱 품질의 기본 요건입니다.",
        [("accessib",), ("a11y",), ("접근성",), ("글꼴",)],
        keywords=("접근성", "큰 글씨"),
    ),
    _cap(
        "mobile.i18n", "다국어 및 로케일",
        "다국어(i18n) 및 로케일 전환",
        [
            "문자열 리소스 분리 및 언어 전환 UI",
            "날짜·숫자 로케일 포맷 적용",
            "선택 언어 영속화 및 누락 키 폴백",
        ],
        "strings.xml 리소스 분리 및 로케일 매니저를 도입해 재기동 없이 전환을 지원합니다.",
        "글로벌 사용자 확장의 전제 조건입니다.",
        [("i18n",), ("locale",), ("다국어",), ("번역",)],
        keywords=("다국어", "번역"),
    ),
    _cap(
        "mobile.backup", "데이터 백업·복원",
        "데이터 백업·복원 및 기기 이전",
        [
            "전체 데이터 백업 파일 생성/내보내기",
            "백업 파일 복원 및 무결성 검증",
            "복원 실패 시 기존 데이터 보존 안내",
        ],
        "데이터 스냅샷을 체크섬과 함께 직렬화하고 복원은 검증 후 원자적으로 반영합니다.",
        "기기 교체·재설치 시 데이터 이동성은 장기 신뢰의 핵심입니다.",
        [("backup",), ("restore",), ("백업",), ("복원",)],
        keywords=("백업", "복원"),
    ),
)

_GAME_CATALOG: Tuple[ValueCapability, ...] = (
    _cap(
        "game.pause_options", "일시정지·재개 및 옵션 메뉴",
        "일시정지·재개 및 옵션 메뉴",
        [
            "플레이 중 일시정지/재개 오버레이 및 상태 보존",
            "난이도·소리·진동 등 옵션 설정 화면",
            "옵션 값 영속화 및 즉시 적용",
        ],
        "게임 루프 상태를 PAUSED/RUNNING FSM 으로 분리하고 옵션을 로컬에 저장합니다.",
        "사용자가 플레이를 중단·재개하고 자신에게 맞게 조정할 수 있어야 장시간 플레이가 가능합니다.",
        [("pause",), ("option",), ("setting",), ("일시정지",), ("옵션",), ("설정",)],
        keywords=("일시정지", "옵션", "설정"),
    ),
    _cap(
        "game.achievement", "업적·도전과제 시스템",
        "업적·도전과제 및 보상 연출",
        [
            "업적 정의·조건 판정 및 달성 순간 토스트 연출",
            "업적 목록 화면과 달성률 표시",
            "업적 달성 상태 영속화",
        ],
        "게임 이벤트 스트림에 업적 조건 평가기를 붙여 달성 시 비동기 알림을 발행합니다.",
        "반복 플레이에 명확한 목표와 성취 보상을 부여하여 리텐션을 끌어올립니다.",
        [("achievement",), ("trophy",), ("업적",), ("도전과제",), ("훈장",)],
        keywords=("업적", "도전과제"),
    ),
    _cap(
        "game.leaderboard", "리더보드·기록 랭킹",
        "최고 기록 리더보드 및 랭킹",
        [
            "점수/시간 기준 로컬·온라인 랭킹 보드",
            "내 순위 하이라이트 및 친구/전체 토글",
            "시즌/주간 초기화 규칙",
        ],
        "점수 제출/조회 API 를 두고 로컬 랭킹을 우선 표시한 뒤 온라인 순위로 동기화합니다.",
        "경쟁 요소는 자발적 재도전을 유도하는 강력한 동기입니다.",
        [("leaderboard",), ("ranking",), ("리더보드",), ("랭킹",)],
        keywords=("리더보드", "랭킹"),
    ),
    _cap(
        "game.customize_shop", "스킨·커스터마이즈 상점",
        "캐릭터 스킨·커스터마이즈 및 상점",
        [
            "획득 재화로 해금하는 스킨/테마 상점",
            "장착 즉시 반영되는 커스터마이즈 미리보기",
            "보유/장착 상태 영속화",
        ],
        "스킨을 데이터 주도 리소스로 정의하고 상점 UI 와 해금 조건을 분리합니다.",
        "수집·표현 욕구는 플레이 지속 시간을 늘리는 핵심 동기입니다.",
        [("shop",), ("skin",), ("cosmetic",), ("상점",), ("스킨",), ("커스터마이즈",)],
        keywords=("스킨", "상점", "커스터마이즈"),
    ),
    _cap(
        "game.mode_variety", "게임 모드 변주·보너스 스테이지",
        "게임 모드 변주 및 보너스 스테이지",
        [
            "무한/타임어택/도전 등 복수 게임 모드",
            "보스 또는 보너스 스테이지 및 전용 규칙",
            "모드별 최고 기록 분리 저장",
        ],
        "모드별 규칙 파라미터를 데이터로 분리하고 씬 전환에서 모드 컨텍스트를 주입합니다.",
        "단일 규칙의 반복은 빠르게 지루해집니다. 모드 변주는 플레이 수명을 늘립니다.",
        [("boss",), ("bonus",), ("stage",), ("mode",), ("보스",), ("보너스",), ("스테이지",)],
        keywords=("모드", "보스", "보너스"),
    ),
    _cap(
        "game.audio_haptics", "사운드·진동 설정 및 볼륨",
        "사운드·진동 설정 및 볼륨 컨트롤",
        [
            "BGM/SFX/진동 개별 볼륨 슬라이더",
            "음소거 상태 및 설정 영속화",
            "저사양 기기 오디오 지연 방어",
        ],
        "오디오 버스 게인과 햅틱 강도를 설정값에 바인딩하고 저장합니다.",
        "사용 환경(소리 나는 곳/조용한 곳)에 맞출 수 있어야 플레이가 이어집니다.",
        [("volume",), ("sound",), ("vibrat",), ("볼륨",), ("사운드",), ("진동",)],
        keywords=("사운드", "진동", "볼륨"),
    ),
    _cap(
        "game.tutorial", "튜토리얼 및 조작 가이드",
        "튜토리얼 및 조작 가이드",
        [
            "첫 실행 튜토리얼 및 단계별 조작 안내",
            "도전 과제형 연습 스테이지",
            "언제든 다시 볼 수 있는 조작 가이드",
        ],
        "스크립트된 튜토리얼 스텝과 입력 힌트 오버레이를 게임 루프에 연결합니다.",
        "신규 사용자가 규칙을 학습하지 못하면 첫 세션에서 이탈합니다.",
        [("tutorial",), ("guide",), ("튜토리얼",), ("가이드",)],
        keywords=("튜토리얼", "가이드"),
    ),
    _cap(
        "game.i18n", "다국어 및 로컬라이제이션",
        "다국어(i18n) 및 로케일 전환",
        [
            "UI/스토리 문자열 리소스 분리 및 언어 전환",
            "선택 언어 영속화 및 기본 로케일 폴백",
            "폰트/글리프 누락 방어",
        ],
        "문자열 리소스 사전을 도입하고 로케일 전환 시 UI 를 재렌더링합니다.",
        "글로벌 출시의 전제 조건입니다.",
        [("i18n",), ("locale",), ("다국어",), ("번역",)],
        keywords=("다국어", "번역"),
    ),
)

_CLI_CATALOG: Tuple[ValueCapability, ...] = (
    _cap(
        "cli.interactive", "대화형 모드(Interactive REPL)",
        "대화형 모드 및 프롬프트 워크플로",
        [
            "무인자 실행 시 진입하는 대화형 프롬프트 모드",
            "단계별 입력 안내·자동완성·히스토리",
            "배치 모드와 대화형 모드 간 옵션 일관성",
        ],
        "argparse/click 과 별개로 readline 기반 REPL 루프를 추가하고 동일 실행 코어를 공유합니다.",
        "옵션을 외울 필요 없이 탐색적으로 사용할 수 있게 하여 학습 비용을 낮춥니다.",
        [("interactive",), ("repl",), ("prompt",), ("대화형",), ("프롬프트",)],
        keywords=("대화형", "프롬프트"),
    ),
    _cap(
        "cli.completion_help", "셸 자동완성 및 매뉴얼",
        "셸 자동완성 및 상세 매뉴얼",
        [
            "bash/zsh/fish 자동완성 스크립트 생성",
            "서브커맨드별 예제 포함 상세 --help",
            "man 페이지/온라인 문서 생성",
        ],
        "커맨드 정의를 단일 소스로 두고 완성 스크립트와 도움말을 자동 생성합니다.",
        "자동완성과 매뉴얼은 CLI 학습 비용을 결정하는 사용성의 핵심입니다.",
        [("completion",), ("autocomplete",), ("자동완성",), ("man",), ("도움말",)],
        keywords=("자동완성", "도움말"),
    ),
    _cap(
        "cli.output_format", "출력 포맷 선택(json/yaml/table)",
        "출력 포맷 선택 및 파이프 친화 출력",
        [
            "--format json/yaml/table/csv 선택 지원",
            "파이프라인 친화적 stdout 과 로그 stderr 분리",
            "비TTY 환경 색상 자동 비활성화",
        ],
        "출력 직렬화 계층을 분리하고 포맷별 렌더러를 등록하는 플러그형 구조로 구현합니다.",
        "다른 도구와 조합(jq, awk)하려면 기계 판독 가능한 출력이 필수입니다.",
        [("format",), ("json",), ("yaml",), ("format",), ("포맷",), ("형식",)],
        keywords=("포맷", "json", "yaml"),
    ),
    _cap(
        "cli.profile", "설정 프로필 및 프리셋",
        "설정 프로필 및 환경 프리셋",
        [
            "명명된 프로필 저장/선택/삭제",
            "환경별 프리셋(dev/prod) 전환",
            "설정 파일과 환경변수 우선순위 계층",
        ],
        "설정 로더에 프로필 계층을 추가하고 우선순위(CLI > env > profile > default)를 명확히 합니다.",
        "환경마다 다른 설정을 반복 입력하지 않게 하여 반복 사용 마찰을 제거합니다.",
        [("profile",), ("preset",), ("프로필",), ("프리셋",)],
        keywords=("프로필", "프리셋"),
    ),
    _cap(
        "cli.plugin", "플러그인·확장 아키텍처",
        "플러그인·확장 아키텍처",
        [
            "외부 플러그인 로딩 및 버전 호환 검사",
            "플러그인 등록/목록/비활성화 명령",
            "플러그인 실패 격리 및 안전한 비활성화",
        ],
        "엔트리포인트 기반 플러그인 레지스트리와 격리된 실행 경계를 도입합니다.",
        "핵심을 건드리지 않고 기능을 확장할 수 있어야 도구 생태계가 성장합니다.",
        [("plugin",), ("extension",), ("플러그인",), ("확장",)],
        keywords=("플러그인", "확장"),
    ),
    _cap(
        "cli.schedule_batch", "배치·스케줄 실행",
        "배치·스케줄 실행 및 파이프라인",
        [
            "여러 입력을 묶어 처리하는 배치 모드",
            "주기 실행(cron/스케줄) 등록 및 관리",
            "배치 결과 요약 리포트 출력",
        ],
        "작업 디스크립터를 정의하고 스케줄러/배치 러너가 동일 실행 코어를 호출하도록 구성합니다.",
        "무인 반복 실행은 자동화 도구의 본질적 가치입니다.",
        [("schedule",), ("batch",), ("cron",), ("스케줄",), ("배치",), ("주기",)],
        keywords=("스케줄", "배치"),
    ),
    _cap(
        "cli.i18n", "다국어 및 메시지 리소스",
        "다국어(i18n) 및 메시지 리소스",
        [
            "메시지 문자열 리소스 분리 및 언어 전환",
            "로케일 자동 감지 및 폴백",
            "누락 메시지 키 검증",
        ],
        "메시지 카탈로그를 도입하고 로케일에 따라 렌더링합니다.",
        "글로벌 개발자 사용자를 위한 접근성 확장입니다.",
        [("i18n",), ("locale",), ("다국어",), ("번역",)],
        keywords=("다국어", "번역"),
    ),
    _cap(
        "cli.theme", "색상 테마 및 출력 커스터마이즈",
        "색상 테마 및 출력 커스터마이즈",
        [
            "라이트/다크 터미널 테마 프리셋",
            "출력 밀도(verbose/quiet) 선택",
            "NO_COLOR 존중 및 사용자 색상 재정의",
        ],
        "출력 렌더러에 테마 토큰과 밀도 설정을 주입합니다.",
        "터미널 환경과 취향에 맞춘 가독성은 사용성의 직접 요소입니다.",
        [("theme",), ("color",), ("테마",), ("색상",)],
        keywords=("테마", "색상"),
    ),
)

_BACKEND_CATALOG: Tuple[ValueCapability, ...] = (
    _cap(
        "api.pagination", "페이지네이션·필터·정렬",
        "목록 API 페이지네이션·필터·정렬",
        [
            "커서/오프셋 기반 페이지네이션 표준화",
            "필드별 필터와 정렬 파라미터",
            "대용량 목록에서도 안정적인 응답 스키마",
        ],
        "쿼리 빌더에 공통 페이지네이션·필터 미들웨어를 적용하고 인덱스를 함께 정의합니다.",
        "목록 API 가 확장되지 않으면 클라이언트는 전체를 받아 스스로 걸러야 합니다. 이는 사용자 체감 성능 저하입니다.",
        [("pagination",), ("filter",), ("sort",), ("페이지네이션",), ("필터",), ("정렬",)],
        keywords=("페이지네이션", "필터", "정렬"),
    ),
    _cap(
        "api.search", "검색 API",
        "전문 검색 API 및 랭킹",
        [
            "키워드/필드 검색 엔드포인트",
            "관련도 랭킹 및 하이라이트",
            "검색 성능을 위한 인덱스 전략",
        ],
        "검색 인덱스(FTS/외부 엔진)를 도입하고 결과 랭킹을 노출합니다.",
        "검색은 데이터가 쌓일수록 사용자가 값을 찾게 하는 핵심 기능입니다.",
        [("search",), ("검색",)],
        keywords=("검색",),
    ),
    _cap(
        "api.docs", "API 문서(OpenAPI/Swagger UI)",
        "대화형 API 문서(OpenAPI/Swagger UI)",
        [
            "OpenAPI 스키마 자동 생성",
            "/docs 대화형 문서 UI 및 예제 요청",
            "스키마 변경 시 문서 자동 동기화",
        ],
        "프레임워크 스키마에서 OpenAPI 를 생성하고 문서 UI 를 라우트에 노출합니다.",
        "문서가 없으면 연동 개발자는 시행착오를 겪습니다. 문서화는 API 의 사용자 경험입니다.",
        [("openapi",), ("swagger",), ("docs",), ("문서",)],
        keywords=("문서", "openapi", "swagger"),
    ),
    _cap(
        "api.webhook", "웹훅·이벤트 구독",
        "웹훅·이벤트 구독 및 재전송",
        [
            "도메인 이벤트 발행 및 웹훅 구독 등록",
            "서명 검증 및 전송 실패 재시도/백오프",
            "구독 관리 API 및 전송 로그",
        ],
        "이벤트 버스와 서명된 웹훅 디스패처를 도입하고 실패 큐로 재전송합니다.",
        "폴링 없이 변경을 전달받는 것은 통합 사용자에게 결정적 가치입니다.",
        [("webhook",), ("event",), ("웹훅",), ("이벤트",)],
        keywords=("웹훅", "이벤트"),
    ),
    _cap(
        "api.upload", "파일 업로드·다운로드",
        "파일 업로드·다운로드 및 스토리지",
        [
            "멀티파트/스트리밍 업로드 엔드포인트",
            "무결성 검증 및 저장소 추상화",
            "서명 URL 기반 안전한 다운로드",
        ],
        "스토리지 추상화 계층과 스트리밍 핸들러를 도입하고 메타데이터를 DB 에 기록합니다.",
        "파일 첨부는 대부분의 실서비스에서 필수 요구이며, 없으면 사용자는 외부 도구로 우회합니다.",
        [("upload",), ("download",), ("업로드",), ("첨부",)],
        keywords=("업로드", "다운로드"),
    ),
    _cap(
        "api.auth_rbac", "인증·권한 및 API 키",
        "인증·권한(RBAC) 및 API 키 관리",
        [
            "토큰/JWT 인증 및 리프레시 흐름",
            "역할 기반 권한(RBAC) 및 스코프",
            "API 키 발급/폐기/회전 관리",
        ],
        "인증 미들웨어와 권한 정책 계층을 도입하고 감사 로그와 연계합니다.",
        "다중 사용자 서비스로 확장하려면 인증·권한이 필수입니다.",
        [("jwt",), ("oauth",), ("rbac",), ("auth", "key"), ("인증",), ("권한",)],
        keywords=("인증", "권한", "api 키"),
    ),
    _cap(
        "api.bulk", "배치·벌크 API",
        "배치·벌크 작업 API",
        [
            "다건 생성/수정 벌크 엔드포인트",
            "부분 실패 결과 리포트(성공/실패 항목별)",
            "비동기 작업 접수 및 상태 조회",
        ],
        "벌크 요청을 트랜잭션 경계와 함께 처리하고 항목별 결과를 반환합니다.",
        "클라이언트가 반복 호출로 지연을 겪지 않게 하는 처리량 가치입니다.",
        [("bulk",), ("batch",), ("벌크",), ("일괄",)],
        keywords=("벌크", "배치"),
    ),
    _cap(
        "api.audit", "감사 로그 및 활동 이력",
        "감사 로그·활동 이력 API",
        [
            "주요 변경에 대한 감사 로그 기록",
            "활동 이력 조회 API 및 필터",
            "보존 정책 및 개인정보 마스킹",
        ],
        "변경 훅에서 감사 이벤트를 발행하고 조회 API 를 제공합니다.",
        "변경 추적은 운영 신뢰와 규정 준수의 기본 요건입니다.",
        [("audit",), ("activity",), ("감사",), ("이력",)],
        keywords=("감사", "이력"),
    ),
    _cap(
        "api.versioning", "API 버저닝 정책",
        "API 버저닝 및 하위 호환 정책",
        [
            "URL/헤더 기반 버전 관리",
            "버전별 deprecation 공지 및 마이그레이션 가이드",
            "구·신 버전 공존 라우팅",
        ],
        "라우터에 버전 프리픽스를 도입하고 변경 정책을 문서화합니다.",
        "연동 클라이언트를 깨지 않고 발전할 수 있게 하는 신뢰 장치입니다.",
        [("version",), ("버전",), ("deprecat",)],
        keywords=("버전",),
    ),
)

_GENERIC_CATALOG: Tuple[ValueCapability, ...] = (
    _cap(
        "generic.export_share", "내보내기·공유",
        "결과물 내보내기·공유 및 저장",
        [
            "결과물을 파일/링크로 내보내기 및 공유",
            "내보내기 포맷 선택과 검증",
            "실패 시 재시도 안내",
        ],
        "기존 데이터 파이프라인에 직렬화·공유 계층을 추가합니다.",
        "결과물의 출구가 없으면 사용자 가치가 외부로 전달되지 않습니다.",
        [("export",), ("share",), ("내보내기",), ("공유",)],
        keywords=("내보내기", "공유"),
    ),
    _cap(
        "generic.search_filter", "검색·필터",
        "검색·필터 및 정렬",
        [
            "실시간 검색 및 결과 하이라이트",
            "카테고리 필터·정렬 옵션",
            "결과 없음 안내와 초기화",
        ],
        "비동기 검색 계층과 필터 상태를 상태 저장소에 연결합니다.",
        "데이터 증가에 대응하는 탐색 효율은 재사용성의 핵심입니다.",
        [("search",), ("filter",), ("검색",), ("필터",)],
        keywords=("검색", "필터"),
    ),
    _cap(
        "generic.settings", "설정 및 개인화",
        "사용자 설정 및 개인화",
        [
            "설정 화면 및 즉시 적용",
            "설정 영속화 및 기본값 복원",
            "잘못된 설정 방어",
        ],
        "설정 상태를 단일 스토어로 관리하고 영속화합니다.",
        "개인화는 장기 사용과 재방문의 최소 조건입니다.",
        [("setting",), ("preference",), ("설정",)],
        keywords=("설정", "개인화"),
    ),
    _cap(
        "generic.notification", "알림·리마인더",
        "알림·리마인더 및 재방문 트리거",
        [
            "주요 이벤트 알림",
            "사용자 지정 리마인더",
            "알림 센터 관리",
        ],
        "이벤트 버스에 알림 구독자를 연결합니다.",
        "재방문 유도의 직접적 수단입니다.",
        [("notif",), ("remind",), ("알림",), ("리마인더",)],
        keywords=("알림", "리마인더"),
    ),
    _cap(
        "generic.onboarding", "온보딩·도움말",
        "온보딩·도움말 및 빈 상태 가이드",
        [
            "단계별 온보딩 투어",
            "기능별 도움말",
            "다음 행동을 제시하는 Empty State",
        ],
        "투어 스텝과 도움말 콘텐츠를 코드와 함께 관리합니다.",
        "첫 사용 가치 전달은 활성화율의 직접 원인입니다.",
        [("onboard",), ("tutorial",), ("help",), ("온보딩",), ("도움말",), ("튜토리얼",)],
        keywords=("온보딩", "도움말"),
    ),
    _cap(
        "generic.a11y", "접근성·단축키",
        "접근성 및 키보드 단축키",
        [
            "핵심 동작 단축키",
            "포커스/ARIA/스크린리더 대응",
            "최소 터치 타깃·색 대비 점검",
        ],
        "접근성 속성과 단축키를 컴포넌트 단위로 적용합니다.",
        "도달 가능한 사용자 풀을 넓히는 도달성 요건입니다.",
        [("a11y",), ("accessib",), ("shortcut",), ("접근성",), ("단축키",)],
        keywords=("접근성", "단축키"),
    ),
    _cap(
        "generic.i18n", "다국어·로컬라이제이션",
        "다국어(i18n) 및 로케일 전환",
        [
            "문자열 리소스 분리",
            "언어 전환 UI 및 폴백",
            "로케일별 포맷 적용",
        ],
        "문자열 카탈로그와 로케일 전환 계층을 도입합니다.",
        "글로벌 확장의 전제 조건입니다.",
        [("i18n",), ("locale",), ("다국어",), ("번역",)],
        keywords=("다국어", "번역"),
    ),
    _cap(
        "generic.backup", "백업·복원",
        "데이터 백업·복원 및 가져오기",
        [
            "전체 데이터 백업/불러오기",
            "외부 데이터 가져오기 및 검증",
            "실패 시 원자적 롤백",
        ],
        "데이터 스냅샷을 버전 태그와 함께 직렬화합니다.",
        "데이터 이동성은 장기 신뢰의 증거입니다.",
        [("backup",), ("restore",), ("import",), ("백업",), ("복원",), ("가져오기",)],
        keywords=("백업", "복원"),
    ),
)


CATALOGS: Dict[str, Tuple[ValueCapability, ...]] = {
    "saas_web": _WEB_CATALOG,
    "mobile_app": _MOBILE_CATALOG,
    "game": _GAME_CATALOG,
    "cli_agent": _CLI_CATALOG,
    "daemon": _CLI_CATALOG,
    "backend_api": _BACKEND_CATALOG,
    "generic": _GENERIC_CATALOG,
}


# ---------------------------------------------------------------------------
# 도메인 정규화
# ---------------------------------------------------------------------------
_DOMAIN_ALIASES = {
    "web": "saas_web", "saas": "saas_web", "saas_web": "saas_web", "webapp": "saas_web",
    "app": "mobile_app", "android": "mobile_app", "mobile": "mobile_app", "mobile_app": "mobile_app",
    "game": "game", "audio": "generic", "unknown": "generic",
    "cli": "cli_agent", "cli_agent": "cli_agent", "tool": "cli_agent",
    "daemon": "daemon", "bot": "daemon",
    "api": "backend_api", "backend": "backend_api", "backend_api": "backend_api",
}


def normalize_domain(domain: Optional[str]) -> str:
    if not domain:
        return "generic"
    key = re.sub(r"[^a-z_]", "", str(domain).strip().lower())
    return _DOMAIN_ALIASES.get(key, "generic")


def catalog_for(domain: Optional[str]) -> Tuple[ValueCapability, ...]:
    return CATALOGS.get(normalize_domain(domain), _GENERIC_CATALOG)


# ---------------------------------------------------------------------------
# 구현 여부 판정
# ---------------------------------------------------------------------------
def _tokens_of(text: str) -> set:
    s = str(text or "")
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)   # camelCase 분해
    toks = set()
    for m in re.findall(r"[a-z0-9]+|[가-힣]+", s.lower()):
        if m:
            toks.add(m)
    return toks


def _evidence_texts(reality: Any, workspace: Optional[str], existing_titles: Optional[Iterable[str]]) -> List[str]:
    """디스크 구현 증거 텍스트를 수집한다 (root goal 은 '구현됨' 증거가 아니므로 제외)."""
    texts: List[str] = []
    if reality is not None:
        texts.extend(getattr(reality, "implemented_components", None) or [])
        texts.extend(getattr(reality, "source_files", None) or [])
        texts.extend(getattr(reality, "entrypoints", None) or [])
        texts.extend(getattr(reality, "completed_milestones", None) or [])
        cur = getattr(reality, "current_milestone", None)
        if cur:
            texts.append(str(cur))
    if workspace and not reality:
        try:
            from codebase_reality_inspector import CodebaseRealityInspector
            _r = CodebaseRealityInspector.inspect(workspace)
            texts.extend(_r.implemented_components or [])
            texts.extend(_r.source_files or [])
            texts.extend(_r.completed_milestones or [])
        except Exception:
            texts.append(" ".join(os.listdir(workspace)) if os.path.isdir(workspace) else "")
    texts.extend(existing_titles or [])
    return [t for t in texts if t]


def _value_brief_tokens(workspace: Optional[str]) -> set:
    """P0-3: 사용자 가치 브리프(누가/단 하나의 Job/틀리면 비용) 토큰을 읽는다."""
    if not workspace:
        return set()
    try:
        from craft_inputs import value_brief_text
        return _tokens_of(value_brief_text(workspace))
    except Exception:
        return set()


def is_capability_implemented(cap: ValueCapability, evidence_tokens: set) -> bool:
    """cap.detect 중 하나의 토큰 묶음이 전부 증거에 존재하면 구현됨으로 본다."""
    for phrase in cap.detect:
        if phrase and all(tok in evidence_tokens for tok in phrase):
            return True
    return False


def _title_already_planned(cap: ValueCapability, existing_titles: Iterable[str]) -> bool:
    cap_tokens = _tokens_of(f"{cap.feature} {cap.title}")
    for title in existing_titles or []:
        t_tokens = _tokens_of(title)
        if not t_tokens or not cap_tokens:
            continue
        overlap = cap_tokens & t_tokens
        if len(overlap) >= 3:
            return True
        # feature 명이 제목에 그대로 포함된 경우
        if len(cap.feature) >= 3 and cap.feature in str(title):
            return True
    return False


# ---------------------------------------------------------------------------
# 선택
# ---------------------------------------------------------------------------
def select_capability(
    domain: Optional[str],
    reality: Any = None,
    workspace: Optional[str] = None,
    root_goal: str = "",
    existing_titles: Optional[Iterable[str]] = None,
) -> Optional[ValueCapability]:
    """아직 구현/기획되지 않은 가장 가치 높은 사용자 기능을 선택한다. 없으면 None."""
    titles = list(existing_titles or [])
    evidence = set()
    for text in _evidence_texts(reality, workspace, titles):
        evidence |= _tokens_of(text)

    candidates: List[ValueCapability] = []
    for cap in catalog_for(domain):
        if is_capability_implemented(cap, evidence):
            continue
        if _title_already_planned(cap, titles):
            continue
        candidates.append(cap)

    if not candidates:
        return None

    goal_tokens = _tokens_of(root_goal)
    brief_tokens = _value_brief_tokens(workspace)

    def _priority_key(cap: ValueCapability) -> Tuple[int, int]:
        # 1순위: 사용자 목표 친화도(기존 동작 보존). 2순위: 가치 브리프(누가/Job/비용) 친화도(P0-3).
        cap_tokens = _tokens_of(f"{cap.feature} {' '.join(cap.keywords)}")
        return (len(cap_tokens & goal_tokens), len(cap_tokens & brief_tokens))

    # 목표 친화도가 높은 후보를 우선하되, 동률이면 가치 브리프, 그다음 카탈로그(가치) 순서를 유지한다.
    return max(candidates, key=_priority_key)


# ---------------------------------------------------------------------------
# 시드/다이제스트 생성
# ---------------------------------------------------------------------------
def build_seed(
    domain: Optional[str],
    root_goal: str = "",
    reality: Any = None,
    workspace: Optional[str] = None,
    existing_titles: Optional[Iterable[str]] = None,
) -> Optional[Dict[str, Any]]:
    """차기 마일스톤 시드(title/what/how/why/visual/digest)를 생성한다. 후보 없으면 None."""
    if not product_value_enabled():
        return None
    cap = select_capability(
        domain=domain, reality=reality, workspace=workspace,
        root_goal=root_goal, existing_titles=existing_titles,
    )
    if cap is None:
        return None

    why = (
        f"{cap.why} 현재 사용자 목표(\"{ (root_goal or '').strip()[:80] }\")의 다음 단계로, "
        f"소스/PRD만 보던 기존 폴백이 놓쳤던 **사용자 체감 신규 기능**을 발주합니다. "
        f"이번 마일스톤은 단순 품질 하드닝/E2E 통과가 아니라 실제 화면·동작에 새 가치를 추가합니다."
    )
    how = (
        f"{cap.how} 기존 검증 완료 코드는 100% 보존(Zero-Regression)하고, "
        f"신규 기능을 기존 데이터/상태/워크플로에 끊김 없이 연결합니다."
    )

    visual_spec = ""
    visual_approved = False
    if normalize_domain(domain) in ("saas_web", "mobile_app", "game"):
        visual_spec = (
            "🎨 [Rule 8 시각 디자인 에셋 생성 승인]\n"
            f"• Type B: [{cap.feature}] 구현에 필요한 아이콘/패널/상태 에셋을 실제 이미지로 생성하여 소스에 바인딩\n"
            "• 단색 사각형·텍스트 임시 대체 금지 (실제 렌더링 품질 확보)"
        )
        visual_approved = True

    digest = format_digest(cap)
    return {
        "title": cap.title,
        "what": list(cap.tasks),
        "how": how,
        "why": why,
        "visual_asset_spec": visual_spec,
        "visual_approved": visual_approved,
        "digest": digest,
        "feature": cap.feature,
        "category": cap.category,
        "capability_id": cap.id,
    }


def format_digest(cap: ValueCapability) -> str:
    lines = [
        "📦 **[제품 가치 기획 (Product Value) — 아직 없는 사용자 기능]**",
        f"- 차기 사용자 체감 기능: {cap.feature}",
        f"- 발주 근거: {cap.why}",
        "- 이 항목은 품질 하드닝/E2E 통과 같은 자기참조적 과제가 아니라,",
        "  사용자가 화면·동작에서 실제로 새로 체감하는 제품 가치입니다.",
    ]
    return "\n".join(lines)


def select_roadmap_plans(
    domain: Optional[str],
    workspace: str,
    existing_titles: Optional[Iterable[str]] = None,
    root_goal: str = "",
    limit: int = 2,
) -> List[Tuple[str, List[str]]]:
    """RoadmapManager 자동 확장용: 아직 없는 사용자 기능 (title, tasks) 목록."""
    if not product_value_enabled():
        return []
    titles = list(existing_titles or [])
    plans: List[Tuple[str, List[str]]] = []
    for _ in range(max(0, limit)):
        seed = build_seed(
            domain=domain, root_goal=root_goal, workspace=workspace,
            existing_titles=titles + [p[0] for p in plans],
        )
        if not seed:
            break
        plans.append((seed["title"], seed["what"]))
    return plans


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------
class ProductValuePlanner:
    """Commander 기획 단계 제품 가치 기능 기획 파사드."""

    @staticmethod
    def enabled() -> bool:
        return product_value_enabled()

    @staticmethod
    def normalize_domain(domain: Optional[str]) -> str:
        return normalize_domain(domain)

    @staticmethod
    def catalog_for(domain: Optional[str]) -> Tuple[ValueCapability, ...]:
        return catalog_for(domain)

    @staticmethod
    def is_hardening_only_plan(title: str, tasks: Optional[Sequence[str]] = None) -> bool:
        return is_hardening_only_plan(title, tasks)

    @staticmethod
    def select_capability(domain, reality=None, workspace=None, root_goal="", existing_titles=None):
        return select_capability(
            domain=domain, reality=reality, workspace=workspace,
            root_goal=root_goal, existing_titles=existing_titles,
        )

    @staticmethod
    def build_seed(domain, root_goal="", reality=None, workspace=None, existing_titles=None):
        return build_seed(
            domain=domain, root_goal=root_goal, reality=reality,
            workspace=workspace, existing_titles=existing_titles,
        )

    @staticmethod
    def select_roadmap_plans(domain, workspace, existing_titles=None, root_goal="", limit=2):
        return select_roadmap_plans(
            domain=domain, workspace=workspace, existing_titles=existing_titles,
            root_goal=root_goal, limit=limit,
        )

    @staticmethod
    def format_digest(cap) -> str:
        return format_digest(cap)
