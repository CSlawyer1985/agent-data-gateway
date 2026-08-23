"""profile 端到端（ADR 0005）：计划不只是计划，它决定实际跑了什么。

这里断言的是「这条链有没有被执行」，不是「worker 装没装」——
后者随机器而变，前者是契约。
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from gateway import cli
from gateway.selftest import make_pdf_digital_with_image


def _run(argv: list[str]) -> int:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        return cli.main(argv)


class ProfileExecutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.pkg = root / "pkg"
        cls.pkg.mkdir()
        (cls.pkg / "带图合同.pdf").write_bytes(make_pdf_digital_with_image())
        cls.results = {}
        for profile in ("fast", "balanced"):
            out = root / f"out_{profile}"
            _run(["scan", str(cls.pkg), "--out", str(out)])
            _run(["parse", str(cls.pkg), "--out", str(out), "--no-index", "--profile", profile])
            cls.results[profile] = json.loads((out / "manifest.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _pdf_item(self, profile):
        return next(it for it in self.results[profile]["items"] if it["kind"] == "pdf")

    def _execution(self, profile, rule_id):
        return next(e for e in self._pdf_item(profile)["chain_executions"] if e["rule_id"] == rule_id)

    def test_signal_probe_reached_the_manifest(self):
        signals = self._pdf_item("fast")["signals"]
        self.assertEqual(signals["text_layer"], "present")
        self.assertEqual(signals["page_images"], "present")

    def test_primary_chain_runs_under_every_profile(self):
        for profile in ("fast", "balanced"):
            self.assertEqual(self._execution(profile, "pdf-digital-simple")["role"], "primary")

    def test_fast_skips_the_parallel_chain_and_says_why(self):
        """少跑必须显性：跳过的链留在记录里，带原因（ADR 0005 决策 5）。"""
        skipped = self._execution("fast", "pdf-visual")
        self.assertEqual(skipped["status"], "skipped")
        self.assertEqual(skipped["reason"], "profile_fast")

    def test_balanced_actually_executes_the_parallel_chain(self):
        """balanced 下这条链必须真的跑过——成没成功取决于 worker 是否可用，不在此断言。"""
        executed = self._execution("balanced", "pdf-visual")
        self.assertNotEqual(executed["status"], "skipped")
        self.assertEqual(executed["role"], "parallel")

    def test_primary_result_is_unaffected_by_the_parallel_chain(self):
        """并跑不得改写主轨结论（spec 11 §4 规则 4）。"""
        fast, balanced = self._pdf_item("fast"), self._pdf_item("balanced")
        self.assertEqual(fast["status"], balanced["status"])
        self.assertEqual(fast["renditions"], balanced["renditions"])

    def test_execution_record_never_leaks_absolute_paths(self):
        """执行记录里的 detail 来自 worker 异常消息，落盘前必须 scrub（spec 09）。"""
        for profile in ("fast", "balanced"):
            blob = json.dumps(self.results[profile], ensure_ascii=False)
            self.assertNotIn(str(self.pkg), blob)
            self.assertNotIn("/private/", blob)


if __name__ == "__main__":
    unittest.main()
