#!/usr/bin/env python3
"""
🧠 Android E2E Dynamic LLM Intent Planner & Autonomous Blind Agent Loop (AGY 3.0 Standard)
Phase 1.5 Senior QA Directive:
1. LLM-Powered Test Intent Planning: Replaces static hardcoded if/else rules with dynamic LLM synthesis.
2. True Blind Autonomous Agent Loop: Implements Observe-Decide-Act-Evaluate cycle with Online Vision.
3. Decoupled MCP Contract: Executes through atomic StepContext and standardized MCP tools.
"""

import os
import re
import time
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple, Callable

from android_mcp_tools import AndroidMCPToolLayer, ToolExecutionResult, get_android_mcp_tools
try:
    from model_registry import AntigravityModelRegistry
except ImportError:
    from agy_agent.model_registry import AntigravityModelRegistry

logger = logging.getLogger("AndroidMCPPlanner")


@dataclass
class WorkflowStep:
    """An individual intent-driven atomic action and verification step."""
    step_id: int
    intent: str
    tool_name: str
    arguments: Dict[str, Any]
    expected_assertion: str
    target_verification: Optional[Dict[str, Any]] = None


@dataclass
class TestIntent:
    """Formal domain-specific test intent and execution roadmap."""
    app_name: str
    goal: str
    detected_capabilities: List[str]
    workflows: List[str]
    steps: List[WorkflowStep] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "app_name": self.app_name,
            "goal": self.goal,
            "detected_capabilities": self.detected_capabilities,
            "workflows": self.workflows,
            "steps": [
                {
                    "step_id": s.step_id,
                    "intent": s.intent,
                    "tool_name": s.tool_name,
                    "arguments": s.arguments,
                    "expected_assertion": s.expected_assertion,
                    "target_verification": s.target_verification
                }
                for s in self.steps
            ]
        }


class LLMTestIntentPlanner:
    """
    Dynamic LLM-Powered Test Intent Planner.
    Decomposes arbitrary natural language test goals into structured, domain-agnostic
    WorkflowStep sequences using LLM (Gemini 3.8/3.7 or local multimodal reasoner).
    Bypasses hardcoded app-specific if/else rules.
    """

    @classmethod
    def plan_intent(
        cls,
        goal: str,
        app_name: str = "App",
        package_name: str = "",
        main_activity: str = "",
        test_assets: Optional[List[str]] = None
    ) -> TestIntent:
        """Plans test intent dynamically using LLM, with robust semantic fallback."""
        # 1. Attempt LLM-based dynamic synthesis
        llm_intent = cls.synthesize_with_llm(
            goal=goal,
            app_name=app_name,
            package_name=package_name,
            main_activity=main_activity,
            test_assets=test_assets
        )
        if llm_intent:
            return llm_intent

        # 2. Semantic dynamic fallback (zero hardcoded app branches)
        return cls.synthesize_semantic_fallback(
            goal=goal,
            app_name=app_name,
            package_name=package_name,
            main_activity=main_activity,
            test_assets=test_assets
        )

    @classmethod
    def synthesize_with_llm(
        cls,
        goal: str,
        app_name: str,
        package_name: str,
        main_activity: str,
        test_assets: Optional[List[str]] = None
    ) -> Optional[TestIntent]:
        """Attempts dynamic LLM workflow decomposition via google.genai."""
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return None

        prompt = f"""You are an expert Autonomous Android QA Planner.
Decompose the following high-level testing goal for an Android app into an ordered, executable test workflow.

App Name: {app_name}
Package Name: {package_name}
Main Activity: {main_activity}
Test Goal: "{goal}"
Provided Test Assets: {test_assets or []}

Available MCP Tools:
- launch_app(package_name, main_activity)
- restart_app(package_name, main_activity, cold)
- tap_element(text_or_id, timeout, max_retries)
- type_text(text)
- press_key(key_code_or_name)
- swipe_screen(direction, duration_ms)
- navigate_page(target_page)
- inject_asset(host_file_path, target_device_path)
- capture_screen(label)
- verify_screen(expected_text, expected_resource_id)
- verify_app_state(package_name)
- verify_file(device_path, min_bytes, expected_hash, check_pdf_header)
- inspect_screen_vision(query)

Return strict JSON only (no markdown code fences):
{{
  "detected_capabilities": ["capability1", "capability2"],
  "workflows": ["workflow title 1", "workflow title 2"],
  "steps": [
    {{
      "step_id": 1,
      "intent": "Short step description",
      "tool_name": "exact_tool_name",
      "arguments": {{"arg_name": "arg_value"}},
      "expected_assertion": "verify_app_state|verify_screen|verify_file",
      "target_verification": {{"expected_text": "...", "device_path": "...", "min_bytes": 1}}
    }}
  ]
}}
"""
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            resolved = AntigravityModelRegistry.resolve_model(role="planner", preferred=os.getenv("GEMINI_PLANNER_MODEL"))
            planner_model = resolved.rsplit("-", 1)[0] if resolved.endswith(("-high", "-middle", "-low")) else resolved
            resp = client.models.generate_content(
                model=planner_model,
                contents=prompt
            )
            if resp and resp.text:
                clean_text = resp.text.strip()
                if clean_text.startswith("```"):
                    clean_text = re.sub(r"^```(?:json)?\n", "", clean_text)
                    clean_text = re.sub(r"\n```$", "", clean_text)
                data = json.loads(clean_text)
                steps = [
                    WorkflowStep(
                        step_id=s.get("step_id", idx + 1),
                        intent=s.get("intent", f"Step {idx + 1}"),
                        tool_name=s.get("tool_name", "verify_app_state"),
                        arguments=s.get("arguments", {}),
                        expected_assertion=s.get("expected_assertion", "verify_app_state"),
                        target_verification=s.get("target_verification")
                    )
                    for idx, s in enumerate(data.get("steps", []))
                ]
                return TestIntent(
                    app_name=app_name,
                    goal=goal,
                    detected_capabilities=data.get("detected_capabilities", []),
                    workflows=data.get("workflows", []),
                    steps=steps
                )
        except Exception as e:
            logger.warning(f"LLM workflow synthesis failed: {e}. Falling back to dynamic semantic planner.")
        return None

    @classmethod
    def synthesize_semantic_fallback(
        cls,
        goal: str,
        app_name: str,
        package_name: str,
        main_activity: str,
        test_assets: Optional[List[str]] = None
    ) -> TestIntent:
        """
        Domain-agnostic dynamic semantic intent compiler.
        Extracts entities, actions, target files, and asserts from natural language goal
        without any hardcoded app-specific if/else rules.
        """
        capabilities: List[str] = ["App Lifecycle"]
        workflows: List[str] = ["Core User Journey"]
        steps: List[WorkflowStep] = []
        step_id = 1

        # 1. Base step: Launch Application
        steps.append(WorkflowStep(
            step_id=step_id,
            intent=f"{app_name} 기동 및 메인 화면 렌더링 확인",
            tool_name="launch_app",
            arguments={"package_name": package_name, "main_activity": main_activity},
            expected_assertion="verify_app_state"
        ))
        step_id += 1

        # 2. Asset Injection (if assets provided or file mentioned)
        file_match = re.search(r'([/\w\-_.]+\.(?:pdf|png|jpg|txt|json|doc|apk))', goal, re.IGNORECASE)
        target_file = file_match.group(1) if file_match else None
        if test_assets:
            for asset in test_assets:
                dst_name = os.path.basename(asset)
                steps.append(WorkflowStep(
                    step_id=step_id,
                    intent=f"테스트 자산 주입 ({dst_name})",
                    tool_name="inject_asset",
                    arguments={"host_file_path": asset, "target_device_path": "/sdcard/Download/"},
                    expected_assertion="verify_file",
                    target_verification={"device_path": f"/sdcard/Download/{dst_name}", "min_bytes": 10}
                ))
                step_id += 1
                capabilities.append("Asset Ingestion")

        # 3. Dynamic Page Navigation (if page number detected)
        page_match = re.search(r'(\d+)\s*(?:페이지|page)', goal, re.IGNORECASE)
        if page_match:
            p_num = int(page_match.group(1))
            steps.append(WorkflowStep(
                step_id=step_id,
                intent=f"페이지 {p_num} 이동 및 뷰포트 전환",
                tool_name="navigate_page",
                arguments={"target_page": p_num},
                expected_assertion="verify_screen",
                target_verification={"expected_text": str(p_num)}
            ))
            step_id += 1
            capabilities.append("Paging Navigation")

        # 4. Dynamic Text Input (if input action detected)
        input_keywords = ["입력", "작성", "기록", "type", "input", "write", "메모"]
        if any(kw in goal for kw in input_keywords):
            # Extract quoted text if present, otherwise default to verified test string
            quote_match = re.search(r'["\']([^"\']+)["\']', goal)
            input_text = quote_match.group(1) if quote_match else "AGY Verified Entry"
            steps.append(WorkflowStep(
                step_id=step_id,
                intent=f"데이터 폼 입력 ('{input_text}')",
                tool_name="type_text",
                arguments={"text": input_text},
                expected_assertion="verify_screen",
                target_verification={"expected_text": input_text[:4]}
            ))
            step_id += 1
            capabilities.append("Data Input")

        # 5. Dynamic File Creation / Save Assertion (if export/save/file mentioned)
        save_keywords = ["저장", "내보내기", "생성", "export", "save", "파일"]
        if any(kw in goal for kw in save_keywords) or target_file:
            out_path = target_file if (target_file and target_file.startswith("/sdcard")) else "/sdcard/Download/output_verified.pdf"
            is_pdf = out_path.lower().endswith(".pdf")
            steps.append(WorkflowStep(
                step_id=step_id,
                intent="결과물 저장 및 스토리지 물리 파일 생성 단언",
                tool_name="tap_element",
                arguments={"text_or_id": "저장"},
                expected_assertion="verify_file",
                target_verification={"device_path": out_path, "min_bytes": 100, "check_pdf_header": is_pdf}
            ))
            step_id += 1
            capabilities.append("File Export")

        # 6. Cold Restart & Persistence Assertion (if persistence or restart mentioned)
        persist_keywords = ["재실행", "유지", "보존", "restart", "persistence", "영속성"]
        if any(kw in goal for kw in persist_keywords):
            steps.append(WorkflowStep(
                step_id=step_id,
                intent="앱 콜드 리스타트 및 데이터 영속성/무크래시 단언",
                tool_name="restart_app",
                arguments={"package_name": package_name, "main_activity": main_activity, "cold": True},
                expected_assertion="verify_app_state"
            ))
            step_id += 1
            capabilities.append("Data Persistence")

        # If no specialized steps were extracted, generate generic robust exploration
        if len(steps) == 1:
            steps.append(WorkflowStep(
                step_id=step_id,
                intent="기본 대화형 화면 요소 탐색 및 상호작용",
                tool_name="tap_element",
                arguments={"text_or_id": "시작"},
                expected_assertion="verify_screen",
                target_verification={}
            ))
            step_id += 1
            steps.append(WorkflowStep(
                step_id=step_id,
                intent="앱 재실행 무결성 단언",
                tool_name="restart_app",
                arguments={"package_name": package_name, "main_activity": main_activity},
                expected_assertion="verify_app_state"
            ))

        return TestIntent(
            app_name=app_name,
            goal=goal,
            detected_capabilities=list(set(capabilities)),
            workflows=workflows,
            steps=steps
        )


class AutonomousBlindAgent:
    """
    TRUE BLIND E2E Autonomous Agent Loop.
    Implements Observe-Decide-Act-Evaluate cycle with Online Vision:
    1. OBSERVE: get_ui_tree() + inspect_screen_vision(goal)
    2. DECIDE: Evaluates goal completion or selects next best action (tap/type/swipe/navigate)
    3. ACT & ASSERT: Executes action under atomic StepContext
    4. EVALUATE & HEAL: Evaluates physical outcome and initiates self-healing if blocked.
    """

    @classmethod
    def run_agentic_loop(
        cls,
        goal: str,
        tool_layer: AndroidMCPToolLayer,
        app_name: str = "App",
        package_name: str = "",
        main_activity: str = "",
        test_assets: Optional[List[str]] = None,
        max_steps: int = 10
    ) -> Dict[str, Any]:
        """
        Autonomous Agentic Execution Loop:
        Continuously observes the device screen (UI tree + Online Vision),
        reasons about current state versus goal, and executes atomic MCP actions.
        """
        t0 = time.time()
        logger.info(f"🤖 [AutonomousBlindAgent] Starting TRUE BLIND Agentic Loop for Goal: '{goal}'")

        # 0. Initial Setup: Inject assets if provided
        if test_assets:
            for asset in test_assets:
                tool_layer.inject_asset(asset, "/sdcard/Download/")

        # 1. Launch App if not running
        launch_res = tool_layer.launch_app(package_name, main_activity)
        time.sleep(1.0)

        history: List[Dict[str, Any]] = []
        step_id = 1
        goal_achieved = False
        final_verdict = "IN_PROGRESS"

        while step_id <= max_steps and not goal_achieved:
            logger.info(f"🔍 [Agent Loop Step {step_id}/{max_steps}] Observing Screen & UI Tree...")

            # --- 1. OBSERVE ---
            # 1a. UiAutomator Tree
            ui_res = tool_layer.get_ui_tree()
            ui_nodes = ui_res.data.get("nodes", []) if ui_res.success else []
            visible_texts = [n.get("text", "") for n in ui_nodes if n.get("text")]

            # 1b. On-Demand Vision Inspection (Strategy 1: Token Optimization)
            # When UI tree contains sufficient text and clickable nodes, skip expensive multimodal Vision calls.
            # Only invoke Gemini Vision when:
            # - Screen has no or minimal text (< 2 visible texts, e.g. Canvas, game, pure drawing)
            # - UI tree has no clickable/interactive elements
            # - Previous step action failed (Auto-Recovery mode, checking for crash/modal/alert dialogs)
            prev_step_failed = bool(history and not history[-1].get("success", True))
            has_interactive_elements = any(n.get("clickable") for n in ui_nodes)
            has_sufficient_text = len(visible_texts) >= 2
            needs_vision = prev_step_failed or not has_sufficient_text or not has_interactive_elements

            if needs_vision:
                logger.info(f"👁️ [On-Demand Vision] Invoking Gemini Vision (prev_failed={prev_step_failed}, texts={len(visible_texts)}, interactive={has_interactive_elements})")
                vision_res = tool_layer.inspect_screen_vision(
                    query=f"Analyze screen for Goal: '{goal}'. Identify main actions, text inputs, errors, or popups."
                )
                vision_data = vision_res.data if vision_res.success else {}
            else:
                logger.info(f"⚡ [Token Optimization] UI Tree provided {len(visible_texts)} texts & interactive nodes. Skipping expensive Vision API.")
                interactive_sample = [n.get("text") or n.get("content_desc") for n in ui_nodes if n.get("clickable") and (n.get("text") or n.get("content_desc"))]
                vision_data = {
                    "vision_summary": f"UI-Tree Fast Path: {len(visible_texts)} visible texts, {len(interactive_sample)} clickable targets (Vision call skipped to conserve tokens).",
                    "is_modal_or_dialog": False,
                    "key_interactive_elements": [{"name": str(t), "position": "center"} for t in interactive_sample[:5]],
                    "screen_state": "content",
                    "findings": ["UI Tree provided sufficient semantic hierarchy; zero visual tokens consumed."]
                }

            # --- 2. DECIDE ---
            decision = cls._decide_next_action(
                goal=goal,
                step_id=step_id,
                visible_texts=visible_texts,
                ui_nodes=ui_nodes,
                vision_data=vision_data,
                history=history,
                package_name=package_name
            )
            logger.info(f"💡 [Agent Decision] Action: {decision['tool_name']}, Args: {decision['arguments']}, Intent: {decision['intent']}")

            if decision.get("goal_achieved"):
                goal_achieved = True
                final_verdict = "GOAL_ACHIEVED"
                break

            # --- 3. ACT & ASSERT (Atomic StepContext) ---
            assertions_to_run = []
            if decision.get("expected_assertion") == "verify_screen":
                exp_txt = decision.get("target_verification", {}).get("expected_text", "")
                assertions_to_run.append(lambda t=exp_txt: tool_layer.verify_screen(t))
            elif decision.get("expected_assertion") == "verify_file":
                p = decision.get("target_verification", {}).get("device_path", "")
                mb = decision.get("target_verification", {}).get("min_bytes", 1)
                chk_pdf = decision.get("target_verification", {}).get("check_pdf_header", False)
                assertions_to_run.append(lambda path=p, b=mb, c=chk_pdf: tool_layer.verify_file(path, min_bytes=b, check_pdf_header=c))
            elif decision.get("expected_assertion") == "verify_app_state":
                assertions_to_run.append(lambda p=package_name: tool_layer.verify_app_state(p))

            with tool_layer.step_context(
                action_name=decision["tool_name"],
                action_desc=decision["intent"],
                assertions=assertions_to_run
            ) as ctx:
                tool_fn = getattr(tool_layer, decision["tool_name"], None)
                if tool_fn and callable(tool_fn):
                    try:
                        act_res = tool_fn(**decision["arguments"])
                        ctx.set_action_result(act_res)
                    except Exception as act_e:
                        ctx.set_action_result(ToolExecutionResult(
                            tool_name=decision["tool_name"],
                            success=False,
                            failure_reason=str(act_e)
                        ))
                else:
                    ctx.set_action_result(ToolExecutionResult(
                        tool_name=decision["tool_name"],
                        success=False,
                        failure_reason=f"Tool not found: {decision['tool_name']}"
                    ))

            chk = ctx.checkpoint
            step_record = {
                "step_id": step_id,
                "intent": decision["intent"],
                "tool": decision["tool_name"],
                "arguments": decision["arguments"],
                "success": chk.success if chk else False,
                "before_screenshot": chk.before_screenshot if chk else None,
                "after_screenshot": chk.after_screenshot if chk else None,
                "vision_insights": vision_data.get("findings", []),
                "checkpoint": chk.to_dict() if chk else {}
            }
            history.append(step_record)

            # --- 4. EVALUATE & RECOVER ---
            if chk and not chk.success:
                logger.warning(f"⚠️ Step {step_id} assertion failed. Triggering auto-recovery...")
                tool_layer.press_key("BACK")
                time.sleep(0.5)

            if decision.get("is_final_step"):
                goal_achieved = (chk and chk.success)
                final_verdict = "GOAL_ACHIEVED" if goal_achieved else "ASSERTION_FAILED"
                break

            step_id += 1

        duration_ms = int((time.time() - t0) * 1000)
        return {
            "goal": goal,
            "app_name": app_name,
            "verdict": final_verdict,
            "goal_achieved": goal_achieved,
            "steps_executed": len(history),
            "duration_ms": duration_ms,
            "trajectory": history
        }

    @classmethod
    def _decide_next_action(
        cls,
        goal: str,
        step_id: int,
        visible_texts: List[str],
        ui_nodes: List[Dict[str, Any]],
        vision_data: Dict[str, Any],
        history: List[Dict[str, Any]],
        package_name: str
    ) -> Dict[str, Any]:
        """
        Autonomous Decision Engine (Observe-Decide-Act-Evaluate):
        1. Injects real-time Online Vision context (screen state, modals, key elements).
        2. Queries LLM Zero-Shot Reasoning if available to dynamically select tool and assertion.
        3. If offline, applies State-Based Graph Navigation (UI tree interactive exploration,
           not keyword regex matching).
        """
        # 1. Attempt LLM Zero-Shot Reasoning with Vision Context
        llm_decision = cls._llm_decide_action(
            goal=goal,
            step_id=step_id,
            visible_texts=visible_texts,
            ui_nodes=ui_nodes,
            vision_data=vision_data,
            history=history,
            package_name=package_name
        )
        if llm_decision:
            return llm_decision

        # 2. Pure State-Based Graph Navigation Fallback (Zero Goal Regexes)
        return cls._state_based_fallback_decision(
            step_id=step_id,
            visible_texts=visible_texts,
            ui_nodes=ui_nodes,
            vision_data=vision_data,
            history=history,
            package_name=package_name
        )

    @classmethod
    def _llm_decide_action(
        cls,
        goal: str,
        step_id: int,
        visible_texts: List[str],
        ui_nodes: List[Dict[str, Any]],
        vision_data: Dict[str, Any],
        history: List[Dict[str, Any]],
        package_name: str
    ) -> Optional[Dict[str, Any]]:
        """Invokes LLM with current screen state, Online Vision insights, and history."""
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return None

        # Build compact interactive element summary
        interactive_nodes = []
        for n in ui_nodes:
            t = n.get("text") or n.get("content_desc") or n.get("resource_id")
            cls_name = n.get("class", "").split(".")[-1]
            if t and (n.get("clickable") or cls_name in ["Button", "EditText", "TextView", "ImageView"]):
                interactive_nodes.append({
                    "text": t,
                    "class": cls_name,
                    "clickable": n.get("clickable", False)
                })

        # Real-time Vision Context injection
        vision_summary = vision_data.get("vision_summary") or (vision_data.get("findings", ["Normal visual screen"])[0] if vision_data.get("findings") else "Active visual frame")
        is_modal = vision_data.get("is_modal_or_dialog", False)
        modal_msg = vision_data.get("modal_title_or_message", "")
        visual_elements = vision_data.get("key_interactive_elements", [])

        prompt = f"""You are the brain of an Autonomous Android E2E Testing Agent.
Goal: "{goal}"
Current Step: {step_id}
Package Name: {package_name}

=== REAL-TIME ONLINE VISION SCREEN STATE ===
Visual Summary: {vision_summary}
Active Modal / Dialog Blocking: {is_modal} ({modal_msg})
Key Visual Elements: {json.dumps(visual_elements, ensure_ascii=False)}

=== OBSERVED INTERACTIVE UI NODES ===
{json.dumps(interactive_nodes[:20], ensure_ascii=False)}

=== ACTION HISTORY SO FAR ===
{json.dumps([{"step": h["step_id"], "tool": h["tool"], "intent": h["intent"], "success": h["success"]} for h in history], ensure_ascii=False)}

Available Tools:
- tap_element(text_or_id: str)
- type_text(text: str)
- press_key(key_code_or_name: str)
- swipe_screen(direction: "up"|"down"|"left"|"right")
- navigate_page(target_page: int)
- restart_app(package_name: str, cold: bool)
- verify_file(device_path: str, min_bytes: int, check_pdf_header: bool)
- verify_screen(expected_text: str)
- verify_app_state(package_name: str)

Based on the Goal, Vision observations, and UI nodes:
1. If the goal has been achieved or verified, set "goal_achieved": true and "is_final_step": true.
2. If a blocking modal or permission dialog is visible, select tap_element to dismiss it.
3. Otherwise, select the single best next action to make progress toward the goal.
4. Bind an appropriate outcome assertion ("verify_screen" | "verify_file" | "verify_app_state").

Return strict JSON only (no markdown fences):
{{
  "goal_achieved": false,
  "tool_name": "tap_element",
  "arguments": {{"text_or_id": "..."}},
  "intent": "Reason for action",
  "expected_assertion": "verify_screen",
  "target_verification": {{"expected_text": "..."}},
  "is_final_step": false
}}
"""
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            resolved = AntigravityModelRegistry.resolve_model(role="planner", preferred=os.getenv("GEMINI_PLANNER_MODEL"))
            planner_model = resolved.rsplit("-", 1)[0] if resolved.endswith(("-high", "-middle", "-low")) else resolved
            resp = client.models.generate_content(
                model=planner_model,
                contents=prompt
            )
            if resp and resp.text:
                clean_text = resp.text.strip()
                if clean_text.startswith("```"):
                    clean_text = re.sub(r"^```(?:json)?\n", "", clean_text)
                    clean_text = re.sub(r"\n```$", "", clean_text)
                decision = json.loads(clean_text)
                if "tool_name" in decision:
                    decision.setdefault("arguments", {})
                    decision.setdefault("intent", f"LLM Action: {decision['tool_name']}")
                    decision.setdefault("expected_assertion", "verify_app_state")
                    decision.setdefault("target_verification", {})
                    return decision
        except Exception as e:
            logger.warning(f"LLM decide action failed: {e}. Falling back to state-based exploration.")
        return None

    @classmethod
    def _state_based_fallback_decision(
        cls,
        step_id: int,
        visible_texts: List[str],
        ui_nodes: List[Dict[str, Any]],
        vision_data: Dict[str, Any],
        history: List[Dict[str, Any]],
        package_name: str
    ) -> Dict[str, Any]:
        """
        Pure State-Based UI Graph Exploration Fallback.
        ZERO goal regexes. Explores based purely on device UI state and vision findings.
        """
        # 1. Roadblock/Dialog dismissal (from Vision or UI Tree)
        is_modal = vision_data.get("is_modal_or_dialog", False)
        modal_tokens = ["허용", "확인", "allow", "ok", "닫기", "동의", "yes", "dismiss"]
        for node in ui_nodes:
            t = node.get("text", "").strip().lower()
            if any(p == t or f" {p} " in f" {t} " for p in modal_tokens):
                return {
                    "tool_name": "tap_element",
                    "arguments": {"text_or_id": node.get("text")},
                    "intent": f"시스템/다이얼로그 팝업 해제 ('{node.get('text')}')",
                    "expected_assertion": "verify_app_state",
                    "target_verification": {}
                }

        # 2. Editable Text Field Interaction (if unvisited EditText present)
        edit_fields = [n for n in ui_nodes if "EditText" in n.get("class", "")]
        already_typed = any(h["tool"] == "type_text" for h in history)
        if edit_fields and not already_typed:
            target_field = edit_fields[0].get("resource_id") or edit_fields[0].get("text") or "input"
            return {
                "tool_name": "type_text",
                "arguments": {"text": "AGY E2E Verified Input"},
                "intent": f"활성 에디터 필드 텍스트 입력 ('{target_field}')",
                "expected_assertion": "verify_screen",
                "target_verification": {"expected_text": "AGY"}
            }

        # 3. Interactive Clickable Elements (Actionable Buttons, Icons)
        tapped_targets = set(h.get("arguments", {}).get("text_or_id", "") for h in history if h["tool"] == "tap_element")
        clickable_nodes = [
            n for n in ui_nodes
            if (n.get("clickable") or "Button" in n.get("class", "")) and (n.get("text") or n.get("resource_id"))
        ]
        unvisited_nodes = [n for n in clickable_nodes if (n.get("text") or n.get("resource_id")) not in tapped_targets]

        if unvisited_nodes:
            chosen = unvisited_nodes[0]
            chosen_target = chosen.get("text") or chosen.get("resource_id")
            return {
                "tool_name": "tap_element",
                "arguments": {"text_or_id": chosen_target},
                "intent": f"미방문 대화형 UI 요소 자율 타격 ('{chosen_target}')",
                "expected_assertion": "verify_screen",
                "target_verification": {"expected_text": chosen.get("text", "")} if chosen.get("text") else {}
            }

        # 4. Viewport Exploration Gesture (Scroll / Swipe)
        if len(history) < 4:
            return {
                "tool_name": "swipe_screen",
                "arguments": {"direction": "down", "duration_ms": 500},
                "intent": "화면 스와이프를 통한 추가 UI 노드 탐색",
                "expected_assertion": "verify_app_state",
                "target_verification": {}
            }

        # 5. Cold Restart and Persistence Assertion (Final Verification)
        if not any(h["tool"] == "restart_app" for h in history):
            return {
                "tool_name": "restart_app",
                "arguments": {"package_name": package_name, "cold": True},
                "intent": "앱 재실행 후 영속성 및 무크래시 단언",
                "expected_assertion": "verify_app_state",
                "target_verification": {},
                "is_final_step": True
            }

        return {
            "tool_name": "verify_app_state",
            "arguments": {"package_name": package_name},
            "intent": "최종 런타임 안정성 및 무크래시 단언",
            "expected_assertion": "verify_app_state",
            "target_verification": {},
            "is_final_step": True
        }

    @classmethod
    def execute_workflow(
        cls,
        intent: TestIntent,
        tool_layer: AndroidMCPToolLayer,
        package_name: str = ""
    ) -> Dict[str, Any]:
        """
        Executes workflow steps sequentially with mandatory atomic StepContext:
        Before Screenshot -> Action Execution -> After Screenshot -> Outcome Assertions.
        """
        t0 = time.time()
        logger.info(f"🚀 Executing Goal-Driven Test Intent: '{intent.goal}' ({len(intent.steps)} steps)")
        step_results = []
        all_passed = True

        for step in intent.steps:
            logger.info(f"▶️ [Step {step.step_id}] {step.intent} (Tool: {step.tool_name})")

            # Formulate assertion lambdas based on step definition
            assertions_to_run = []
            if step.expected_assertion == "verify_app_state":
                pkg = step.arguments.get("package_name") or package_name
                assertions_to_run.append(lambda p=pkg: tool_layer.verify_app_state(p))
            elif step.expected_assertion == "verify_screen" and step.target_verification:
                txt = step.target_verification.get("expected_text", "")
                assertions_to_run.append(lambda t=txt: tool_layer.verify_screen(t))
            elif step.expected_assertion == "verify_file" and step.target_verification:
                p = step.target_verification.get("device_path", "")
                mb = step.target_verification.get("min_bytes", 1)
                chk_pdf = step.target_verification.get("check_pdf_header", False)
                assertions_to_run.append(lambda path=p, b=mb, c=chk_pdf: tool_layer.verify_file(path, min_bytes=b, check_pdf_header=c))

            # Execute under atomic StepContext
            with tool_layer.step_context(
                action_name=step.tool_name,
                action_desc=step.intent,
                assertions=assertions_to_run
            ) as ctx:
                tool_fn = getattr(tool_layer, step.tool_name, None)
                if tool_fn and callable(tool_fn):
                    try:
                        res = tool_fn(**step.arguments)
                        ctx.set_action_result(res)
                        if not res.success:
                            all_passed = False
                    except Exception as e:
                        all_passed = False
                        ctx.set_action_result(ToolExecutionResult(tool_name=step.tool_name, success=False, failure_reason=str(e)))
                else:
                    all_passed = False
                    ctx.set_action_result(ToolExecutionResult(tool_name=step.tool_name, success=False, failure_reason=f"Tool not found: {step.tool_name}"))

            chk = ctx.checkpoint
            chk_dict = chk.to_dict() if chk else {}
            step_results.append({
                "step_id": step.step_id,
                "intent": step.intent,
                "tool": step.tool_name,
                "success": chk.success if chk else False,
                "failure_reason": chk.failure_reason if chk else "No checkpoint recorded",
                "checkpoint": chk_dict
            })
            if chk and not chk.success:
                all_passed = False

        duration_ms = int((time.time() - t0) * 1000)
        return {
            "intent_goal": intent.goal,
            "app_name": intent.app_name,
            "all_passed": all_passed,
            "total_steps": len(intent.steps),
            "duration_ms": duration_ms,
            "step_results": step_results
        }


class TestIntentPlanner:
    """
    Unified Test Intent & Autonomous Agent Entrypoint.
    Backwards compatible while delegating to LLMTestIntentPlanner and AutonomousBlindAgent.
    """

    @classmethod
    def create_intent_from_goal(
        cls,
        goal: str,
        app_name: str = "App",
        package_name: str = "",
        main_activity: str = "",
        test_assets: Optional[List[str]] = None
    ) -> TestIntent:
        return LLMTestIntentPlanner.plan_intent(
            goal=goal,
            app_name=app_name,
            package_name=package_name,
            main_activity=main_activity,
            test_assets=test_assets
        )

    @classmethod
    def execute_workflow(
        cls,
        intent: TestIntent,
        tool_layer: AndroidMCPToolLayer,
        package_name: str = ""
    ) -> Dict[str, Any]:
        return AutonomousBlindAgent.execute_workflow(
            intent=intent,
            tool_layer=tool_layer,
            package_name=package_name
        )

    @classmethod
    def run_blind_agent(
        cls,
        goal: str,
        tool_layer: AndroidMCPToolLayer,
        app_name: str = "App",
        package_name: str = "",
        main_activity: str = "",
        test_assets: Optional[List[str]] = None,
        max_steps: int = 10
    ) -> Dict[str, Any]:
        return AutonomousBlindAgent.run_agentic_loop(
            goal=goal,
            tool_layer=tool_layer,
            app_name=app_name,
            package_name=package_name,
            main_activity=main_activity,
            test_assets=test_assets,
            max_steps=max_steps
        )
