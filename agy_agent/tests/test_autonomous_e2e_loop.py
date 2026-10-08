#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_autonomous_e2e_loop.py
===========================
협업 파이프라인(!mode 2/3)에 삽입된 자율 E2E 루프(`autonomous_e2e_loop.py`)의
핵심 동작을 검증한다.

검증 축:
  1. 환경 스위치 (enabled / max_rounds)
  2. 테스트 명령 자동 탐지 (pytest / npm, 셸 주입 없는 argv)
  3. 실제 서브프로세스 테스트 실행 (통과/실패 returncode 물리 검증)
  4. 결함 분석 — 테스트 실패=BLOCKER, 환경 미비=비차단 갭, GUI 스크린샷 결손=MAJOR
  5. 스크린샷 신선도 실측
  6. 물리 증거 파일(reports/gate4/autonomous_e2e_report.json) 기록
  7. 프롬프트/피드백 직렬화
"""
import json
import os
import shutil
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import autonomous_e2e_loop as ael  # noqa: E402


# ----------------------------------------------------------------------
# 1. 환경 스위치
# ----------------------------------------------------------------------
def test_enabled_default_and_off(monkeypatch):
    monkeypatch.delenv("AUTONOMOUS_E2E_LOOP_ENABLED", raising=False)
    assert ael.autonomous_e2e_enabled() is True
    for off in ("0", "false", "no", "off"):
        monkeypatch.setenv("AUTONOMOUS_E2E_LOOP_ENABLED", off)
        assert ael.autonomous_e2e_enabled() is False


def test_max_rounds_env(monkeypatch):
    monkeypatch.delenv("AUTONOMOUS_E2E_MAX_ROUNDS", raising=False)
    assert ael.autonomous_e2e_max_rounds() == 2
    monkeypatch.setenv("AUTONOMOUS_E2E_MAX_ROUNDS", "5")
    assert ael.autonomous_e2e_max_rounds() == 5
    monkeypatch.setenv("AUTONOMOUS_E2E_MAX_ROUNDS", "-3")
    assert ael.autonomous_e2e_max_rounds() == 0


# ----------------------------------------------------------------------
# 2. 테스트 명령 자동 탐지
# ----------------------------------------------------------------------
def test_detect_python_pytest(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    cmds = ael.detect_test_commands(str(tmp_path))
    names = [c["name"] for c in cmds]
    assert "pytest" in names
    pytest_spec = next(c for c in cmds if c["name"] == "pytest")
    assert isinstance(pytest_spec["argv"], list)
    assert "-m" in pytest_spec["argv"] and "pytest" in pytest_spec["argv"]


def test_detect_node_npm_only_with_real_script(tmp_path):
    if not shutil.which("npm"):
        pytest.skip("npm 미설치 환경 — 탐지 로직만 검증 불가")
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8"
    )
    names = [c["name"] for c in ael.detect_test_commands(str(tmp_path))]
    assert "npm-test" in names

    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "echo \"Error: no test specified\" && exit 1"}}), encoding="utf-8"
    )
    names2 = [c["name"] for c in ael.detect_test_commands(str(tmp_path))]
    assert "npm-test" not in names2


def test_detect_empty_workspace_has_no_commands(tmp_path):
    assert ael.detect_test_commands(str(tmp_path)) == []


# ----------------------------------------------------------------------
# 3. 실제 서브프로세스 실행
# ----------------------------------------------------------------------
def test_execute_one_real_subprocess_pass_and_fail(tmp_path):
    loop = ael.AutonomousE2ELoop
    ok = loop._execute_one(
        str(tmp_path),
        {"name": "pass", "command": "python -c ok", "argv": [sys.executable, "-c", "print('ok')"]},
        timeout=30,
    )
    assert ok.passed is True
    assert ok.returncode == 0
    assert ok.reason == ""

    bad = loop._execute_one(
        str(tmp_path),
        {"name": "fail", "command": "python -c fail", "argv": [sys.executable, "-c", "import sys; sys.exit(1)"]},
        timeout=30,
    )
    assert bad.passed is False
    assert bad.returncode == 1
    assert bad.reason == ""


def test_execute_one_missing_binary_is_nonfatal(tmp_path):
    loop = ael.AutonomousE2ELoop
    res = loop._execute_one(
        str(tmp_path),
        {"name": "nope", "command": "definitely-not-a-binary", "argv": ["definitely-not-a-binary-xyz"]},
        timeout=10,
    )
    assert res.passed is False
    assert res.reason  # 도구 미설치 사유가 기록되어야 한다


# ----------------------------------------------------------------------
# 4. 결함 분석
# ----------------------------------------------------------------------
def _run(name, rc, passed, tail=""):
    return ael.E2ETestRun(name=name, command=f"cmd {name}", returncode=rc, passed=passed, duration_sec=1.0, output_tail=tail)


def test_analyze_test_failure_is_blocker(tmp_path):
    runs = [_run("pytest", 1, False, "FAILED tests/test_a.py::test_b - AssertionError")]
    defects, gaps, verdict = ael.AutonomousE2ELoop.analyze(str(tmp_path), runs, [], [])
    assert verdict == "FAIL"
    assert any(d.severity == "BLOCKER" and d.category == "TEST_FAILURE" for d in defects)
    report = ael.AutonomousE2EReport(workspace=str(tmp_path), verdict=verdict, ran_tests=True, test_runs=runs, defects=defects, gaps=gaps)
    assert report.has_blocking_defects() is True


def test_analyze_runtime_error_classified(tmp_path):
    runs = [_run("pytest", 1, False, "Traceback (most recent call last):\nNullPointerException")]
    defects, _gaps, verdict = ael.AutonomousE2ELoop.analyze(str(tmp_path), runs, [], [])
    assert verdict == "FAIL"
    assert any(d.category == "RUNTIME_ERROR" for d in defects)


def test_analyze_env_failure_is_nonblocking_gap(tmp_path):
    runs = [_run("pytest", 5, False, "no tests ran in 0.01s")]
    defects, gaps, verdict = ael.AutonomousE2ELoop.analyze(str(tmp_path), runs, [], [])
    assert defects == []
    assert verdict == "BLOCKED"
    assert any(g.category == "TEST_UNVERIFIED" for g in gaps)


def test_analyze_all_passed_is_pass(tmp_path):
    runs = [_run("pytest", 0, True, "3 passed")]
    defects, gaps, verdict = ael.AutonomousE2ELoop.analyze(str(tmp_path), runs, [], [])
    assert verdict == "PASS"
    assert defects == []


def test_analyze_gui_missing_screenshot_is_major(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    defects, _gaps, verdict = ael.AutonomousE2ELoop.analyze(
        str(tmp_path), [_run("pytest", 0, True)], [], ["src/App.tsx"]
    )
    assert verdict == "FAIL"
    assert any(d.severity == "MAJOR" and d.category == "VISUAL_EVIDENCE_MISSING" for d in defects)


def test_analyze_gui_with_screenshot_has_no_visual_defect(tmp_path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    shot = tmp_path / "reports" / "gate5" / "screenshots" / "a.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"\x89PNG" + b"x" * 800)
    defects, _gaps, verdict = ael.AutonomousE2ELoop.analyze(
        str(tmp_path), [_run("pytest", 0, True)], [str(shot)], ["src/App.tsx"]
    )
    assert verdict == "PASS"
    assert not any(d.category == "VISUAL_EVIDENCE_MISSING" for d in defects)


def test_analyze_no_tests_is_nonblocking_gap(tmp_path):
    defects, gaps, verdict = ael.AutonomousE2ELoop.analyze(str(tmp_path), [], [], [])
    assert defects == []
    assert verdict == "BLOCKED"
    assert any(g.category == "NO_TESTS" for g in gaps)


# ----------------------------------------------------------------------
# 5. 스크린샷 신선도 실측
# ----------------------------------------------------------------------
def test_collect_screenshots_freshness(tmp_path):
    d = tmp_path / "reports" / "gate5" / "screenshots"
    d.mkdir(parents=True)
    fresh = d / "fresh.png"
    fresh.write_bytes(b"\x89PNG" + b"a" * 800)
    old = d / "old.png"
    old.write_bytes(b"\x89PNG" + b"b" * 800)
    old_time = time.time() - 3600
    os.utime(str(old), (old_time, old_time))

    all_shots = ael.AutonomousE2ELoop.collect_screenshots(str(tmp_path))
    assert str(fresh) in all_shots and str(old) in all_shots

    fresh_only = ael.AutonomousE2ELoop.collect_screenshots(str(tmp_path), session_start_time=time.time() - 60)
    assert str(fresh) in fresh_only
    assert str(old) not in fresh_only


# ----------------------------------------------------------------------
# 6. 물리 증거 파일
# ----------------------------------------------------------------------
def test_run_writes_evidence_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ael.AutonomousE2ELoop,
        "run_test_suites",
        classmethod(lambda cls, ws, timeout=None: [_run("pytest", 0, True, "1 passed")]),
    )
    report = ael.AutonomousE2ELoop.run(str(tmp_path), round_index=1, session_start_time=None, changed_files=[])
    assert report.verdict == "PASS"
    evidence = tmp_path / "reports" / "gate4" / "autonomous_e2e_report.json"
    assert evidence.is_file(), "물리 증거 파일이 생성되어야 한다"
    data = json.loads(evidence.read_text(encoding="utf-8"))
    assert data["verdict"] == "PASS"
    assert data["test_run_count"] == 1
    assert data["tests_passed"] == 1


def test_run_never_raises_on_missing_workspace(tmp_path):
    missing = str(tmp_path / "does_not_exist")
    report = ael.AutonomousE2ELoop.run(missing, round_index=1)
    assert isinstance(report, ael.AutonomousE2EReport)
    assert report.verdict in ("BLOCKED", "FAIL", "PASS")


# ----------------------------------------------------------------------
# 7. 프롬프트 / 피드백 직렬화
# ----------------------------------------------------------------------
def test_format_blocks_include_defects():
    run = _run("pytest", 1, False, "FAILED tests/test_a.py::test_b")
    defects, gaps, verdict = ael.AutonomousE2ELoop.analyze("/nonexistent", [run], [], [])
    report = ael.AutonomousE2EReport(workspace="/nonexistent", verdict=verdict, ran_tests=True, test_runs=[run], defects=defects, gaps=gaps)
    report.summary = ael.AutonomousE2ELoop._build_summary(report)

    block = ael.AutonomousE2ELoop.format_prompt_block(report)
    assert "자율 E2E 검증 리포트" in block
    assert "BLOCKER" in block
    assert "[QA 필수 지침]" in block

    directive = ael.AutonomousE2ELoop.build_fix_directive(report)
    assert "AUTONOMOUS-E2E-FIX" in directive

    assert ael.AutonomousE2ELoop.has_blocking_defects(report) is True
    assert ael.AutonomousE2ELoop.format_prompt_block(None) == ""
    assert ael.AutonomousE2ELoop.build_fix_directive(None) == ""


# ----------------------------------------------------------------------
# 8. 전수조사(Exhaustive): 표본 컵 없이 전체 스크린샷을 검사한다.
# ----------------------------------------------------------------------
def test_count_blank_frames_scans_all_not_just_eight(tmp_path):
    from PIL import Image

    d = tmp_path / "shots"
    d.mkdir()
    shots = []
    for i in range(12):
        p = d / f"blank_{i}.png"
        Image.new("RGB", (24, 24), color=(0, 0, 0)).save(str(p))
        shots.append(str(p))

    # 종전에는 [:8] 표본만 검사했으나, 이제 수집된 12장 전체를 검사한다.
    assert ael.AutonomousE2ELoop._count_blank_frames(shots) == 12


def test_analyze_duplicate_frames_is_major_freeze(tmp_path):
    from PIL import Image, ImageDraw

    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    d = tmp_path / "reports" / "gate5" / "screenshots"
    d.mkdir(parents=True)

    img = Image.new("RGB", (48, 48), color=(10, 20, 30))
    ImageDraw.Draw(img).line((0, 0, 47, 47), fill=(255, 255, 255), width=3)
    a = d / "a.png"
    b = d / "b.png"
    img.save(str(a))
    img.save(str(b))  # a 와 바이트 완전 동일 → UI Freeze 후보

    defects, _gaps, verdict = ael.AutonomousE2ELoop.analyze(
        str(tmp_path), [_run("pytest", 0, True)], [str(a), str(b)], ["src/App.tsx"]
    )
    assert verdict == "FAIL"
    assert any(d_.severity == "MAJOR" and d_.category == "VISUAL_UI_FREEZE" for d_ in defects)


def test_report_counts_duplicate_and_blank_frames(tmp_path):
    import random
    from PIL import Image

    d = tmp_path / "reports" / "gate5" / "screenshots"
    d.mkdir(parents=True)
    p = d / "blank.png"
    # stddev < 2.0(빈 화면)이지만 500바이트 필터를 통과하도록 0/1 저분산 노이즈로 채운다.
    random.seed(0)
    w = h = 64
    data = bytes(random.randint(0, 1) for _ in range(w * h * 3))
    Image.frombytes("RGB", (w, h), data).save(str(p))

    report = ael.AutonomousE2ELoop.run(str(tmp_path), round_index=1)
    out = report.to_dict()
    assert out["screenshot_count"] == 1
    assert out["blank_frame_count"] == 1
    assert out["duplicate_frame_count"] == 0


# ----------------------------------------------------------------------
# 9. 스크린샷 전수 비전 분석 & 커버리지 기록 (모든 기능/화면 꼼꼼 검사)
# ----------------------------------------------------------------------
def _inspection(shot, verdict, score=None, defects=None, degraded=False):
    return ael.E2EScreenshotInspection(
        screenshot=shot,
        engine="test-vision",
        verdict=verdict,
        score=score,
        defects=defects or [],
        degraded=degraded,
    )


def test_e2e_vision_enabled_default_and_off(monkeypatch):
    monkeypatch.delenv("AUTONOMOUS_E2E_VISION_ENABLED", raising=False)
    assert ael.autonomous_e2e_vision_enabled() is True
    monkeypatch.setenv("AUTONOMOUS_E2E_VISION_ENABLED", "0")
    assert ael.autonomous_e2e_vision_enabled() is False


def test_inspect_vision_skips_external_calls_under_pytest():
    # 테스트 환경에서는 외부 Vision API 호출이 원천 차단되어야 한다 (쿼터 보호).
    assert ael.AutonomousE2ELoop.inspect_screenshots_vision(["whatever.png"]) == []


def test_inspect_vision_analyzes_every_screenshot(monkeypatch):
    """전수(1장도 빠뜨리지 않음): 입력된 스크린샷 수만큼 정확히 개별 분석한다."""
    import vision_attachment_analyzer as vaa

    calls = []

    async def fake_analyze(image_path, user_prompt="", role="general"):
        calls.append((image_path, role))
        return {
            "success": True,
            "engine": "fake-vision",
            "degraded": False,
            "analysis": (
                "[VISUAL_VERDICT]: FAIL\n[VISUAL_SCORE]: 55\n"
                "[VISUAL_SUMMARY]: 레이아웃 잘림\n[VISUAL_DEFECTS]:\n"
                "- MAJOR | OVERFLOW | 상단 | 텍스트가 화면 밖으로 잘림 | 여백 조정\n"
            ),
        }

    monkeypatch.setattr(vaa.VisionAttachmentAnalyzer, "analyze_image_file", fake_analyze)
    # pytest/unittest 모듈과 UNITTEST_RUNNING 가드가 있으면 차단되므로 일시 해제한다.
    monkeypatch.delitem(sys.modules, "pytest", raising=False)
    monkeypatch.delitem(sys.modules, "unittest", raising=False)
    monkeypatch.delenv("UNITTEST_RUNNING", raising=False)

    shots = [f"/tmp/shot_{i}.png" for i in range(3)]
    res = ael.AutonomousE2ELoop.inspect_screenshots_vision(shots)

    assert len(res) == 3, "스크린샷 3장이면 3장 모두 검사해야 한다(전수)"
    assert [c[0] for c in calls] == shots
    assert all(r.verdict == "FAIL" and r.score == 55 for r in res)
    assert all(r.verified for r in res)
    assert all(c[1] == "design_critique" for c in calls)


def test_analyze_vision_fail_is_major_defect(tmp_path):
    insp = [_inspection("home.png", "FAIL", 40, [
        {"severity": "MAJOR", "rule_id": "OVERFLOW", "location": "상단", "issue": "잘림", "fix": "수정"}
    ])]
    defects, _gaps, verdict = ael.AutonomousE2ELoop.analyze(
        str(tmp_path), [_run("pytest", 0, True)], ["home.png"], [], insp
    )
    assert verdict == "FAIL"
    assert any(d.severity == "MAJOR" and d.category == "VISUAL_VISION_DEFECT" for d in defects)


def test_analyze_vision_warn_is_nonblocking_gap(tmp_path):
    insp = [_inspection("home.png", "WARN", 78)]
    defects, gaps, verdict = ael.AutonomousE2ELoop.analyze(
        str(tmp_path), [_run("pytest", 0, True)], ["home.png"], [], insp
    )
    assert defects == []
    assert verdict == "PASS"
    assert any(g.category == "VISUAL_VISION_WARN" for g in gaps)


def test_analyze_degraded_inspection_is_not_a_defect(tmp_path):
    # 비전 API 실패(메타데이터 폴백)는 결함으로 승격하지 않고 '미검증'으로 정직하게 남긴다.
    insp = [_inspection("home.png", "SKIPPED", None, degraded=True)]
    defects, gaps, verdict = ael.AutonomousE2ELoop.analyze(
        str(tmp_path), [_run("pytest", 0, True)], ["home.png"], [], insp
    )
    assert defects == []
    assert verdict == "PASS"
    assert any(g.category == "VISUAL_VISION_UNVERIFIED" for g in gaps)


def test_run_records_vision_inspections_and_coverage(tmp_path, monkeypatch):
    insp = [
        _inspection("a.png", "PASS", 91),
        _inspection("b.png", "FAIL", 50, [
            {"severity": "MAJOR", "rule_id": "OVERFLOW", "location": "x", "issue": "y", "fix": "z"}
        ]),
    ]
    monkeypatch.setattr(
        ael.AutonomousE2ELoop, "run_test_suites",
        classmethod(lambda cls, ws, timeout=None: [_run("pytest", 0, True, "1 passed")]),
    )
    monkeypatch.setattr(
        ael.AutonomousE2ELoop, "collect_screenshots",
        classmethod(lambda cls, ws, session_start_time=None: ["a.png", "b.png"]),
    )
    monkeypatch.setattr(
        ael.AutonomousE2ELoop, "inspect_screenshots_vision",
        classmethod(lambda cls, shots: insp),
    )

    report = ael.AutonomousE2ELoop.run(str(tmp_path), round_index=1, session_start_time=None, changed_files=[])
    data = report.to_dict()

    assert data["vision_inspected_count"] == 2
    assert data["vision_failed_count"] == 1
    assert data["coverage"]["screenshots_captured"] == 2
    assert data["coverage"]["screenshots_vision_analyzed"] == 2
    assert data["coverage"]["blocking_defects"] == 1

    evidence = tmp_path / "reports" / "gate4" / "autonomous_e2e_report.json"
    persisted = json.loads(evidence.read_text(encoding="utf-8"))
    assert persisted["vision_failed_count"] == 1
    assert persisted["coverage"]["screenshots_vision_analyzed"] == 2


def test_format_block_lists_each_screenshot():
    insp = [
        _inspection("a.png", "PASS", 91),
        _inspection("b.png", "FAIL", 40, [
            {"severity": "MAJOR", "rule_id": "OVERFLOW", "location": "x", "issue": "y", "fix": "z"}
        ]),
    ]
    run = _run("pytest", 0, True)
    defects, gaps, verdict = ael.AutonomousE2ELoop.analyze(
        "/ws", [run], ["a.png", "b.png"], [], insp
    )
    report = ael.AutonomousE2EReport(
        workspace="/ws", verdict=verdict, ran_tests=True,
        test_runs=[run], vision_inspections=insp, defects=defects, gaps=gaps,
    )
    report.summary = ael.AutonomousE2ELoop._build_summary(report)

    block = ael.AutonomousE2ELoop.format_prompt_block(report)
    assert "전수 비전 분석" in block
    assert "`a.png`" in block and "`b.png`" in block
    assert "전수 검사 원칙" in block
