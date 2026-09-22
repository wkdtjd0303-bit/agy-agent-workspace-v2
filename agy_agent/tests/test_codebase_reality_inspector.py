#!/usr/bin/env python3
"""
Unit tests for CodebaseRealityInspector.
Validates:
1. Empty workspace inspection.
2. Android project inspection with Kotlin/Compose files and components.
3. Reading ROADMAP.md completed vs in-progress milestones.
4. Summary text generation with anti-duplication commander mandates.
"""

import os
import sys
import shutil
import tempfile
import unittest

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from codebase_reality_inspector import CodebaseRealityInspector, CodebaseReality


class TestCodebaseRealityInspector(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_reality_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_empty_workspace_inspection(self):
        reality = CodebaseRealityInspector.inspect(self.test_dir)
        self.assertTrue(reality.is_empty)
        self.assertIn("초기 상태", reality.summary_text)
        self.assertEqual(len(reality.source_files), 0)
        self.assertEqual(len(reality.implemented_components), 0)

    def test_android_codebase_inspection(self):
        # Create fake Android files
        app_dir = os.path.join(self.test_dir, "app", "src", "main", "java", "com", "example", "pdfapp")
        test_dir = os.path.join(self.test_dir, "app", "src", "test", "java", "com", "example", "pdfapp")
        os.makedirs(app_dir, exist_ok=True)
        os.makedirs(test_dir, exist_ok=True)

        # Build files
        with open(os.path.join(self.test_dir, "build.gradle.kts"), "w") as f:
            f.write("// root build file")
        with open(os.path.join(self.test_dir, "AndroidManifest.xml"), "w") as f:
            f.write("<manifest/>")

        # Source files
        with open(os.path.join(app_dir, "MainActivity.kt"), "w") as f:
            f.write("class MainActivity")
        with open(os.path.join(app_dir, "PdfViewerScreen.kt"), "w") as f:
            f.write("class PdfViewerScreen")
        with open(os.path.join(app_dir, "AnnotationCanvas.kt"), "w") as f:
            f.write("class AnnotationCanvas")

        # Test file
        with open(os.path.join(test_dir, "AnnotationCanvasTest.kt"), "w") as f:
            f.write("class AnnotationCanvasTest")

        # ROADMAP.md
        roadmap_content = (
            "# Roadmap\n\n"
            "## v1.0 — 코어 PDF 뷰어 ✅\n"
            "- [x] 렌더링 엔진\n\n"
            "## v2.0 — 실시간 주석 캔버스 🔄\n"
            "- [ ] 펜 드로잉\n"
        )
        with open(os.path.join(self.test_dir, "ROADMAP.md"), "w", encoding="utf-8") as f:
            f.write(roadmap_content)

        reality = CodebaseRealityInspector.inspect(self.test_dir)

        self.assertFalse(reality.is_empty)
        self.assertIn("Android", reality.tech_stack)
        self.assertIn("MainActivity.kt", reality.entrypoints[0])
        self.assertIn("PdfViewerScreen", reality.implemented_components)
        self.assertIn("AnnotationCanvas", reality.implemented_components)
        self.assertEqual(len(reality.source_files), 3)
        self.assertEqual(len(reality.test_files), 1)

        # Roadmap status
        self.assertEqual(len(reality.completed_milestones), 1)
        self.assertIn("v1.0 — 코어 PDF 뷰어", reality.completed_milestones[0])
        self.assertIn("v2.0 — 실시간 주석 캔버스", reality.current_milestone)

        # Summary text
        self.assertIn("PdfViewerScreen", reality.summary_text)
        self.assertIn("사령관 엄명", reality.summary_text)


if __name__ == "__main__":
    unittest.main()
