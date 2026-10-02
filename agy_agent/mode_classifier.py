import re
from typing import Tuple, Optional

try:
    import mode_registry
except Exception:  # pragma: no cover - 무중단 폴백 (레지스트리 부재 시에도 모듈 import 실패 금지)
    mode_registry = None  # type: ignore

# Keywords/patterns indicating single-agent mode (questions, inquiries, conceptual explanations, inspections)
QUESTION_INDICATORS = [
    r"\?",
    r"무슨\s*(뜻|의미|역할|기능|차이)",
    r"어떤\s*(차이|의미|역할|기능|옵션|설정)",
    r"어떻게\s*(동작|작동|사용|해|해야|하는지|설정)",
    r"왜\s*(그런|이런|발생|안되는|안나오는|안돼)",
    r"설명\s*(해줘|부탁|바람|해|요청)",
    r"알려\s*(줘|주세요|바람)",
    r"무엇\s*(인가요|인가|인지|이야|야)",
    r"뭐\s*(야|지|니|임)",
    r"궁금\s*(해|해요|합니다)",
    r"조회\s*(해줘|해|줘|부탁)",
    r"요약\s*(해줘|해|줘|부탁)",
    r"번역\s*(해줘|해|줘|부탁)",
    r"검색\s*(해줘|해|줘|부탁)",
    r"목록\s*(보여줘|출력|확인|알려줘)",
    r"상태\s*(확인|보여줘|조회|알려줘)",
    r"확인\s*(해줘|해|줘|부탁)",
    r"추천\s*(해줘|해|줘|부탁)",
    r"구조\s*(보여줘|알려줘|설명)",
    r"help\b",
    r"what\s+(is|are|does|should)",
    r"how\s+(to|do|does|can)",
    r"why\b",
    r"explain\b",
    r"tell\s+me",
    r"summarize\b",
    r"show\s+me",
    r"어떻게\s*(할까|될까|맞을까|풀까|접근|하면|할지|해야|바꿀|나을|좋을)",
    r"생각\s*(해줘|해봐|은|이|이야|어때)",
    r"의견\s*(줘|있어|부탁|나눠|말해|알려)",
    r"고민\s*(해줘|돼|되네|이야|중)",
    r"모르겠\s*(네|어|음|다)",
]

# Keywords/patterns indicating trivial lightweight edits
TRIVIAL_EDIT_INDICATORS = [
    r"오타\s*(수정|고쳐|변경)",
    r"typo",
    r"주석\s*(추가|달아|작성|수정|제거)",
    r"포트\s*(번호)?\s*(변경|수정|바꿔)",
    r"변수명\s*(변경|수정|바꿔)",
    r"단순\s*(수정|질문|변경|교체)",
    r"로그\s*(한줄|한 줄)?\s*(추가|출력|삭제)",
    r"문구\s*(수정|변경|바꿔)",
    r"텍스트\s*(수정|변경|바꿔)",
    r"버전\s*(확인|체크|업데이트)",
]

# Keywords/patterns indicating 3-Agent Trio collaboration (feature creation, complex coding, UI/UX design)
PAIR_DEVELOPMENT_INDICATORS = [
    r"개발\s*(해줘|해|진행|시작|부탁)",
    r"구현\s*(해줘|해|바람|시작|부탁)",
    r"만들어\s*(줘|봐|주세요)",
    r"생성\s*(해줘|해|바람|부탁)",
    r"구축\s*(해줘|해|바람|부탁)",
    r"설계\s*(해줘|해|바람|부탁)",
    r"기능\s*(추가|구현|개발|확장|개편)",
    r"컴포넌트\s*(추가|구현|개발|생성)",
    r"엔드포인트\s*(추가|구현|개발)",
    r"api\s*(구현|개발|연동|추가|연결)",
    r"페이지\s*(만들어|구현|개발|추가|짜줘|작성)",
    r"화면\s*(만들어|구현|개발|추가|짜줘|작성)",
    r"디자인\s*(해줘|해|작업|개편|개선|변경|적용|수정|짜줘|만들어|부탁|예쁘게)",
    r"스타일\s*(해줘|해|작업|개편|개선|변경|적용|수정|짜줘|만들어|부탁|예쁘게|css)",
    r"ui\b|ux\b|gui\b|레이아웃|뷰\s*(만들어|짜줘|추가)",
    r"짜줘|짜봐|작성해줘|구성해줘|배치해줘|적용해줘|반영해줘",
    r"추가\s*(해줘|해|부탁|바람)?",
    r"개선\s*(해줘|해|부탁|바람)",
    r"개편\s*(해줘|해|부탁|바람)",
    r"웹앱|어플|앱|시스템|모듈|라이브러리|프레임워크|풀스택",
    r"full\s*stack|frontend|backend|react|vue|next|svelte|fastapi|express|django|flask|spring",
    r"create\b|implement\b|build\b|develop\b|make\b|design\b|add\b|apply\b",
]

# Keywords/patterns indicating debugging, bug fixes, refactoring, optimization
PAIR_BUG_AND_REFACTOR_INDICATORS = [
    r"버그\s*(수정|고쳐|해결|잡아|원인)",
    r"에러\s*(수정|고쳐|해결|디버깅|원인|잡아)",
    r"오류\s*(수정|고쳐|해결|디버깅|패치|잡아)",
    r"디버깅\s*(해줘|해|진행|부탁)",
    r"리팩토링\s*(해줘|해|진행|부탁)",
    r"최적화\s*(해줘|해|진행|부탁)",
    r"패치\s*(해줘|해|진행|부탁)",
    r"고쳐\s*(줘|주세요|봐)",
    r"안\s*돼|안\s*됨|동작\s*하지\s*않|작동\s*안\s*함|안\s*돌아가",
    r"크래시|crash|exception|예외|장애|실패|failed|broken",
    r"fix\b|debug\b|resolve\b|refactor\b|optimize\b|patch\b",
]

# Keywords/patterns indicating testing, QA, auditing
PAIR_TESTING_INDICATORS = [
    r"테스트\s*(코드|작성|추가|실행|진행|해줘|검증)",
    r"test\b|testing\b|pytest\b|jest\b|vitest\b|playwright\b|cypress\b",
    r"e2e\s*(테스트|검증|시나리오)",
    r"단위\s*테스트|통합\s*테스트",
    r"검증\s*(해줘|진행|해|부탁)",
    r"검사\s*(해줘|진행|해|부탁)",
    r"감사\s*(해줘|진행|해|부탁)",
    r"audit\b|qa\b|quality\b|security\b|보안\s*점검|취약점",
]


# Keywords/patterns indicating research, comparison, market survey, purchasing guide, document synthesis, hardware search
RESEARCH_INDICATORS = [
    r"리서치\s*(해줘|진행|해|부탁|바람)?",
    r"조사\s*(해줘|진행|해|부탁|바람)?",
    r"비교\s*(해줘|진행|해|부탁|표|해봐|분석)?",
    r"분석\s*(해줘|진행|해|부탁|바람)?",
    # [FIX] '추천해주는 알고리즘적 오류'처럼 "추천"이 요청이 아니라 서술(불만)로 쓰인 경우를
    #       리서치 의도로 오인해 코딩 요청이 ResearchRunner(Fact Checker)로 잘못 라우팅되던 버그 수정.
    #       맨몸 '추천해' 대신 실제 요청형(추천해줘/추천 리스트/추천 모델 …)만 리서치로 판정한다.
    r"추천\s*(?:해\s*줘|해\s*주세요|해\s*줄래|해\s*봐|해줘|해줄|해줄래|리스트|제품|모델|부탁|알려줘|좀)",
    r"시세\s*(확인|조회|알려줘|정리)",
    r"가격\s*(비교|조회|알려줘|정리|대|얼마)",
    r"스펙\s*(비교|정리|확인|알려줘)",
    r"구매\s*(가이드|리스트|추천|팁|정리|계획|예정|의향)",
    r"살\s*건데|살거라|구매할|구매를\s*할|사려고|살만한",
    r"장단점\s*(비교|정리|알려줘)",
    r"시장\s*조사",
    r"트렌드\s*(조사|분석)",
    r"자료\s*(조사|수집|정리)",
    r"문서\s*(작성|정리|가이드)",
    r"가이드\s*(작성|정리|만들어|알려줘)",
    r"매물\s*(찾아|검색|조회|알려줘)",
    r"당근\s*(마켓)?\s*(에서)?",
    r"알아봐\s*(줘|주세요|봐|줄래)",
    r"찾아\s*(줘|주세요|봐|줄래|서\s*알려|서)",
    r"정리\s*(해줘|해|부탁|바람)",
    r"research\b|benchmark\b|survey\b|compare\b|comparison\b",
    # Hardware / Device / PC purchasing & recommendation inquiries
    r"미니\s*pc|mini\s*pc",
    r"(pc|컴퓨터|노트북|기기|장비|서버용\s*pc|하드웨어|부품|모니터|데스크탑)\s*(가|를|가\s*하나)?\s*(필요|알아|추천|사려|구매|골라)",
    r"(기기|pc|제품|모델)였으면\s*좋겠|기기\s*추천|pc\s*추천",
    r"어떤\s*(게|모델|제품|기기|pc)\s*(좋을까|나을까|추천|괜찮|맞을까)",
    r"뭐가\s*(좋을까|나을까|괜찮|맞을까)",
    r"견적\s*(짜줘|내줘|봐줘|알려줘|추천)",
    # Pricing, amounts, used markets, and shopping inquiries
    # [FIX] '합산 금액/결제 금액'처럼 명세서·결제·정산 문맥의 단독 명사가 리서치로 오분류되어
    #       코딩 요청이 ResearchRunner(Fact Checker)로 잘못 라우팅되던 버그 수정.
    #       시세 조회/비교를 뜻하는 실제 동사가 뒤따를 때만 리서치 의도로 판정한다.
    r"(금액|가격|시세|비용|실구매가|최저가|중고가)\s*(?:이|가|은|는|도|만)?\s*(?:비교|조회|알려|정리|차이|다른|다르|얼마|확인)",
    r"금액\s*(비교|조회|알려줘|정리|대|얼마|확인|차이|다르|차이나)",
    r"(중고|당근|당근마켓|번개장터|중고나라|알리|알리익스프레스|다나와|네이버쇼핑|쿠팡)\s*(매물|시세|가격|비교|검색|조회|추천)?",
    r"가성비\s*(추천|비교|모델|제품|pc|기기)?",
]

# Keywords/patterns indicating directional advice, architecture review, strategic consulting, or problem brainstorming
CONSULTING_INDICATORS = [
    r"방향(성)?\s*(잡아|어때|제시|추천|알려|모색|설정|논의|고민|정해|으로)",
    r"어떻게\s*(풀|접근|생각|하면|할지|해야|바꿀|나을|좋을)",
    r"어떻게\s*.*모르겠",
    r"의견\s*(줘|물어|있어|부탁|나눠|말해|듣고)",
    r"자문|컨설팅|조언|상담|고민",
    r"빗장|강박|규칙\s*(풀|완화|조정|수정|변경|유연)",
    r"장단점\s*(분석|비교|평가)",
    r"전략\s*(수립|방향|제안)",
    r"생각\s*(해봐|해보자|해줘|은\s*어때)",
    r"질문|궁금|알려줘|설명해|맞아\?|인가요|인거야|어떤\s*방식|어떻게\s*동작",
    r"이건\s*질문",
    # System performance, health, troubleshooting, diagnosis, and context inquiries
    r"부하\s*(받고|있어|걸려|상황|측정|체크|점검|상태)?",
    r"원인\s*(이\s*뭐|파악|분석|알려|규명|찾아|점검)",
    r"왜\s*(이래|이러|딴소리|못\s*알아|안\s*돼|이해\s*못)",
    r"딴소리|말귀|이해를\s*못|이해\s*못하고|요청사항\s*이해",
    r"리셋|!reset|컨텍스트\s*(초기화|비워|정리|날려|상태)",
    r"상태\s*(어때|점검|확인|진단|체크|어떤)",
    r"진단\s*(해줘|해|부탁|바람)?",
    r"시스템\s*(상태|점검|진단|부하|체크)",
]

# Keywords/patterns indicating marketing, store ASO, copy, and operational automation
OPS_MARKETING_INDICATORS = [
    r"스토어|플레이스토어|앱스토어|구글플레이|원스토어",
    r"aso\b|앱스토어\s*최적화|키워드\s*(추출|분석|선정|최적화)",
    r"마케팅|홍보|광고|카피라이팅|홍보\s*문구|랜딩\s*문구|소개글|앱\s*설명|릴리즈\s*노트",
    r"sns|인스타|인스타그램|트위터|스레드|포스팅|블로그\s*(글|작성|포스팅)",
    r"배포\s*자동화|fastlane|스토어\s*등록",
]

# Keywords/patterns specifically indicating system diagnosis, health, load, troubleshooting, or reset queries
DIAGNOSTIC_INDICATORS = [
    r"부하\s*(이|가|를|도|의)?\s*(받고|있|체크|점검|상태|원인|심해|걸려|상황|측정|발생)",
    r"(시스템|서버|프로세스)[^\n\.\,]{0,15}부하",
    r"원인\s*(이|을|은)?\s*(규명|분석|파악|이\s*뭐|이\s*무엇|알려|조사|점검|찾아|무엇)",
    r"상태\s*(이|가|는|를)?\s*(어때|점검|확인|체크|조사|보고|진단|어떠|어떤)",
    r"(시스템|서버|봇|프로세스|리소스)[^\n\.\,]{0,15}상태",
    r"컨텍스트\s*(초기화|리셋|유지|정리|삭제|날아|비워|상태)",
    r"컨텍스트[^\n\.\,]{0,15}(초기화|리셋|비워)",
    r"!reset\b",
    r"리셋[^\n\.\,]{0,15}(잘\s*되|초기화|안\s*되|동작|확인|제대로|됐어|됐나|했을|했을\s*때)",
    r"(딴소리|말귀|이해를?\s*못|이해\s*못하고)",
    r"왜\s*(딴소리|이러|이래|엉뚱|못\s*알아)",
    r"시스템[^\n\.\,]{0,15}(진단|점검|모니터링)",
    r"진단\s*(해줘|해|부탁|바람|결과|보고)",
]

def is_diagnostic_prompt(prompt: Optional[str]) -> bool:
    """
    Checks whether a user prompt is asking about system performance, health, troubleshooting,
    diagnosis, or context reset, rather than a coding/implementation request.
    Uses contextual regexes to prevent false positives on coding phrases like
    'UI 상태 관리 추가해줘', '컨텍스트 메뉴 구현', '비밀번호 리셋 기능'.
    """
    if not prompt:
        return False
    text = str(prompt).strip()
    return any(re.search(pat, text, re.IGNORECASE) for pat in DIAGNOSTIC_INDICATORS)


# ==============================================================================
# AGY 4.x Risk Engine: R0 ~ R4 Five-Tier Worst-Case Escalation Taxonomy
# ==============================================================================

# R4: Critical Security, Database, Auth, Payment, Build/Pipeline, Release
R4_CRITICAL_PATTERNS = [
    # Auth & Security
    r"is_authenticated|is_admin|permission|password|secret|token|jwt|oauth|session_token|crypto|aes|cipher|hashlib|bcrypt",
    r"인증|권한|로그인|비밀번호|패스워드|토큰|세션|암호화|보안|취약점|xss|csrf|sql_injection",
    # DB & Schema & Migration
    r"schema|migration|sqlite|postgres|mysql|alter table|create table|drop table|foreign key|primary key|column|database",
    r"마이그레이션|데이터베이스|스키마|테이블|컬럼",
    # Build & Pipeline & Release
    r"build\.gradle|settings\.gradle|package\.json|pom\.xml|dockerfile|\.env|fastlane|github/workflows",
    r"빌드\s*설정|배포\s*파이프라인|스토어\s*(제출|등록|출시)|상용\s*릴리즈|릴리스|상용\s*배포|apk\s*서명",
    # Payment & Financial
    r"payment|billing|checkout|in_app_purchase|refund|order_id",
    r"결제|인앱\s*결제|환불|과금",
]

# R3: Core System Architecture, State Management, Network API, Complex Logic
R3_SYSTEM_PATTERNS = [
    r"api/|endpoint|fetch\(|axios|retrofit|okhttp|websocket|socket|http_client",
    r"api\s*(연동|호출|통신)|웹소켓|네트워크\s*통신|엔드포인트",
    r"redux|vuex|zustand|pinia|viewmodel|state_machine|live_data|mutable_state_of",
    r"상태\s*관리|뷰모델|비즈니스\s*로직|코어\s*로직|동기화|트랜잭션",
    r"아키텍처\s*(개편|변경|재설계)|대규모\s*리팩토링|의존성\s*주입|dependency injection",
]

# R2: Standard UI Components, Page Layouts, Normal Bug Fixes, Event Handlers
R2_FEATURE_PATTERNS = [
    r"component|screen|dialog|modal|button|listview|recyclerview|card|composable",
    r"화면\s*(추가|구현|개발)|컴포넌트|다이얼로그|모달|버튼\s*이벤트|폼\s*입력|클릭\s*리스너",
    r"기능\s*(추가|구현|개발)|버그\s*(수정|해결)|오류\s*패치|예외\s*처리",
]

# R1: Cosmetic, Typo, Text, Comments, Isolated CSS/Styling (< 30dp/color tweak)
R1_COSMETIC_PATTERNS = [
    r"문구\s*(수정|변경|교체|바꿔)",
    r"텍스트\s*(수정|변경|교체|바꿔)",
    r"오타\s*(수정|변경|고쳐)",
    r"typo",
    r"주석\s*(추가|작성|수정|제거)",
    r"색상\s*(변경|수정|바꿔)",
    r"여백\s*(조정|수정|변경)",
    r"마진|패딩|margin|padding|color|font-size|background-color",
    r"단순\s*(수정|변경|개선)",
]


def classify_risk_level(prompt: str, diff_info: Optional[dict] = None) -> Tuple[str, str]:
    """
    Classifies the task risk level into R0 ~ R4 using Worst-Case Escalation (Diff Content ≠ Diff Size):
    - R0 (Inquiry/Consulting): Single Agent only (No QA, No E2E)
    - R1 (Low / Cosmetic): Coder -> Fast Flash QA -> Immediate Pass (Auditor 생략, E2E 생략)
    - R2 (Medium / UI Feature): Coder -> QA -> Fast Auditor (선택적 E2E)
    - R3 (High / System Impact): Coder -> Senior QA -> Chief Auditor -> 표준 E2E
    - R4 (Critical / Security/DB/Release): Coder -> Deep Pro QA -> Chief Auditor -> Full KVM/E2E + Gate 0~7 Lock

    Returns:
        (risk_level, reason_str) where risk_level is 'LOW' | 'MEDIUM' | 'HIGH' for backward-compatibility,
        and reason_str contains the precise R-Tier badge [R0~R4].
    """
    clean_prompt = str(prompt or "").strip().lower()

    # Worst-Case Tracking: The single highest risk detection escalates the entire tier!
    highest_tier = "R1"
    primary_reason = "소규모 경량/문구/스타일 변경"

    # 1. Inspect Diff Content (Line-by-Line AST/Regex scan, NOT just line count!)
    if diff_info and isinstance(diff_info, dict):
        changed_files = diff_info.get("changed_files", [])
        raw_diff = diff_info.get("raw_diff", "")
        raw_diff_lower = raw_diff.lower()

        # Check sensitive file extensions or critical config files
        for f in changed_files:
            f_lower = f.lower()
            if any(k in f_lower for k in [
                "build.gradle", "settings.gradle", "package.json", "pom.xml",
                "dockerfile", ".env", "schema", "migration", "auth", "security"
            ]):
                highest_tier = "R4"
                primary_reason = f"민감 설정 및 빌드/보안 코어 파일({f}) 변경 감지 (R4 Critical)"
                break

        # Check line content inside diff additions (+)
        if highest_tier != "R4":
            diff_additions = "\n".join([line[1:] for line in raw_diff_lower.splitlines() if line.startswith("+") and not line.startswith("+++")])
            
            # R4 content check in diff
            for pat in R4_CRITICAL_PATTERNS:
                if re.search(pat, diff_additions):
                    highest_tier = "R4"
                    primary_reason = f"코드 내 보안/인증/DB/권한 민감 로직 변경 감지 (R4 Critical: Diff 1줄이라도 에스컬레이션)"
                    break

            # R3 content check in diff
            if highest_tier != "R4":
                for pat in R3_SYSTEM_PATTERNS:
                    if re.search(pat, diff_additions):
                        highest_tier = "R3"
                        primary_reason = "코어 상태관리/네트워크 API/비즈니스 로직 변경 감지 (R3 System Impact)"
                        break

            # R2 content check in diff
            if highest_tier not in ("R4", "R3"):
                for pat in R2_FEATURE_PATTERNS:
                    if re.search(pat, diff_additions):
                        highest_tier = "R2"
                        primary_reason = "UI 컴포넌트/화면/일반 기능 코드 변경 감지 (R2 Feature/UI)"
                        break

    # 2. Inspect User Prompt Intent (If diff is empty or initial turn)
    if highest_tier != "R4":
        # R4 prompt check
        for pat in R4_CRITICAL_PATTERNS:
            if re.search(pat, clean_prompt):
                highest_tier = "R4"
                primary_reason = "요청 내 보안/인증/DB/결제/릴리즈 고위험 의도 감지 (R4 Critical)"
                break

    if highest_tier not in ("R4", "R3"):
        # R3 prompt check
        for pat in R3_SYSTEM_PATTERNS:
            if re.search(pat, clean_prompt):
                highest_tier = "R3"
                primary_reason = "코어 API/상태관리/시스템 아키텍처 개편 의도 감지 (R3 System Impact)"
                break

    if highest_tier not in ("R4", "R3", "R2"):
        # R2 prompt check
        for pat in R2_FEATURE_PATTERNS:
            if re.search(pat, clean_prompt):
                highest_tier = "R2"
                primary_reason = "신규 UI 컴포넌트/화면/기능 구현 의도 감지 (R2 Feature/UI)"
                break

    # Check for pure cosmetic low risk (R1)
    if highest_tier == "R1":
        has_cosmetic = any(re.search(pat, clean_prompt) for pat in R1_COSMETIC_PATTERNS)
        if not has_cosmetic and len(clean_prompt) >= 60:
            highest_tier = "R2"
            primary_reason = "일반 기능 개발/수정 작업 (R2 Normal-Path)"

    # Map R-Tier to backward-compatible LOW / MEDIUM / HIGH
    if highest_tier == "R4":
        return "HIGH", f"🚨 [{highest_tier}] {primary_reason} (Deep-Path: Deep Pro QA + Pro Auditor + Full E2E)"
    elif highest_tier == "R3":
        return "HIGH", f"🛡️ [{highest_tier}] {primary_reason} (Deep-Path: Pro QA + Auditor + 표준 E2E)"
    elif highest_tier == "R2":
        return "MEDIUM", f"⚖️ [{highest_tier}] {primary_reason} (Normal-Path: QA + Auditor + 선택적 E2E)"
    else:
        return "LOW", f"⚡ [{highest_tier}] {primary_reason} (Fast-Path: Fast QA 쾌속 검증 + Client Auditor 전수 승인)"




def classify_task_type(prompt: str) -> str:
    """
    Determines the task domain type: 'research', 'coding', 'consulting', 'ops_marketing', or 'general'.
    
    Args:
        prompt: The user's input prompt text.
        
    Returns:
        'research' | 'coding' | 'consulting' | 'ops_marketing' | 'general'
    """
    clean_prompt = str(prompt or "").strip()
    clean_prompt_lower = clean_prompt.lower()
    
    if not clean_prompt:
        return "general"

    has_research = any(re.search(pat, clean_prompt_lower) for pat in RESEARCH_INDICATORS)
    has_consulting = any(re.search(pat, clean_prompt_lower) for pat in CONSULTING_INDICATORS)
    has_ops = any(re.search(pat, clean_prompt_lower) for pat in OPS_MARKETING_INDICATORS)
    has_bug_fix = any(re.search(pat, clean_prompt_lower) for pat in PAIR_BUG_AND_REFACTOR_INDICATORS)
    has_test = any(re.search(pat, clean_prompt_lower) for pat in PAIR_TESTING_INDICATORS)
    has_dev = any(re.search(pat, clean_prompt_lower) for pat in PAIR_DEVELOPMENT_INDICATORS)
    
    is_code_explicit = any(kw in clean_prompt_lower for kw in [
        "코드", "함수", "api", "css", "html", "react", "vue", "fastapi", "배포", "db", "쿼리", "컴포넌트", "개발", "구현"
    ])

    # Prioritize coding/consulting/research/ops over generic diagnostic
    if has_research and not (has_bug_fix or (has_dev and is_code_explicit)):
        return "research"

    if has_consulting and not (has_bug_fix or (has_dev and is_code_explicit)):
        return "consulting"

    if has_ops and not (has_bug_fix or (has_dev and is_code_explicit)):
        return "ops_marketing"
        
    # Explicit coding/bugfix/testing priority unless explicit research/consulting intent
    if (has_dev or has_bug_fix or has_test) and not (has_research or has_consulting or has_ops):
        return "coding"
        
    if (has_research or has_consulting or has_ops) and has_dev:
        if not is_code_explicit:
            if has_research:
                return "research"
            if has_consulting:
                return "consulting"
            return "ops_marketing"
        return "coding"

    # Diagnostic fallback (lower priority than coding/consulting/research/ops)
    is_quota_or_leak = any(kw in clean_prompt_lower for kw in [
        "할당량", "쿼터", "누수", "줄어들", "줄어", "사용량", "quota", "leak"
    ])
    is_inquiry_or_eval = any(kw in clean_prompt_lower for kw in [
        "몇점", "몇 점", "어때", "감점", "원인", "어떻게 생각해",
        "진단", "평가", "피드백", "궁금", "질문"
    ])
    if is_quota_or_leak or is_inquiry_or_eval:
        return "diagnostic"
        
    return "general"


def classify_mode(prompt: str, session_mode: Any = "auto") -> Tuple[str, str]:
    """
    Determines effective execution mode ('single' or 'pair') and human-readable reason.
    
    Args:
        prompt: The user's input prompt text.
        session_mode: The channel's configured mode string or ChannelSession object.
        
    Returns:
        (effective_mode, reason_str)
        effective_mode is either 'single', 'pair', 'pair_quad', 'quad', 'evolve', 'read', etc.
    """
    session = None
    if hasattr(session_mode, "mode"):
        session = session_mode
        mode_normalized = str(getattr(session, "mode", "auto") or "auto").lower().strip()
    else:
        mode_normalized = str(session_mode or "auto").lower().strip()

    first_token = mode_normalized.split()[0] if mode_normalized else ""

    multi_label = "4중 쿼드"
    chain_preview = "qa2"
    try:
        import config
        if session and hasattr(session, "get_active_extended_qas"):
            cfgs = session.get_active_extended_qas()
        else:
            cfgs = config.get_extended_qa_configs()
        if cfgs:
            multi_cnt = len(cfgs) + 3
            multi_label = f"{multi_cnt}중 다중" if len(cfgs) > 1 else "4중 쿼드"
            chain_preview = " ➔ ".join([f"{c.get('name', 'qa').lower()}" for c in cfgs])
        else:
            multi_label = "3중"
            chain_preview = "기본 qa1"
    except Exception:
        pass

    # 1. Manually pinned modes
    #    🎛️ SSOT: 별칭 해석은 mode_registry.normalize_mode() 가 유일한 기준이다.
    #    (기존에는 본 함수가 자체 튜플을 하드코딩해 registry 와 어긋날 수 있었고,
    #     `!mode 2+` 가 `!mode 2` 로 오인되는 드리프트의 원인이었다.)
    canonical = (
        mode_registry.normalize_mode(mode_normalized)
        if mode_registry is not None
        else mode_normalized
    )
    known = (
        mode_registry.is_known_mode(mode_normalized)
        if mode_registry is not None
        else mode_normalized in (
            "read", "pair", "single", "pair_quad", "mode3", "mode3_plus", "single_qa2",
        )
    )
    if known and canonical != "auto":
        if canonical == "read":
            return "read", "수동 고정 모드 (🔍 3중 읽기/기획 전용 아키텍처 자문)"
        if canonical == "mode3_plus":
            return "mode3_plus", f"수동 고정 모드 (🤖 {multi_label} 에이전트 무인 자율 순환 · !mode 3+)"
        if canonical == "mode3":
            return "mode3", "수동 고정 모드 (🤖 3중 에이전트 무인 자율 순환 · !mode 3)"
        if canonical == "pair_quad":
            task_type = classify_task_type(prompt)
            if task_type == "research":
                return "pair_quad", f"수동 고정 모드 (👥 {multi_label} 에이전트 심층 리서치 협업 · !mode 2+)"
            return "pair_quad", f"수동 고정 모드 (👥 {multi_label} 에이전트 사용자 주도 협업 · !mode 2+)"
        if canonical == "single_qa2":
            return "single_qa2", "수동 고정 모드 (🥷 단일 qa2 에이전트 전용)"
        if canonical == "pair":
            task_type = classify_task_type(prompt)
            if task_type == "research":
                return "pair", "수동 고정 모드 (👥 3중 에이전트 심층 리서치 협업)"
            return "pair", "수동 고정 모드 (👥 3중 에이전트 사용자 주도 협업 · !mode 2)"
        if canonical == "single":
            return "single", "수동 고정 모드 (👤 단일 에이전트)"

    # 2. Auto mode intelligent heuristic classification
    clean_prompt = str(prompt or "").strip()
    clean_prompt_lower = clean_prompt.lower()

    if not clean_prompt:
        return "single", "빈 프롬프트 (단일 에이전트)"

    # Fast check: trivial lightweight edits
    is_trivial = any(re.search(pat, clean_prompt_lower) for pat in TRIVIAL_EDIT_INDICATORS)

    # Check for research / comparison / analysis intent
    has_research = any(re.search(pat, clean_prompt_lower) for pat in RESEARCH_INDICATORS)

    # Check for consulting / direction / meta intent
    has_consulting = any(re.search(pat, clean_prompt_lower) for pat in CONSULTING_INDICATORS)

    # Check for marketing / store ops intent
    has_ops = any(re.search(pat, clean_prompt_lower) for pat in OPS_MARKETING_INDICATORS)

    # Check for testing / verification intent
    has_test = any(re.search(pat, clean_prompt_lower) for pat in PAIR_TESTING_INDICATORS)

    # Check for bug / error / refactor / fix intent
    has_bug_fix = any(re.search(pat, clean_prompt_lower) for pat in PAIR_BUG_AND_REFACTOR_INDICATORS)

    # Check for development / build / implement / design intent
    has_dev = any(re.search(pat, clean_prompt_lower) for pat in PAIR_DEVELOPMENT_INDICATORS)

    # Check for question / explanation / inquiry intent
    has_question = any(re.search(pat, clean_prompt_lower) for pat in QUESTION_INDICATORS)

    # Check if code development was explicitly requested
    is_code_explicit = any(kw in clean_prompt_lower for kw in [
        "코드", "함수", "api", "css", "html", "react", "vue", "fastapi", "배포", "db", "쿼리", "컴포넌트", "개발", "구현"
    ])

    # Structural complexity checks
    has_bullet_points = bool(re.search(r"(\n\s*[-*•\d]\.|\n\s*\d+\))", clean_prompt))
    has_code_block = "```" in clean_prompt
    is_long_prompt = len(clean_prompt) >= 150

    # Decision tree:
    # A. Trivial edit (e.g. "오타 수정", "주석 달기") -> Single
    if is_trivial and not (has_dev or has_test):
        return "single", "단순 오타/주석/경량 설정 수정"

    # B. Research intent -> Pair (ResearchRunner)
    if has_research and not (is_code_explicit or has_bug_fix):
        return "pair", "심층 시장/스펙 비교 리서치 과제"

    # C. Strategic consulting & directional thinking -> Single (Thoughtful and fast without coding bias)
    if has_consulting and not (is_code_explicit or has_bug_fix):
        return "single", "방향성 자문 및 전략 고민"

    # D. Marketing / Store Ops without heavy code dev -> Single for copy/metadata, Pair for automation
    if has_ops and not (is_code_explicit or has_bug_fix):
        if any(kw in clean_prompt_lower for kw in ["스크립트", "파이프라인", "연동", "자동화", "api"]):
            return "pair", "스토어/마케팅 운영 자동화 개발"
        return "single", "마케팅 전략 및 스토어 메타데이터 작성"

    # E. Development, bug fix, testing -> Pair (TrioAgentRunner)
    if has_dev:
        return "pair", "신규 기능 개발/디자인/컴포넌트 구현"
    if has_bug_fix:
        return "pair", "버그 수정/오류 디버깅/최적화"
    if has_test:
        return "pair", "테스트 코드/E2E 품질 검증"
        
    # F. In-depth research / comparison with multi-step or long prompt -> Pair
    if has_research and (has_bullet_points or is_long_prompt or len(clean_prompt) >= 60):
        return "pair", "심층 시장/스펙 비교 리서치 과제"

    # G. Multi-step complex prompts -> Pair
    if has_bullet_points or has_code_block or is_long_prompt:
        return "pair", "다단계 요구사항/복합 과제"

    # H. Pure question / inquiry / explanation -> Single
    if has_question:
        return "single", "단순 질의/개념 설명/코드 분석"

    # I. Short prompt default heuristic (< 40 characters) -> Single
    if len(clean_prompt) < 40:
        return "single", "단문 질의/경량 작업"

    # J. General task fallback -> Pair for high-quality production outputs
    return "pair", "일반 개발/작업 요청"


def get_mode_description(
    mode: str,
    session: Optional[Any] = None,
    selected_qas: Optional[List[Dict[str, Any]]] = None
) -> str:
    """Returns a rich formatted description of a mode."""
    mode_normalized = (mode or "auto").lower().strip()
    first_token = mode_normalized.split()[0] if mode_normalized else ""
    mode_compact = mode_normalized.replace(" ", "")
    ext_qa_desc = "qa2"
    multi_label = "4중 쿼드"
    try:
        import config
        if selected_qas is not None:
            cfgs = selected_qas
        elif session and hasattr(session, "get_active_extended_qas"):
            cfgs = session.get_active_extended_qas()
        else:
            cfgs = config.get_extended_qa_configs()
        if cfgs:
            multi_count = len(cfgs) + 3
            multi_label = f"{multi_count}중 다중" if len(cfgs) > 1 else "4중 쿼드"
            ext_qa_desc = " ➔ ".join([f"{c.get('name', 'qa').lower()}" for c in cfgs])
        else:
            multi_label = "3중"
            ext_qa_desc = "기본 qa1"
    except Exception:
        pass

    # 🎛️ SSOT: 표시 문자열도 mode_registry 해석을 단일 기준으로 사용한다.
    canonical = (
        mode_registry.normalize_mode(mode_normalized)
        if mode_registry is not None
        else mode_normalized
    )
    known = (
        mode_registry.is_known_mode(mode_normalized)
        if mode_registry is not None
        else False
    )
    if known:
        if canonical == "auto":
            return "🤖 **스마트 자동 모드 (Auto)** (질문/경량은 Single, 개발/수정/테스트/심층리서치는 👥 3중 협업 자동 분류)"
        if canonical == "read":
            return "🔍 **3중 읽기/기획 전용 모드 (Read)** (코드 수정 없이 3대 두뇌가 코드베이스를 탐색하여 최적 아키텍처 및 방향성 모색)"
        if canonical == "mode3_plus":
            chain_desc = f"Coder ➔ qa1 ➔ {ext_qa_desc} ➔ Auditor ➔ Commander" if ext_qa_desc != "기본 qa1" else "Coder ➔ qa1 ➔ Auditor ➔ Commander"
            return f"🤖 **무인 자율 순환 다중 에이전트 협업 모드 (Autonomous Multi-QA · !mode 3+)** (폐루프 무인 자율 순환: User ➔ Commander ➔ {chain_desc} ➔ Commander 차기 마일스톤 자동 순환)"
        if canonical == "mode3":
            return "🤖 **무인 자율 순환 3중 에이전트 협업 모드 (Autonomous Trio · !mode 3)** (폐루프 무인 자율 순환: User ➔ Commander ➔ Coder ↔ QA ↔ Auditor ➔ Commander 차기 마일스톤 자동 순환)"
        if canonical == "pair_quad":
            chain_desc = f"Coder ➔ qa1 ➔ {ext_qa_desc} ➔ Auditor" if ext_qa_desc != "기본 qa1" else "Coder ➔ qa1 ➔ Auditor"
            return f"👥 **사용자 주도 {multi_label} 에이전트 협업 모드 (Pair+ · !mode 2+)** ({chain_desc} 단일 과제 완결 후 사용자 대기)"
        if canonical == "single_qa2":
            return "🥷 **단일 qa2 에이전트 모드 (qa2 Solo)** (1545711652987600937 전용 모드로 동작합니다)"
        if canonical == "pair":
            return "👥 **사용자 주도 3중 에이전트 협업 모드 (Pair · !mode 2)** (개발: Coder ↔ QA ↔ Auditor / 단일 과제 완결 후 사용자 대기)"
        if canonical == "single":
            return "👤 **단일 에이전트 모드 (Single · !mode 1)** (초고속 단일 에이전트 답변)"
    return f"⚙️ **{mode} 모드**"




