import os
import sys
import shutil
import tempfile
import unittest
from PIL import Image, ImageDraw

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from session_manager import ChannelSession, SessionManager
from discord_utils import (
    extract_local_attachments,
    prepare_message_payload,
    EXCLUDED_DIR_NAMES,
)
from evidence_validator import EvidenceValidator
from trio_agent_runner import TrioAgentRunner


def create_test_image(path: str, color_pattern: str = "gradient", fmt: str = "PNG"):
    """Generate deterministic test images with rich pixel variation."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    width, height = 120, 120
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    if color_pattern == "gradient":
        for y in range(height):
            for x in range(width):
                r = (x * 2) % 256
                g = (y * 2) % 256
                b = (x + y) % 256
                img.putpixel((x, y), (r, g, b))
    elif color_pattern == "boxes":
        draw.rectangle([10, 10, 50, 50], fill=(255, 0, 0))
        draw.rectangle([60, 10, 110, 50], fill=(0, 255, 0))
        draw.rectangle([10, 60, 50, 110], fill=(0, 0, 255))
        draw.rectangle([60, 60, 110, 110], fill=(255, 255, 0))
    elif color_pattern == "circles":
        draw.ellipse([15, 15, 65, 65], fill=(128, 0, 128))
        draw.ellipse([50, 50, 105, 105], fill=(0, 128, 128))
    elif color_pattern == "distinct_other":
        draw.rectangle([0, 0, width, height], fill=(30, 40, 50))
        draw.line([(0, 0), (width, height)], fill=(200, 200, 200), width=4)
        draw.line([(0, height), (width, 0)], fill=(100, 250, 100), width=4)

    img.save(path, format=fmt)
    return path


class TestImageProvenanceAndAntiPlagiarism(unittest.TestCase):
    """
    Comprehensive test suite verifying physical provenance, cryptographic SHA-256
    and perceptual dHash anti-plagiarism defenses (Rules 6 & 6b), and inter-round
    stale deliverable reuse detection (Rule 7).
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_anti_plagiarism_")
        self.workspace = os.path.join(self.test_dir, "workspace")
        os.makedirs(self.workspace, exist_ok=True)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    # =========================================================================
    # 1. Session Manager Isolation & Hash Tracking
    # =========================================================================
    def test_session_manager_reference_registration_and_hashes(self):
        session = ChannelSession(channel_id=99999, workspace=self.workspace)
        self.assertEqual(session.user_reference_images, [])
        self.assertEqual(session.user_reference_hashes_set, set())
        self.assertEqual(session.round_deliverable_image_hashes, {})

        # Register reference image 1
        img1_path = os.path.join(self.workspace, ".references", "user_inputs", "mock1.png")
        create_test_image(img1_path, "boxes")
        sha1 = EvidenceValidator.compute_file_sha256(img1_path)
        dhash1 = EvidenceValidator.compute_image_dhash(img1_path)

        session.register_user_reference_image(
            filename="mock1.png",
            file_path=img1_path,
            sha256=sha1,
            dhash=dhash1,
        )

        self.assertEqual(len(session.user_reference_images), 1)
        self.assertIn(sha1, session.user_reference_hashes_set)

        # Duplicate registration should be safely ignored
        session.register_user_reference_image(
            filename="mock1.png",
            file_path=img1_path,
            sha256=sha1,
            dhash=dhash1,
        )
        self.assertEqual(len(session.user_reference_images), 1)

        # Register reference image 2
        img2_path = os.path.join(self.workspace, ".references", "user_inputs", "mock2.png")
        create_test_image(img2_path, "circles")
        sha2 = EvidenceValidator.compute_file_sha256(img2_path)
        dhash2 = EvidenceValidator.compute_image_dhash(img2_path)
        session.register_user_reference_image(
            filename="mock2.png",
            file_path=img2_path,
            sha256=sha2,
            dhash=dhash2,
        )
        self.assertEqual(len(session.user_reference_images), 2)
        self.assertEqual(session.user_reference_hashes_set, {sha1, sha2})
        self.assertEqual(session.user_reference_dhashes_set, {dhash1, dhash2})

        # Clear references
        session.clear_user_reference_images()
        self.assertEqual(len(session.user_reference_images), 0)
        self.assertEqual(len(session.user_reference_hashes_set), 0)
        self.assertEqual(len(session.user_reference_dhashes_set), 0)

        # Re-register and test reset_conversation clearing
        session.register_user_reference_image(filename="mock1.png", file_path=img1_path, sha256=sha1, dhash=dhash1)
        self.assertEqual(len(session.user_reference_images), 1)
        session.reset_conversation()
        self.assertEqual(len(session.user_reference_images), 0)
        self.assertEqual(len(session.user_reference_hashes_set), 0)
        self.assertEqual(len(session.user_reference_dhashes_set), 0)

        # Test SessionManager delegation
        mgr = SessionManager(clear_on_startup=True)
        mgr_sess = mgr.get_session(99999)
        mgr_sess.register_user_reference_image(filename="mock1.png", file_path=img1_path, sha256=sha1, dhash=dhash1)
        self.assertEqual(len(mgr_sess.user_reference_images), 1)
        mgr.clear_user_reference_images(99999)
        self.assertEqual(len(mgr_sess.user_reference_images), 0)

        # Per-round deliverable image hashes tracking
        session.round_deliverable_image_hashes[1] = {"reports/gate5/screen.png": {"sha256": "hash_r1"}}
        session.round_deliverable_image_hashes[2] = {"reports/gate5/screen.png": {"sha256": "hash_r2"}}
        self.assertEqual(len(session.round_deliverable_image_hashes), 2)
        self.assertEqual(session.round_deliverable_image_hashes[1]["reports/gate5/screen.png"]["sha256"], "hash_r1")

    def test_session_manager_cross_message_reference_accumulation(self):
        """Verifies that user reference images accumulate across messages and rounds without premature clearing."""
        session = ChannelSession(channel_id=88888, workspace=self.workspace)

        # Message 1 attachments
        img1 = os.path.join(self.workspace, ".references", "user_inputs", "turn1.png")
        create_test_image(img1, "boxes")
        sha1 = EvidenceValidator.compute_file_sha256(img1)
        session.register_user_reference_image("turn1.png", img1, sha1)

        # Message 2 attachments arrive (clear_user_reference_images is NOT called)
        img2 = os.path.join(self.workspace, ".references", "user_inputs", "turn2.png")
        create_test_image(img2, "circles")
        sha2 = EvidenceValidator.compute_file_sha256(img2)
        session.register_user_reference_image("turn2.png", img2, sha2)

        # Both reference images must be retained
        self.assertEqual(len(session.user_reference_images), 2)
        self.assertEqual(session.user_reference_hashes_set, {sha1, sha2})

        # Plagiarizing turn1 in Round 2 must still be caught
        fake_deliverable = os.path.join(self.workspace, "output", "screen_from_turn1.png")
        os.makedirs(os.path.dirname(fake_deliverable), exist_ok=True)
        shutil.copyfile(img1, fake_deliverable)

        is_plagiarized, violations = EvidenceValidator.detect_reference_image_plagiarism(
            self.workspace, session.user_reference_images
        )
        self.assertTrue(is_plagiarized)
        self.assertTrue(any("screen_from_turn1.png" in v for v in violations))

    # =========================================================================
    # 2. Discord Transport Layer Exclusions
    # =========================================================================
    def test_discord_utils_isolation_and_exclusion(self):
        self.assertIn(".references", EXCLUDED_DIR_NAMES)
        self.assertNotIn("user_inputs", EXCLUDED_DIR_NAMES)

        # Create files
        ref_user_file = os.path.join(self.workspace, ".references", "user_inputs", "ref_ui.png")
        create_test_image(ref_user_file, "boxes")
        ref_sha = EvidenceValidator.compute_file_sha256(ref_user_file)

        plagiarized_file = os.path.join(self.workspace, "reports", "gate5", "screenshots", "deliverable.png")
        os.makedirs(os.path.dirname(plagiarized_file), exist_ok=True)
        shutil.copyfile(ref_user_file, plagiarized_file)

        valid_file = os.path.join(self.workspace, "reports", "gate5", "screenshots", "genuine.png")
        create_test_image(valid_file, "distinct_other")

        text = (
            f"Here are the screenshots:\n"
            f"1. User reference: `{ref_user_file}`\n"
            f"2. Plagiarized: `{plagiarized_file}`\n"
            f"3. Genuine: `{valid_file}`"
        )

        # 1. Without excluded_hashes: path-based filter blocks .references
        att_no_hashes = extract_local_attachments(text, workspace_root=self.workspace)
        att_no_hashes_paths = [os.path.normpath(p) for p in att_no_hashes]
        self.assertNotIn(os.path.normpath(ref_user_file), att_no_hashes_paths)

        # 2. With excluded_hashes: both .references AND matching sha256 are blocked
        att_with_hashes = extract_local_attachments(
            text, workspace_root=self.workspace, excluded_hashes={ref_sha}
        )
        att_with_hashes_paths = [os.path.normpath(p) for p in att_with_hashes]
        self.assertNotIn(os.path.normpath(ref_user_file), att_with_hashes_paths)
        self.assertNotIn(os.path.normpath(plagiarized_file), att_with_hashes_paths)
        self.assertIn(os.path.normpath(valid_file), att_with_hashes_paths)

        # 3. prepare_message_payload respects excluded_hashes
        chunks, files = prepare_message_payload(
            text, workspace_root=self.workspace, excluded_hashes={ref_sha}
        )
        file_names = [f.filename for f in files]
        for f in files:
            if hasattr(f, "close"):
                f.close()
            elif hasattr(f, "fp") and hasattr(f.fp, "close"):
                f.fp.close()
        self.assertNotIn("ref_ui.png", file_names)
        self.assertNotIn("deliverable.png", file_names)
        self.assertIn("genuine.png", file_names)

    def test_discord_utils_dhash_transport_filtering(self):
        """Verifies that re-compressed or format-converted user reference images (perceptual clones) are filtered at the Discord transport layer via excluded_dhashes."""
        # 1. Create a user reference image (PNG)
        ref_path = os.path.join(self.workspace, "user_ref.png")
        create_test_image(ref_path, "boxes", fmt="PNG")
        ref_sha = EvidenceValidator.compute_file_sha256(ref_path)
        ref_dhash = EvidenceValidator.compute_image_dhash(ref_path)
        self.assertIsNotNone(ref_dhash)

        # 2. Coder generates a deliverable by converting the reference image to JPEG (compression / format shift)
        clone_path = os.path.join(self.workspace, "reports", "gate5", "clone_ui.jpg")
        os.makedirs(os.path.dirname(clone_path), exist_ok=True)
        with Image.open(ref_path) as img:
            img.convert("RGB").save(clone_path, format="JPEG", quality=85)

        clone_sha = EvidenceValidator.compute_file_sha256(clone_path)
        clone_dhash = EvidenceValidator.compute_image_dhash(clone_path)

        # Binary hash is completely different due to format change and lossy compression!
        self.assertNotEqual(ref_sha, clone_sha)
        # Perceptual hash distance must be <= 3 (same visual content)
        self.assertLessEqual(EvidenceValidator.hamming_distance(ref_dhash, clone_dhash), 3)

        # 3. Create a genuine new deliverable (different visual content)
        genuine_path = os.path.join(self.workspace, "reports", "gate5", "genuine_dashboard.png")
        create_test_image(genuine_path, "distinct_other", fmt="PNG")

        text = f"Deliverables:\n- Clone: `{clone_path}`\n- Genuine: `{genuine_path}`"

        # If only excluded_hashes is provided, clone_path would slip through SHA-256 check
        att_sha_only = extract_local_attachments(text, workspace_root=self.workspace, excluded_hashes={ref_sha})
        self.assertIn(os.path.normpath(clone_path), [os.path.normpath(p) for p in att_sha_only])

        # When excluded_dhashes is provided, clone_path is intercepted and filtered out!
        att_with_dhash = extract_local_attachments(
            text, workspace_root=self.workspace, excluded_hashes={ref_sha}, excluded_dhashes={ref_dhash}
        )
        norm_att = [os.path.normpath(p) for p in att_with_dhash]
        self.assertNotIn(os.path.normpath(clone_path), norm_att, "Perceptual clone must be blocked by excluded_dhashes")
        self.assertIn(os.path.normpath(genuine_path), norm_att, "Genuine deliverable must be retained")

        # prepare_message_payload also drops the clone
        chunks, files = prepare_message_payload(
            text, workspace_root=self.workspace, excluded_hashes={ref_sha}, excluded_dhashes={ref_dhash}
        )
        filenames = [f.filename for f in files]
        for f in files:
            if hasattr(f, "close"):
                f.close()
            elif hasattr(f, "fp") and hasattr(f.fp, "close"):
                f.fp.close()
        self.assertNotIn("clone_ui.jpg", filenames)
        self.assertIn("genuine_dashboard.png", filenames)

    def test_discord_utils_user_inputs_folder_not_false_positive(self):
        """Verifies that legitimate project folders/files containing 'user_inputs' or 'references' are NOT falsely excluded, while exact isolation folders are strictly excluded."""
        legit_dir = os.path.join(self.workspace, "user_inputs")
        os.makedirs(legit_dir, exist_ok=True)
        legit_file = os.path.join(legit_dir, "customer_survey.png")
        create_test_image(legit_file, "circles")

        legit_ref_dir = os.path.join(self.workspace, "references")
        os.makedirs(legit_ref_dir, exist_ok=True)
        legit_ref_file = os.path.join(legit_ref_dir, "architecture.png")
        create_test_image(legit_ref_file, "distinct_other")

        legit_file2 = os.path.join(self.workspace, "user_inputs_summary.png")
        create_test_image(legit_file2, "boxes")

        # Bad isolated folders
        isolated_ref = os.path.join(self.workspace, ".references", "user_inputs", "isolated.png")
        create_test_image(isolated_ref, "boxes")

        isolated_att = os.path.join(self.workspace, "agy_discord_att_123", "temp.png")
        create_test_image(isolated_att, "boxes")

        text = (
            f"Review files:\n"
            f"1. `{legit_file}`\n"
            f"2. `{legit_ref_file}`\n"
            f"3. `{legit_file2}`\n"
            f"4. `{isolated_ref}`\n"
            f"5. `{isolated_att}`\n"
        )
        attachments = extract_local_attachments(text, workspace_root=self.workspace)
        norm_attachments = [os.path.normpath(p) for p in attachments]

        # Legitimate paths are kept
        self.assertIn(os.path.normpath(legit_file), norm_attachments)
        self.assertIn(os.path.normpath(legit_ref_file), norm_attachments)
        self.assertIn(os.path.normpath(legit_file2), norm_attachments)

        # Isolated paths are strictly excluded
        self.assertNotIn(os.path.normpath(isolated_ref), norm_attachments)
        self.assertNotIn(os.path.normpath(isolated_att), norm_attachments)

    # =========================================================================
    # 3. Cryptographic SHA-256 Plagiarism Detection (Rule 6)
    # =========================================================================
    def test_evidence_validator_sha256_plagiarism_detection(self):
        user_ref_path = os.path.join(self.workspace, ".references", "user_inputs", "input.png")
        create_test_image(user_ref_path, "gradient")
        user_sha = EvidenceValidator.compute_file_sha256(user_ref_path)
        user_dhash = EvidenceValidator.compute_image_dhash(user_ref_path)

        user_reference_images = [
            {
                "file_path": user_ref_path,
                "filename": "input.png",
                "sha256": user_sha,
                "dhash": user_dhash,
            }
        ]

        # Scenario A: Plagiarized copy placed into deliverables directory
        deliverable_path = os.path.join(self.workspace, "reports", "gate5", "screenshots", "screen1.png")
        os.makedirs(os.path.dirname(deliverable_path), exist_ok=True)
        shutil.copyfile(user_ref_path, deliverable_path)

        is_plagiarized, violations = EvidenceValidator.detect_reference_image_plagiarism(
            self.workspace, reference_images=user_reference_images
        )
        self.assertTrue(is_plagiarized)
        self.assertEqual(len(violations), 1)
        self.assertIn("Anti-Mocking Rule 6", violations[0])
        self.assertIn("screen1.png", violations[0])
        self.assertIn("input.png", violations[0])

        # Verify gate validation and anti-mocking engine fail hard
        clean, all_violations, _ = EvidenceValidator.verify_anti_mocking_and_authenticity(
            self.workspace, user_reference_images=user_reference_images
        )
        self.assertFalse(clean)
        self.assertTrue(any("Rule 6" in v for v in all_violations))

        gate5_pass, gate5_msg, _ = EvidenceValidator.validate_gate(
            self.workspace, 5, user_reference_images=user_reference_images
        )
        self.assertFalse(gate5_pass)
        self.assertIn("Rule 6", gate5_msg)

    # =========================================================================
    # 4. Perceptual dHash Plagiarism Detection (Rule 6b)
    # =========================================================================
    def test_evidence_validator_perceptual_dhash_plagiarism_detection(self):
        user_ref_path = os.path.join(self.workspace, ".references", "user_inputs", "original.png")
        create_test_image(user_ref_path, "boxes")
        user_sha = EvidenceValidator.compute_file_sha256(user_ref_path)
        user_dhash = EvidenceValidator.compute_image_dhash(user_ref_path)

        user_reference_images = [
            {
                "file_path": user_ref_path,
                "filename": "original.png",
                "sha256": user_sha,
                "dhash": user_dhash,
            }
        ]

        # Scenario B: Coder converts the PNG to JPEG with high quality
        # This changes SHA-256 completely, but perceptual dHash remains identical or <= 3 distance
        compressed_deliverable = os.path.join(self.workspace, "output", "converted_screen.jpg")
        os.makedirs(os.path.dirname(compressed_deliverable), exist_ok=True)
        with Image.open(user_ref_path) as im:
            im.convert("RGB").save(compressed_deliverable, format="JPEG", quality=95)

        comp_sha = EvidenceValidator.compute_file_sha256(compressed_deliverable)
        comp_dhash = EvidenceValidator.compute_image_dhash(compressed_deliverable)

        # Verify SHA-256 is indeed different but dHash distance is <= 3
        self.assertNotEqual(user_sha, comp_sha)
        dist = EvidenceValidator.hamming_distance(user_dhash, comp_dhash)
        self.assertLessEqual(dist, 3)

        is_plagiarized, violations = EvidenceValidator.detect_reference_image_plagiarism(
            self.workspace, reference_images=user_reference_images
        )
        self.assertTrue(is_plagiarized)
        self.assertTrue(any("Anti-Mocking Rule 6b" in v for v in violations))
        self.assertTrue(any("converted_screen.jpg" in v for v in violations))

        clean, all_violations, _ = EvidenceValidator.verify_anti_mocking_and_authenticity(
            self.workspace, user_reference_images=user_reference_images
        )
        self.assertFalse(clean)
        self.assertTrue(any("Rule 6b" in v for v in all_violations))

    def test_pillow_resampling_backward_compatibility(self):
        """Verifies compute_image_dhash works and filter resolution falls back to Image.LANCZOS."""
        test_img_path = os.path.join(self.workspace, "reports", "gate5", "screenshots", "compat.png")
        create_test_image(test_img_path, "boxes")

        dhash = EvidenceValidator.compute_image_dhash(test_img_path)
        self.assertIsNotNone(dhash)
        self.assertEqual(len(dhash), 16)  # 64-bit hex is 16 hex chars

        # Test legacy Pillow fallback resolution logic when Resampling is absent
        resampling = None
        lanczos = getattr(resampling, "LANCZOS", None) if resampling else None
        if lanczos is None:
            lanczos = getattr(Image, "LANCZOS", 1)
        self.assertEqual(lanczos, getattr(Image, "LANCZOS", 1))

    # =========================================================================
    # 5. Genuine Distinct Deliverable Passes
    # =========================================================================
    def test_evidence_validator_genuine_deliverable_passes(self):
        user_ref_path = os.path.join(self.workspace, ".references", "user_inputs", "ref.png")
        create_test_image(user_ref_path, "circles")
        user_reference_images = [
            {
                "file_path": user_ref_path,
                "filename": "ref.png",
                "sha256": EvidenceValidator.compute_file_sha256(user_ref_path),
                "dhash": EvidenceValidator.compute_image_dhash(user_ref_path),
            }
        ]

        # Coder created a genuinely distinct UI
        genuine_path = os.path.join(self.workspace, "reports", "gate5", "screenshots", "new_screen.png")
        create_test_image(genuine_path, "distinct_other")

        is_plagiarized, violations = EvidenceValidator.detect_reference_image_plagiarism(
            self.workspace, reference_images=user_reference_images
        )
        self.assertFalse(is_plagiarized)
        self.assertEqual(violations, [])

    # =========================================================================
    # 6. Inter-Round Deliverable Stale Repetition Detection (Rule 7)
    # =========================================================================
    def test_evidence_validator_stale_round_repetition_detection(self):
        screen1 = os.path.join(self.workspace, "reports", "gate5", "screenshots", "screen1.png")
        create_test_image(screen1, "gradient")

        # Round 1 collects current workspace deliverable hashes
        r1_hashes = EvidenceValidator.collect_workspace_image_hashes(self.workspace)
        self.assertIn("reports/gate5/screenshots/screen1.png", r1_hashes)

        # Scenario A: Round 2 runs, but Coder recycled the exact same screen1.png without modifying it
        is_stale, violations = EvidenceValidator.detect_stale_round_images(self.workspace, r1_hashes)
        self.assertTrue(is_stale)
        self.assertTrue(any("Anti-Mocking Rule 7" in v for v in violations))

        clean, all_violations, _ = EvidenceValidator.verify_anti_mocking_and_authenticity(
            self.workspace, previous_round_image_hashes=r1_hashes
        )
        self.assertFalse(clean)
        self.assertTrue(any("Rule 7" in v for v in all_violations))

        # Scenario B: Round 2 modifies screen1.png with new content
        create_test_image(screen1, "distinct_other")
        is_stale_updated, violations_updated = EvidenceValidator.detect_stale_round_images(
            self.workspace, r1_hashes
        )
        self.assertFalse(is_stale_updated)
        self.assertEqual(violations_updated, [])

        clean_updated, all_violations_updated, _ = EvidenceValidator.verify_anti_mocking_and_authenticity(
            self.workspace, previous_round_image_hashes=r1_hashes
        )
        self.assertTrue(clean_updated)
        self.assertFalse(any("Rule 7" in v for v in all_violations_updated))

    def test_evidence_validator_stale_round_partial_change_vs_total_stale(self):
        """Verifies multi-file deliverables: only 100% stale repeats trigger Rule 7; partial progress or additions pass."""
        img_a = os.path.join(self.workspace, "reports", "gate5", "screenshots", "imgA.png")
        img_b = os.path.join(self.workspace, "reports", "gate5", "screenshots", "imgB.png")
        create_test_image(img_a, "gradient")
        create_test_image(img_b, "boxes")

        # Collect Round 1 state
        r1_hashes = EvidenceValidator.collect_workspace_image_hashes(self.workspace)
        self.assertEqual(len(r1_hashes), 2)

        # Case 1: 100% stale repeat in Round 2
        is_stale_both, v_both = EvidenceValidator.detect_stale_round_images(self.workspace, r1_hashes)
        self.assertTrue(is_stale_both)
        self.assertTrue(any("Rule 7" in v for v in v_both))

        # Case 2: One image updated in Round 2
        create_test_image(img_a, "distinct_other")
        is_stale_partial, v_partial = EvidenceValidator.detect_stale_round_images(self.workspace, r1_hashes)
        self.assertFalse(is_stale_partial)
        self.assertEqual(v_partial, [])

        # Case 3: Revert imgA back to gradient (so both A and B match R1), but add new imgC
        create_test_image(img_a, "gradient")
        img_c = os.path.join(self.workspace, "reports", "gate5", "screenshots", "imgC.png")
        create_test_image(img_c, "circles")
        is_stale_new_add, v_new_add = EvidenceValidator.detect_stale_round_images(self.workspace, r1_hashes)
        self.assertFalse(is_stale_new_add)
        self.assertEqual(v_new_add, [])

        # Case 4: Non-UI / backend / bot task prompt correctly exempts from Rule 7
        is_stale_non_ui, v_non_ui = EvidenceValidator.detect_stale_round_images(
            self.workspace, r1_hashes, prompt="!reset 명령어 컨텍스트 초기화 및 최신 코드 반영 점검"
        )
        self.assertFalse(is_stale_non_ui)
        self.assertEqual(v_non_ui, [])

        # Case 5: Mode 2+ / QA2 targeted squad orchestration prompt correctly exempts from Rule 7
        is_stale_mode2_qa, v_mode2_qa = EvidenceValidator.detect_stale_round_images(
            self.workspace, r1_hashes, prompt="지금 mode 2+ 를 하면 qa2 solar 의 의견만 듣고 싶다. 지정 가능하도록 해줄래?"
        )
        self.assertFalse(is_stale_mode2_qa)
        self.assertEqual(v_mode2_qa, [])

    # =========================================================================
    # 7. TrioAgentRunner Programmatic Override Verification
    # =========================================================================
    def test_trio_agent_runner_programmatic_overrides(self):
        # 1. Verify QA approval detection helper
        self.assertTrue(TrioAgentRunner.is_qa_approved("[STATUS: APPROVED]\nAll tests passed!"))
        self.assertFalse(TrioAgentRunner.is_qa_approved("[STATUS: REJECTED]\nFound defects"))
        self.assertTrue(TrioAgentRunner.is_auditor_approved("[STATUS: AUDIT_PASSED]\nLooks great"))
        self.assertFalse(TrioAgentRunner.is_auditor_approved("[STATUS: AUDIT_FAILED]\nMissing features"))

        # 2. Simulate Senior QA Blind Approval Override
        qa_raw_output = "음~ 잘 만들었네! 아주 완벽해.\n[STATUS: APPROVED]\n합격입니다!"
        qa_approved = TrioAgentRunner.is_qa_approved(qa_raw_output)
        self.assertTrue(qa_approved)

        is_anti_mock_clean = False
        anti_mock_violations = ["🚨 [Anti-Mocking Rule 6] Plagiarized user reference image"]

        if qa_approved and not is_anti_mock_clean:
            qa_approved = False
            overridden_qa_output = (
                f"[STATUS: REJECTED]\n"
                f"🚨 **[품질 게이트 강제 반려 — Anti-Mocking / 레퍼런스 이미지 표절 적발]**\n"
                + "\n".join(f"• {v}" for v in anti_mock_violations)
            )

        self.assertFalse(qa_approved)
        self.assertIn("[STATUS: REJECTED]", overridden_qa_output)
        self.assertIn("품질 게이트 강제 반려", overridden_qa_output)
        self.assertIn("Rule 6", overridden_qa_output)

        # 3. Simulate Extended QA Squad Blind Approval Override
        ext_qa_name = "EdgeCaseQA"
        ext_raw_output = "[STATUS: APPROVED]\nPassed all boundary conditions."
        ext_passed = TrioAgentRunner.is_qa_approved(ext_raw_output)
        self.assertTrue(ext_passed)

        if ext_passed and not is_anti_mock_clean:
            ext_passed = False
            overridden_ext_output = (
                f"[STATUS: REJECTED]\n"
                f"🚨 **[{ext_qa_name} 품질 게이트 강제 반려 — Anti-Mocking / 레퍼런스 이미지 표절 적발]**\n"
                + "\n".join(f"• {v}" for v in anti_mock_violations)
            )

        self.assertFalse(ext_passed)
        self.assertIn("[STATUS: REJECTED]", overridden_ext_output)
        self.assertIn(ext_qa_name, overridden_ext_output)

        # 4. Simulate Auditor Blind Approval Override
        auditor_raw_output = "[STATUS: AUDIT_PASSED] [VALUE: HIGH] [ACTION: PROCEED]\nEverything is wonderful!"
        auditor_passed = TrioAgentRunner.is_auditor_approved(auditor_raw_output)
        self.assertTrue(auditor_passed)

        if auditor_passed and not is_anti_mock_clean:
            auditor_passed = False
            overridden_auditor_output = (
                f"[STATUS: AUDIT_FAILED] [ACTION: STOP]\n"
                f"🚨 **[감사관 최종 감사 거부 — Anti-Mocking / 레퍼런스 이미지 표절 적발]**\n"
                + "\n".join(f"• {v}" for v in anti_mock_violations)
            )

        self.assertFalse(auditor_passed)
        self.assertIn("[STATUS: AUDIT_FAILED] [ACTION: STOP]", overridden_auditor_output)
        self.assertIn("감사관 최종 감사 거부", overridden_auditor_output)

        # 5. Ground Truth Verification bypass blocked when is_anti_mock_clean is False
        gates_all_passed = True
        diff_has_changes = True
        bypass_allowed = gates_all_passed and is_anti_mock_clean and diff_has_changes
        self.assertFalse(bypass_allowed, "Ground Truth Verification bypass must be strictly blocked if anti-mocking violations exist")

    # =========================================================================
    # 8. Attachment Handler EvidenceValidator Resolution Resilience
    # =========================================================================
    def test_on_message_attachment_dhash_resolution_resilience(self):
        """Verifies that attachment image dhash calculation safely resolves EvidenceValidator without NameError."""
        test_img_path = os.path.join(self.workspace, "test_resilience.png")
        create_test_image(test_img_path, "gradient")

        # Simulate empty globals dictionary without EvidenceValidator
        isolated_globals = {}
        dhash = None
        try:
            ev_cls = isolated_globals.get("EvidenceValidator")
            if ev_cls is None:
                from evidence_validator import EvidenceValidator as ev_cls
            dhash = ev_cls.compute_image_dhash(test_img_path)
        except Exception:
            dhash = None

        self.assertIsNotNone(dhash)
        self.assertEqual(len(dhash), 16)


    # =========================================================================
    # 9. Script-Level Reference Extraction & Tracing Forgery (Rule 6c)
    # =========================================================================
    def test_evidence_validator_script_reference_tracing_detection(self):
        """
        Verifies that EvidenceValidator detects Coder writing asset generation scripts
        that open user reference images (via PIL/OpenCV) to trace contours or sample pixels (Rule 6c).
        """
        scripts_dir = os.path.join(self.workspace, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)

        # Register a user reference image
        ref_path = os.path.join(self.workspace, ".references", "user_inputs", "sample_bear.png")
        create_test_image(ref_path, "gradient")
        user_refs = [{
            "filename": "sample_bear.png",
            "path": ref_path,
            "sha256": EvidenceValidator.compute_file_sha256(ref_path),
            "dhash": EvidenceValidator.compute_image_dhash(ref_path)
        }]

        # Create a mock generation script that illicitly opens reference image and extracts masks
        cheating_script = os.path.join(scripts_dir, "reproduce_calendar_bear.py")
        with open(cheating_script, "w", encoding="utf-8") as f:
            f.write(
                'import numpy as np\n'
                'from PIL import Image\n'
                'REF_IMAGE_PATH = ".references/user_inputs/sample_bear.png"\n'
                'ref_img = Image.open(REF_IMAGE_PATH).convert("RGB")\n'
                'ref_arr = np.array(ref_img)\n'
                'fg = np.any(ref_arr < 245, axis=-1)\n'
                'out = Image.fromarray(fg.astype(np.uint8) * 255)\n'
                'out.save("output_bear.png")\n'
            )

        is_clean, violations, details = EvidenceValidator.verify_anti_mocking_and_authenticity(
            workspace=self.workspace,
            user_reference_images=user_refs
        )

        self.assertFalse(is_clean, "Script opening reference image to extract masks must be flagged as non-compliant")
        self.assertTrue(any("Rule 6c" in v for v in violations), f"Expected Rule 6c violation in: {violations}")
        self.assertIn("script_reference_tracing", details)

    def test_evidence_validator_evaluation_script_exempted(self):
        """
        Verifies that legitimate evaluation/metric scripts (evaluate_*.py) comparing deliverables
        against references are exempted from Rule 6c tracing violation.
        """
        scripts_dir = os.path.join(self.workspace, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)

        eval_script = os.path.join(scripts_dir, "evaluate_calendar_bear.py")
        with open(eval_script, "w", encoding="utf-8") as f:
            f.write(
                'from PIL import Image\n'
                'import numpy as np\n'
                'REF_IMAGE_PATH = ".references/user_inputs/sample_bear.png"\n'
                'ref_img = Image.open(REF_IMAGE_PATH)\n'
                'cand_img = Image.open("output/main_360.png")\n'
                '# compute SSIM metric only\n'
                'diff = np.mean(np.array(ref_img) - np.array(cand_img))\n'
                'print(f"Similarity metric: {diff}")\n'
            )

        is_clean, violations, _ = EvidenceValidator.verify_anti_mocking_and_authenticity(
            workspace=self.workspace,
            user_reference_images=[{"filename": "sample_bear.png", "path": "some/path"}]
        )

        # Evaluation script should NOT trigger Rule 6c
        r6c_violations = [v for v in violations if "Rule 6c" in v]
        self.assertEqual(len(r6c_violations), 0, f"Evaluation script falsely flagged: {r6c_violations}")

    def test_user_question_priority_and_anti_tracing_in_constitution_and_safety_headers(self):
        """
        Verifies that TOOL_SAFETY_HEADER and PROJECT_CONSTITUTION.md include both
        User Question Direct Answer First and Anti-Tracing Law.
        """
        from agent_runner import TOOL_SAFETY_HEADER
        self.assertIn("사용자 질문에 대한 직접 답변 최우선 원칙", TOOL_SAFETY_HEADER)
        self.assertIn("User Question Direct Answer First", TOOL_SAFETY_HEADER)
        self.assertIn("레퍼런스 이미지 외곽선 추출(Tracing) 및 소스코드 내 로드 원천 금지", TOOL_SAFETY_HEADER)
        self.assertIn("Anti-Tracing", TOOL_SAFETY_HEADER)

        const_path = os.path.join(WORKSPACE_ROOT, "PROJECT_CONSTITUTION.md")
        self.assertTrue(os.path.isfile(const_path))
        with open(const_path, "r", encoding="utf-8") as cf:
            const_text = cf.read()
        self.assertIn("Anti-Tracing Law", const_text)
        self.assertIn("User Question Direct Answer First", const_text)

    def test_auditor_and_coder_prompts_prioritize_user_direct_answer(self):
        """
        Verifies that TrioAgentRunner's auditor and coder prompt templates mandate
        User Direct Answer as Section (1) rather than rigid meta-collaboration reports.
        """
        trio_runner_file = os.path.join(WORKSPACE_ROOT, "trio_agent_runner.py")
        with open(trio_runner_file, "r", encoding="utf-8") as tf:
            trio_code = tf.read()

        self.assertIn("### 💬 사용자 질문 및 요청에 대한 직접 답변", trio_code)
        self.assertIn("User Question Direct Answer First", trio_code)
        self.assertIn("외곽선 추출(Tracing)", trio_code)

    def test_detect_base64_embedded_reference_image_tracing(self):
        """
        Verifies that Coder attempting to bypass static file checks by embedding
        base64 encoded reference images or using base64.b64decode to forge assets
        is detected and rejected under Rule 6c.
        """
        scripts_dir = os.path.join(self.workspace, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)

        cheating_b64_script = os.path.join(scripts_dir, "build_traced_emoticon.py")
        with open(cheating_b64_script, "w", encoding="utf-8") as f:
            f.write(
                'import base64\n'
                'import io\n'
                'from PIL import Image\n'
                'b64_data = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="\n'
                'raw_bytes = base64.b64decode(b64_data)\n'
                'img = Image.open(io.BytesIO(raw_bytes))\n'
                'img.save("output_emoticon.png")\n'
            )

        is_clean, violations, details = EvidenceValidator.verify_anti_mocking_and_authenticity(
            workspace=self.workspace
        )

        self.assertFalse(is_clean, "Script using base64 decoded images to forge deliverables must be rejected")
        self.assertTrue(any("Rule 6c" in v for v in violations), f"Expected Rule 6c violation in: {violations}")
        self.assertIn("script_reference_tracing", details)

    def test_audit_runtime_asset_provenance_rule_6d(self):
        """
        Verifies that Rule 6d audits .blend procedural files and quality_gate_audit.json
        for verified procedural metrics and zero external image textures.
        """
        audit_file = os.path.join(self.workspace, "quality_gate_audit.json")
        with open(audit_file, "w", encoding="utf-8") as af:
            af.write(
                '{\n'
                '  "overall_score": 95.51,\n'
                '  "passed_95_gate": true,\n'
                '  "full_ssim_percent": 92.68,\n'
                '  "edge_chamfer_percent": 98.61\n'
                '}\n'
            )

        # Create dummy blend file
        blend_path = os.path.join(self.workspace, "calendar_bear_master.blend")
        with open(blend_path, "wb") as bf:
            bf.write(b"BLENDER_V500")

        is_clean, violations, details = EvidenceValidator.audit_runtime_asset_provenance(
            workspace=self.workspace
        )
        self.assertTrue(is_clean)
        self.assertEqual(len(violations), 0)
        self.assertIn("calendar_bear_master.blend", details.get("found_blend_files", []))
        self.assertEqual(details.get("quality_gate_audit", {}).get("overall_score"), 95.51)

    def test_extended_qa_prompt_enforces_user_question_priority(self):
        """
        Verifies that extended QA prompt template (for QA2, QA4, QA6, etc.)
        strictly mandates User Question Direct Answer First and Anti-Tracing.
        """
        trio_runner_file = os.path.join(WORKSPACE_ROOT, "trio_agent_runner.py")
        with open(trio_runner_file, "r", encoding="utf-8") as tf:
            trio_code = tf.read()

        self.assertIn("사용자 원본 질문 및 요청 직접 답변 최우선 확인 (User Question Direct Answer First)", trio_code)
        self.assertIn("안티 트레이싱 (Anti-Mocking, Anti-Tracing & Provenance)", trio_code)


if __name__ == "__main__":
    unittest.main()


