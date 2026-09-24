#!/usr/bin/env python3
"""
Feedback Intent Classifier & Vocabulary Coherence Engine.
Single Source of Truth (SoT) for Mode 3 Autonomous Product Planning vs User Feedback Directives.

Unifies intent classification across:
1. bot.py (classify_user_intent)
2. commander.py (_build_heuristic_5w1h_plan & decompose_user_feedback)
3. commander.py (_build_heuristic_adaptive_plan)
4. commander.py (plan_milestone_5w1h_async anti-hallucination post-guard)
5. roadmap_manager.py (auto_expand_roadmap)
"""

import os
import re
from dataclasses import dataclass
from typing import List, Set, Tuple, Optional, Dict, Any


# ──────────────────────────────────────────────
# Shared Keyword Taxonomies (Single Source of Truth)
# ──────────────────────────────────────────────

FEEDBACK_DEFECT_KEYWORDS: List[str] = [
    "피드백", "버그", "오류", "크래시", "crash", "튕김", "튕기", "튕긴", "튕겨",
    "별로", "느림", "느려", "멈춤", "멈춰", "멈추", "안 됨", "안됨", "안 돼", "안돼",
    "안 좋아", "안좋아", "안 좋", "안좋", "이상해", "이상하", "이상함", "이상한", "이상",
    "문제 있어", "문제있어", "문제 발생", "문제점", "문제", "깨져", "망가", "망가짐",
    "망가졌", "엉망", "심각", "불량",
    "안 눌림", "안눌림", "안 눌려", "안눌려", "마음에 안", "약해보여", "이슈", "불만",
    "동작 안", "먹통", "깨짐", "오작동", "지연", "프리징", "버벅", "버벅임", "버벅거",
    "강제종료", "강종", "죽어", "죽는", "로딩 안", "안 열려", "안열려", "에러",
    "수정해줘", "고쳐줘", "해결해줘"
]

CRASH_STABILITY_KEYWORDS: List[str] = [
    "튕김", "튕기", "튕긴", "튕겨", "크래시", "crash", "종료", "강제종료", "강종",
    "죽어", "죽는", "안정성", "최적화", "오류", "에러", "멈춤", "멈춰", "멈추",
    "프리징", "먹통", "버벅", "버벅임", "버벅거", "안 열려", "안열려",
    "버그", "누수", "결함", "오작동", "성능"
]

EXPLICIT_NEW_PROJECT_KEYWORDS: List[str] = [
    "새 프로젝트", "처음부터", "신규 프로젝트", "새로 만들어줘", "새로 개발해줘",
    "새로 제작해줘", "create new", "start over",
    "앱 개발", "서비스 개발", "프로젝트 개발", "신규 개발"
]

GENERAL_CREATION_VERBS: List[str] = [
    "만들어줘", "개발해줘", "구현해줘", "제작해줘", "시작해줘", "create", "build a"
]

DESIGN_REFRESH_KEYWORDS: List[str] = [
    "로고", "앱 이름", "앱명", "리브랜딩", "디자인", "컬러", "색상", "버튼",
    "가독성", "룩앤필", "스타일", "팔레트", "통일성", "연결성", "깔끔", "크게", "작게",
    "예쁘게", "세련", "다듬", "조잡", "촌스럽", "답답", "시원", "글씨", "폰트",
    "허전", "채워", "아이콘", "그레이톤", "타이포", "ui"
]

FEATURE_EXPANSION_KEYWORDS: List[str] = [
    "추가", "기능", "신규", "연동", "확장", "새로운", "지원", "add", "feature", "new",
    "검색", "필터", "정렬", "내보내기", "공유", "동기화", "알림"
]


@dataclass
class IntentVerdict:
    """Classified intent verdict for a user prompt."""
    is_new_goal: bool
    is_feedback_directive: bool
    has_feedback_signal: bool
    intent_type: str            # 'defect', 'design', 'feature', 'general_feedback', 'new_project', 'chit_chat', 'inquiry'
    title_prefix: str           # Prefix starting with '사용자 피드백' for 100% backward compatibility
    title_category: str         # Human-readable category
    cleaned_prompt: str
    is_actionable: bool = True  # False for chit_chat and inquiry

    def to_bot_tuple(self) -> Tuple[bool, bool]:
        """Backward-compatible tuple (is_new_goal, has_feedback_signal) for bot.py."""
        return self.is_new_goal, (self.has_feedback_signal and self.is_actionable)


class FeedbackIntentClassifier:
    """
    Unified intent classifier and vocabulary coherence checker.
    Single Source of Truth (SoT) for user feedback directive vs roadmap planning.
    """

    @classmethod
    def clean_prompt(cls, prompt: str) -> str:
        """Strip command prefixes like !mode 3 or /mode 3."""
        if not prompt:
            return ""
        return re.sub(r'^[!/]?mode\s+\S+\s*', '', prompt, flags=re.IGNORECASE).strip()

    @classmethod
    def classify(
        cls,
        prompt: str,
        project_initialized: bool = True,
        is_explicit_m3: bool = False,
        workspace: Optional[str] = None
    ) -> IntentVerdict:
        """
        Classifies user prompt into IntentVerdict.

        Rules:
        1. If project is ALREADY initialized (ROADMAP.md exists):
           - Only explicit new project creation phrases ('새 프로젝트', '처음부터', etc.) trigger is_new_goal = True.
           - Colloquial modification/creation requests ('디자인 좀 통일성 있게 만들어줘', '버튼 키워줘', '검색 기능 추가해줘')
             do NOT wipe the project; they are routed as feedback directives (is_feedback_directive = True).
        2. If project is NOT yet initialized (fresh start):
           - General creation verbs or is_explicit_m3 trigger is_new_goal = True.
        3. Intent types and prefixes:
           - Defect / Crash: '사용자 피드백 긴급 반영'
           - Design / UI polish: '사용자 피드백 디자인 개선'
           - Feature additions: '사용자 피드백 기능 확장'
           - General directives: '사용자 피드백 지시 반영'
           - Chit-chat / Inquiries: '일반 대화' / '상태 조회' (Non-actionable, pipeline skipped)
        """
        raw_p = prompt or ""
        clean_p = cls.clean_prompt(raw_p)
        clean_lower = clean_p.lower()

        # Guard against internal automated directives
        is_internal_directive = any(clean_p.startswith(p) for p in [
            "🎯 [", "사용자 원본 목표:", "[무인 자율", "[🎯", "[📍", "[🔧", "👑 ["
        ])
        if is_internal_directive:
            return IntentVerdict(
                is_new_goal=False,
                is_feedback_directive=False,
                has_feedback_signal=False,
                intent_type="internal",
                title_prefix="내부 지시",
                title_category="내부 지시",
                cleaned_prompt=clean_p,
                is_actionable=True
            )

        # 1. Defect & complaint detection
        has_negative_signal = any(k in clean_lower for k in [
            "안 좋", "안좋", "이상", "문제", "깨져", "망가", "엉망", "심각", "불량",
            "에러", "오류", "버그", "크래시", "멈춤", "느림", "안 됨", "안됨", "실패", "안 돼", "안돼"
        ])
        has_defect_word = any(k in clean_lower for k in FEEDBACK_DEFECT_KEYWORDS)
        has_crash_word = any(k in clean_lower for k in CRASH_STABILITY_KEYWORDS)
        has_commerce_defect = any(k in clean_lower for k in ["결제", "장바구니", "주문"]) and any(
            c in clean_lower for c in ["멈춤", "오류", "안됨", "안 됨", "안눌림", "안 눌림", "느림", "버그", "프리징", "실패"]
        )
        is_defect = has_defect_word or has_crash_word or has_commerce_defect or has_negative_signal

        # 2. Line-start structured bullet check
        has_structured_bullet = bool(re.search(r'(?m)^\s*(?:\d+[.)]|[①-⑩]|[-*•])\s+', raw_p))

        # 3. Explicit new project creation keywords & regex patterns
        is_explicit_new_creation = (
            any(k in clean_lower for k in EXPLICIT_NEW_PROJECT_KEYWORDS)
            or bool(re.search(r'새(?:로운)?\s*(?:[\w가-힣]+\s*)?(?:앱|프로젝트|서비스|게임|솔루션|프로그램|도구|사이트|웹)\s*(?:만들|개발|제작|구현|시작|하고|원해|필요|구상|설계|기획)', clean_lower))
            or bool(re.search(r'처음부터\s*(?:다시|새로|시작|만들|개발)', clean_lower))
            or bool(re.search(r'\b(?:create|build|start)\s+(?:a\s+)?new\s+(?:[\w]+\s+)?(?:app|project|service|game|tool|system)\b', clean_lower))
        )

        # 4. Explicit feedback phrases
        has_explicit_feedback_phrase = any(k in clean_lower for k in [
            "피드백 반영", "버그 수정", "크래시 해결", "요청사항 반영", "수정해줘", "고쳐줘", "개선해줘",
            "바꿔줘", "변경해줘", "다듬어줘", "리팩토링해줘", "반영해줘", "최적화해줘"
        ])

        # 5. Design & UI polish check
        has_design_req = any(k in clean_lower for k in DESIGN_REFRESH_KEYWORDS)

        # 6. Feature addition check
        has_feature_req = any(k in clean_lower for k in FEATURE_EXPANSION_KEYWORDS)

        # 7. Action verbs (refinement/modification only, not initial creation)
        has_action_verb = any(v in clean_lower for v in [
            "수정", "개선", "바꿔", "고쳐", "삭제", "제거", "추가", "연결", "최적화", "정리",
            "다듬어", "리팩토링", "반영", "줄여", "높여", "맞춰"
        ])

        # 8. Chit-chat / Non-actionable Inquiry Check
        CHIT_CHAT_KEYWORDS = {
            "안녕", "안녕하세요", "안뇽", "하이", "반가워", "반갑습니다", "하이요", "안녕요",
            "hello", "hi", "hey", "good morning", "good evening", "good afternoon",
            "고마워", "고마워요", "감사", "감사합니다", "감사해요", "수고했어", "수고하세요",
            "수고하셨습니다", "수고", "thanks", "thank you", "thx"
        }
        INQUIRY_KEYWORDS = [
            "현재 상태", "진행 상황", "진행상황", "현황", "어디까지", "지금 뭐해",
            "status", "progress", "state", "현재 진행"
        ]

        # Defect B fix: Normalize chit-chat text by removing Korean laugh/cry chars, punctuation, emojis
        chitchat_norm = re.sub(r"[ㅋㅎㅠㅜ]", "", clean_lower)
        chitchat_norm = re.sub(r"[^\w\s가-힣a-zA-Z0-9]", " ", chitchat_norm).strip()
        chitchat_norm_tight = re.sub(r"\s+", " ", chitchat_norm).strip()
        chitchat_words = set(chitchat_norm_tight.split()) if chitchat_norm_tight else set()

        is_chitchat = (
            (
                chitchat_norm_tight in CHIT_CHAT_KEYWORDS
                or (bool(chitchat_words) and chitchat_words.issubset(CHIT_CHAT_KEYWORDS))
                or (len(chitchat_words) <= 2 and bool(chitchat_words.intersection(CHIT_CHAT_KEYWORDS)))
            )
            and not (is_defect or has_negative_signal or has_design_req or has_feature_req or has_explicit_feedback_phrase or is_explicit_new_creation or has_action_verb)
        )

        # Defect C fix: Inquiries must NOT contain negative signals or defect complaints
        is_inquiry = (
            any(k in clean_lower for k in INQUIRY_KEYWORDS)
            and not (is_defect or has_negative_signal or has_design_req or has_feature_req or has_explicit_feedback_phrase or is_explicit_new_creation or has_action_verb)
        )

        has_feedback_signal = (
            not (is_chitchat or is_inquiry or is_explicit_new_creation) and (
                has_explicit_feedback_phrase
                or (has_structured_bullet and (is_defect or has_action_verb or has_design_req or has_feature_req))
                or is_defect
                or has_design_req
                or has_feature_req
                or has_action_verb
            )
        )

        # Check structured bullet new product spec (e.g. '쇼핑몰 앱 만들어줘\n1. 상품 목록\n2. 장바구니\n3. 결제')
        is_numbered_spec = (
            has_structured_bullet
            and any(k in clean_lower for k in GENERAL_CREATION_VERBS)
            and not (is_defect or has_design_req)
        )

        # Goal determination
        if not project_initialized:
            has_creation_intent = is_explicit_m3 or any(k in clean_lower for k in GENERAL_CREATION_VERBS)
            is_new_goal = is_explicit_new_creation or is_numbered_spec or (has_creation_intent and not has_feedback_signal)
        else:
            is_new_goal = is_explicit_new_creation or is_numbered_spec

        # Feedback directive determination
        # On an initialized project, only actionable requests are treated as feedback directives.
        # Chit-chat and inquiries are NOT feedback directives!
        is_actionable = not (is_chitchat or is_inquiry)
        is_feedback_directive = (
            bool(clean_p)
            and is_actionable
            and not is_new_goal
            and project_initialized
        )

        # Taxonomy and prefix selection
        if is_chitchat:
            intent_type = "chit_chat"
            title_prefix = "일반 대화"
            title_category = "잡담/인사"
        elif is_inquiry:
            intent_type = "inquiry"
            title_prefix = "상태 조회"
            title_category = "현황 문의"
        elif is_new_goal:
            intent_type = "new_project"
            title_prefix = "신규 프로젝트 기획"
            title_category = "신규 프로젝트"
        elif is_defect:
            intent_type = "defect"
            title_prefix = "사용자 피드백 긴급 반영"
            title_category = "긴급 결함 대응"
        elif has_design_req:
            intent_type = "design"
            title_prefix = "사용자 피드백 디자인 개선"
            title_category = "디자인/UI 고도화"
        elif has_feature_req:
            intent_type = "feature"
            title_prefix = "사용자 피드백 기능 확장"
            title_category = "기능 확장"
        else:
            intent_type = "general_feedback"
            title_prefix = "사용자 피드백 지시 반영"
            title_category = "지시 반영"

        return IntentVerdict(
            is_new_goal=is_new_goal,
            is_feedback_directive=is_feedback_directive,
            has_feedback_signal=has_feedback_signal,
            intent_type=intent_type,
            title_prefix=title_prefix,
            title_category=title_category,
            cleaned_prompt=clean_p,
            is_actionable=is_actionable
        )

    # ──────────────────────────────────────────────
    # Generalized Vocabulary Overlap & Anti-Hallucination
    # ──────────────────────────────────────────────

    _STOPWORDS: Set[str] = {
        "v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10", "v11",
        "phase", "단계", "구현", "적용", "개발", "연동", "추가", "개선", "지원",
        "the", "and", "for", "with", "item", "task", "milestone", "and", "in",
        "of", "on", "to", "a", "an", "is", "및", "등", "수", "관련"
    }

    @classmethod
    def extract_tokens(cls, text: str) -> Set[str]:
        """Extract meaningful alphanumeric & Korean tokens from text, filtering stopwords."""
        if not text:
            return set()
        raw = re.findall(r'[a-zA-Z가-힣0-9]+', text.lower())
        return {t for t in raw if len(t) >= 2 and t not in cls._STOPWORDS}

    @classmethod
    def compute_vocabulary_overlap(cls, candidate: str, references: List[str]) -> float:
        """
        Computes token overlap ratio of candidate relative to references.
        Returns value in [0.0, 1.0].
        """
        cand_tokens = cls.extract_tokens(candidate)
        if not cand_tokens:
            return 0.0

        ref_tokens: Set[str] = set()
        for ref in references:
            ref_tokens.update(cls.extract_tokens(ref))

        if not ref_tokens:
            return 0.0

        intersection = cand_tokens.intersection(ref_tokens)
        return len(intersection) / len(cand_tokens)

    @classmethod
    def is_phantom_or_divergent(
        cls,
        candidate_title: str,
        expected_title: str,
        expected_version: str,
        user_prompt: str = ""
    ) -> bool:
        """
        Generalized anti-hallucination check without hardcoded keyword blacklists.
        Detects if candidate_title is an off-target phantom hallucination.

        Evaluates:
        1. Empty or whitespace title.
        2. Major version divergence (e.g. candidate specifies v11.0 when expected is v1.0 / v2.0).
        3. Zero vocabulary overlap with expected title, prompt, and expected version.
        """
        if not candidate_title or not candidate_title.strip():
            return True

        cand = candidate_title.strip()

        # 1. Version divergence check (strictly anchored to version tokens e.g. v1.0, ver 2, version 3)
        cand_ver_m = re.search(r'\b(?:v|ver|version)\s*(\d+)(?:\.\d+)?\b', cand.lower())
        exp_ver_m = re.search(r'\b(?:v|ver|version)\s*(\d+)(?:\.\d+)?\b', (expected_version or "").lower())
        if cand_ver_m and exp_ver_m:
            try:
                cand_maj = int(cand_ver_m.group(1))
                exp_maj = int(exp_ver_m.group(1))
                if (cand_maj - exp_maj) >= 3:
                    return True
            except (ValueError, TypeError):
                pass

        # 2. Vocabulary overlap check
        cand_tokens = cls.extract_tokens(cand)
        if len(cand_tokens) >= 2:
            overlap = cls.compute_vocabulary_overlap(
                cand,
                [expected_title or "", user_prompt or "", expected_version or ""]
            )
            if overlap == 0.0:
                return True

        return False
