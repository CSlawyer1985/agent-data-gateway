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


class _ProfileFixture(unittest.TestCase):
    """只建 fixture、不含断言，供下面两个测试类共用（避免继承导致重跑）。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.pkg = root / "pkg"
        cls.pkg.mkdir()
        (cls.pkg / "带图合同.pdf").write_bytes(make_pdf_digital_with_image())
        cls.results = {}
        cls.outs = {}
        for profile in ("fast", "balanced", "quality"):
            out = root / f"out_{profile}"
            _run(["scan", str(cls.pkg), "--out", str(out)])
            _run(["parse", str(cls.pkg), "--out", str(out), "--no-index", "--profile", profile])
            cls.outs[profile] = out
            cls.results[profile] = json.loads((out / "manifest.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _pdf_item(self, profile):
        return next(it for it in self.results[profile]["items"] if it["kind"] == "pdf")

    def _execution(self, profile, rule_id):
        return next(e for e in self._pdf_item(profile)["chain_executions"] if e["rule_id"] == rule_id)


class ProfileExecutionTest(_ProfileFixture):
    def test_signal_probe_reached_the_manifest(self):
        signals = self._pdf_item("fast")["signals"]
        self.assertEqual(signals["text_layer"], "present")
        self.assertEqual(signals["page_images"], "present")

    def test_primary_chain_runs_under_every_profile(self):
        for profile in ("fast", "balanced", "quality"):
            self.assertEqual(self._execution(profile, "pdf-digital-simple")["role"], "primary")

    def test_fast_drops_heavy_tools_from_the_primary_chain(self):
        """fast 要的是尽快出结果，重工具主动不试——记在 skipped_workers 里。"""
        primary = self._execution("fast", "pdf-digital-simple")
        dropped = [row["worker"] for row in primary.get("skipped_workers", [])]
        self.assertIn("docling", dropped)
        self.assertEqual(self._execution("balanced", "pdf-digital-simple").get("skipped_workers"), [])

    def test_fast_skips_the_parallel_chain_and_says_why(self):
        """少跑必须显性：跳过的链留在记录里，带原因（ADR 0005 决策 5）。"""
        skipped = self._execution("fast", "pdf-visual")
        self.assertEqual(skipped["status"], "skipped")
        self.assertEqual(skipped["reason"], "profile_fast")

    def test_only_quality_executes_the_parallel_chain(self):
        """并跑属于 quality。balanced 是「单个更好的工作流」，不是「多个」。"""
        self.assertEqual(self._execution("balanced", "pdf-visual")["status"], "skipped")
        executed = self._execution("quality", "pdf-visual")
        self.assertNotEqual(executed["status"], "skipped")
        self.assertEqual(executed["role"], "parallel")

    def test_primary_result_is_unaffected_by_the_parallel_chain(self):
        """并跑不得改写主轨结论（spec 11 §4 规则 4）。"""
        balanced, quality = self._pdf_item("balanced"), self._pdf_item("quality")
        self.assertEqual(balanced["status"], quality["status"])
        self.assertEqual(balanced["renditions"], quality["renditions"])

    def test_execution_record_never_leaks_absolute_paths(self):
        """执行记录里的 detail 来自 worker 异常消息，落盘前必须 scrub（spec 09）。"""
        for profile in ("fast", "balanced", "quality"):
            blob = json.dumps(self.results[profile], ensure_ascii=False)
            self.assertNotIn(str(self.pkg), blob)
            self.assertNotIn("/private/", blob)


class StatusTellsTheTruthTest(_ProfileFixture):
    """「已读全」只在文件都读到、且计划的链都跑过时才说得出口。

    spec 11 §6.2 不变量 1：快速档的含义是「你让我先跑快的」，不是「我假装提完了」。
    """

    def _status(self, profile):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["status", str(self.pkg), "--out", str(self.outs[profile])])
        return code, out.getvalue()

    def test_fast_never_claims_everything_was_read(self):
        code, text = self._status("fast")
        self.assertNotIn("已读全", text)
        self.assertIn("未执行", text)
        self.assertIn("pdf-visual", text)
        self.assertIn("profile_fast", text)

    def test_unrun_chain_makes_it_one_not_zero(self):
        """退出码 1 = 完成但有未读全项。链没跑就是没读全，不能报 0。"""
        self.assertEqual(self._status("fast")[0], 1)

    def test_quality_may_claim_it_when_nothing_was_skipped(self):
        code, text = self._status("quality")
        self.assertIn("已读全", text)
        self.assertEqual(code, 0)

    def test_json_carries_the_same_fact_for_agents(self):
        """人看的那句话和 agent 读的字段必须同源，不能只修一边。"""
        out, _err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(_err):
            cli.main(["status", str(self.pkg), "--out", str(self.outs["fast"]), "--json"])
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["chains_not_run_count"], 1)
        self.assertEqual(payload["chains_not_run"][0]["rule_id"], "pdf-visual")

    def test_manifest_and_report_show_executed_and_skipped_chains(self):
        """机器字段已有；两个 Markdown 入口也必须说清哪条跑了、哪条没跑。"""
        fast_out = self.outs["fast"]
        self.assertEqual(_run(["report", str(self.pkg), "--out", str(fast_out)]), 0)
        for path in (fast_out / "manifest.md", fast_out / "reports" / "report.md"):
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertIn("解析链执行记录", text)
                self.assertIn("带图合同.pdf", text)
                self.assertIn("pdf-digital-simple", text)
                self.assertIn("已完成", text)
                self.assertIn("未用 `docling`（profile_fast_heavy）", text)
                self.assertIn("pdf-visual", text)
                self.assertIn("未执行", text)
                self.assertIn("原因 `profile_fast`", text)


if __name__ == "__main__":
    unittest.main()
