#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_discord_screenshot_gallery.py

!mode 3 스크린샷 디스코드 공유 기능의 회귀 테스트.

검증 항목:
1. 후보 수집: 대표 디렉터리 스캔, .references 제외, diff 이미지 제외, 최신순 정렬.
2. 선택 필터: 최소 용량 미달/초과 제외, sha256 중복 제거, already_sent 재전송 방지,
   사용자 레퍼런스 hash/dHash 제외.
3. 시각 무결성: 빈/검은 화면은 제외.
4. 전송: 검증 통과분만 discord.File로 첨부되고 already_sent가 갱신됨.
"""

import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from PIL import Image  # noqa: E402

from discord_screenshot_gallery import (  # noqa: E402
    collect_candidate_screenshots,
    count_stale_screenshots,
    file_sha256,
    select_gallery_screenshots,
    send_screenshot_gallery,
)


def _make_png(path: str, color=(255, 0, 0), size=(160, 160)):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = Image.new("RGB", size, color)
    # non-blank: draw a few contrasting blocks so validate_image_content passes
    for i in range(0, size[0], 20):
        for j in range(0, size[1], 20):
            if (i // 20 + j // 20) % 2 == 0:
                for x in range(i, min(i + 10, size[0])):
                    for y in range(j, min(j + 10, size[1])):
                        img.putpixel((x, y), (10, 200, 240))
    img.save(path)


def _make_noisy_png(path: str, size=(420, 320), seed: int = 0):
    """노이즈가 섞인 PNG — 실제 스크린샷처럼 >1KB이며 비주얼 무결성 검사도 통과한다."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rng = random.Random(seed)
    img = Image.new("RGB", size)
    for y in range(size[1]):
        for x in range(0, size[0], 3):
            v = rng.randint(0, 255)
            for dx in range(min(3, size[0] - x)):
                img.putpixel((x + dx, y), (v, (v * 7) % 256, (v * 13) % 256))
    img.save(path)


class TestCollectCandidates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gallery_collect_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_collects_from_standard_dirs_and_filters(self):
        gate5 = os.path.join(self.tmp, "reports", "gate5", "screenshots")
        _make_png(os.path.join(gate5, "home.png"))
        _make_png(os.path.join(gate5, "before_after_diff.png"))  # diff → 제외
        _make_png(os.path.join(gate5, "00_preview.png"))  # 00_ → 제외
        refs = os.path.join(self.tmp, ".references", "screenshots")
        _make_png(os.path.join(refs, "user_ref.png"))  # .references → 제외

        found = collect_candidate_screenshots(self.tmp)
        names = [os.path.basename(p) for p in found]
        self.assertIn("home.png", names)
        self.assertNotIn("before_after_diff.png", names)
        self.assertNotIn("00_preview.png", names)
        self.assertNotIn("user_ref.png", names)

    def test_extra_candidates_are_included(self):
        extra = os.path.join(self.tmp, "explicit.png")
        _make_png(extra)
        found = collect_candidate_screenshots(self.tmp, extra=[extra])
        self.assertIn(os.path.abspath(extra), found)


class TestSelectGallery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gallery_select_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _p(self, name):
        return os.path.join(self.tmp, name)

    def test_dedup_by_hash(self):
        p1 = self._p("a.png")
        _make_png(p1, color=(255, 0, 0))
        # identical bytes → same hash
        p2 = self._p("a_copy.png")
        import shutil
        shutil.copyfile(p1, p2)
        selected = select_gallery_screenshots([p1, p2], validate_image=False)
        self.assertEqual(len(selected), 1)

    def test_already_sent_skipped(self):
        p1 = self._p("a.png")
        _make_png(p1)
        sent = {file_sha256(p1)}
        self.assertEqual(select_gallery_screenshots([p1], already_sent=sent, validate_image=False), [])

    def test_excluded_reference_hash_skipped(self):
        p1 = self._p("a.png")
        _make_png(p1)
        excluded = {file_sha256(p1)}
        self.assertEqual(
            select_gallery_screenshots([p1], excluded_hashes=excluded, validate_image=False), []
        )

    def test_min_bytes_filters_tiny(self):
        p1 = self._p("tiny.png")
        _make_png(p1)
        self.assertEqual(select_gallery_screenshots([p1], min_bytes=10_000_000, validate_image=False), [])

    def test_blank_image_rejected_by_validation(self):
        p_blank = self._p("blank.png")
        os.makedirs(self.tmp, exist_ok=True)
        Image.new("RGB", (200, 200), (255, 255, 255)).save(p_blank)
        self.assertEqual(select_gallery_screenshots([p_blank], validate_image=True), [])

    def test_valid_image_passes(self):
        p1 = self._p("ok.png")
        _make_png(p1)
        out = select_gallery_screenshots([p1], validate_image=True)
        self.assertEqual(out, [p1])

    def test_max_files_limit(self):
        paths = []
        for i in range(6):
            p = self._p(f"s{i}.png")
            _make_png(p, color=(i * 30 % 255, 100, 50))
            paths.append(p)
        out = select_gallery_screenshots(paths, max_files=3, validate_image=False)
        self.assertEqual(len(out), 3)


class TestSendGallery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gallery_send_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_send_attaches_and_updates_sent(self):
        p1 = os.path.join(self.tmp, "home.png")
        _make_png(p1)
        sent = set()

        with mock.patch(
            "discord_utils.safe_send_discord_message", new=mock.AsyncMock()
        ) as mocked:
            count = await send_screenshot_gallery(
                channel=object(), candidates=[p1], already_sent=sent
            )

        self.assertEqual(count, 1)
        mocked.assert_awaited_once()
        # files kwarg must carry exactly one attachment
        _args, kwargs = mocked.call_args
        self.assertEqual(len(kwargs.get("files") or []), 1)
        self.assertIn(file_sha256(p1), sent)

    async def test_send_returns_zero_when_nothing_valid(self):
        with mock.patch(
            "discord_utils.safe_send_discord_message", new=mock.AsyncMock()
        ) as mocked:
            count = await send_screenshot_gallery(channel=object(), candidates=[])
        self.assertEqual(count, 0)
        mocked.assert_not_awaited()


class TestRoundFreshness(unittest.TestCase):
    """REJECTED → 재작업 라운드에서 '이전 라운드 스크린샷 재사용'이 차단되는지 검증."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gallery_fresh_")
        self.gate5 = os.path.join(self.tmp, "reports", "gate5", "screenshots")
        import time
        self.now = time.time()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _old(self):
        p = os.path.join(self.gate5, "round1_screen.png")
        _make_png(p, color=(200, 30, 30))
        os.utime(p, (self.now - 600, self.now - 600))
        return p

    def _new(self):
        p = os.path.join(self.gate5, "round2_screen.png")
        _make_png(p, color=(30, 200, 90))
        os.utime(p, (self.now, self.now))
        return p

    def test_collect_filters_previous_round_files(self):
        old, new = self._old(), self._new()
        found = collect_candidate_screenshots(self.tmp, min_mtime=self.now - 10)
        self.assertIn(os.path.abspath(new), found)
        self.assertNotIn(os.path.abspath(old), found)

    def test_select_filters_previous_round_files(self):
        old, new = self._old(), self._new()
        sel = select_gallery_screenshots([old, new], min_mtime=self.now - 10, validate_image=False)
        self.assertEqual(sel, [new])

    def test_already_sent_dedup_still_works_without_time_filter(self):
        old, new = self._old(), self._new()
        sent = {file_sha256(old)}
        sel = select_gallery_screenshots([old, new], already_sent=sent, validate_image=False)
        self.assertEqual(sel, [new])

    def test_count_stale_screenshots(self):
        self._old()
        self._new()
        self.assertEqual(count_stale_screenshots(self.tmp, self.now - 10), 1)


class TestSendGalleryFreshness(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gallery_fresh_send_")
        import time
        self.now = time.time()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_only_this_round_screenshot_is_shared(self):
        old = os.path.join(self.tmp, "round1.png")
        _make_png(old, color=(180, 40, 40))
        os.utime(old, (self.now - 900, self.now - 900))
        new = os.path.join(self.tmp, "round2.png")
        _make_png(new, color=(40, 180, 90))
        os.utime(new, (self.now, self.now))

        sent = set()
        with mock.patch("discord_utils.safe_send_discord_message", new=mock.AsyncMock()) as m:
            count = await send_screenshot_gallery(
                channel=object(), candidates=[old, new],
                already_sent=sent, min_mtime=self.now - 10,
            )
        self.assertEqual(count, 1)
        _a, kw = m.call_args
        self.assertEqual(len(kw.get("files") or []), 1)
        self.assertIn(file_sha256(new), sent)
        self.assertNotIn(file_sha256(old), sent)

    async def test_nothing_shared_when_no_fresh_capture(self):
        old = os.path.join(self.tmp, "round1.png")
        _make_png(old)
        os.utime(old, (self.now - 900, self.now - 900))
        with mock.patch("discord_utils.safe_send_discord_message", new=mock.AsyncMock()) as m:
            count = await send_screenshot_gallery(
                channel=object(), candidates=[old], min_mtime=self.now - 10
            )
        self.assertEqual(count, 0)
        m.assert_not_awaited()


class TestDownstreamFreshnessGuards(unittest.TestCase):
    """VCL 실측도 라운드 경계를 존중해야 한다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gallery_downstream_")
        import time
        self.now = time.time()
        gate5 = os.path.join(self.tmp, "reports", "gate5", "screenshots")
        self.old = os.path.join(gate5, "r1.png")
        _make_png(self.old, color=(200, 50, 50))
        os.utime(self.old, (self.now - 800, self.now - 800))
        self.new = os.path.join(gate5, "r2.png")
        _make_png(self.new, color=(50, 200, 50))
        os.utime(self.new, (self.now, self.now))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_visual_craft_find_screenshots_respects_round_start(self):
        from visual_craft_loop import find_screenshots
        found = find_screenshots(self.tmp, self.now - 10)
        self.assertIn(self.new, found)
        self.assertNotIn(self.old, found)

    def test_visual_craft_without_time_filter_returns_both(self):
        from visual_craft_loop import find_screenshots
        found = find_screenshots(self.tmp, None)
        self.assertIn(self.old, found)
        self.assertIn(self.new, found)


class TestVisionAnalyzerFreshness(unittest.IsolatedAsyncioTestCase):
    async def test_inspect_for_qa_ignores_previous_round_screenshot(self):
        from vision_attachment_analyzer import VisionAttachmentAnalyzer
        import time
        tmp = tempfile.mkdtemp(prefix="vision_fresh_")
        try:
            now = time.time()
            gate5 = os.path.join(tmp, "reports", "gate5", "screenshots")
            old = os.path.join(gate5, "r1.png")
            _make_noisy_png(old, seed=1)
            os.utime(old, (now - 800, now - 800))
            new = os.path.join(gate5, "r2.png")
            _make_noisy_png(new, seed=2)
            os.utime(new, (now, now))

            with mock.patch.object(
                VisionAttachmentAnalyzer, "analyze_image_file",
                new=mock.AsyncMock(return_value={"success": True, "analysis": "ok"}),
            ):
                res = await VisionAttachmentAnalyzer.inspect_workspace_screenshots_for_qa(
                    tmp, min_mtime=now - 10
                )
            self.assertIsNotNone(res)
            self.assertEqual(res.get("screenshot_path"), new)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestRunnerWiring(unittest.TestCase):
    """스크린샷 갤러리가 실제 !mode 3 러너에 배선되어 있는지 고정한다."""

    def test_runner_imports_and_sends_gallery(self):
        runner = WORKSPACE_ROOT / "trio_agent_runner.py"
        code = runner.read_text(encoding="utf-8")
        self.assertIn("from discord_screenshot_gallery import", code)
        self.assertIn("collect_candidate_screenshots(", code)
        self.assertIn("send_screenshot_gallery(", code)
        # 라운드 간 중복 전송 방지 상태
        self.assertIn("_vcl_gallery_sent_hashes", code)

    def test_runner_binds_every_visual_consumer_to_round_start(self):
        """VCL·Vision 검사·갤러리 3경로 모두 라운드 시작 시각으로 묶여야 한다."""
        code = (WORKSPACE_ROOT / "trio_agent_runner.py").read_text(encoding="utf-8")
        self.assertIn("_round_start_ts = time.time()", code)
        # 1) VCL
        self.assertIn("session_start_time=_round_start_ts", code)
        # 2) Gemini Vision 사전 검사
        self.assertIn("min_mtime=_round_start_ts", code)
        # 3) 신선도 경고
        self.assertIn("count_stale_screenshots(", code)


if __name__ == "__main__":
    unittest.main()
