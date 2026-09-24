import os
import json
import re
import time
import logging
from dataclasses import dataclass, asdict, field
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("QualityStateManager")


@dataclass
class QualityContract:
    id: str                        # e.g., "QC-G-01", "QC-V-01", "QC-U-01", "QC-P-01", "QC-A-01"
    category: str                  # "gameplay", "visual", "ux", "performance", "architecture"
    description: str               # What is being verified
    level: int                     # 1: Mechanical (AST/Code/File/Metric), 2: Binary Vision, 3: Exploratory
    verification_rule: str         # Concrete evaluation rule/expression
    status: str = "UNVERIFIED"     # "UNVERIFIED", "PASS", "FAIL"
    evidence: str = ""             # Discovered code line, measurement, or reason

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class QualityStateManager:
    """
    Manages the machine-enforced Quality State Store, Delta tracking,
    and regression detection for AGY Mode 3 / Collaborative Pipelines.
    Prevents LLM self-evaluation fraud by calculating scores programmatically:
        score = (passed_qcs / total_qcs) * 100
    """

    STATE_FILENAME = "quality_state.json"

    @classmethod
    def get_state_path(cls, workspace: str) -> str:
        reports_dir = os.path.join(workspace, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        return os.path.join(reports_dir, cls.STATE_FILENAME)

    @classmethod
    def load_state(cls, workspace: str) -> Dict[str, Any]:
        """Loads the current quality state from disk. Returns empty baseline if absent."""
        path = cls.get_state_path(workspace)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read quality state from {path}: {e}")

        return {
            "version": "v0.0",
            "updated_at": 0.0,
            "categories": {
                "gameplay": {"passed": 0, "total": 0, "score": 0.0},
                "visual": {"passed": 0, "total": 0, "score": 0.0},
                "ux": {"passed": 0, "total": 0, "score": 0.0},
                "performance": {"passed": 0, "total": 0, "score": 0.0},
                "architecture": {"passed": 0, "total": 0, "score": 0.0}
            },
            "overall_score": 0.0,
            "previous_overall_score": 0.0,
            "delta": 0.0,
            "history": [],
            "regression": {
                "functional_ac_passed": True,
                "has_regression": False,
                "details": ""
            },
            "active_qcs": []
        }

    @classmethod
    def save_state(cls, workspace: str, state_data: Dict[str, Any]) -> str:
        """Saves the quality state dictionary to disk as formatted JSON."""
        path = cls.get_state_path(workspace)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2, ensure_ascii=False)
            logger.info(f"Quality state saved to {path} (overall_score={state_data.get('overall_score', 0):.1f})")
            return path
        except Exception as e:
            logger.error(f"Failed to save quality state to {path}: {e}")
            return ""

    @classmethod
    def _verify_mechanically_on_disk(cls, workspace: str, qc: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Performs mechanical Level 1 static analysis across workspace files
        to physically verify the existence of code tokens, formulas, or functions.
        """
        if not workspace or not os.path.isdir(workspace):
            return False, "워크스페이스 경로 무효"

        exts = (".js", ".html", ".ts", ".py", ".kt", ".java", ".jsx", ".tsx", ".css")
        rule = qc.get("verification_rule", "")
        desc = qc.get("description", "")
        qc_id = qc.get("id", "")

        # Extract meaningful candidate identifier keywords
        candidates = []
        for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", rule + " " + desc):
            if word.lower() not in ("level", "pass", "fail", "true", "false", "and", "or", "not", "this", "that", "with"):
                candidates.append(word)

        # Domain/QC-specific keyword boosters
        if "QC-G-01" in qc_id:
            candidates.extend(["elapsedTime", "baseSpeed", "acceleration", "speed", "time", "gravity"])
        elif "QC-G-02" in qc_id:
            candidates.extend(["collision", "isGameOver", "hp", "health", "lives", "AABB", "intersect"])
        elif "QC-U-01" in qc_id:
            candidates.extend(["shake", "screenShake", "flash", "scorePopup", "vibrate", "feedback"])
        elif "QC-V-01" in qc_id:
            candidates.extend(["drawImage", "sprite", "particle", "assets", "Image", "render"])
        elif "QC-P-01" in qc_id:
            candidates.extend(["requestAnimationFrame", "cancelAnimationFrame", "pool", "cleanup"])
        elif "QC-A-01" in qc_id:
            candidates.extend(["Manager", "Pipeline", "Controller", "Service", "localStorage"])

        candidates = list(dict.fromkeys(candidates))

        for root, _, files in os.walk(workspace):
            if any(ign in root for ign in (".git", "node_modules", ".gradle", "build", "dist", ".gemini", "reports", "__pycache__")):
                continue
            for f in files:
                if f.endswith(exts):
                    fpath = os.path.join(root, f)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="ignore") as fp:
                            content = fp.read()
                            for c in candidates:
                                if c in content:
                                    return True, f"{f} 소스코드 내 '{c}' 물리적 토큰 검출 완료"
                    except Exception:
                        continue

        return False, "디스크 소스코드 상 관련 키워드 미검출"

    @classmethod
    def parse_qa_qc_verdicts(
        cls,
        qa_output: str,
        expected_qcs: List[Dict[str, Any]],
        workspace: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Parses QA output text to extract PASS/FAIL verdicts for each defined QC ID.
        Strictly prohibits LLM self-praise fraud: does NOT grant PASS purely on [STATUS: APPROVED].
        Falls back to physical disk static analysis (Level 1 Mechanical) if QA omitted explicit mention.
        """
        if not expected_qcs:
            return []

        results = []
        clean_text = qa_output or ""

        for qc in expected_qcs:
            qc_id = qc.get("id", "").strip()
            if not qc_id:
                continue

            # Case-insensitive regex search for QC-ID followed by PASS or FAIL
            pattern = re.compile(
                rf"(?:\[|\b){re.escape(qc_id)}(?:\]|\b)\s*[:：\-~]?\s*(?:\*\*)?\s*(PASS|PASSED|FAIL|FAILED)\b",
                re.IGNORECASE
            )
            match = pattern.search(clean_text)

            status = "UNVERIFIED"
            evidence = ""

            if match:
                raw_verdict = match.group(1).upper()
                status = "PASS" if "PASS" in raw_verdict else "FAIL"

                # Extract line or sentence around match as evidence
                start_pos = max(0, match.start() - 20)
                end_pos = min(len(clean_text), match.end() + 150)
                snippet = clean_text[start_pos:end_pos].replace("\n", " ").strip()
                evidence = snippet[:200]
            else:
                # Level 1 Mechanical fallback: verify on disk instead of blindly approving
                qc_level = qc.get("level", 1)
                if workspace and os.path.isdir(workspace) and qc_level == 1:
                    mech_ok, mech_ev = cls._verify_mechanically_on_disk(workspace, qc)
                    if mech_ok:
                        status = "PASS"
                        evidence = f"[기계적 디스크 검증] {mech_ev}"
                    else:
                        status = "FAIL"
                        evidence = f"QA 명시 검증 누락 및 디스크 정적 미확인 ({mech_ev})"
                else:
                    status = "FAIL"
                    evidence = "QA 검수문에서 해당 QC 항목 명시적 검증 누락 (자화자찬 방어 차단)"

            updated_qc = dict(qc)
            updated_qc["status"] = status
            updated_qc["evidence"] = evidence
            results.append(updated_qc)

        return results

    @classmethod
    def evaluate_and_update(
        cls,
        workspace: str,
        milestone_version: str,
        qc_results: List[Dict[str, Any]],
        functional_ac_passed: bool = True,
        build_succeeded: bool = True,
        error_count: int = 0
    ) -> Dict[str, Any]:
        """
        Mechanically calculates scores by counting passed vs total QCs.
        Calculates category scores, overall score, delta vs previous state,
        and regression flags. Automatically syncs Ground Truth to WORKING_MEMORY.md.
        """
        current_state = cls.load_state(workspace)
        prev_overall = current_state.get("overall_score", 0.0)

        # Group QCs by category
        cat_counts: Dict[str, Dict[str, int]] = {
            "gameplay": {"passed": 0, "total": 0},
            "visual": {"passed": 0, "total": 0},
            "ux": {"passed": 0, "total": 0},
            "performance": {"passed": 0, "total": 0},
            "architecture": {"passed": 0, "total": 0}
        }

        total_qcs = len(qc_results)
        total_passed = 0

        for q in qc_results:
            cat = q.get("category", "architecture").lower().strip()
            if cat not in cat_counts:
                cat = "architecture"

            cat_counts[cat]["total"] += 1
            if q.get("status") == "PASS":
                cat_counts[cat]["passed"] += 1
                total_passed += 1

        # Calculate category scores
        categories_result = {}
        for cat, data in cat_counts.items():
            tot = data["total"]
            pas = data["passed"]
            # If no QCs were specified for this category in this round, retain previous category score
            if tot == 0:
                prev_cat_score = current_state.get("categories", {}).get(cat, {}).get("score", 0.0)
                categories_result[cat] = {
                    "passed": 0,
                    "total": 0,
                    "score": prev_cat_score
                }
            else:
                score = round((pas / tot) * 100.0, 1)
                categories_result[cat] = {
                    "passed": pas,
                    "total": tot,
                    "score": score
                }

        # Calculate overall score: ratio of passed QCs across active set
        if total_qcs > 0:
            overall_score = round((total_passed / total_qcs) * 100.0, 1)
        else:
            overall_score = prev_overall

        delta = round(overall_score - prev_overall, 1)

        # Regression detection
        has_regression = False
        reg_details = []
        if not functional_ac_passed:
            has_regression = True
            reg_details.append("핵심 기능 인수 기준(Functional AC) 미달")
        if not build_succeeded:
            has_regression = True
            reg_details.append("컴파일/빌드 실패 감지")
        if error_count > 0:
            has_regression = True
            reg_details.append(f"런타임/구문 에러 {error_count}건 발생")

        # History entry
        history_entry = {
            "milestone": milestone_version,
            "timestamp": time.time(),
            "overall_score": overall_score,
            "delta": delta,
            "categories": {c: categories_result[c]["score"] for c in categories_result},
            "has_regression": has_regression
        }

        history = current_state.get("history", [])
        history.append(history_entry)

        new_state = {
            "version": milestone_version,
            "updated_at": time.time(),
            "categories": categories_result,
            "overall_score": overall_score,
            "previous_overall_score": prev_overall,
            "delta": delta,
            "history": history[-20:],  # Keep last 20 snapshots
            "regression": {
                "functional_ac_passed": functional_ac_passed,
                "has_regression": has_regression,
                "details": "; ".join(reg_details) if reg_details else "무결점 유지 (회귀 없음)"
            },
            "active_qcs": qc_results
        }

        cls.save_state(workspace, new_state)
        cls.sync_to_working_memory(workspace, new_state)
        return new_state

    @classmethod
    def sync_to_working_memory(cls, workspace: str, state_data: Dict[str, Any]) -> bool:
        """
        Synchronizes the machine-evaluated Quality State Store directly into WORKING_MEMORY.md
        so both LLMs and humans have permanent, immutable visibility over quality deltas.
        """
        if not workspace or not os.path.isdir(workspace):
            return False

        wm_path = os.path.join(workspace, "WORKING_MEMORY.md")
        overall = state_data.get("overall_score", 0.0)
        delta = state_data.get("delta", 0.0)
        delta_sign = f"+{delta:.1f}" if delta >= 0 else f"{delta:.1f}"
        version = state_data.get("version", "v1.0")
        reg = state_data.get("regression", {})
        reg_status = "🚨 회귀 감지 (Regression)" if reg.get("has_regression") else "✅ 무결점 통과 (No Regression)"

        categories = state_data.get("categories", {})
        cat_rows = []
        for cat_name, cat_val in categories.items():
            sc = cat_val.get("score", 0.0)
            p = cat_val.get("passed", 0)
            t = cat_val.get("total", 0)
            stat = f"{p}/{t}" if t > 0 else "유지"
            cat_rows.append(f"| {cat_name.upper()} | {stat} | `{sc:.1f}%` |")

        qc_rows = []
        for qc in state_data.get("active_qcs", [])[:6]:
            st_icon = "✅ PASS" if qc.get("status") == "PASS" else "❌ FAIL"
            qc_rows.append(f"- **[{qc.get('id')}]** `{st_icon}`: {qc.get('description', '')} *(증거: {qc.get('evidence', '')[:90]})*")

        section_content = (
            "## 📊 Quality State Store (정량적 품질 지표 및 증분 추적)\n\n"
            f"> **마일스톤**: `{version}` | **종합 기계 점수**: `{overall:.1f}%` (증분: `{delta_sign}%`) | **회귀 검증**: {reg_status}\n\n"
            "| 품질 차원 (Dimension) | 통과/전체 | 측정 점수 |\n"
            "| :--- | :---: | :---: |\n"
            + "\n".join(cat_rows) + "\n\n"
            "**최근 활성 Quality Contract (QC) 검증 결과**:\n"
            + ("\n".join(qc_rows) if qc_rows else "- 검증된 활성 QC 없음") + "\n"
        )

        try:
            if os.path.isfile(wm_path):
                with open(wm_path, "r", encoding="utf-8") as f:
                    content = f.read()

                if "## 📊 Quality State Store" in content:
                    pattern = re.compile(r"## 📊 Quality State Store.*?(?=\n## |\Z)", re.DOTALL)
                    new_content = pattern.sub(section_content.strip() + "\n\n", content)
                else:
                    # Insert after first header or at end
                    if "\n## " in content:
                        idx = content.find("\n## ")
                        new_content = content[:idx] + "\n\n" + section_content + content[idx:]
                    else:
                        new_content = content + "\n\n" + section_content

                with open(wm_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                logger.info(f"Quality State Store synchronized to {wm_path}")
                return True
            else:
                with open(wm_path, "w", encoding="utf-8") as f:
                    f.write(f"# Working Memory\n\n{section_content}\n")
                return True
        except Exception as e:
            logger.warning(f"Failed to sync quality state to WORKING_MEMORY.md: {e}")
            return False

    @classmethod
    def format_state_summary_for_commander(cls, workspace: str) -> str:
        """
        Generates a high-density, structured Quality State brief for Commander
        to anchor the next milestone's Value x Risk x Cost decisions.
        """
        state = cls.load_state(workspace)
        overall = state.get("overall_score", 0.0)
        delta = state.get("delta", 0.0)
        delta_str = f"+{delta:.1f}" if delta >= 0 else f"{delta:.1f}"
        categories = state.get("categories", {})

        cat_lines = []
        lowest_cat = None
        lowest_score = 101.0

        for cat_name, cat_data in categories.items():
            sc = cat_data.get("score", 0.0)
            p = cat_data.get("passed", 0)
            t = cat_data.get("total", 0)
            # Domain relevance guard: a dimension never verified in this project
            # (e.g. GAMEPLAY leaking into a PDF app) must not be reported as a
            # "worst quality gap" that misdirects Commander's next milestone.
            if t == 0 and sc == 0.0:
                continue
            stat = f"{sc:.1f}% ({p}/{t})" if t > 0 else f"{sc:.1f}% (이전 유지)"
            cat_lines.append(f"  • **{cat_name.upper()}**: {stat}")
            if sc < lowest_score:
                lowest_score = sc
                lowest_cat = cat_name

        reg = state.get("regression", {})
        reg_status = "🚨 회귀 감지 (Regression Detected)" if reg.get("has_regression") else "✅ 무결점 유지 (No Regression)"

        summary = (
            f"📊 **[기계적 Quality State Store 현황 — {state.get('version', 'v1.0')}]**\n"
            f"- **종합 기계 점수**: `{overall:.1f}%` (직전 대비 증분 Delta: `{delta_str}%`)\n"
            f"- **회귀 상태**: {reg_status} ({reg.get('details', '정상')})\n"
            f"- **카테고리별 정량 품질 실측치**:\n"
            + ("\n".join(cat_lines) if cat_lines else "  • 아직 기계 검증된 품질 차원 없음 (신규/초기 프로젝트)") + "\n"
            f"- **최대 결손 품질 영역 (Worst Quality Gap)**: `{lowest_cat.upper() if lowest_cat else 'NONE'}` (`{lowest_score:.1f}%`)\n"
            f"💡 **사령관 전략 지침**: 차기 마일스톤 기획 시 결손 1위 영역(`{lowest_cat}`)과 핵심 사용자 가치를 "
            f"4D 매트릭스(Value x Risk x Cost)로 대조하여 집중 개선 QC를 발주하십시오."
        )
        return summary

    @classmethod
    def generate_domain_default_qcs(
        cls,
        domain: str,
        title: str = "",
        tasks: Optional[List[str]] = None,
        milestone_version: str = "v1.0"
    ) -> List[Dict[str, Any]]:
        """
        Generates 3~5 verifiable, atomic Quality Contracts (Level 1 & Level 2)
        based on project domain and milestone version (Hierarchical Depth Progression).
        - v1.0: Core Fidelity (Base Invariance & Core Loop)
        - v2.0: Game Feel & Juice (Tactile feedback, naming refactor, acceleration curves)
        - v3.0: Risk vs Reward (Near miss / graze, score multipliers, combo dilemmas)
        - v4.0+: Pacing & Escalation (Dynamic environmental hazards, phase shifts)
        """
        dom = (domain or "saas_web").lower()
        v_str = (milestone_version or "v1.0").lower()
        is_v1 = ("v1.0" in v_str or v_str in ("v1", "1", "v1.0"))
        is_v2 = ("v2" in v_str or "2" in v_str)
        is_v3 = ("v3" in v_str or "3" in v_str)
        is_v4_plus = ("v4" in v_str or "v5" in v_str or "4" in v_str or "5" in v_str)

        qcs = []

        if dom == "game":
            if is_v2:
                # Loop 2: Game Feel & Juice + Naming Refinement
                qcs = [
                    QualityContract(
                        id="QC-A-01",
                        category="architecture",
                        description="아키텍처 명명 규칙 정합성 (비표준 식별자 리팩토링 및 표준 매니저 분리)",
                        level=1,
                        verification_rule="사과flow 등 조잡한 식별자 배제 및 AppleDropManager/PlayerController 등 도메인 클래스 분리"
                    ),
                    QualityContract(
                        id="QC-G-01",
                        category="gameplay",
                        description="시간 경과(elapsedTime) 또는 가속도 계수 기반 동적 낙하 속도 가중치 수식",
                        level=1,
                        verification_rule="낙하 로직에 고정 상수가 아닌 elapsedTime 또는 acceleration 계수가 곱해진 물리 수식 존재"
                    ),
                    QualityContract(
                        id="QC-U-01",
                        category="ux",
                        description="피격 및 충돌 시 시각적 손맛 피드백 (Screen Shake or Flash or Haptic)",
                        level=1,
                        verification_rule="충돌 발생 시 화면 흔들림(shake) 또는 캔버스 플래시 연출 함수가 호출되는가?"
                    ),
                    QualityContract(
                        id="QC-V-01",
                        category="visual",
                        description="사과 바닥 충돌 시 스쿼시/파티클 폭발 또는 스프라이트 에셋 렌더링",
                        level=2,
                        verification_rule="바닥 충돌 시 단순 소멸이 아닌 파티클 배열 생성 또는 drawImage 스프라이트 바인딩"
                    ),
                    QualityContract(
                        id="QC-P-01",
                        category="performance",
                        description="엔티티 생성/소멸 시 오브젝트 누수 방어 및 60fps 무결점 유지",
                        level=1,
                        verification_rule="화면 밖 사과 오브젝트 메모리 회수(splice/filter) 및 cancelAnimationFrame 클린업"
                    )
                ]
            elif is_v3:
                # Loop 3: Risk vs Reward (Dilemma, Near Miss / Graze, Combo)
                qcs = [
                    QualityContract(
                        id="QC-G-01",
                        category="gameplay",
                        description="스치기(Graze/Near Miss, 5~15px) 아슬아슬 회피 판정 및 보너스 득점",
                        level=1,
                        verification_rule="피격 박스 외곽 스치기 반경(graze radius) 판정 및 보너스 점수 가산 로직 존재"
                    ),
                    QualityContract(
                        id="QC-G-02",
                        category="gameplay",
                        description="특수 엔티티(황금 사과/독사과) 분기 및 콤보 피버 모드 시스템",
                        level=1,
                        verification_rule="무작위 확률의 특수 사과 스폰 및 연속 회피 시 콤보 배수(comboMultiplier) 증가"
                    ),
                    QualityContract(
                        id="QC-U-01",
                        category="ux",
                        description="스치기 성공 시 'CLOSE!' 텍스트 팝업 및 실시간 콤보 HUD 표시",
                        level=1,
                        verification_rule="스치기 이벤트 수신 시 화면에 콤보 카운트 및 부동 텍스트(Floating Text) 렌더링"
                    ),
                    QualityContract(
                        id="QC-A-01",
                        category="architecture",
                        description="최고 기록(High Score) LocalStorage 영속화 및 1초 재도전 루프",
                        level=1,
                        verification_rule="localStorage.getItem/setItem 연동 및 게임오버 시 즉각 재시작 핸들러 바인딩"
                    ),
                    QualityContract(
                        id="QC-P-01",
                        category="performance",
                        description="다중 콤보/파티클 렌더링 중 가비지 컬렉션(GC) 스파이크 및 프레임 드랍 0건",
                        level=1,
                        verification_rule="반복 프레임 내 불필요한 힙 객체 할당 억제 및 60fps 유지"
                    )
                ]
            elif is_v4_plus:
                # Loop 4+: Pacing & Escalation (Environmental hazards, Phase transition)
                qcs = [
                    QualityContract(
                        id="QC-G-01",
                        category="gameplay",
                        description="시간 경과 페이즈 전환 및 환경 기믹 (바람 편향/wobble 궤적 진동)",
                        level=1,
                        verification_rule="시간 경과(30초+) 시 바람(wind) 벡터가 X좌표에 가산되거나 sin(t) wobble 수식 적용"
                    ),
                    QualityContract(
                        id="QC-U-01",
                        category="ux",
                        description="상단 낙하 위험 구역 사전 경고선(Telegraphing) 및 바람 주의보 UI",
                        level=1,
                        verification_rule="사과 낙하 0.5초 전 상단 가이드 라인 또는 바람 방향 경고 인디케이터 렌더링"
                    ),
                    QualityContract(
                        id="QC-G-02",
                        category="gameplay",
                        description="거대 보스 사과 또는 집중 다중 탄막 패턴 발생",
                        level=1,
                        verification_rule="일정 점수/시간 도달 시 특수 대형 장애물 또는 방사형 스폰 패턴 분기"
                    ),
                    QualityContract(
                        id="QC-V-01",
                        category="visual",
                        description="페이즈별 배경 색조 전환 및 분위기 고조 애니메이션",
                        level=2,
                        verification_rule="페이즈 변경 시 캔버스 배경 스타일 또는 동적 컬러 매트릭스 전환"
                    ),
                    QualityContract(
                        id="QC-P-01",
                        category="performance",
                        description="장시간(10분+) 무중단 구동 시 메모리 누수 0건 및 렌더링 안정성",
                        level=1,
                        verification_rule="장시간 구동 시 엔티티 배열 크기 무한 증가 방어 및 안정적 GC 유지"
                    )
                ]
            else:
                # Loop 1: Core Fidelity (Base Invariance & Core Playable Prototype)
                qcs = [
                    QualityContract(
                        id="QC-G-01",
                        category="gameplay",
                        description="키보드/터치 입력 연동 및 플레이어 좌우 이동 물리 바인딩",
                        level=1,
                        verification_rule="keydown/touch 이벤트 리스너가 플레이어 X좌표 증분에 정상 바인딩되어 있는가?"
                    ),
                    QualityContract(
                        id="QC-G-02",
                        category="gameplay",
                        description="상단 사과 엔티티 스폰 및 Y축 프레임당 낙하 물리 연산",
                        level=1,
                        verification_rule="무작위 X좌표 사과 생성 및 requestAnimationFrame 루프 내 Y좌표 증가 로직"
                    ),
                    QualityContract(
                        id="QC-G-03",
                        category="gameplay",
                        description="플레이어-사과 정밀 충돌 판정(AABB) 및 게임오버 상태 전이",
                        level=1,
                        verification_rule="충돌 발생 시 isGameOver 플래그 전환 또는 생명력 감소 및 루프 분기 연결"
                    ),
                    QualityContract(
                        id="QC-V-01",
                        category="visual",
                        description="캔버스 초기화, 뷰포트 스케일링 및 기본 엔티티 시각 렌더링",
                        level=2,
                        verification_rule="canvas context clearRect 및 플레이어/사과 요소 화면 출력 확인"
                    ),
                    QualityContract(
                        id="QC-P-01",
                        category="performance",
                        description="60fps 메인 렌더 루프 및 런타임/콘솔 에러 0건 무결점",
                        level=1,
                        verification_rule="requestAnimationFrame 메인 루프 가동 및 치명적 구문/런타임 에러 0건"
                    )
                ]
        elif dom == "mobile_app":
            qcs = [
                QualityContract(
                    id="QC-U-01",
                    category="ux",
                    description="주요 버튼 및 대화형 요소 터치 타깃 48dp 이상 확보",
                    level=1,
                    verification_rule="버튼/아이콘 컴포넌트의 Modifier.size 또는 padding이 최소 48dp 규격을 준수하는가?"
                ),
                QualityContract(
                    id="QC-V-01",
                    category="visual",
                    description="텍스트 클리핑(세로 쪼개짐) 및 UI 오버플로우 제로화",
                    level=2,
                    verification_rule="텍스트 줄바꿈(저\\n장) 결함 없이 명확한 폰트 스케일 및 줄바꿈 처리(TextOverflow.Ellipsis 등)"
                ),
                QualityContract(
                    id="QC-A-01",
                    category="architecture",
                    description="상태 분리 및 단방향 데이터 흐름(UDF / ViewModel or Redux-like state)",
                    level=1,
                    verification_rule="UI 렌더링 함수 내 직접 상태 오염 없이 ViewModel/State 객체로 격리되어 있는가?"
                ),
                QualityContract(
                    id="QC-P-01",
                    category="performance",
                    description="비동기 I/O 코루틴 처리 및 메인 스레드 블로킹 0건",
                    level=1,
                    verification_rule="파일/네트워크 작업 시 Dispatchers.IO 또는 비동기 비블로킹 패턴 사용 여부"
                )
            ]
        elif dom in ("cli_agent", "daemon"):
            qcs = [
                QualityContract(
                    id="QC-A-01",
                    category="architecture",
                    description="명령행 인자(Argparse/Click) 유효성 검증 및 도움말(--help) 출력 완결",
                    level=1,
                    verification_rule="인자 누락 시 크래시 없이 적절한 사용법 안내 및 exit code != 0 처리"
                ),
                QualityContract(
                    id="QC-U-01",
                    category="ux",
                    description="장시간 작업 시 실시간 진행률(Progress Bar or Spinner or Step Indicator) 피드백",
                    level=1,
                    verification_rule="침묵(Silent freeze) 없이 터미널에 현재 작업 단계가 ANSI/컬러로 실시간 출력되는가?"
                ),
                QualityContract(
                    id="QC-P-01",
                    category="performance",
                    description="Graceful Shutdown (SIGINT/Ctrl+C) 프로세스 및 리소스 정상 해제",
                    level=1,
                    verification_rule="SIGINT 시 임시 파일 정리 및 자식 프로세스 안전 종료 핸들러 존재"
                )
            ]
        else: # saas_web, general
            qcs = [
                QualityContract(
                    id="QC-V-01",
                    category="visual",
                    description="반응형 레이아웃 및 뷰포트 오버플로우 방어",
                    level=2,
                    verification_rule="모바일(390px) ~ 데스크톱(1280px) 가로 스크롤 버그 없이 flex/grid 반응형 처리"
                ),
                QualityContract(
                    id="QC-U-01",
                    category="ux",
                    description="입력 폼 유효성 검증 및 사용자 친화적 에러 메시지 노출",
                    level=1,
                    verification_rule="비어있거나 잘못된 입력 시 브라우저 기본 alert 대신 인라인 에러 피드백 표시"
                ),
                QualityContract(
                    id="QC-A-01",
                    category="architecture",
                    description="상수 및 설정값(Config/Env)의 하드코딩 완전 분리",
                    level=1,
                    verification_rule="URL, API 키, 색상 토큰이 컴포넌트 내부에 인라인 하드코딩되지 않고 별도 파일로 분리"
                ),
                QualityContract(
                    id="QC-P-01",
                    category="performance",
                    description="렌더링 지연 방어 및 자원 정리(Cleanup Hook)",
                    level=1,
                    verification_rule="이벤트 리스너/타이머가 unmount 시 안전하게 removeEventListener/clearTimeout 되는가?"
                )
            ]

        return [q.to_dict() for q in qcs]
