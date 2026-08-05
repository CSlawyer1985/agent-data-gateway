"""压缩包安全模型单元测试（spec 06）。"""

import io
import tarfile
import unittest
import zipfile
from pathlib import Path

from gateway.security import ArchiveGuard, safe_join
from gateway.errors import SecurityViolation
from tests.fixtures import make_zip_bomb, make_zip_normal, make_zip_symlink, make_zip_traversal


class ZipSecurityTest(unittest.TestCase):
    def guard(self):
        return ArchiveGuard()

    def _zf(self, data: bytes):
        return zipfile.ZipFile(io.BytesIO(data))

    def test_normal_zip_all_approved(self):
        plan = self.guard().review_zip(self._zf(make_zip_normal()), depth=1)
        names = {v.name for v in plan.approved}
        self.assertIn("a.txt", names)
        self.assertIn("b.csv", names)
        self.assertFalse(plan.truncated)

    def test_path_traversal_rejected(self):
        plan = self.guard().review_zip(self._zf(make_zip_traversal()), depth=1)
        rejected = {r.name for r in plan.rejected}
        self.assertIn("../evil.txt", rejected)
        self.assertIn("ok.txt", {v.name for v in plan.approved})

    def test_symlink_rejected(self):
        plan = self.guard().review_zip(self._zf(make_zip_symlink()), depth=1)
        rejected = {r.name for r in plan.rejected}
        self.assertIn("link.txt", rejected)

    def test_zip_bomb_rejected_by_ratio(self):
        plan = self.guard().review_zip(self._zf(make_zip_bomb()), depth=1)
        ratios = [r.reason for r in plan.rejected]
        self.assertTrue(any("压缩比" in r for r in ratios), ratios)

    def test_name_checks(self):
        g = self.guard()
        self.assertIsNotNone(g.check_name("../a"))
        self.assertIsNotNone(g.check_name("/etc/passwd"))
        self.assertIsNotNone(g.check_name("C:\\windows"))
        self.assertIsNone(g.check_name("dir/a.txt"))

    def test_safe_join_blocks_escape(self):
        with self.assertRaises(SecurityViolation):
            with __import__("tempfile").TemporaryDirectory() as d:
                safe_join(Path(d), "../escape")

    def test_tar_path_traversal_rejected(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo("../evil.txt")
            data = b"x"
            info.size = 1
            tf.addfile(info, io.BytesIO(data))
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r") as tf:
            plan = self.guard().review_tar(tf, depth=1)
        self.assertIn("../evil.txt", {r.name for r in plan.rejected})


if __name__ == "__main__":
    unittest.main()
