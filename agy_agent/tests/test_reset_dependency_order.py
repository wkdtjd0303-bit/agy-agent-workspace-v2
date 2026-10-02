import os
import sys
import tempfile

# unittest 실행 환경임을 표시해서 import 시 봇 기동을 막음
os.environ["UNITTEST_RUNNING"] = "1"

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import bot


def test_reload_helper_modules_orders_dependencies_first():
    """`from x import Y` 를 쓰는 모듈보다 의존 모듈이 먼저 리로드되어야 한다."""
    tmp = tempfile.mkdtemp(prefix="agy_reload_order_")
    for name in ("leaf", "mid", "root", "cycle_a", "cycle_b"):
        with open(os.path.join(tmp, f"{name}.py"), "w", encoding="utf-8") as f:
            f.write("")

    with open(os.path.join(tmp, "mid.py"), "w", encoding="utf-8") as f:
        f.write("from leaf import X\n")
    with open(os.path.join(tmp, "root.py"), "w", encoding="utf-8") as f:
        f.write("import mid\n")
    with open(os.path.join(tmp, "cycle_a.py"), "w", encoding="utf-8") as f:
        f.write("from cycle_b import X\n")
    with open(os.path.join(tmp, "cycle_b.py"), "w", encoding="utf-8") as f:
        f.write("from cycle_a import X\n")

    ordered = bot._order_modules_for_reload(
        ["root", "mid", "leaf", "cycle_a", "cycle_b"], tmp
    )

    assert ordered.index("leaf") < ordered.index("mid") < ordered.index("root")
    # 순환 import 도 누락 없이 포함되어야 한다 (폴백).
    assert set(ordered) == {"root", "mid", "leaf", "cycle_a", "cycle_b"}


def test_reset_reflects_freshly_reloaded_dependency_classes():
    """!reset 핫리로드 후 importer 가 최신 dependency 클래스 객체를 바라봐야 한다.

    (버그) importer 를 dependency 보다 먼저 리로드하면 `from x import Y` 바인딩이
    옛 클래스 객체로 남아, dependency 를 수정해도 !reset 후 반영되지 않았다.
    """
    import evidence_validator
    import quality_state_manager
    import trio_agent_runner
    import feedback_classifier
    import commander

    bot.reload_helper_modules()

    assert trio_agent_runner.EvidenceValidator is evidence_validator.EvidenceValidator
    assert trio_agent_runner.QualityStateManager is quality_state_manager.QualityStateManager
    assert commander.FeedbackIntentClassifier is feedback_classifier.FeedbackIntentClassifier
