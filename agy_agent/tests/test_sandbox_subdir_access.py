#!/usr/bin/env python3
"""
Unit tests for the OpenCode channel-isolation sandbox policy.

정책(요구사항):
  • 상위폴더(조상/형제 디렉토리) → 접근 금지(deny)
  • 워크스페이스 및 그 '하위'의 심볼릭 링크가 가리키는 실제 대상 → 접근 허용(allow)

즉 "상위폴더는 막고, 하위폴더(링크 대상 포함)는 열어주는" 경계를 검증합니다.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

from engine_manager import OpenCodeAdapter  # noqa: E402


class TestSandboxSubdirAccess(unittest.TestCase):
    def setUp(self):
        # bwrap 은 /tmp 를 새 tmpfs 로 덮어쓰므로, 심볼릭 링크 대상이 살아있도록
        # /tmp 밖(홈 캐시)에 테스트 트리를 만듭니다.
        cache_dir = os.path.expanduser("~/.cache")
        try:
            os.makedirs(cache_dir, exist_ok=True)
            self.base = tempfile.mkdtemp(prefix="agy_sandbox_test_", dir=cache_dir)
        except (PermissionError, OSError) as e:
            self.skipTest(f"~/.cache is not writable in this sandbox environment: {e}")
        self.ws = os.path.join(self.base, "ws")
        self.repo = os.path.join(self.base, "repo")
        os.makedirs(os.path.join(self.ws, "normal_sub"), exist_ok=True)
        os.makedirs(os.path.join(self.repo, "agy_agent"), exist_ok=True)
        with open(os.path.join(self.repo, "agy_agent", "deep.py"), "w") as f:
            f.write("original\n")
        with open(os.path.join(self.repo, "top.py"), "w") as f:
            f.write("original\n")
        os.symlink(os.path.join(self.repo, "agy_agent"), os.path.join(self.ws, "agy_agent"))
        os.symlink(os.path.join(self.repo, "top.py"), os.path.join(self.ws, "top.py"))

    def tearDown(self):
        if hasattr(self, "base") and os.path.exists(self.base):
            shutil.rmtree(self.base, ignore_errors=True)

    def test_collect_includes_linked_subdir_but_not_parent(self):
        roots = OpenCodeAdapter._collect_linked_allowed_roots(self.ws)
        # 링크 대상(하위폴더/파일)은 포함
        self.assertIn(os.path.join(self.repo, "agy_agent"), roots)
        self.assertIn(os.path.join(self.repo, "top.py"), roots)
        # 상위/형제 디렉토리 자체는 포함되지 않음
        self.assertNotIn(self.base, roots)
        self.assertNotIn(self.repo, roots)
        self.assertNotIn(os.path.dirname(self.base), roots)

    def test_permission_patterns_deny_first_allow_linked(self):
        roots = OpenCodeAdapter._collect_linked_allowed_roots(self.ws)
        pats = OpenCodeAdapter._build_permission_patterns(roots)
        keys = list(pats.keys())
        # 마지막 일치 규칙이 승리하므로 전체 deny 가 먼저 와야 함
        self.assertEqual(keys[0], "*")
        self.assertEqual(pats["*"], "deny")
        self.assertEqual(pats.get(os.path.join(self.repo, "agy_agent")), "allow")
        self.assertEqual(pats.get(os.path.join(self.repo, "agy_agent") + "/*"), "allow")

    def test_build_env_injects_patterns(self):
        env = OpenCodeAdapter()._build_env(self.ws)
        import json
        self.assertIn("OPENCODE_CONFIG_CONTENT", env)
        content = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        ext = content["permission"]["external_directory"]
        self.assertEqual(ext["*"], "deny")
        self.assertEqual(ext.get(os.path.join(self.repo, "agy_agent") + "/*"), "allow")

    @unittest.skipUnless(shutil.which("bwrap"), "bubblewrap(bwrap) 미설치")
    def test_bwrap_allows_linked_subdir_denies_parent(self):
        oc = OpenCodeAdapter()
        probe = (
            "echo -n 'linked='; (echo changed >> agy_agent/deep.py && echo OK) || echo BLOCKED; "
            "echo -n 'parent='; (touch " + os.path.join(self.base, "escaped") + " && echo OK) || echo BLOCKED"
        )
        cmd = oc._wrap_sandbox(["bash", "-c", probe], self.ws)
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=self.ws,
            env=oc._build_env(self.ws), timeout=60,
        )
        out = result.stdout
        self.assertIn("linked=OK", out, msg=result.stderr)
        self.assertIn("parent=BLOCKED", out, msg=result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.base, "escaped")))


if __name__ == "__main__":
    unittest.main()
